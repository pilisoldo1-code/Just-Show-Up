-- Vuelta atrás de 20261110000000_videos_grabaciones_independientes.sql: deja las 6 funciones exactamente como en
-- 20261106000000_videos_on_demand.sql, vuelve video.id_clase obligatorio y quita la tabla de grabaciones independientes.
-- Se niega si ya hay videos de grabaciones independientes: primero hay que limpiarlos y borrarlos a propósito.
do $$
begin
    if exists (select 1 from public.video where id_grabacion is not null) then
        raise exception 'Hay videos de grabaciones independientes: la vuelta atrás no los borra.';
    end if;
end;
$$;

drop function public.grabacion_independiente_crear(text, bigint, text);
drop function public._grabacion_tipo_coherente() cascade;
drop function public.grabacion_independiente_cancelar(bigint);
drop function public.mis_grabaciones_independientes();
create or replace function public._video_coherente()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not exists (select 1 from public.clase c
                   where c.id = new.id_clase and c.id_empresa = new.id_empresa and c.id_profesora = new.id_profesora) then
        raise exception 'El video no coincide con la empresa y la profesora de su clase.' using errcode = '23503';
    end if;
    return new;
end;
$$;

create or replace function public.videos_disponibles(p_id_video bigint default null)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
begin
    if v_empresa is null or not (public.es_alumna_activa() or public.es_docente()) then
        return '[]'::jsonb;
    end if;
    return coalesce((
        select jsonb_agg(jsonb_build_object(
                   'id', v.id,
                   'clase', coalesce(t.nombre, 'Clase'),
                   'fecha', c.fecha,
                   'hora_inicio', c.hora_inicio,
                   'profesora', btrim(p.nombre || ' ' || p.apellido),
                   'duracion_seg', v.duracion_seg,
                   'vence_en', v.vence_en,
                   'segundos_restantes', floor(extract(epoch from v.vence_en - now()))::bigint
               ) order by v.publicado_en desc)
        from public.video v
        join public.clase c on c.id = v.id_clase
        join public.profiles p on p.id = v.id_profesora
        left join public.tipo_clase t on t.id = c.id_tipo_clase
        where v.id_empresa = v_empresa and v.estado = 'publicado' and now() < v.vence_en
          and (p_id_video is null or v.id = p_id_video)
    ), '[]'::jsonb);
end;
$$;

create or replace function public.videos_ultimas_grabaciones(p_limite integer default 20)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
begin
    if v_empresa is null or not public.es_admin() then
        return '[]'::jsonb;
    end if;
    return coalesce((
        select jsonb_agg(jsonb_build_object(
                   'id', x.id,
                   'clase', coalesce(t.nombre, 'Clase'),
                   'fecha', c.fecha,
                   'hora_inicio', c.hora_inicio,
                   'estado', x.estado,
                   'vencido', x.vence_en is not null and x.vence_en <= now(),
                   'nunca_publicado', x.publicado_en is null,
                   'intentos', x.intentos,
                   'ultimo_error', x.ultimo_error,
                   'limpieza_intentos', x.limpieza_intentos,
                   'limpieza_error', x.limpieza_error,
                   'actualizado_en', x.actualizado_en
               ) order by x.actualizado_en desc)
        from (select * from public.video v where v.id_empresa = v_empresa
              order by v.actualizado_en desc limit least(greatest(coalesce(p_limite, 20), 1), 50)) x
        join public.clase c on c.id = x.id_clase
        left join public.tipo_clase t on t.id = c.id_tipo_clase
    ), '[]'::jsonb);
end;
$$;

