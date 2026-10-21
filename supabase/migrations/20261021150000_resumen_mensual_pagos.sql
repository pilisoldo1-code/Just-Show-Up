-- Módulo 6: resumen mensual y pagos (sin Mercado Pago: registro manual).
-- Un resumen agrupa, por alumna y por mes, lo que se cobra: reservas
-- confirmadas de clases programadas que ya pasaron (presente, ausente o sin
-- registrar). Las reservas canceladas (por la alumna a tiempo o por un
-- bloqueo de la profesora) no se cobran.
-- * Solo se generan y cobran meses ya cerrados (hora de Montevideo); el mes en
--   curso la app lo muestra como provisorio, sin guardar nada.
-- * resumen_mensual_detalle guarda la foto de cada clase cobrada (fecha, tipo,
--   precio): lo que ve la alumna en Mis facturas no cambia aunque después
--   cambien los tipos o los precios.
-- * Un pago cubre el total exacto (sin pagos parciales). Como mucho un pago
--   vigente por resumen (índice único). Un pago no se borra: se anula, con
--   motivo, fecha y responsable, y el resumen vuelve a pendiente.
-- Reemplaza a 20261021000000_resumen_mensual_pagos.sql del PR #13 (nunca aplicada).

create table public.resumen_mensual (
    id bigint generated always as identity primary key,
    id_alumna uuid not null references public.profiles(id),
    mes int not null check (mes between 1 and 12),
    anio int not null check (anio >= 2024),
    cantidad_clases int not null default 0 check (cantidad_clases >= 0),
    total numeric(10,2) not null default 0 check (total >= 0),
    estado text not null default 'pendiente' check (estado in ('pendiente', 'pagado')),
    creado_en timestamptz not null default now(),
    unique (id_alumna, mes, anio)
);

create table public.resumen_mensual_detalle (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    id_reserva bigint not null unique references public.reserva(id),
    id_clase bigint not null references public.clase(id),
    fecha date not null,
    hora_inicio time not null,
    id_tipo_clase bigint references public.tipo_clase(id),
    tipo_nombre text,
    precio numeric(10,2) not null check (precio >= 0)
);
create index on public.resumen_mensual_detalle (id_resumen_mensual);

create table public.pago (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    monto numeric(10,2) not null check (monto > 0),
    medio_pago text not null check (medio_pago in ('efectivo', 'transferencia')),
    fecha date not null,
    estado text not null default 'vigente' check (estado in ('vigente', 'anulado')),
    registrado_por uuid references public.profiles(id),
    creado_en timestamptz not null default now(),
    anulado_en timestamptz,
    anulado_por uuid references public.profiles(id),
    motivo_anulacion text,
    check ((estado = 'anulado') = (anulado_en is not null and anulado_por is not null
                                   and coalesce(btrim(motivo_anulacion), '') <> ''))
);
-- Como mucho un pago vigente por resumen: frena doble click y dos pestañas aunque falle la app.
create unique index pago_un_vigente_por_resumen on public.pago (id_resumen_mensual) where estado = 'vigente';

-- Un CHECK no puede usar "hoy": la fecha futura la frena un trigger (hora de Montevideo).
create or replace function public._pago_fecha_valida()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.fecha > (now() at time zone 'America/Montevideo')::date then
        raise exception 'La fecha del pago no puede ser futura' using errcode = 'check_violation';
    end if;
    return new;
end;
$$;
create trigger pago_fecha_valida before insert or update of fecha on public.pago
    for each row execute function public._pago_fecha_valida();
revoke execute on function public._pago_fecha_valida() from public, anon, authenticated;

alter table public.resumen_mensual enable row level security;
alter table public.resumen_mensual_detalle enable row level security;
alter table public.pago enable row level security;

-- Lectura: la profesora ve todo; cada alumna solo lo suyo (Mis facturas).
create policy "resumen_mensual: leer propio o profesora" on public.resumen_mensual
    for select to authenticated
    using (public.es_profesora() or id_alumna = (select auth.uid()));

create policy "resumen_mensual_detalle: leer propio o profesora" on public.resumen_mensual_detalle
    for select to authenticated
    using (
        public.es_profesora()
        or exists (select 1 from public.resumen_mensual rm
                   where rm.id = id_resumen_mensual and rm.id_alumna = (select auth.uid()))
    );

create policy "pago: leer propio o profesora" on public.pago
    for select to authenticated
    using (
        public.es_profesora()
        or exists (select 1 from public.resumen_mensual rm
                   where rm.id = id_resumen_mensual and rm.id_alumna = (select auth.uid()))
    );

-- Nadie escribe directo (ni la profesora): solo generar_resumenes, registrar_pago y anular_pago.
revoke all on public.resumen_mensual, public.resumen_mensual_detalle, public.pago from anon;
revoke insert, update, delete, truncate, references, trigger
    on public.resumen_mensual, public.resumen_mensual_detalle, public.pago from authenticated;

-- true si el mes ya terminó en Montevideo (el mes en curso y los futuros están abiertos).
create or replace function public._mes_cerrado(p_mes int, p_anio int)
returns boolean
language sql
stable
set search_path = ''
as $$
    select make_date(p_anio, p_mes, 1) + interval '1 month' <= (now() at time zone 'America/Montevideo')::date;
$$;
revoke execute on function public._mes_cerrado(int, int) from public, anon, authenticated;

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
      and public._inicio_clase(c.fecha, c.hora_inicio) <= now();
$$;
revoke execute on function public._cobrables(int, int) from public, anon, authenticated;

