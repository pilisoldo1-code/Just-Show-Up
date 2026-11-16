-- Vuelta atrás de las notificaciones (fases A a D): migraciones 20261113000000_notificaciones_parametros,
-- 20261114000000_notificaciones_correos, 20261115000000_notificaciones_correos_avisos y
-- 20261116000000_lista_espera_automatica. Todo o nada. Antes, correr notificaciones_cron_desactivar.sql si las tareas
-- programadas se habían creado. Borra la cola de envíos (su historial) y la preferencia de correo de las personas;
-- no toca reservas, listas de espera, facturas ni pagos (la lista de espera vuelve a procesarse como antes).
begin;

do $$
declare
    v_n integer := 0;
begin
    -- sin pg_cron instalado (cron.job no existe) no hay nada que revisar; se consulta con execute para no fallar
    if to_regclass('cron.job') is not null then
        execute $q$select count(*) from cron.job where jobname in ('jsu-notificaciones', 'jsu-limpieza-cron')$q$ into v_n;
    end if;
    if v_n > 0 then
        raise exception 'Primero correr notificaciones_cron_desactivar.sql (las tareas programadas siguen creadas).';
    end if;
end;
$$;

drop function if exists public.notificaciones_tick();
drop function if exists public._procesar_ofertas_vencidas();
drop function if exists public.notificaciones_lista_espera_activar(boolean);
drop function if exists public._notificaciones_generar();
drop function if exists public._notificaciones_llamar_despachador();
drop function if exists public.notificaciones_reclamar(integer);
drop function if exists public.notificaciones_resultado(bigint, text, text);
drop function if exists public.notificaciones_resumen();
drop function if exists public.notificaciones_correo_activar(boolean);
drop function if exists public.notificaciones_guardar_secreto(text, text);
drop function if exists public.cambiar_preferencia_correos(boolean);
drop function if exists public._notificacion_datos(public.notificacion_envio);
drop function if exists public.notificaciones_configurar(text, integer);
drop function if exists public.novedades_parametros();
drop function if exists public.novedades_videos_iniciados();
drop table if exists public.notificacion_envio;
drop table if exists public.notificacion_parametros;
alter table public.profiles drop column if exists recibir_correos;
delete from vault.secrets where name in ('notificaciones_url', 'notificaciones_secreto');

commit;
