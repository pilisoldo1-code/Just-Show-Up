-- Vuelta atrás de 20261111000000_videos_regla_15_minutos.sql: deja public._video_evaluar exactamente como la dejó
-- 20261110000000_videos_grabaciones_independientes.sql (verificado el 10/11/2026: idéntica a producción, md5 de
-- pg_get_functiondef = c789cf0e693ee53d2b1dbed2691d533f). No toca datos.

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
    v_grabaciones bigint[];
    gi public.grabacion_independiente;
    c public.clase;
    v public.video;
begin
    select * into g from public.video_parametros;
    select array_agg(x.id) into v_clases
    from public.clase x
    where x.id_profesora = p_id_profesora and x.estado <> 'cancelada'
      and x.fecha between v_fecha - 1 and v_fecha + 1
      and abs(extract(epoch from public._inicio_clase(x.fecha, x.hora_inicio) - p_inicio)) <= g.ventana_clase_seg;
    -- Una grabación ya asociada a una solicitud (sus reintentos) va solo a esa. Si no, la toma la solicitud que todavía
    -- espera y en cuyo plazo empezó la grabación.
    select array_agg(x.id) into v_grabaciones
    from public.grabacion_independiente x
    where x.id_profesora = p_id_profesora and x.cancelada_en is null and x.inicio_grabacion = p_inicio;
    if v_grabaciones is null then
        select array_agg(x.id) into v_grabaciones
        from public.grabacion_independiente x
        where x.id_profesora = p_id_profesora and x.cancelada_en is null and x.inicio_grabacion is null
          and p_inicio between x.creada_en - make_interval(secs => g.independiente_tolerancia_seg) and x.vence_espera_en;
    end if;
    if v_clases is null and v_grabaciones is null then
        return jsonb_build_object('resultado', 'sin_clase');
    end if;
    -- Nunca se adivina: una clase y una grabación independiente a la vez también es ambiguo.
    if coalesce(array_length(v_clases, 1), 0) + coalesce(array_length(v_grabaciones, 1), 0) > 1 then
        return jsonb_build_object('resultado', 'clase_ambigua');
    end if;
    if v_grabaciones is not null then
        select * into gi from public.grabacion_independiente where id = v_grabaciones[1];
        select * into v from public.video where id_grabacion = gi.id;
        if found and v.estado = 'publicado' then
            return jsonb_build_object('resultado', 'ya_publicado', 'id_video', v.id);
        end if;
        if found and v.estado = 'eliminado' and v.publicado_en is not null then
            return jsonb_build_object('resultado', 'ya_eliminado', 'id_video', v.id);
        end if;
        if not public._grabacion_habilitada(gi.id) then
            return jsonb_build_object('resultado', 'no_autorizada');
        end if;
        if coalesce(p_duracion_seg, 0) < g.independiente_min_seg or p_duracion_seg > g.independiente_max_seg then
            return jsonb_build_object('resultado', 'duracion_fuera_de_rango');
        end if;
        if (select coalesce(sum(x.tamano_bytes), 0) from public.video x
            where x.estado <> 'eliminado' and x.id_grabacion is distinct from gi.id) + p_tamano_bytes > g.almacenamiento_max_bytes then
            return jsonb_build_object('resultado', 'sin_espacio');
        end if;
        return jsonb_build_object('resultado', 'autorizada', 'id_grabacion', gi.id, 'id_empresa', gi.id_empresa);
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
    if coalesce(p_duracion_seg, 0) < least(c.duracion * 60 - g.margen_menos_seg, g.clase_min_seg)
       or p_duracion_seg > c.duracion * 60 + g.margen_mas_seg then
        return jsonb_build_object('resultado', 'duracion_fuera_de_rango');
    end if;
    if (select coalesce(sum(x.tamano_bytes), 0) from public.video x
        where x.estado <> 'eliminado' and x.id_clase is distinct from c.id) + p_tamano_bytes > g.almacenamiento_max_bytes then
        return jsonb_build_object('resultado', 'sin_espacio');
    end if;
    return jsonb_build_object('resultado', 'autorizada', 'id_clase', c.id, 'id_empresa', c.id_empresa);
end;
$$;
