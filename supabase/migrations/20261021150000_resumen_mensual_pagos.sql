-- Módulo 6: facturación mensual y pagos (simulados: proyecto educativo, sin requisitos
-- fiscales ni pasarela de pagos). Reemplaza a 20261021000000_resumen_mensual_pagos.sql
-- del PR #13 (nunca aplicada).
--
-- * resumen_mensual = la factura de una alumna en un mes. Dos estados separados:
--   estado_factura (borrador / confirmada / anulada) y estado_pago (pendiente / pagado /
--   sin_cargo). Una sola factura no anulada por alumna y mes.
-- * resumen_mensual_detalle = una línea por clase cobrada, con su foto (fecha, hora,
--   tipo, precio_original) y el importe final después de un ajuste. Lo que se cobra:
--   reservas confirmadas de clases programadas que ya empezaron (_cobrables).
-- * Fechas (hora de Montevideo): durante el mes solo hay un provisorio (lo calcula la
--   app, sin guardar nada). Desde el último día del mes la profesora prepara borradores
--   (sincronizar_borradores) y los ajusta. Confirmar exige que hayan terminado todas las
--   clases programadas del mes. La alumna ve sus facturas confirmadas recién desde el
--   día 1 del mes siguiente (lo controla RLS).
-- * Los motivos (ajustes, descuentos, anulaciones) son internos: viven en tablas que
--   solo lee la profesora.
-- * Nadie escribe directo en estas tablas (ni la profesora): solo estas funciones.
--   Orden de bloqueo en todo el módulo: factura -> líneas -> pago. Las operaciones de
--   varias facturas (sincronizar, confirmar el mes) toman además un candado por mes.

-- ---------- fechas ----------

-- Único punto de "ahora" del módulo (las pruebas locales lo reemplazan para simular fechas).
create or replace function public._ahora()
returns timestamptz
language sql
stable
set search_path = ''
as $$
    select now();
$$;

create or replace function public._hoy_mvd()
returns date
language sql
stable
set search_path = ''
as $$
    select (public._ahora() at time zone 'America/Montevideo')::date;
$$;

-- Desde el último día del mes se pueden preparar borradores.
create or replace function public._mes_en_revision(p_mes int, p_anio int)
returns boolean
language sql
stable
set search_path = ''
as $$
    select public._hoy_mvd() >= (make_date(p_anio, p_mes, 1) + interval '1 month' - interval '1 day')::date;
$$;

-- Momento en que la alumna empieza a ver la factura: día 1 del mes siguiente, 00:00.
create or replace function public._inicio_publicacion(p_mes int, p_anio int)
returns timestamptz
language sql
immutable
set search_path = ''
as $$
    select (make_date(p_anio, p_mes, 1) + interval '1 month')::timestamp at time zone 'America/Montevideo';
$$;

-- Ninguna clase programada del mes sigue sin terminar (inicio + duración).
create or replace function public._periodo_finalizado(p_mes int, p_anio int)
returns boolean
language sql
stable
set search_path = ''
as $$
    select not exists (
        select 1 from public.clase c
        where c.estado = 'programada'
          and c.fecha >= make_date(p_anio, p_mes, 1)
          and c.fecha < make_date(p_anio, p_mes, 1) + interval '1 month'
          and public._inicio_clase(c.fecha, c.hora_inicio) + make_interval(mins => c.duracion) > public._ahora()
    );
$$;

-- ---------- tablas ----------

create sequence public.factura_numero_seq;

