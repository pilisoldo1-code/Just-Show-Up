-- Vuelta atrás de 20261106000000_videos_on_demand.sql. Se niega si quedan archivos sin borrar.
-- El bucket `videos-clases` no se borra acá: Supabase no permite borrar Storage por SQL; hacerlo desde el panel.
do $$
begin
    if exists (select 1 from public.video where estado <> 'eliminado') then
        raise exception 'Hay videos con archivos guardados: primero hay que limpiarlos.';
    end if;
end;
$$;

drop function public.video_marcar_eliminado(bigint);
drop function public.videos_a_limpiar(integer);
drop function public.video_error(bigint, uuid, text);
drop function public.video_publicar(bigint, uuid);
drop function public.video_de_agente(bigint, uuid);
drop function public.video_registrar(uuid, timestamptz, text, integer, bigint, jsonb, text);
drop function public._video_partes_validas(jsonb, bigint);
drop function public.video_agente_identificar(text);
drop function public.video_reproducir(bigint);
drop function public.videos_disponibles(bigint);
drop table public.video_reproduccion;
drop table public.video_agente;
drop table public.video;
drop function public._video_coherente();
