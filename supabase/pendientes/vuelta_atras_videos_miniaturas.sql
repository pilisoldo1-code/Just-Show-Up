-- Vuelta atrás de 20261108000000_videos_miniaturas.sql: deja la tabla y los permisos como antes de las miniaturas.
-- Borra las miniaturas guardadas (son solo un fotograma de cada grabación; los videos no se tocan).
drop trigger video_miniatura_vigente on public.video;
drop function public._video_miniatura_vigente();
drop function public.video_guardar_miniatura(bigint, uuid, bytea);
revoke select (miniatura) on public.video from authenticated;
alter table public.video drop column miniatura;
do $$
begin
    if to_regclass('supabase_migrations.schema_migrations') is not null then
        delete from supabase_migrations.schema_migrations where version = '20261108000000';
    end if;
end;
$$;