create table public.resumen_mensual (
    id bigint generated always as identity primary key,
    id_alumna uuid not null references public.profiles(id),
    mes int not null check (mes between 1 and 12),
    anio int not null check (anio >= 2024),
    estado_factura text not null default 'borrador' check (estado_factura in ('borrador', 'confirmada', 'anulada')),
    estado_pago text not null default 'pendiente' check (estado_pago in ('pendiente', 'pagado', 'sin_cargo')),
    cantidad_clases int not null default 0 check (cantidad_clases >= 0),
    subtotal_original numeric(10,2) not null default 0 check (subtotal_original >= 0),
    total_lineas numeric(10,2) not null default 0 check (total_lineas >= 0),
    descuento_tipo text check (descuento_tipo in ('monto', 'porcentaje')),
    descuento_valor numeric(10,2),
    descuento_monto numeric(10,2) not null default 0 check (descuento_monto >= 0),
    total numeric(10,2) not null default 0 check (total >= 0),
    version int not null default 1,
    numero text unique,
    confirmada_en timestamptz,
    confirmada_por uuid references public.profiles(id),
    anulada_en timestamptz,
    anulada_por uuid references public.profiles(id),
    reemplaza_a bigint references public.resumen_mensual(id),
    creado_en timestamptz not null default now(),
    check ((descuento_tipo is null) = (descuento_valor is null)),
    check (descuento_tipo <> 'porcentaje' or descuento_valor > 0 and descuento_valor <= 100),
    check (descuento_tipo <> 'monto' or descuento_valor > 0),
    check (total = total_lineas - descuento_monto),
    check ((estado_factura = 'borrador') = (numero is null and confirmada_en is null)),
    check ((estado_factura = 'anulada') = (anulada_en is not null)),
    check (estado_factura <> 'borrador' or estado_pago = 'pendiente')
);
create unique index resumen_mensual_una_vigente on public.resumen_mensual (id_alumna, mes, anio)
    where estado_factura <> 'anulada';

create table public.resumen_mensual_detalle (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    id_reserva bigint not null references public.reserva(id),
    id_clase bigint not null references public.clase(id),
    fecha date not null,
    hora_inicio time not null,
    id_tipo_clase bigint references public.tipo_clase(id),
    tipo_nombre text,
    precio_original numeric(10,2) not null check (precio_original >= 0),
    ajuste_tipo text not null default 'ninguno' check (ajuste_tipo in ('ninguno', 'importe', 'bonificada')),
    importe_final numeric(10,2) not null check (importe_final >= 0 and importe_final <= precio_original),
    unique (id_resumen_mensual, id_reserva),
    check (ajuste_tipo <> 'ninguno' or importe_final = precio_original),
    check (ajuste_tipo <> 'bonificada' or importe_final = 0),
    check (ajuste_tipo <> 'importe' or importe_final <> precio_original)
);

-- Historial interno de la factura (ajustes, descuentos, confirmación, anulación).
create table public.factura_historial (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    id_linea bigint,
    accion text not null,
    detalle jsonb not null default '{}'::jsonb,
    motivo text,
    por uuid references public.profiles(id),
    en timestamptz not null default now()
);
create index on public.factura_historial (id_resumen_mensual);

create table public.pago (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    monto numeric(10,2) not null check (monto > 0),
    medio_pago text not null check (medio_pago in ('efectivo', 'transferencia')),
    fecha date not null,
    estado text not null default 'vigente' check (estado in ('vigente', 'anulado')),
    registrado_por uuid references public.profiles(id),
    creado_en timestamptz not null default now()
);
-- Como mucho un pago vigente por factura: frena doble click y dos pestañas aunque falle la app.
create unique index pago_un_vigente_por_resumen on public.pago (id_resumen_mensual) where estado = 'vigente';

-- Datos internos de la anulación de un pago (la alumna no los ve).
create table public.pago_anulacion (
    id_pago bigint primary key references public.pago(id),
    motivo text not null check (length(btrim(motivo)) >= 3),
    anulado_por uuid not null references public.profiles(id),
    anulado_en timestamptz not null default now()
);

-- Un CHECK no puede usar "hoy": la fecha futura la frena un trigger.
create or replace function public._pago_fecha_valida()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.fecha > public._hoy_mvd() then
        raise exception 'La fecha del pago no puede ser futura' using errcode = 'check_violation';
    end if;
    return new;