-- Calcula lo cobrable del mes por alumna y lo guarda junto con el detalle.
-- Idempotente: se puede volver a correr sin duplicar, y nunca toca un resumen
-- pagado ni su detalle ni sus pagos. Un resumen pendiente que ya no tiene clases
-- cobrables queda en 0.
-- Devuelve {"resultado": ok | no_autorizada | datos_invalidos | mes_abierto, "generados": n}
create or replace function public.generar_resumenes(p_mes int, p_anio int)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_generados integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_mes is null or p_mes not between 1 and 12 or p_anio is null or p_anio < 2024 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if not public._mes_cerrado(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'mes_abierto');
    end if;
    -- dos generaciones del mismo mes a la vez (dos pestañas) se ordenan
    perform pg_advisory_xact_lock(hashtext('generar_resumenes'), p_anio * 100 + p_mes);

    -- el "where" del on conflict se evalúa sobre la fila bloqueada: si otro pedido
    -- acaba de registrar el pago, ese resumen no se toca
    with agregado as (
        select id_alumna, count(*) as cantidad, sum(precio) as total from public._cobrables(p_mes, p_anio) group by id_alumna
    ), upsert as (
        insert into public.resumen_mensual (id_alumna, mes, anio, cantidad_clases, total)
        select id_alumna, p_mes, p_anio, cantidad, total from agregado
        on conflict (id_alumna, mes, anio) do update
            set cantidad_clases = excluded.cantidad_clases, total = excluded.total
            where public.resumen_mensual.estado = 'pendiente'
        returning 1
    )
    select count(*) into v_generados from upsert;

    update public.resumen_mensual rm set cantidad_clases = 0, total = 0
    where rm.mes = p_mes and rm.anio = p_anio and rm.estado = 'pendiente'
      and not exists (select 1 from public._cobrables(p_mes, p_anio) c where c.id_alumna = rm.id_alumna);

    delete from public.resumen_mensual_detalle d
    using public.resumen_mensual rm
    where rm.id = d.id_resumen_mensual and rm.mes = p_mes and rm.anio = p_anio and rm.estado = 'pendiente';

    insert into public.resumen_mensual_detalle
        (id_resumen_mensual, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre, precio)
    select rm.id, c.id_reserva, c.id_clase, c.fecha, c.hora_inicio, c.id_tipo_clase, c.tipo_nombre, c.precio
    from public._cobrables(p_mes, p_anio) c
    join public.resumen_mensual rm on rm.id_alumna = c.id_alumna and rm.mes = p_mes and rm.anio = p_anio
    where rm.estado = 'pendiente';

    return jsonb_build_object('resultado', 'ok', 'generados', v_generados);
end;
$$;

-- Registra el pago del total exacto de un resumen pendiente de un mes cerrado.
-- Devuelve {"resultado": ok | no_autorizada | datos_invalidos | no_existe | mes_abierto
--           | ya_pagado | monto_distinto | fecha_futura, "id_pago": n}
create or replace function public.registrar_pago(p_id_resumen bigint, p_monto numeric, p_medio text, p_fecha date)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_resumen public.resumen_mensual;
    v_id bigint;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_monto is null or p_medio is null or p_medio not in ('efectivo', 'transferencia') or p_fecha is null then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if p_fecha > (now() at time zone 'America/Montevideo')::date then
        return jsonb_build_object('resultado', 'fecha_futura');
    end if;
    -- orden de bloqueo en todo el módulo: primero el resumen, después el pago
    select * into v_resumen from public.resumen_mensual where id = p_id_resumen for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if not public._mes_cerrado(v_resumen.mes, v_resumen.anio) then
        return jsonb_build_object('resultado', 'mes_abierto');
    end if;
    if v_resumen.estado = 'pagado' then
        return jsonb_build_object('resultado', 'ya_pagado');
    end if;
    if v_resumen.total <= 0 or p_monto <> v_resumen.total then
        return jsonb_build_object('resultado', 'monto_distinto', 'total', v_resumen.total);
    end if;
    insert into public.pago (id_resumen_mensual, monto, medio_pago, fecha, registrado_por)
    values (p_id_resumen, v_resumen.total, p_medio, p_fecha, auth.uid())
    returning id into v_id;
    update public.resumen_mensual set estado = 'pagado' where id = p_id_resumen;
    return jsonb_build_object('resultado', 'ok', 'id_pago', v_id);
exception when unique_violation then
    return jsonb_build_object('resultado', 'ya_pagado');
end;
$$;

-- Anula un pago vigente (queda en el historial con motivo, fecha y responsable)
-- y devuelve el resumen a pendiente.
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
    v_motivo text := btrim(coalesce(p_motivo, ''));
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if length(v_motivo) < 3 or length(v_motivo) > 300 then
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
    update public.pago
    set estado = 'anulado', anulado_en = now(), anulado_por = auth.uid(), motivo_anulacion = v_motivo
    where id = p_id_pago;
    update public.resumen_mensual set estado = 'pendiente' where id = v_id_resumen;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

revoke execute on function public.generar_resumenes(int, int) from public, anon;
revoke execute on function public.registrar_pago(bigint, numeric, text, date) from public, anon;
revoke execute on function public.anular_pago(bigint, text) from public, anon;
grant execute on function public.generar_resumenes(int, int) to authenticated;
grant execute on function public.registrar_pago(bigint, numeric, text, date) to authenticated;
grant execute on function public.anular_pago(bigint, text) to authenticated;
