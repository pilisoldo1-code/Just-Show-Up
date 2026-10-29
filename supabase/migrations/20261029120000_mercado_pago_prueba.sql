-- Mercado Pago Checkout Pro, SOLO MODO DE PRUEBA (proyecto universitario: dinero ficticio).
-- Solo agrega: no modifica ni borra datos existentes. Mercado Pago queda APAGADO
-- (mp_configuracion.habilitado = false) hasta que se encienda a propósito.
-- El Access Token de prueba se guarda cifrado en Supabase Vault (secreto 'mp_access_token') y
-- solo lo puede leer el servidor (service_role). Alumnas, profesora y anon no pueden leerlo.
-- El pago se registra solo después de consultarlo a Mercado Pago (lo hace el servidor).

-- ---------- pagos: nuevo medio y número de operación ----------

alter table public.pago drop constraint pago_medio_pago_check;
alter table public.pago add constraint pago_medio_pago_check
    check (medio_pago in ('efectivo', 'transferencia', 'mercado_pago'));
alter table public.pago add column id_transaccion text;
-- Un pago de Mercado Pago se registra una sola vez (aunque lleguen la vuelta y el aviso a la vez).
create unique index pago_id_transaccion_unico on public.pago (id_transaccion) where id_transaccion is not null;
alter table public.pago add constraint pago_mercado_pago_con_operacion
    check ((medio_pago = 'mercado_pago') = (id_transaccion is not null));

-- ---------- configuración (una sola fila) ----------

create table public.mp_configuracion (
    id smallint primary key default 1 check (id = 1),
    habilitado boolean not null default false,
    modo text not null default 'prueba' check (modo = 'prueba'),
    actualizado_en timestamptz not null default now()
);
insert into public.mp_configuracion (id) values (1);
alter table public.mp_configuracion enable row level security;
revoke all on public.mp_configuracion from anon, authenticated;

-- ---------- intentos de pago y avisos ----------

-- Cada vez que una alumna toca "Pagar con Mercado Pago" (el importe sale de la factura).
create table public.mp_intento (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    id_alumna uuid not null references public.profiles(id),
    referencia text not null unique,
    monto numeric(10,2) not null check (monto > 0),
    preference_id text,
    estado text not null default 'creado' check (estado in ('creado', 'aprobado', 'rechazado', 'a_revisar')),
    id_pago_mp text,
    id_pago bigint references public.pago(id),
    detalle text,
    creado_en timestamptz not null default now(),
    actualizado_en timestamptz not null default now()
);
create index on public.mp_intento (id_resumen_mensual);
create index on public.mp_intento (id_alumna, estado);

-- Registro técnico de lo que llega de Mercado Pago (sin datos de tarjetas ni credenciales).
create table public.mp_evento (
    id bigint generated always as identity primary key,
    recibido_en timestamptz not null default now(),
    origen text not null check (origen in ('aviso', 'vuelta', 'conciliacion')),
    tipo text,
    id_recurso text,
    firma text check (firma in ('valida', 'invalida', 'sin_clave', 'no_aplica')),
    resultado text
);

alter table public.mp_intento enable row level security;
alter table public.mp_evento enable row level security;
create policy "mp_intento: profesora o propio" on public.mp_intento
    for select to authenticated using (public.es_profesora() or id_alumna = (select auth.uid()));
create policy "mp_evento: solo profesora" on public.mp_evento
    for select to authenticated using (public.es_profesora());
revoke all on public.mp_intento, public.mp_evento from anon;
revoke insert, update, delete, truncate, references, trigger on public.mp_intento, public.mp_evento from authenticated;

-- ---------- funciones solo del servidor (service_role) ----------

-- Configuración para el servidor: interruptor + credenciales de prueba guardadas en Vault.
create or replace function public.mp_configuracion_servidor()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'habilitado', (select habilitado from public.mp_configuracion where id = 1),
        'access_token', (select decrypted_secret from vault.decrypted_secrets where name = 'mp_access_token' limit 1),
        'webhook_secret', (select decrypted_secret from vault.decrypted_secrets where name = 'mp_webhook_secret' limit 1)
    );
$$;