end;
$$;
create trigger pago_fecha_valida before insert or update of fecha on public.pago
    for each row execute function public._pago_fecha_valida();

-- ---------- quién lee qué ----------

alter table public.resumen_mensual enable row level security;
alter table public.resumen_mensual_detalle enable row level security;
alter table public.factura_historial enable row level security;
alter table public.pago enable row level security;
alter table public.pago_anulacion enable row level security;

-- La alumna ve sus facturas confirmadas desde el día 1 del mes siguiente, y las anuladas
-- solo si se anularon cuando ya estaban publicadas. Nunca borradores.
create policy "resumen_mensual: profesora o propia publicada" on public.resumen_mensual
    for select to authenticated
    using (
        public.es_profesora()
        or (id_alumna = (select auth.uid())
            and ((estado_factura = 'confirmada' and public._ahora() >= public._inicio_publicacion(mes, anio))
                 or (estado_factura = 'anulada' and anulada_en >= public._inicio_publicacion(mes, anio))))
    );

-- Las líneas y los pagos se ven si se ve la factura (la subconsulta ya aplica la política de arriba).
create policy "resumen_mensual_detalle: si se ve la factura" on public.resumen_mensual_detalle
    for select to authenticated
    using (exists (select 1 from public.resumen_mensual rm where rm.id = id_resumen_mensual));

create policy "pago: profesora o vigente de una factura visible" on public.pago
    for select to authenticated
    using (public.es_profesora()
           or (estado = 'vigente' and exists (select 1 from public.resumen_mensual rm where rm.id = id_resumen_mensual)));

create policy "factura_historial: solo profesora" on public.factura_historial
    for select to authenticated using (public.es_profesora());

create policy "pago_anulacion: solo profesora" on public.pago_anulacion
    for select to authenticated using (public.es_profesora());

revoke all on public.resumen_mensual, public.resumen_mensual_detalle, public.factura_historial,
    public.pago, public.pago_anulacion from anon;
revoke insert, update, delete, truncate, references, trigger
    on public.resumen_mensual, public.resumen_mensual_detalle, public.factura_historial,
       public.pago, public.pago_anulacion from authenticated;
revoke all on sequence public.factura_numero_seq from anon, authenticated;

-- ---------- internas ----------

-- Reservas que se cobran en un mes (la regla de cobro vive solo acá).
create or replace function public._cobrables(p_mes int, p_anio int)
returns table (id_reserva bigint, id_alumna uuid, id_clase bigint, fecha date, hora_inicio time,
               id_tipo_clase bigint, tipo_nombre text, precio numeric)
language sql
stable
set search_path = ''
as $$
    select r.id, r.id_alumna, c.id, c.fecha, c.hora_inicio, c.id_tipo_clase, t.nombre, c.precio
    from public.reserva r
    join public.clase c on c.id = r.id_clase
    left join public.tipo_clase t on t.id = c.id_tipo_clase
    where r.estado = 'confirmada'
      and c.estado = 'programada'
      and c.fecha >= make_date(p_anio, p_mes, 1)
      and c.fecha < make_date(p_anio, p_mes, 1) + interval '1 month'
      and public._inicio_clase(c.fecha, c.hora_inicio) <= public._ahora();
$$;

-- Si hoy cae dentro del mes, genera las clases de esta semana: así una clase de hoy que
-- todavía no se generó no hace creer que el período terminó.
create or replace function public._generar_semana_actual(p_mes int, p_anio int)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := public._hoy_mvd();
begin
    if extract(month from v_hoy) = p_mes and extract(year from v_hoy) = p_anio then
        perform public.generar_clases(v_hoy - (extract(isodow from v_hoy)::int - 1));
    end if;
end;
$$;

-- Recalcula los importes. Sube la versión si algo cambió (o si p_forzar).
create or replace function public._recalcular_factura(p_id bigint, p_forzar boolean)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
    v_cant int; v_orig numeric; v_lineas numeric; v_desc numeric;
