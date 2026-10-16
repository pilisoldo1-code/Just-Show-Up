-- Planificación: licencias y cancelaciones puntuales de la profesora.
-- Un bloqueo cancela las clases de un rango de fechas (todas, o solo un horario).
-- Las reservas de esas clases se cancelan (no se cobran) y la lista de espera expira.
-- Al quitar el bloqueo, las clases futuras vuelven a estar disponibles.
create table public.bloqueo (
    id bigint generated always as identity primary key,
    tipo text not null check (tipo in ('licencia', 'puntual')),
    fecha_desde date not null,
    fecha_hasta date not null,
    id_horario bigint references public.horario(id),
    motivo text not null default '',
    creado_en timestamptz not null default now(),
    check (fecha_hasta >= fecha_desde)
);

alter table public.bloqueo enable row level security;

create policy "bloqueo: leer profesora" on public.bloqueo
    for select to authenticated using (public.es_profesora());

revoke all on public.bloqueo from anon;
revoke insert, update, delete, truncate on public.bloqueo from authenticated;

create or replace function public._clase_bloqueada(p_fecha date, p_id_horario bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.bloqueo b
        where p_fecha between b.fecha_desde and b.fecha_hasta
          and (b.id_horario is null or b.id_horario = p_id_horario)
    );
$$;

-- Devuelve {"resultado": ok | no_autorizada | fechas_invalidas, "clases": n, "reservas": n}
create or replace function public.crear_bloqueo(
    p_tipo text, p_desde date, p_hasta date, p_id_horario bigint, p_motivo text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_clases bigint[];
    v_reservas integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_tipo not in ('licencia', 'puntual') or p_desde is null or p_hasta is null
       or p_desde < v_hoy or p_hasta < p_desde then
        return jsonb_build_object('resultado', 'fechas_invalidas');
    end if;
    insert into public.bloqueo (tipo, fecha_desde, fecha_hasta, id_horario, motivo)
    values (p_tipo, p_desde, p_hasta, p_id_horario, coalesce(trim(p_motivo), ''));

    -- Solo clases que todavía no empezaron.
    with canceladas as (
        update public.clase c set estado = 'cancelada'
        where c.estado = 'programada'
          and c.fecha between p_desde and p_hasta
          and (p_id_horario is null or c.id_horario = p_id_horario)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from canceladas;

    with r as (
        update public.reserva set estado = 'cancelada', fecha_cancelacion = now()
        where id_clase = any(v_clases) and estado = 'confirmada'
        returning 1
    )
    select count(*) into v_reservas from r;

    update public.listadeespera set estado = 'expirado'
    where id_clase = any(v_clases) and estado in ('en_espera', 'ofrecido');
    update public.clase set cantidad_inscriptas = 0 where id = any(v_clases);

    return jsonb_build_object('resultado', 'ok', 'clases', coalesce(array_length(v_clases, 1), 0),
                              'reservas', v_reservas);
end;
$$;

-- Devuelve {"resultado": ok | no_autorizada | no_existe, "clases": n}
create or replace function public.quitar_bloqueo(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_b public.bloqueo;
    v_clases integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    delete from public.bloqueo where id = p_id returning * into v_b;
    if v_b.id is null then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    -- Vuelven a estar disponibles las clases futuras que no tapa otro bloqueo.
    -- Las reservas canceladas no se restauran: cada alumna vuelve a reservar.
    with r as (
        update public.clase c set estado = 'programada'
        where c.estado = 'cancelada'
          and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
          and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
          and not public._clase_bloqueada(c.fecha, c.id_horario)
        returning 1
    )
    select count(*) into v_clases from r;
    return jsonb_build_object('resultado', 'ok', 'clases', v_clases);
end;
$$;

-- generar_clases: las clases que caen dentro de un bloqueo nacen canceladas.
create or replace function public.generar_clases(p_lunes date)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
begin
    if auth.uid() is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    if extract(isodow from p_lunes) <> 1 or p_lunes < v_hoy - 7 or p_lunes > v_hoy + 28 then
        return;
    end if;
    insert into public.clase (id_horario, fecha, hora_inicio, duracion, precio, estado)
    select h.id, d.fecha, h.hora_inicio, h.duracion, h.precio,
           case when public._clase_bloqueada(d.fecha, h.id) then 'cancelada' else 'programada' end
    from public.horario h
    cross join lateral (
        select p_lunes + (array_position(
            array['lunes','martes','miercoles','jueves','viernes','sabado','domingo'],
            h.dia_semana) - 1) as fecha
    ) d
    where h.activo
    on conflict (id_horario, fecha) do nothing;
end;
$$;

revoke execute on function public._clase_bloqueada(date, bigint) from public, anon, authenticated;
revoke execute on function public.crear_bloqueo(text, date, date, bigint, text) from public, anon;
revoke execute on function public.quitar_bloqueo(bigint) from public, anon;
grant execute on function public.crear_bloqueo(text, date, date, bigint, text) to authenticated;
grant execute on function public.quitar_bloqueo(bigint) to authenticated;
