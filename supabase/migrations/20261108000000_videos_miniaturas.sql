-- Videos On Demand: miniatura de cada grabación (un JPEG chico que genera el programa local con FFmpeg).
-- - Se guarda en la misma fila del video y se lee con la misma política de acceso que la grabación: empresa activa,
--   publicada, sin vencer y alumna activa o docente. No se agrega ninguna política ni se toca Storage.
-- - Es opcional: sin miniatura el video se publica igual y la biblioteca muestra un fondo con el ícono de reproducción.
-- - Al pasar a «eliminado» (limpieza de las 24 h o subida abandonada) se borra con la fila, sin tocar las funciones
--   de limpieza. Si el registro se rehace con otro archivo, la miniatura anterior también se borra.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_videos_miniaturas.sql

alter table public.video add column miniatura bytea
    check (miniatura is null
           or (octet_length(miniatura) between 1000 and 102400
               and substring(miniatura from 1 for 3) = '\xffd8ff'::bytea));

grant select (miniatura) on public.video to authenticated;

-- Solo el servidor (con la clave de administrador), solo para un video de esa profesora que todavía se está subiendo.
create or replace function public.video_guardar_miniatura(p_id_video bigint, p_id_profesora uuid, p_miniatura bytea)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
    if p_miniatura is null or octet_length(p_miniatura) not between 1000 and 102400
       or substring(p_miniatura from 1 for 3) <> '\xffd8ff'::bytea then
        return jsonb_build_object('resultado', 'invalida');
    end if;
    update public.video set miniatura = p_miniatura
    where id = p_id_video and id_profesora = p_id_profesora and estado = 'subiendo';
    if not found then
        return jsonb_build_object('resultado', 'no_corresponde');
    end if;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

revoke execute on function public.video_guardar_miniatura(bigint, uuid, bytea) from public, anon, authenticated;
grant execute on function public.video_guardar_miniatura(bigint, uuid, bytea) to service_role;

create or replace function public._video_miniatura_vigente()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.estado = 'eliminado' or new.huella_sha256 is distinct from old.huella_sha256 then
        new.miniatura := null;
    end if;
    return new;
end;
$$;

revoke execute on function public._video_miniatura_vigente() from public, anon, authenticated;

create trigger video_miniatura_vigente before update on public.video
    for each row execute function public._video_miniatura_vigente();