begin
    select * into f from public.resumen_mensual where id = p_id;
    select count(*), coalesce(sum(precio_original), 0), coalesce(sum(importe_final), 0)
      into v_cant, v_orig, v_lineas
      from public.resumen_mensual_detalle where id_resumen_mensual = p_id;
    v_desc := case f.descuento_tipo
        when 'monto' then least(f.descuento_valor, v_lineas)
        when 'porcentaje' then round(v_lineas * f.descuento_valor / 100, 2)
        else 0 end;
    if p_forzar or (v_cant, v_orig, v_lineas, v_desc) is distinct from
                   (f.cantidad_clases, f.subtotal_original, f.total_lineas, f.descuento_monto) then
        update public.resumen_mensual
        set cantidad_clases = v_cant, subtotal_original = v_orig, total_lineas = v_lineas,
            descuento_monto = v_desc, total = v_lineas - v_desc, version = version + 1
        where id = p_id;
    end if;
end;
$$;

-- Las líneas de la factura coinciden con lo cobrable de la alumna en ese mes.
create or replace function public._factura_al_dia(p_id bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select not exists (
        (select c.id_reserva from public.resumen_mensual f, public._cobrables(f.mes, f.anio) c
         where f.id = p_id and c.id_alumna = f.id_alumna)
        except
        (select id_reserva from public.resumen_mensual_detalle where id_resumen_mensual = p_id)
    ) and not exists (
        (select id_reserva from public.resumen_mensual_detalle where id_resumen_mensual = p_id)
        except
        (select c.id_reserva from public.resumen_mensual f, public._cobrables(f.mes, f.anio) c
         where f.id = p_id and c.id_alumna = f.id_alumna)
    );
$$;

-- Firma del conjunto de borradores confirmables de un mes (ids + versiones).
create or replace function public._firma_mes(p_mes int, p_anio int)
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select md5(coalesce(string_agg(id || ':' || version, ',' order by id), ''))
    from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and cantidad_clases > 0;
$$;

create or replace function public._confirmar(p_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
begin
    select * into f from public.resumen_mensual where id = p_id;
    update public.resumen_mensual
    set estado_factura = 'confirmada',
        numero = 'JSU-' || f.anio || '-' || lpad(f.mes::text, 2, '0') || '-' || lpad(nextval('public.factura_numero_seq')::text, 4, '0'),
        confirmada_en = public._ahora(), confirmada_por = auth.uid(),
        estado_pago = case when f.total = 0 then 'sin_cargo' else 'pendiente' end,
        version = version + 1
    where id = p_id;
    insert into public.factura_historial (id_resumen_mensual, accion, detalle, por)
    values (p_id, 'confirmada', jsonb_build_object('total', f.total, 'cantidad_clases', f.cantidad_clases), auth.uid());
end;
$$;

-- ---------- borradores ----------

-- Crea o actualiza los borradores del mes según las reservas, sin perder los ajustes.
-- No toca facturas confirmadas ni anuladas. Idempotente.
-- Devuelve {"resultado": ok | no_autorizada | datos_invalidos | fuera_de_revision,
--           "creadas": n, "actualizadas": n}
create or replace function public.sincronizar_borradores(p_mes int, p_anio int)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid;
    f public.resumen_mensual;
    v_creadas int := 0;
    v_actualizadas int := 0;
    v_quitadas int;
    v_agregadas int;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_mes is null or p_mes not between 1 and 12 or p_anio is null or p_anio < 2024 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if not public._mes_en_revision(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'fuera_de_revision');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), p_anio * 100 + p_mes);
    perform public._generar_semana_actual(p_mes, p_anio);

    for v_alumna in
        select id_alumna from public._cobrables(p_mes, p_anio)
        union
        select id_alumna from public.resumen_mensual
        where mes = p_mes and anio = p_anio and estado_factura = 'borrador'
        order by 1
    loop
        select * into f from public.resumen_mensual
        where id_alumna = v_alumna and mes = p_mes and anio = p_anio and estado_factura <> 'anulada'
        for update;
        if found and f.estado_factura <> 'borrador' then
            continue;
        end if;
        if not found then
            insert into public.resumen_mensual (id_alumna, mes, anio) values (v_alumna, p_mes, p_anio)
            returning * into f;
            v_creadas := v_creadas + 1;
        end if;

        -- líneas que ya no se cobran (si tenían un ajuste, queda en el historial)
        with quitadas as (
            delete from public.resumen_mensual_detalle d
            where d.id_resumen_mensual = f.id
              and d.id_reserva not in (select c.id_reserva from public._cobrables(p_mes, p_anio) c where c.id_alumna = v_alumna)
            returning d.*
        ), log as (
            insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, por)
            select f.id, q.id, 'linea_quitada',
                   jsonb_build_object('fecha', q.fecha, 'hora', q.hora_inicio, 'tipo', q.tipo_nombre,
                                      'ajuste_tipo', q.ajuste_tipo, 'importe_final', q.importe_final),
                   auth.uid()
            from quitadas q where q.ajuste_tipo <> 'ninguno'
        )
        select count(*) into v_quitadas from quitadas;

        insert into public.resumen_mensual_detalle
            (id_resumen_mensual, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre, precio_original, importe_final)
        select f.id, c.id_reserva, c.id_clase, c.fecha, c.hora_inicio, c.id_tipo_clase, c.tipo_nombre, c.precio, c.precio
        from public._cobrables(p_mes, p_anio) c
        where c.id_alumna = v_alumna
        on conflict (id_resumen_mensual, id_reserva) do nothing;
        get diagnostics v_agregadas = row_count;

        if v_quitadas + v_agregadas > 0 then
            perform public._recalcular_factura(f.id, true);
            v_actualizadas := v_actualizadas + 1;
        end if;

        -- una alumna sin clases no tiene factura (si nunca se tocó, el borrador vacío se borra)
        if not exists (select 1 from public.resumen_mensual_detalle where id_resumen_mensual = f.id)
           and not exists (select 1 from public.factura_historial where id_resumen_mensual = f.id) then
            delete from public.resumen_mensual where id = f.id;
        end if;
    end loop;

    return jsonb_build_object('resultado', 'ok', 'creadas', v_creadas, 'actualizadas', v_actualizadas);