create or replace function public._video_evaluar(p_id_profesora uuid, p_inicio timestamptz, p_duracion_seg integer, p_tamano_bytes bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    g public.video_parametros;
    v_fecha date := (p_inicio at time zone 'America/Montevideo')::date;
    v_clases bigint[];
    c public.clase;
    v public.video;
begin
    select * into g from public.video_parametros;
    select array_agg(x.id) into v_clases
    from public.clase x
    where x.id_profesora = p_id_profesora and x.estado <> 'cancelada'
      and x.fecha between v_fecha - 1 and v_fecha + 1
      and abs(extract(epoch from public._inicio_clase(x.fecha, x.hora_inicio) - p_inicio)) <= g.ventana_clase_seg;
    if v_clases is null then
        return jsonb_build_object('resultado', 'sin_clase');
    end if;
    if array_length(v_clases, 1) > 1 then
        return jsonb_build_object('resultado', 'clase_ambigua');
    end if;
    select * into c from public.clase where id = v_clases[1];
    select * into v from public.video where id_clase = c.id;
    if found and v.estado = 'publicado' then
        return jsonb_build_object('resultado', 'ya_publicado', 'id_video', v.id);
    end if;
    if found and v.estado = 'eliminado' and v.publicado_en is not null then
        return jsonb_build_object('resultado', 'ya_eliminado', 'id_video', v.id);
    end if;
    if not public._video_programada(c.id) then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(p_duracion_seg, 0) < c.duracion * 60 - g.margen_menos_seg
       or p_duracion_seg > c.duracion * 60 + g.margen_mas_seg then
        return jsonb_build_object('resultado', 'duracion_fuera_de_rango');
    end if;
    if (select coalesce(sum(x.tamano_bytes), 0) from public.video x
        where x.estado <> 'eliminado' and x.id_clase <> c.id) + p_tamano_bytes > g.almacenamiento_max_bytes then
        return jsonb_build_object('resultado', 'sin_espacio');
    end if;
    return jsonb_build_object('resultado', 'autorizada', 'id_clase', c.id, 'id_empresa', c.id_empresa);
end;
$$;

create or replace function public.video_registrar(
    p_id_profesora uuid, p_inicio timestamptz, p_huella text, p_duracion_seg integer, p_tamano_bytes bigint,
    p_partes jsonb, p_lista_hls text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    e jsonb;
    v_clase bigint;
    v public.video;
begin
    if p_huella !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_lista_hls), '') = ''
       or not public._video_partes_validas(p_partes, p_tamano_bytes) then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    e := public._video_evaluar(p_id_profesora, p_inicio, p_duracion_seg, p_tamano_bytes);
    if e ->> 'resultado' <> 'autorizada' then
        return e;
    end if;
    v_clase := (e ->> 'id_clase')::bigint;
    perform pg_advisory_xact_lock(hashtext('video_clase'), v_clase::integer);
    if exists (select 1 from public.video x where x.huella_sha256 = p_huella and x.id_clase <> v_clase) then
        return jsonb_build_object('resultado', 'huella_repetida');
    end if;
    select * into v from public.video where id_clase = v_clase for update;
    if found and v.estado = 'publicado' then
        return jsonb_build_object('resultado', 'ya_publicado', 'id_video', v.id);
    end if;
    if found then
        update public.video set estado = 'subiendo', huella_sha256 = p_huella, duracion_seg = p_duracion_seg,
            tamano_bytes = p_tamano_bytes, partes = p_partes, lista_hls = p_lista_hls, intentos = intentos + 1,
            ultimo_error = null, archivo_eliminado_en = null, actualizado_en = now()
        where id = v.id returning * into v;
    else
        insert into public.video (id_empresa, id_clase, id_profesora, huella_sha256, duracion_seg, tamano_bytes,
                                  partes, lista_hls)
        values ((e ->> 'id_empresa')::bigint, v_clase, p_id_profesora, p_huella, p_duracion_seg, p_tamano_bytes,
                p_partes, p_lista_hls)
        returning * into v;
    end if;
    return jsonb_build_object('resultado', 'ok', 'id_video', v.id, 'id_empresa', v.id_empresa, 'partes', v.partes);
end;
$$;

create or replace function public.video_publicar(p_id_video bigint, p_id_profesora uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.video;
begin
    select * into v from public.video where id = p_id_video and id_profesora = p_id_profesora for update;
    if not found then
        return jsonb_build_object('resultado', 'no_encontrado');
    end if;
    if v.estado = 'eliminado' then
        return jsonb_build_object('resultado', 'ya_eliminado');
    end if;
    if v.estado = 'error' then
        return jsonb_build_object('resultado', 'no_subiendo');
    end if;
    if v.estado = 'subiendo' then
        if not public._video_programada(v.id_clase) then
            return jsonb_build_object('resultado', 'no_autorizada');
        end if;
        update public.video set estado = 'publicado', publicado_en = now(), vence_en = now() + interval '24 hours',
            ultimo_error = null, actualizado_en = now()
        where id = v.id returning * into v;
    end if;
    return jsonb_build_object('resultado', 'ok', 'publicado_en', v.publicado_en, 'vence_en', v.vence_en);
end;
$$;

drop function public._grabacion_habilitada(bigint);
drop trigger video_coherente on public.video;
alter table public.video drop constraint video_clase_o_grabacion;
alter table public.video drop column id_grabacion;
alter table public.video alter column id_clase set not null;
create trigger video_coherente before insert or update of id_empresa, id_clase, id_profesora on public.video
    for each row execute function public._video_coherente();
drop table public.grabacion_independiente;
alter table public.video_parametros drop column independiente_ventana_seg, drop column independiente_tolerancia_seg,
    drop column independiente_min_seg, drop column independiente_max_seg;
do $$
begin
    if to_regclass('supabase_migrations.schema_migrations') is not null then
        delete from supabase_migrations.schema_migrations where version = '20261110000000';
    end if;
end;
$$;
