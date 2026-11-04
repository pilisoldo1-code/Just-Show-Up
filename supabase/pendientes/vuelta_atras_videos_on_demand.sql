-- Vuelta atrás de 20261106000000_videos_on_demand.sql. Se niega si quedan archivos sin borrar.
-- El bucket `videos-clases` no se borra acá: Supabase no permite borrar Storage por SQL; hacerlo desde el panel.
do $$
begin
    if exists (select 1 from public.video where estado <> 'eliminado') then
        raise exception 'Hay videos con archivos guardados: primero hay que limpiarlos.';
    end if;
end;
$$;

drop function public.video_limpieza_error(bigint, text);
drop function public.video_marcar_eliminado(bigint);
drop function public.videos_a_limpiar(integer);
drop function public.video_error(bigint, uuid, text);
drop function public.video_publicar(bigint, uuid);
drop function public.video_de_agente(bigint, uuid);
drop function public.video_registrar(uuid, timestamptz, text, integer, bigint, jsonb, text);
drop function public.video_consultar(uuid, timestamptz, integer);
drop function public._video_evaluar(uuid, timestamptz, integer, bigint);
drop function public._video_programada(bigint);
drop function public._video_partes_validas(jsonb, bigint);
drop function public.video_agente_identificar(text);
drop function public.video_para_firmar(bigint);
drop function public.video_progreso(bigint, integer);
drop function public.video_iniciar(bigint);
drop function public._video_vigente(bigint);
drop function public._video_inicio_ciclo(integer);
drop function public.videos_disponibles(bigint);
drop function public.video_programar(bigint, boolean);
drop function public.videos_configuracion();
drop table public.video_reproduccion;
drop table public.video_agente;
drop table public.video;
drop table public.video_programacion;
drop table public.video_parametros;
drop function public._video_coherente();