end;
$$;

-- Bloquea la factura y valida que se pueda modificar. Devuelve un código de error o null.
create or replace function public._validar_borrador(p_id bigint, p_version int)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
begin
    if not public.es_profesora() then
        return 'no_autorizada';
    end if;
    select * into f from public.resumen_mensual where id = p_id for update;
    if not found then
        return 'no_existe';
    end if;
    if f.estado_factura <> 'borrador' then
        return 'no_es_borrador';
    end if;
    if p_version is distinct from f.version then
        return 'version_cambio';
    end if;
    return null;
end;
$$;

create or replace function public._motivo_valido(p_motivo text)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select length(btrim(coalesce(p_motivo, ''))) between 3 and 300;
$$;

-- Ajusta una clase: 'bonificada' (importe 0) o 'importe' (entre 0 y el precio original).
-- Devuelve {"resultado": ok | no_autorizada | no_existe | no_es_borrador | version_cambio
--           | motivo_invalido | importe_invalido, "version": n}
create or replace function public.ajustar_linea(p_id_linea bigint, p_tipo text, p_importe numeric, p_motivo text, p_version int)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
    v_err text;
    l public.resumen_mensual_detalle;
    v_importe numeric;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select id_resumen_mensual into v_id from public.resumen_mensual_detalle where id = p_id_linea;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    v_err := public._validar_borrador(v_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select * into l from public.resumen_mensual_detalle where id = p_id_linea for update;
    if p_tipo = 'bonificada' then
        v_importe := 0;
    elsif p_tipo = 'importe' and p_importe is not null and p_importe >= 0
          and p_importe < l.precio_original and p_importe = round(p_importe, 2) then
        v_importe := p_importe;
    else
        return jsonb_build_object('resultado', 'importe_invalido');
    end if;
    if l.precio_original = 0 then
        return jsonb_build_object('resultado', 'importe_invalido');
    end if;
    update public.resumen_mensual_detalle
    set ajuste_tipo = case when v_importe = 0 then 'bonificada' else 'importe' end, importe_final = v_importe
    where id = p_id_linea;
    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por)
    values (v_id, p_id_linea, 'ajuste_linea',
            jsonb_build_object('fecha', l.fecha, 'hora', l.hora_inicio, 'tipo', l.tipo_nombre,
                               'precio_original', l.precio_original,
                               'antes', l.importe_final, 'despues', v_importe),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = v_id));
