-- Notificaciones, fase B: infraestructura de correos (sin activar nada).
-- - notificacion_envio: un registro por aviso y canal. La clave (tipo, referencia, canal) es única: el mismo aviso nunca
--   se encola dos veces, aunque dos tareas corran a la vez. Estados: pendiente → enviando → enviado; con error vuelve a
--   pendiente con espera creciente y, al llegar al máximo de intentos, fallido. descartado = ya no corresponde (se pagó,
--   se canceló, venció...); omitido = la persona no quiere correos o el correo es ficticio.
-- - Parámetros nuevos en notificacion_parametros: interruptor general (correo_activo, APAGADO), topes por hora y por día
--   (por debajo del límite de 500 de Gmail, que comparte con los correos de inicio de sesión), intentos, hora de envío de
--   los avisos de facturas y la dirección del sitio para los enlaces.
-- - profiles.recibir_correos: preferencia «Recibir notificaciones por correo electrónico» (Mi perfil). Solo afecta los
--   avisos automáticos: los correos de Supabase Auth (invitación, recuperar contraseña, cambio de correo) no pasan por acá.
-- - notificaciones_reclamar / notificaciones_resultado: las usa el despachador del servidor (service_role) para tomar un
--   lote (for update skip locked: dos despachadores a la vez nunca toman la misma fila) y anotar el resultado.
-- - notificaciones_tick(): la llama pg_cron cada minuto. Si el interruptor está encendido y hay pendientes, avisa al
--   despachador con pg_net (dirección y secreto en Vault). Las tareas programadas NO se crean acá: ver
--   supabase/pendientes/notificaciones_cron_activar.sql.
-- No cambia datos existentes.

alter table public.notificacion_parametros
    add column correo_activo boolean not null default false,
    add column correo_tope_dia integer not null default 200 check (correo_tope_dia between 1 and 450),
    add column correo_tope_hora integer not null default 40 check (correo_tope_hora between 1 and 100),
    add column correo_max_intentos integer not null default 5 check (correo_max_intentos between 1 and 10),
    add column correo_hora_envio integer not null default 9 check (correo_hora_envio between 0 and 23),
    add column correo_desde timestamptz,
    add column correo_permitir_ficticios boolean not null default false,
    add column sitio_url text not null default 'https://just-show-up-theta.vercel.app'
        check (sitio_url ~ '^https?://[a-z0-9.:-]+$');

alter table public.profiles add column recibir_correos boolean not null default true;
grant select (recibir_correos) on public.profiles to authenticated;

create table public.notificacion_envio (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_usuario uuid references public.profiles(id) on delete set null,
    tipo text not null check (tipo in ('clase_cancelada', 'recordatorio_clase', 'factura_nueva', 'factura_proxima',
                                       'factura_vencida', 'factura_vencida_2', 'lugar_disponible',
                                       'reserva_confirmada', 'pago_registrado')),
    referencia bigint not null,
    canal text not null default 'correo' check (canal in ('correo')),
    estado text not null default 'pendiente'
        check (estado in ('pendiente', 'enviando', 'enviado', 'fallido', 'descartado', 'omitido')),
    prioridad integer not null default 5 check (prioridad between 0 and 9),
    intentos integer not null default 0 check (intentos >= 0),
    proximo_intento timestamptz not null default now(),
    reclamado_en timestamptz,
    enviado_en timestamptz,
    ultimo_error text check (length(ultimo_error) <= 300),
    motivo text check (length(motivo) <= 60),
    creado_en timestamptz not null default now(),
    actualizado_en timestamptz not null default now()
);
create unique index notificacion_envio_unica on public.notificacion_envio (tipo, referencia, canal);
create index notificacion_envio_cola on public.notificacion_envio (estado, proximo_intento);
create index notificacion_envio_enviados on public.notificacion_envio (enviado_en) where enviado_en is not null;
create index on public.notificacion_envio (id_usuario);
create index on public.notificacion_envio (id_empresa);

alter table public.notificacion_envio enable row level security;
revoke all on public.notificacion_envio from public, anon, authenticated;

-- ---------- preferencia de la persona con sesión ----------

