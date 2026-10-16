-- Planificación, parte 2: registro de las reservas canceladas por la profesora y
-- recuperación de esas reservas al eliminar el bloqueo.
-- Compatible con los datos existentes: columna nueva opcional (las filas actuales
-- quedan en NULL = no las canceló la profesora); funciones con la misma firma.

-- Bloqueo que canceló la reserva. NULL = la canceló la alumna (o sigue activa).
-- Sin clave foránea a propósito: el número se conserva como registro aunque el
-- bloqueo se elimine (por ejemplo, reservas de clases que ya pasaron).
alter table public.reserva add column id_bloqueo bigint;
create index reserva_id_bloqueo_idx on public.reserva (id_bloqueo) where id_bloqueo is not null;
comment on column public.reserva.id_bloqueo is 'Bloqueo de la profesora que canceló esta reserva. NULL = la canceló la alumna o sigue activa. Sin FK a propósito: se conserva como registro aunque el bloqueo se elimine.';

-- Devuelve {"resultado": ok | no_autorizada | fechas_invalidas | motivo_vacio | horario_invalido,
--           "id": n, "clases": n, "reservas": n, "espera": n}
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
    v_id bigint;
    v_clases bigint[];
    v_reservas integer;
    v_espera integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_tipo not in ('licencia', 'puntual') or p_desde is null or p_hasta is null
       or p_desde < v_hoy or p_hasta < p_desde then
        return jsonb_build_object('resultado', 'fechas_invalidas');
    end if;
    if coalesce(trim(p_motivo), '') = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;
    if p_id_horario is not null and not exists (select 1 from public.horario where id = p_id_horario) then
        return jsonb_build_object('resultado', 'horario_invalido');
    end if;

    insert into public.bloqueo (tipo, fecha_desde, fecha_hasta, id_horario, motivo)
    values (p_tipo, p_desde, p_hasta, p_id_horario, trim(p_motivo))
    returning id into v_id;

    -- Se bloquean las clases (igual que reservar): una reserva simultánea espera y
    -- después encuentra la clase cancelada. Lo que se cancela es lo que hay en este
    -- momento, aunque haya cambiado desde la vista previa.
    perform 1 from public.clase c
    where c.estado = 'programada'
      and c.fecha between p_desde and p_hasta
      and (p_id_horario is null or c.id_horario = p_id_horario)
      and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    order by c.id
    for update;

    with canceladas as (
        update public.clase c set estado = 'cancelada', cantidad_inscriptas = 0
        where c.estado = 'programada'
          and c.fecha between p_desde and p_hasta
          and (p_id_horario is null or c.id_horario = p_id_horario)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from canceladas;

    with r as (
        update public.reserva
        set estado = 'cancelada', fecha_cancelacion = now(), id_bloqueo = v_id
        where id_clase = any(v_clases) and estado = 'confirmada'
        returning 1
    )
    select count(*) into v_reservas from r;

    with e as (
        update public.listadeespera set estado = 'expirado'
        where id_clase = any(v_clases) and estado in ('en_espera', 'ofrecido')
        returning 1
    )
    select count(*) into v_espera from e;

    return jsonb_build_object('resultado', 'ok', 'id', v_id,
                              'clases', coalesce(array_length(v_clases, 1), 0),
                              'reservas', v_reservas, 'espera', v_espera);
end;
$$;

-- Devuelve {"resultado": ok | no_autorizada | no_existe | finalizado,
--           "clases": n, "reservas": n, "sin_cupo": n}
create or replace function public.quitar_bloqueo(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_b public.bloqueo;
    v_clases bigint[];
    v_recuperadas integer;
    v_sin_cupo integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_b from public.bloqueo where id = p_id for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if v_b.fecha_hasta < v_hoy then
        return jsonb_build_object('resultado', 'finalizado');
    end if;
    delete from public.bloqueo where id = p_id;

    -- Vuelven las clases futuras del rango (también las que se generaron canceladas
    -- durante el bloqueo) que no estén cubiertas por otro bloqueo.
    perform 1 from public.clase c
    where c.estado = 'cancelada'
      and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
      and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
      and public._inicio_clase(c.fecha, c.hora_inicio) > now()
      and not public._clase_bloqueada(c.fecha, c.id_horario)
    order by c.id
    for update;

    with r as (
        update public.clase c set estado = 'programada'
        where c.estado = 'cancelada'
          and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
          and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
          and not public._clase_bloqueada(c.fecha, c.id_horario)
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from r;

    -- Se recuperan solo las reservas que canceló este bloqueo (nunca las que
    -- canceló la alumna), sin duplicar y sin pasar el cupo actual, en el orden
    -- en que se habían hecho.
    with candidatas as (
        select r.id, r.id_clase,
               row_number() over (partition by r.id_clase order by r.fecha_reserva, r.id) as orden,
               row_number() over (partition by r.id_alumna, r.id_clase order by r.fecha_reserva desc, r.id desc) as unica,
               h.cupo_max - (select count(*) from public.reserva x
                             where x.id_clase = r.id_clase and x.estado = 'confirmada') as libres
        from public.reserva r
        join public.clase c on c.id = r.id_clase
        join public.horario h on h.id = c.id_horario
        where r.id_bloqueo = p_id and r.estado = 'cancelada' and r.id_clase = any(v_clases)
          and not exists (select 1 from public.reserva y
                          where y.id_alumna = r.id_alumna and y.id_clase = r.id_clase
                            and y.estado = 'confirmada')
    ),
    elegidas as (
        select id, id_clase, row_number() over (partition by id_clase order by orden) as n, libres
        from candidatas where unica = 1
    ),
    ok as (
        update public.reserva r
        set estado = 'confirmada', fecha_cancelacion = null, id_bloqueo = null
        from elegidas e
        where e.id = r.id and e.n <= e.libres
        returning 1
    )
    select count(*) into v_recuperadas from ok;

    select count(*) into v_sin_cupo from public.reserva
    where id_bloqueo = p_id and estado = 'cancelada' and id_clase = any(v_clases);

    -- Las reservas de clases que siguen cubiertas por otro bloqueo pasan a ese
    -- bloqueo, para recuperarlas cuando se elimine. Las de clases pasadas conservan
    -- el número como registro.
    update public.reserva r
    set id_bloqueo = (
        select b.id from public.bloqueo b join public.clase c on c.id = r.id_clase
        where c.fecha between b.fecha_desde and b.fecha_hasta
          and (b.id_horario is null or b.id_horario = c.id_horario)
        order by b.id limit 1)
    where r.id_bloqueo = p_id and r.estado = 'cancelada'
      and exists (select 1 from public.clase c
                  where c.id = r.id_clase and public._clase_bloqueada(c.fecha, c.id_horario));

    update public.clase c
    set cantidad_inscriptas = (select count(*) from public.reserva x
                               where x.id_clase = c.id and x.estado = 'confirmada')
    where c.id = any(v_clases);

    return jsonb_build_object('resultado', 'ok', 'clases', coalesce(array_length(v_clases, 1), 0),
                              'reservas', v_recuperadas, 'sin_cupo', v_sin_cupo);
end;
$$;

revoke execute on function public.crear_bloqueo(text, date, date, bigint, text) from public, anon;
revoke execute on function public.quitar_bloqueo(bigint) from public, anon;
grant execute on function public.crear_bloqueo(text, date, date, bigint, text) to authenticated;
grant execute on function public.quitar_bloqueo(bigint) to authenticated;
