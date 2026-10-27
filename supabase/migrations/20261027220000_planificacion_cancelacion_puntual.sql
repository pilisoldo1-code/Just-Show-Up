-- Planificación: cancelar clases puntuales de un día (una o varias, elegidas por checkbox),
-- sin afectar las demás clases de ese mismo día. Reutiliza el mecanismo de bloqueo que ya
-- existe para licencias: crea un bloqueo 'puntual' por cada clase cancelada (mismo id_horario
-- y fecha_desde = fecha_hasta = la fecha de esa clase), así "Bloqueos registrados" y
-- "Eliminar" (con su recuperación de reservas) funcionan igual, clase por clase.
-- Todo en una sola función: si alguna clase elegida ya no es válida (cancelada, ya empezada,
-- de un horario desactivado, o no existe), no se cancela ninguna (todo o nada).

-- Devuelve {"resultado": ok | no_autorizada | sin_clases | motivo_vacio | clases_invalidas,
--           "clases": n, "reservas": n, "espera": n}
create or replace function public.crear_bloqueos_puntuales(p_ids_clase bigint[], p_motivo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_motivo text := trim(coalesce(p_motivo, ''));
    v_ids bigint[];
    v_total integer;
    v_validas integer;
    v_reservas integer := 0;
    v_espera integer := 0;
    v_bloqueo_id bigint;
    rec record;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;

    select array_agg(distinct x) into v_ids from unnest(coalesce(p_ids_clase, '{}')) x;
    v_total := coalesce(array_length(v_ids, 1), 0);
    if v_total = 0 then
        return jsonb_build_object('resultado', 'sin_clases');
    end if;
    if v_motivo = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;

    -- Bloquea las filas involucradas (evita carreras con una alumna reservando o cancelando
    -- a la vez) y recién ahí valida: tienen que existir, estar programadas, no haber empezado
    -- y pertenecer a un horario activo.
    perform 1 from public.clase c where c.id = any(v_ids) order by c.id for update;

    select count(*) into v_validas from public.clase c
    join public.horario h on h.id = c.id_horario
    where c.id = any(v_ids) and c.estado = 'programada' and h.activo
      and public._inicio_clase(c.fecha, c.hora_inicio) > now();
    if v_validas <> v_total then
        return jsonb_build_object('resultado', 'clases_invalidas');
    end if;

    -- Un bloqueo por clase (cada clase tiene una combinación fecha + horario propia).
    for rec in select c.id, c.fecha, c.id_horario from public.clase c where c.id = any(v_ids)
    loop
        insert into public.bloqueo (tipo, fecha_desde, fecha_hasta, id_horario, motivo)
        values ('puntual', rec.fecha, rec.fecha, rec.id_horario, v_motivo)
        returning id into v_bloqueo_id;

        update public.clase set estado = 'cancelada', cantidad_inscriptas = 0 where id = rec.id;

        with r as (
            update public.reserva set estado = 'cancelada', fecha_cancelacion = now(), id_bloqueo = v_bloqueo_id
            where id_clase = rec.id and estado = 'confirmada'
            returning 1
        )
        select v_reservas + count(*) into v_reservas from r;

        with e as (
            update public.listadeespera set estado = 'expirado'
            where id_clase = rec.id and estado in ('en_espera', 'ofrecido')
            returning 1
        )
        select v_espera + count(*) into v_espera from e;
    end loop;

    return jsonb_build_object('resultado', 'ok', 'clases', v_total, 'reservas', v_reservas, 'espera', v_espera);
end;
$$;

revoke execute on function public.crear_bloqueos_puntuales(bigint[], text) from public, anon;
grant execute on function public.crear_bloqueos_puntuales(bigint[], text) to authenticated;