end;
$$;

create or replace function public.quitar_ajuste_linea(p_id_linea bigint, p_motivo text, p_version int)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
    v_err text;
    l public.resumen_mensual_detalle;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select id_resumen_mensual into v_id from public.resumen_mensual_detalle where id = p_id_linea;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    v_err := public._validar_borrador(v_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select * into l from public.resumen_mensual_detalle where id = p_id_linea for update;
    if l.ajuste_tipo = 'ninguno' then
        return jsonb_build_object('resultado', 'sin_ajuste');
    end if;
    update public.resumen_mensual_detalle set ajuste_tipo = 'ninguno', importe_final = precio_original
    where id = p_id_linea;
    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por)
    values (v_id, p_id_linea, 'ajuste_quitado',
            jsonb_build_object('fecha', l.fecha, 'hora', l.hora_inicio, 'tipo', l.tipo_nombre,
                               'precio_original', l.precio_original, 'antes', l.importe_final, 'despues', l.precio_original),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = v_id));
end;
$$;

-- Descuento general: 'monto' (fijo, como mucho el total de las clases) o 'porcentaje' (0 a 100).
-- p_tipo null = quitar el descuento.
create or replace function public.descuento_general(p_id bigint, p_tipo text, p_valor numeric, p_motivo text, p_version int)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_err text;
    f public.resumen_mensual;