-- Guarda (o reemplaza) una credencial de prueba en Vault. Devuelve 'ok' o 'nombre_invalido'.
create or replace function public.mp_guardar_secreto(p_nombre text, p_valor text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id uuid;
begin
    if p_nombre not in ('mp_access_token', 'mp_webhook_secret') or coalesce(btrim(p_valor), '') = '' then
        return 'nombre_invalido';
    end if;
    select id into v_id from vault.secrets where name = p_nombre;
    if v_id is null then
        perform vault.create_secret(btrim(p_valor), p_nombre, 'Mercado Pago - SOLO credenciales de prueba');
    else
        perform vault.update_secret(v_id, btrim(p_valor));
    end if;
    return 'ok';
end;
$$;

create or replace function public.mp_cambiar_habilitado(p_habilitado boolean)
returns boolean
language sql
security definer
set search_path = ''
as $$
    update public.mp_configuracion set habilitado = p_habilitado, actualizado_en = now() where id = 1
    returning habilitado;
$$;

create or replace function public.mp_guardar_preferencia(p_id_intento bigint, p_preference_id text)
returns void
language sql
security definer
set search_path = ''
as $$
    update public.mp_intento set preference_id = p_preference_id, actualizado_en = now() where id = p_id_intento;
$$;

create or replace function public.mp_registrar_evento(p_origen text, p_tipo text, p_id_recurso text,
                                                      p_firma text, p_resultado text)
returns void
language sql
security definer
set search_path = ''
as $$
    insert into public.mp_evento (origen, tipo, id_recurso, firma, resultado)
    values (p_origen, left(p_tipo, 60), left(p_id_recurso, 60), p_firma, left(p_resultado, 120));
$$;

-- Registra el resultado de un pago YA CONSULTADO a Mercado Pago por el servidor.
-- Solo un pago aprobado, del importe exacto del intento y del total actual de una factura
-- confirmada y pendiente, pasa la factura a "pagado". Idempotente por número de operación.
-- Devuelve {"resultado": ok | ya_registrado | rechazado | pendiente | a_revisar | intento_desconocido}
create or replace function public.registrar_pago_mercado_pago(p_referencia text, p_id_pago_mp text, p_estado text,
                                                              p_monto numeric, p_moneda text, p_fecha date)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id_resumen bigint;
    i public.mp_intento;
    f public.resumen_mensual;
    v_id_pago bigint;
    v_motivo text;
begin
    select id_resumen_mensual into v_id_resumen from public.mp_intento where referencia = p_referencia;
    if not found or coalesce(p_id_pago_mp, '') = '' then
        return jsonb_build_object('resultado', 'intento_desconocido');
    end if;
    -- mismo orden de bloqueo que el resto de la facturación: factura y después lo demás
    select * into f from public.resumen_mensual where id = v_id_resumen for update;
    select * into i from public.mp_intento where referencia = p_referencia for update;

    if exists (select 1 from public.pago where id_transaccion = p_id_pago_mp) then
        update public.mp_intento set estado = 'aprobado', actualizado_en = now()
        where id = i.id and estado <> 'aprobado' and id_pago_mp = p_id_pago_mp;
        return jsonb_build_object('resultado', 'ya_registrado');
    end if;

    if p_estado is distinct from 'approved' then
        if i.estado in ('creado', 'rechazado') then
            update public.mp_intento
            set estado = case when p_estado in ('rejected', 'cancelled') then 'rechazado' else estado end,
                id_pago_mp = p_id_pago_mp, detalle = left(p_estado, 40), actualizado_en = now()
            where id = i.id;
        end if;
        return jsonb_build_object('resultado',
            case when p_estado in ('rejected', 'cancelled') then 'rechazado' else 'pendiente' end);
    end if;

    v_motivo := case
        when i.estado in ('aprobado', 'a_revisar') then 'el intento ya tenía otro pago'
        when p_moneda is distinct from 'UYU' or p_monto is distinct from i.monto then 'importe o moneda distintos al intento'
        when f.estado_factura <> 'confirmada' then 'la factura ya no está confirmada'
        when f.estado_pago <> 'pendiente'
             or exists (select 1 from public.pago where id_resumen_mensual = f.id and estado = 'vigente')
            then 'la factura ya tenía un pago'
        when f.total <> p_monto then 'el total de la factura cambió'
    end;
    if v_motivo is not null then
        if i.estado <> 'aprobado' then
            update public.mp_intento set estado = 'a_revisar', id_pago_mp = p_id_pago_mp, detalle = v_motivo,
                                         actualizado_en = now()
            where id = i.id;
        end if;
        return jsonb_build_object('resultado', 'a_revisar', 'motivo', v_motivo);
    end if;

    insert into public.pago (id_resumen_mensual, monto, medio_pago, fecha, registrado_por, id_transaccion)
    values (f.id, p_monto, 'mercado_pago', least(coalesce(p_fecha, public._hoy_mvd()), public._hoy_mvd()),
            i.id_alumna, p_id_pago_mp)
    returning id into v_id_pago;
    update public.resumen_mensual set estado_pago = 'pagado' where id = f.id;
    update public.mp_intento set estado = 'aprobado', id_pago_mp = p_id_pago_mp, id_pago = v_id_pago,
                                 detalle = null, actualizado_en = now()
    where id = i.id;
    return jsonb_build_object('resultado', 'ok', 'id_pago', v_id_pago);
exception when unique_violation then
    return jsonb_build_object('resultado', 'ya_registrado');
end;
$$;

-- ---------- función de la alumna ----------

-- La alumna inicia el pago de SU factura confirmada, publicada y pendiente. Solo alumnas
-- ficticias (@demo.invalid / @prueba.invalid) y solo si Mercado Pago está habilitado.
-- Devuelve {"resultado": ok | deshabilitado | no_autorizada | alumna_no_habilitada | no_existe
--           | no_disponible | ya_pagada | sin_cargo, "referencia", "monto", "numero", "id_intento"}
create or replace function public.crear_intento_mp(p_id_resumen bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_mail text;
    f public.resumen_mensual;
    v_ref text;
    v_id bigint;
begin
    if not coalesce((select habilitado from public.mp_configuracion where id = 1), false) then
        return jsonb_build_object('resultado', 'deshabilitado');
    end if;
    if v_uid is null or public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select lower(mail) into v_mail from public.profiles where id = v_uid;
    if v_mail is null or not (v_mail like '%@demo.invalid' or v_mail like '%@prueba.invalid') then
        return jsonb_build_object('resultado', 'alumna_no_habilitada');
    end if;
    select * into f from public.resumen_mensual where id = p_id_resumen for update;
    if not found or f.id_alumna <> v_uid then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if f.estado_factura <> 'confirmada' or public._ahora() < public._inicio_publicacion(f.mes, f.anio) then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    if f.estado_pago = 'sin_cargo' or f.total <= 0 then
        return jsonb_build_object('resultado', 'sin_cargo');
    end if;
    if f.estado_pago <> 'pendiente'
       or exists (select 1 from public.pago where id_resumen_mensual = f.id and estado = 'vigente') then
        return jsonb_build_object('resultado', 'ya_pagada');
    end if;
    v_ref := 'JSU-' || f.id || '-' || replace(gen_random_uuid()::text, '-', '');
    insert into public.mp_intento (id_resumen_mensual, id_alumna, referencia, monto)
    values (f.id, v_uid, v_ref, f.total)
    returning id into v_id;
    return jsonb_build_object('resultado', 'ok', 'id_intento', v_id, 'referencia', v_ref,
                              'monto', f.total, 'numero', f.numero, 'mes', f.mes, 'anio', f.anio);
end;
$$;

-- ---------- permisos de las funciones ----------

revoke execute on function public.mp_configuracion_servidor(), public.mp_guardar_secreto(text, text),
    public.mp_cambiar_habilitado(boolean), public.mp_guardar_preferencia(bigint, text),
    public.mp_registrar_evento(text, text, text, text, text),
    public.registrar_pago_mercado_pago(text, text, text, numeric, text, date),
    public.crear_intento_mp(bigint) from public, anon, authenticated;
grant execute on function public.mp_configuracion_servidor(), public.mp_guardar_secreto(text, text),
    public.mp_cambiar_habilitado(boolean), public.mp_guardar_preferencia(bigint, text),
    public.mp_registrar_evento(text, text, text, text, text),
    public.registrar_pago_mercado_pago(text, text, text, numeric, text, date) to service_role;
grant execute on function public.crear_intento_mp(bigint) to authenticated;