create or replace function public.cambiar_preferencia_correos(p_recibir boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id uuid := (select auth.uid());
begin
    if v_id is null then
        return 'sin_sesion';
    end if;
    if p_recibir is null then
        return 'datos_invalidos';
    end if;
    update public.profiles set recibir_correos = p_recibir where id = v_id;
    if not found then
        return 'sin_sesion';
    end if;
    -- apagarlos frena también lo que ya estaba en cola
    if not p_recibir then
        update public.notificacion_envio
           set estado = 'omitido', motivo = 'preferencia', actualizado_en = now()
         where id_usuario = v_id and estado = 'pendiente';
    end if;
    return 'ok';
end;
$$;

-- ---------- vigencia de un aviso (la fase C la completa por tipo) ----------

-- Datos para armar el correo, o null si el aviso ya no corresponde. Esta versión solo existe para probar la
-- infraestructura; la fase C la reemplaza con la regla de cada tipo.
create or replace function public._notificacion_datos(p_envio public.notificacion_envio)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object('tipo', p_envio.tipo, 'referencia', p_envio.referencia);
$$;

-- ---------- despachador (solo service_role) ----------

create or replace function public.notificaciones_reclamar(p_lote integer default 20)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_par public.notificacion_parametros;
    v_cupo integer;
    v_fila public.notificacion_envio;
    v_datos jsonb;
    v_mail text;
    v_recibe boolean;
    v_activa boolean;
    v_empresa text;
    v_salida jsonb := '[]'::jsonb;
begin
    select * into v_par from public.notificacion_parametros where id = 1;
    if not found or not v_par.correo_activo then
        return v_salida;
    end if;
    -- una toma que quedó colgada (el servidor se cortó a mitad) vuelve a la cola; el intento ya quedó contado
    update public.notificacion_envio
       set estado = case when intentos >= v_par.correo_max_intentos then 'fallido' else 'pendiente' end,
           ultimo_error = coalesce(ultimo_error, 'sin respuesta del despachador'), actualizado_en = now()
     where estado = 'enviando' and reclamado_en < now() - interval '10 minutes';

    -- topes: lo enviado (o en envío) en la última hora y en las últimas 24 horas
    v_cupo := least(
        greatest(p_lote, 0),
        v_par.correo_tope_hora - (select count(*) from public.notificacion_envio
                                   where estado = 'enviando' or (estado = 'enviado' and enviado_en > now() - interval '1 hour')),
        v_par.correo_tope_dia - (select count(*) from public.notificacion_envio
                                  where estado = 'enviando' or (estado = 'enviado' and enviado_en > now() - interval '24 hours')));
    if v_cupo <= 0 then
        return v_salida;
    end if;

    for v_fila in
        select * from public.notificacion_envio
         where estado = 'pendiente' and proximo_intento <= now()
         order by prioridad, proximo_intento, id
         limit v_cupo
         for update skip locked
    loop
        select p.mail, p.recibir_correos into v_mail, v_recibe from public.profiles p where p.id = v_fila.id_usuario;
        select m.activa and m.es_alumna into v_activa from public.membresia m
         where m.id_usuario = v_fila.id_usuario and m.id_empresa = v_fila.id_empresa;
        if v_mail is null or not coalesce(v_activa, false) then
            update public.notificacion_envio set estado = 'descartado', motivo = 'sin_alumna', actualizado_en = now()
             where id = v_fila.id;
            continue;
        end if;
        if not v_recibe then
            update public.notificacion_envio set estado = 'omitido', motivo = 'preferencia', actualizado_en = now()
             where id = v_fila.id;
            continue;
        end if;
        if not v_par.correo_permitir_ficticios and lower(v_mail) ~ '\.(invalid|test|example|localhost)$' then
            update public.notificacion_envio set estado = 'omitido', motivo = 'correo_ficticio', actualizado_en = now()
             where id = v_fila.id;
            continue;
        end if;
        v_datos := public._notificacion_datos(v_fila);
        if v_datos is null then
            update public.notificacion_envio set estado = 'descartado', motivo = 'ya_no_corresponde', actualizado_en = now()
             where id = v_fila.id;
            continue;
        end if;
        select e.nombre into v_empresa from public.empresa e where e.id = v_fila.id_empresa;
        update public.notificacion_envio
           set estado = 'enviando', intentos = intentos + 1, reclamado_en = now(), actualizado_en = now()
         where id = v_fila.id;
        v_salida := v_salida || jsonb_build_array(jsonb_build_object(
            'id', v_fila.id, 'tipo', v_fila.tipo, 'mail', v_mail, 'empresa', v_empresa, 'sitio', v_par.sitio_url,
            'datos', v_datos));
    end loop;
    return v_salida;
end;
$$;

-- p_resultado: 'enviado' | 'error' (cuenta como intento: espera 2, 4, 8... minutos, hasta 1 hora) |
-- 'liberar' (no se llegó a intentar, por ejemplo porque se cortó la conexión con el servidor de correo: no cuenta).
create or replace function public.notificaciones_resultado(p_id bigint, p_resultado text, p_error text default null)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_max integer := (select correo_max_intentos from public.notificacion_parametros where id = 1);
    v_fila public.notificacion_envio;
begin
    select * into v_fila from public.notificacion_envio where id = p_id for update;
    if not found or v_fila.estado <> 'enviando' then
        return 'no_corresponde';
    end if;
    if p_resultado = 'enviado' then
        update public.notificacion_envio
           set estado = 'enviado', enviado_en = now(), ultimo_error = null, actualizado_en = now()
         where id = p_id;
    elsif p_resultado = 'error' then
        update public.notificacion_envio
           set estado = case when v_fila.intentos >= v_max then 'fallido' else 'pendiente' end,
               proximo_intento = now() + least(power(2, v_fila.intentos)::integer, 60) * interval '1 minute',
               ultimo_error = left(coalesce(p_error, 'error'), 300), actualizado_en = now()
         where id = p_id;
    elsif p_resultado = 'liberar' then
        update public.notificacion_envio
           set estado = 'pendiente', intentos = greatest(intentos - 1, 0), actualizado_en = now()
         where id = p_id;
    else
        return 'resultado_invalido';
    end if;
    return 'ok';
end;
$$;

create or replace function public.notificaciones_resumen()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'activo', (select correo_activo from public.notificacion_parametros where id = 1),
        'por_estado', coalesce((select jsonb_object_agg(estado, n) from (
            select estado, count(*) n from public.notificacion_envio group by estado) x), '{}'::jsonb),
        'enviados_24h', (select count(*) from public.notificacion_envio
                         where estado = 'enviado' and enviado_en > now() - interval '24 hours'),
        'enviados_1h', (select count(*) from public.notificacion_envio
                        where estado = 'enviado' and enviado_en > now() - interval '1 hour'));
