-- Videos On Demand: la «regla de los 15 minutos» ya no impide publicar una grabación registrada con «Empezar a grabar».
-- Antes, cualquier clase de la profesora que empezara 15 minutos antes o después de la grabación competía con su
-- grabación independiente y el resultado era clase_ambigua (no se publicaba). Desde el 09/11 ya no se eligen horarios
-- para grabar, así que esas clases nunca pueden ser el destino del video.
-- Cambio: en public._video_evaluar solo cuentan como candidatas las clases con grabación ACTIVADA para su horario
-- (_video_programada, la misma condición que ya exigía después) o las que ya tienen su video (reintentos).
-- Todo lo demás queda igual: una sola solicitud esperando por profesora, plazo de 3 horas, tolerancia de 2 minutos,
-- profesora del token, duraciones, topes, publicación, vencimiento a las 24 h y limpieza. Una clase con grabación
-- activada sigue siendo ambigua con una solicitud a la misma hora (no se adivina).
-- Sin solicitud, una grabación cerca de una clase sin grabación activada sigue dando no_autorizada (como antes).
-- No toca tablas, datos, reservas, asistencias, facturación ni planificación. Vuelta atrás:
-- supabase/pendientes/vuelta_atras_videos_regla_15_minutos.sql

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
      and abs(extract(epoch from public._inicio_clase(x.fecha, x.hora_inicio) - p_inicio)) <= g.ventana_clase_seg
      -- Solo compiten las clases con grabación activada (o que ya tienen su video, para sus reintentos): una clase
      -- presencial sin grabación activada no vuelve ambigua una grabación registrada con «Empezar a grabar».
      and (public._video_programada(x.id) or exists (select 1 from public.video vv where vv.id_clase = x.id));
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
        -- Sin solicitud y cerca de una clase suya sin grabación activada: como siempre, no se autoriza (no reintenta).
        if exists (select 1 from public.clase x
                   where x.id_profesora = p_id_profesora and x.estado <> 'cancelada'
                     and x.fecha between v_fecha - 1 and v_fecha + 1
                     and abs(extract(epoch from public._inicio_clase(x.fecha, x.hora_inicio) - p_inicio)) <= g.ventana_clase_seg) then
            return jsonb_build_object('resultado', 'no_autorizada');
        end if;
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
