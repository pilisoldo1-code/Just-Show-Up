-- Vuelta atrás de 20261104000000_asistencias_correcciones.sql: marcar_asistencia vuelve a ser idéntica a la de
-- producción y se quitan las funciones nuevas. El historial NO se borra si ya tiene filas (se conserva, sin uso).
begin;

drop function public.historial_asistencias_clase(bigint);
drop function public.reservas_con_factura_confirmada(bigint[]);
drop function public.asistencias_pendientes_resumen();
drop function public.resumen_asistencias_mes(integer, integer);
drop function public.corregir_asistencias(jsonb, text);

create or replace function public.marcar_asistencia(p_id_reserva bigint, p_estado text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_inicio timestamptz;
    v_duracion integer;
    v_clase bigint;
begin
    if not public.es_docente() then
        return 'no_autorizada';
    end if;
    if p_estado not in ('presente', 'ausente') then
        raise exception 'estado inválido' using errcode = '22023';
    end if;
    select public._inicio_clase(c.fecha, c.hora_inicio), c.duracion, c.id into v_inicio, v_duracion, v_clase
    from public.reserva r join public.clase c on c.id = r.id_clase
    where r.id = p_id_reserva and r.estado = 'confirmada' and r.id_empresa = public.empresa_activa();
    if v_inicio is null then
        return 'sin_reserva';
    end if;
    if not public.puede_gestionar_clase(v_clase) then
        return 'no_autorizada';
    end if;
    if now() < v_inicio or now() > v_inicio + make_interval(mins => v_duracion) then
        return 'fuera_de_horario';
    end if;
    insert into public.asistencia (id_reserva, estado) values (p_id_reserva, p_estado)
    on conflict (id_reserva) do update set estado = excluded.estado;
    return 'ok';
end;
$$;

do $$
begin
    if not exists (select 1 from public.asistencia_historial) then
        drop table public.asistencia_historial;
    else
        raise notice 'asistencia_historial tiene filas: se conserva para no perder el historial.';
    end if;
end;
$$;

commit;