$$;

-- ---------- tarea de cada minuto (la crea notificaciones_cron_activar.sql, nunca esta migración) ----------

create or replace function public._notificaciones_llamar_despachador()
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_url text;
    v_secreto text;
begin
    if to_regprocedure('net.http_post(text, jsonb, jsonb, jsonb, integer)') is null then
        return 'sin_pg_net';
    end if;
    select decrypted_secret into v_url from vault.decrypted_secrets where name = 'notificaciones_url';
    select decrypted_secret into v_secreto from vault.decrypted_secrets where name = 'notificaciones_secreto';
    if v_url is null or v_secreto is null or v_url !~ '^https?://' then
        return 'sin_configurar';
    end if;
    execute 'select net.http_post(url := $1, body := $2, headers := $3, timeout_milliseconds := 20000)'
        using v_url, '{}'::jsonb,
              jsonb_build_object('Content-Type', 'application/json', 'Authorization', 'Bearer ' || v_secreto);
    return 'llamado';
end;
$$;

create or replace function public.notificaciones_tick()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_par public.notificacion_parametros;
    v_despachador text := 'nada_pendiente';
begin
    -- si la tarea anterior sigue corriendo, esta no hace nada (nunca dos a la vez)
    if not pg_try_advisory_xact_lock(hashtext('notificaciones_tick')) then
        return jsonb_build_object('resultado', 'ocupado');
    end if;
    select * into v_par from public.notificacion_parametros where id = 1;
    if not v_par.correo_activo then
        return jsonb_build_object('resultado', 'apagado');
    end if;
    if exists (select 1 from public.notificacion_envio where estado = 'pendiente' and proximo_intento <= now()) then
        v_despachador := public._notificaciones_llamar_despachador();
    end if;
    return jsonb_build_object('resultado', 'ok', 'despachador', v_despachador);
end;
$$;

-- ---------- interruptor y parámetros de correo (solo la superadministradora) ----------