begin
    v_err := public._validar_borrador(p_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select * into f from public.resumen_mensual where id = p_id;
    if p_tipo is null then
        if f.descuento_tipo is null then
            return jsonb_build_object('resultado', 'sin_descuento');
        end if;
    elsif p_valor is null or p_valor <= 0 or p_valor <> round(p_valor, 2)
          or (p_tipo = 'porcentaje' and p_valor > 100)
          or (p_tipo = 'monto' and p_valor > f.total_lineas)
          or p_tipo not in ('monto', 'porcentaje') then
        return jsonb_build_object('resultado', 'descuento_invalido');
    end if;
    update public.resumen_mensual set descuento_tipo = p_tipo, descuento_valor = case when p_tipo is null then null else p_valor end
    where id = p_id;
    insert into public.factura_historial (id_resumen_mensual, accion, detalle, motivo, por)
    values (p_id, case when p_tipo is null then 'descuento_quitado' else 'descuento_general' end,
            jsonb_build_object('antes', jsonb_build_object('tipo', f.descuento_tipo, 'valor', f.descuento_valor),
                               'despues', jsonb_build_object('tipo', p_tipo, 'valor', p_valor)),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(p_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = p_id));
end;
$$;

-- ---------- confirmación ----------

-- Solo lectura: qué se confirmaría con "Confirmar todas".
create or replace function public.previsualizar_confirmacion_mes(p_mes int, p_anio int)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    return jsonb_build_object(
        'resultado', 'ok',
        'en_revision', public._mes_en_revision(p_mes, p_anio),
        'periodo_finalizado', public._periodo_finalizado(p_mes, p_anio),
        'cantidad', count(*) filter (where cantidad_clases > 0),
        'total', coalesce(sum(total) filter (where cantidad_clases > 0), 0),
        'sin_clases', count(*) filter (where cantidad_clases = 0),
        'desactualizadas', count(*) filter (where cantidad_clases > 0 and not public._factura_al_dia(id)),
        'firma', public._firma_mes(p_mes, p_anio))
    from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador';
end;
$$;

-- Devuelve {"resultado": ok | no_autorizada | no_existe | no_es_borrador | version_cambio
--           | periodo_abierto | sin_clases | desactualizada, "numero": text}
create or replace function public.confirmar_factura(p_id bigint, p_version int)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
    v_err text;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into f from public.resumen_mensual where id = p_id;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), f.anio * 100 + f.mes);
    perform public._generar_semana_actual(f.mes, f.anio);
    v_err := public._validar_borrador(p_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._mes_en_revision(f.mes, f.anio) or not public._periodo_finalizado(f.mes, f.anio) then
        return jsonb_build_object('resultado', 'periodo_abierto');
    end if;
    select * into f from public.resumen_mensual where id = p_id;
    if f.cantidad_clases = 0 then
        return jsonb_build_object('resultado', 'sin_clases');
    end if;
    if not public._factura_al_dia(p_id) then
        return jsonb_build_object('resultado', 'desactualizada');
    end if;
    perform public._confirmar(p_id);
    return jsonb_build_object('resultado', 'ok', 'numero', (select numero from public.resumen_mensual where id = p_id));
end;
$$;

-- Todo o nada. p_firma = la firma que mostró la vista previa.
-- Devuelve {"resultado": ok | no_autorizada | periodo_abierto | cambio | desactualizada | nada,
--           "confirmadas": n, "total": x}
create or replace function public.confirmar_facturas_mes(p_mes int, p_anio int, p_firma text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
    v_n int := 0;
    v_total numeric := 0;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), p_anio * 100 + p_mes);
    perform public._generar_semana_actual(p_mes, p_anio);
    if not public._mes_en_revision(p_mes, p_anio) or not public._periodo_finalizado(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'periodo_abierto');
    end if;
    perform 1 from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador' order by id for update;
    if p_firma is distinct from public._firma_mes(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'cambio');
    end if;
    for v_id in
        select id from public.resumen_mensual
        where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and cantidad_clases > 0
        order by id
    loop
        if not public._factura_al_dia(v_id) then
            raise exception 'desactualizada' using errcode = 'P0001';
        end if;
        v_total := v_total + (select total from public.resumen_mensual where id = v_id);
        perform public._confirmar(v_id);
        v_n := v_n + 1;
    end loop;
    if v_n = 0 then
        return jsonb_build_object('resultado', 'nada');
    end if;
    return jsonb_build_object('resultado', 'ok', 'confirmadas', v_n, 'total', v_total);
exception when raise_exception then
    if sqlerrm = 'desactualizada' then
        return jsonb_build_object('resultado', 'desactualizada');
    end if;
    raise;
end;
$$;

-- ---------- pagos (simulados) ----------

