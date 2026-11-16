-- Notificaciones, fase D: lista de espera automática (funciona aunque nadie tenga la plataforma abierta).
-- Hasta ahora las ofertas vencidas se procesaban cuando alguien abría una pantalla o hacía una acción en esa clase. Con
-- esto, la tarea de cada minuto también las procesa:
-- - _procesar_ofertas_vencidas(): busca clases programadas que todavía no empezaron y que tienen una oferta vencida, o
--   lugares libres con alumnas esperando, y llama a la MISMA función de siempre, _procesar_lista_espera(clase): vence la
--   oferta, respeta el orden de la lista y ofrece solo los lugares libres (bloquea la fila de la clase igual que una
--   reserva: sin sobrecupo ni reservas duplicadas). No se cambió ninguna regla de reservas.
-- - Interruptor propio notificacion_parametros.lista_espera_automatica (APAGADO; solo la superadministradora).
-- - notificaciones_tick(): versión final (lista de espera, generar avisos y avisar al despachador). Con los correos
--   encendidos, la siguiente alumna recibe el correo «¡Se liberó un lugar para vos!» en la misma vuelta; dentro de la
--   plataforma lo ve siempre (Novedades, Mis inscripciones).
-- No cambia datos existentes.

alter table public.notificacion_parametros add column lista_espera_automatica boolean not null default false;

create or replace function public._procesar_ofertas_vencidas()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_clase bigint;
    v_n integer := 0;
begin
    for v_clase in
        select c.id
          from public.clase c
         where c.estado = 'programada'
           and public._inicio_clase(c.fecha, c.hora_inicio) > now()
           and (exists (select 1 from public.listadeespera le
                         where le.id_clase = c.id and le.estado = 'ofrecido' and le.oferta_vence_en <= now())
                or (exists (select 1 from public.listadeespera le where le.id_clase = c.id and le.estado = 'en_espera')
                    and c.cupo_max > (select count(*) from public.reserva r
                                       where r.id_clase = c.id and r.estado = 'confirmada')
                                   + (select count(*) from public.listadeespera le
                                       where le.id_clase = c.id and le.estado = 'ofrecido')))
         order by c.fecha, c.hora_inicio, c.id
         limit 200
    loop
        perform public._procesar_lista_espera(v_clase);
        v_n := v_n + 1;
    end loop;
    return v_n;
end;
$$;

create or replace function public.notificaciones_lista_espera_activar(p_activo boolean)
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
       set lista_espera_automatica = p_activo, actualizado_en = now(), actualizado_por = (select auth.uid())
     where id = 1;
    return 'ok';
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
    v_clases integer := 0;
    v_nuevos integer := 0;
    v_despachador text := 'nada_pendiente';
begin
    -- si la tarea anterior sigue corriendo, esta no hace nada (nunca dos a la vez)
    if not pg_try_advisory_xact_lock(hashtext('notificaciones_tick')) then
        return jsonb_build_object('resultado', 'ocupado');
    end if;
    select * into v_par from public.notificacion_parametros where id = 1;
    if not v_par.correo_activo and not v_par.lista_espera_automatica then
        return jsonb_build_object('resultado', 'apagado');
    end if;
    if v_par.lista_espera_automatica then
        v_clases := public._procesar_ofertas_vencidas();
    end if;
    if v_par.correo_activo then
        v_nuevos := public._notificaciones_generar();
        if exists (select 1 from public.notificacion_envio where estado = 'pendiente' and proximo_intento <= now()) then
            v_despachador := public._notificaciones_llamar_despachador();
        end if;
    else
        v_despachador := 'correos_apagados';
    end if;
    return jsonb_build_object('resultado', 'ok', 'clases_lista_espera', v_clases, 'nuevos', v_nuevos,
                              'despachador', v_despachador);
end;
$$;

revoke execute on function public._procesar_ofertas_vencidas(), public.notificaciones_tick(),
    public.notificaciones_lista_espera_activar(boolean) from public, anon, authenticated, service_role;
grant execute on function public.notificaciones_lista_espera_activar(boolean) to authenticated;