create or replace function public.notificaciones_correo_activar(p_activo boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return 'sin_permiso';
    end if;
    if p_activo is null then
        return 'valor_invalido';
    end if;
    update public.notificacion_parametros
       set correo_activo = p_activo,
           -- al encender por primera vez, los avisos de hechos anteriores (reservas, pagos...) no se mandan
           correo_desde = case when p_activo then coalesce(correo_desde, now()) else correo_desde end,
           actualizado_en = now(), actualizado_por = (select auth.uid())
     where id = 1;
    -- apagado, notificaciones_reclamar no entrega nada; lo que quede en cola se revalida antes de mandarse
    return 'ok';
end;
$$;

create or replace function public.notificaciones_configurar(p_clave text, p_valor integer)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return 'sin_permiso';
    end if;
    if p_valor is null then
        return 'valor_invalido';
    end if;
    if p_clave = 'factura_aviso_dias' and p_valor between 1 and 30 then
        update public.notificacion_parametros set factura_aviso_dias = p_valor where id = 1;
    elsif p_clave = 'video_aviso_horas' and p_valor between 1 and 23 then
        update public.notificacion_parametros set video_aviso_horas = p_valor where id = 1;
    elsif p_clave = 'reserva_aviso_horas' and p_valor between 1 and 168 then
        update public.notificacion_parametros set reserva_aviso_horas = p_valor where id = 1;
    elsif p_clave = 'pago_aviso_dias' and p_valor between 1 and 30 then
        update public.notificacion_parametros set pago_aviso_dias = p_valor where id = 1;
    elsif p_clave = 'correo_tope_dia' and p_valor between 1 and 450 then
        update public.notificacion_parametros set correo_tope_dia = p_valor where id = 1;
    elsif p_clave = 'correo_tope_hora' and p_valor between 1 and 100 then
        update public.notificacion_parametros set correo_tope_hora = p_valor where id = 1;
    elsif p_clave = 'correo_max_intentos' and p_valor between 1 and 10 then
        update public.notificacion_parametros set correo_max_intentos = p_valor where id = 1;
    elsif p_clave = 'correo_hora_envio' and p_valor between 0 and 23 then
        update public.notificacion_parametros set correo_hora_envio = p_valor where id = 1;
    elsif p_clave in ('factura_aviso_dias', 'video_aviso_horas', 'reserva_aviso_horas', 'pago_aviso_dias',
                      'correo_tope_dia', 'correo_tope_hora', 'correo_max_intentos', 'correo_hora_envio') then
        return 'valor_invalido';
    else
        return 'clave_invalida';
    end if;
    update public.notificacion_parametros
       set actualizado_en = now(), actualizado_por = (select auth.uid())
     where id = 1;
    return 'ok';
end;
$$;

-- Guarda la dirección del despachador o el secreto en Vault (solo service_role, desde scripts/notificaciones_secretos.py).
create or replace function public.notificaciones_guardar_secreto(p_nombre text, p_valor text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id uuid;
begin
    if p_nombre not in ('notificaciones_url', 'notificaciones_secreto') or coalesce(btrim(p_valor), '') = ''
       or (p_nombre = 'notificaciones_url' and btrim(p_valor) !~ '^https?://[^ ]+/tareas/notificaciones$')
       or (p_nombre = 'notificaciones_secreto' and length(btrim(p_valor)) < 32) then
        return 'valor_invalido';
    end if;
    select id into v_id from vault.secrets where name = p_nombre;
    if v_id is null then
        perform vault.create_secret(btrim(p_valor), p_nombre, 'Notificaciones por correo');
    else
        perform vault.update_secret(v_id, btrim(p_valor));
    end if;
    return 'ok';
end;
$$;

revoke execute on function public.notificaciones_guardar_secreto(text, text) from public, anon, authenticated;
grant execute on function public.notificaciones_guardar_secreto(text, text) to service_role;

revoke execute on function public.cambiar_preferencia_correos(boolean), public._notificacion_datos(public.notificacion_envio),
    public.notificaciones_reclamar(integer), public.notificaciones_resultado(bigint, text, text),
    public.notificaciones_resumen(), public._notificaciones_llamar_despachador(), public.notificaciones_tick(),
    public.notificaciones_correo_activar(boolean)
    from public, anon, authenticated;
grant execute on function public.cambiar_preferencia_correos(boolean), public.notificaciones_correo_activar(boolean)
    to authenticated;
grant execute on function public.notificaciones_reclamar(integer), public.notificaciones_resultado(bigint, text, text),
    public.notificaciones_resumen() to service_role;
-- _notificacion_datos, _notificaciones_llamar_despachador y notificaciones_tick: solo el dueño (pg_cron corre como postgres)
revoke execute on function public._notificacion_datos(public.notificacion_envio),
    public._notificaciones_llamar_despachador(), public.notificaciones_tick() from service_role;