-- Registra el pago del total exacto de una factura confirmada.
-- Devuelve {"resultado": ok | no_autorizada | datos_invalidos | no_existe | no_confirmada
--           | ya_pagado | sin_cargo | monto_distinto | fecha_futura, "id_pago": n}
create or replace function public.registrar_pago(p_id_resumen bigint, p_monto numeric, p_medio text, p_fecha date)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
    v_id bigint;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_monto is null or p_medio is null or p_medio not in ('efectivo', 'transferencia') or p_fecha is null then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if p_fecha > public._hoy_mvd() then
        return jsonb_build_object('resultado', 'fecha_futura');
    end if;
    select * into f from public.resumen_mensual where id = p_id_resumen for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if f.estado_factura <> 'confirmada' then
        return jsonb_build_object('resultado', 'no_confirmada');
    end if;
    if f.estado_pago = 'pagado' then
        return jsonb_build_object('resultado', 'ya_pagado');
    end if;
    if f.estado_pago = 'sin_cargo' then
        return jsonb_build_object('resultado', 'sin_cargo');
    end if;
    if p_monto <> f.total then
        return jsonb_build_object('resultado', 'monto_distinto', 'total', f.total);
    end if;
    insert into public.pago (id_resumen_mensual, monto, medio_pago, fecha, registrado_por)
    values (p_id_resumen, f.total, p_medio, p_fecha, auth.uid())
    returning id into v_id;
    update public.resumen_mensual set estado_pago = 'pagado' where id = p_id_resumen;
    return jsonb_build_object('resultado', 'ok', 'id_pago', v_id);
exception when unique_violation then
    return jsonb_build_object('resultado', 'ya_pagado');
end;
$$;

-- Anula un pago vigente: queda en el historial (motivo interno en pago_anulacion) y la
-- factura vuelve a pendiente de pago.
-- Devuelve {"resultado": ok | no_autorizada | motivo_invalido | no_existe | ya_anulado}
create or replace function public.anular_pago(p_id_pago bigint, p_motivo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id_resumen bigint;
    v_estado text;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select id_resumen_mensual into v_id_resumen from public.pago where id = p_id_pago;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    perform 1 from public.resumen_mensual where id = v_id_resumen for update;
    select estado into v_estado from public.pago where id = p_id_pago for update;
    if v_estado <> 'vigente' then
        return jsonb_build_object('resultado', 'ya_anulado');
    end if;
    update public.pago set estado = 'anulado' where id = p_id_pago;
    insert into public.pago_anulacion (id_pago, motivo, anulado_por) values (p_id_pago, btrim(p_motivo), auth.uid());
    update public.resumen_mensual set estado_pago = 'pendiente' where id = v_id_resumen;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

-- ---------- permisos de las funciones ----------

-- Las de fechas las usan las políticas de lectura: las puede ejecutar cualquiera con sesión.
revoke execute on function public._ahora(), public._hoy_mvd(), public._mes_en_revision(int, int), public._inicio_publicacion(int, int),
    public._periodo_finalizado(int, int) from public, anon;
grant execute on function public._ahora(), public._hoy_mvd(), public._mes_en_revision(int, int), public._inicio_publicacion(int, int),
    public._periodo_finalizado(int, int) to authenticated;

revoke execute on function public._cobrables(int, int), public._generar_semana_actual(int, int),
    public._recalcular_factura(bigint, boolean), public._factura_al_dia(bigint), public._firma_mes(int, int),
    public._confirmar(bigint), public._validar_borrador(bigint, int), public._motivo_valido(text),
    public._pago_fecha_valida() from public, anon, authenticated;

revoke execute on function public.sincronizar_borradores(int, int), public.ajustar_linea(bigint, text, numeric, text, int),
    public.quitar_ajuste_linea(bigint, text, int), public.descuento_general(bigint, text, numeric, text, int),
    public.previsualizar_confirmacion_mes(int, int), public.confirmar_factura(bigint, int),
    public.confirmar_facturas_mes(int, int, text), public.registrar_pago(bigint, numeric, text, date),
    public.anular_pago(bigint, text) from public, anon;
grant execute on function public.sincronizar_borradores(int, int), public.ajustar_linea(bigint, text, numeric, text, int),
    public.quitar_ajuste_linea(bigint, text, int), public.descuento_general(bigint, text, numeric, text, int),
    public.previsualizar_confirmacion_mes(int, int), public.confirmar_factura(bigint, int),
    public.confirmar_facturas_mes(int, int, text), public.registrar_pago(bigint, numeric, text, date),
    public.anular_pago(bigint, text) to authenticated;
