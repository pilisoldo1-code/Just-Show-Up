-- Módulo 6: resumen mensual y pagos (sin Mercado Pago: registro manual).
-- Un resumen agrupa, por alumna y por mes, lo que se cobra: reservas
-- confirmadas de clases programadas que ya pasaron (presente, ausente o sin
-- registrar). Las reservas canceladas (por la alumna a tiempo o por un
-- bloqueo de la profesora) no se cobran. Un pago marca el resumen como
-- pagado; no hay pagos parciales ni reintentos sobre un resumen ya pagado.
create table public.resumen_mensual (
    id bigint generated always as identity primary key,
    id_alumna uuid not null references public.profiles(id),
    mes int not null check (mes between 1 and 12),
    anio int not null check (anio >= 2024),
    cantidad_clases int not null default 0,
    total numeric(10,2) not null default 0,
    estado text not null default 'pendiente' check (estado in ('pendiente', 'pagado')),
    creado_en timestamptz not null default now(),
    unique (id_alumna, mes, anio)
);

create table public.pago (
    id bigint generated always as identity primary key,
    id_resumen_mensual bigint not null references public.resumen_mensual(id),
    monto numeric(10,2) not null check (monto > 0),
    medio_pago text not null check (medio_pago in ('efectivo', 'transferencia')),
    fecha date not null,
    creado_en timestamptz not null default now()
);

alter table public.resumen_mensual enable row level security;
alter table public.pago enable row level security;

-- Lectura: la profesora ve todo; cada alumna solo lo suyo (preparado para
-- cuando exista "Mis facturas"; este módulo no agrega esa pantalla).
create policy "resumen_mensual: leer propio o profesora" on public.resumen_mensual
    for select to authenticated
    using (public.es_profesora() or id_alumna = (select auth.uid()));

create policy "pago: leer propio o profesora" on public.pago
    for select to authenticated
    using (
        public.es_profesora()
        or exists (select 1 from public.resumen_mensual rm
                   where rm.id = id_resumen_mensual and rm.id_alumna = (select auth.uid()))
    );

-- Nadie escribe directo: solo generar_resumenes() y registrar_pago().
revoke all on public.resumen_mensual from anon;
revoke all on public.pago from anon;
revoke insert, update, delete, truncate on public.resumen_mensual from authenticated;
revoke insert, update, delete, truncate on public.pago from authenticated;

-- Agrega lo cobrable del mes por alumna y lo guarda (upsert). Idempotente:
-- se puede volver a correr para el mismo mes sin duplicar, y nunca pisa un
-- resumen que ya está pagado (el "where" del on conflict lo protege).
-- Devuelve {"resultado": ok | no_autorizada | datos_invalidos, "generados": n}
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

    with cobrables as (
        select r.id_alumna, c.precio
        from public.reserva r
        join public.clase c on c.id = r.id_clase
        where r.estado = 'confirmada'
          and c.estado = 'programada'
          and extract(month from c.fecha) = p_mes
          and extract(year from c.fecha) = p_anio
          and public._inicio_clase(c.fecha, c.hora_inicio) <= now()
    ),
    agregado as (
        select id_alumna, count(*) as cantidad, sum(precio) as total
        from cobrables
        group by id_alumna
    ),
    upsert as (
        insert into public.resumen_mensual (id_alumna, mes, anio, cantidad_clases, total)
        select id_alumna, p_mes, p_anio, cantidad, total from agregado
        on conflict (id_alumna, mes, anio) do update
            set cantidad_clases = excluded.cantidad_clases, total = excluded.total
            where public.resumen_mensual.estado = 'pendiente'
        returning 1
    )
    select count(*) into v_generados from upsert;

    return jsonb_build_object('resultado', 'ok', 'generados', v_generados);
end;
$$;

-- Devuelve {"resultado": ok | no_autorizada | ya_pagado | no_existe | datos_invalidos}
create or replace function public.registrar_pago(p_id_resumen bigint, p_monto numeric, p_medio text, p_fecha date)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_estado text;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_monto is null or p_monto <= 0 or p_medio not in ('efectivo', 'transferencia') or p_fecha is null then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    select estado into v_estado from public.resumen_mensual where id = p_id_resumen;
    if v_estado is null then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if v_estado = 'pagado' then
        return jsonb_build_object('resultado', 'ya_pagado');
    end if;
    insert into public.pago (id_resumen_mensual, monto, medio_pago, fecha)
    values (p_id_resumen, p_monto, p_medio, p_fecha);
    update public.resumen_mensual set estado = 'pagado' where id = p_id_resumen;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

revoke execute on function public.generar_resumenes(int, int) from public, anon;
revoke execute on function public.registrar_pago(bigint, numeric, text, date) from public, anon;
grant execute on function public.generar_resumenes(int, int) to authenticated;
grant execute on function public.registrar_pago(bigint, numeric, text, date) to authenticated;
