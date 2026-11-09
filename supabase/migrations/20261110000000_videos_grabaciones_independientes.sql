-- Videos On Demand: grabaciones independientes (contenido grabado especialmente para la biblioteca, sin horario,
-- clase presencial, reserva ni asistencia).
-- - La profesora crea una «grabación independiente» con un nombre, un tipo de clase ACTIVO de su empresa (del catálogo
--   de Tipos de clase existente) y una descripción opcional, desde Videos On Demand.
--   Desde ese momento tiene un PLAZO de 3 horas (independiente_ventana_seg) para EMPEZAR a grabar con Zoom en su
--   computadora. El plazo no limita la duración del video.
-- - El programa local no cambia: informa la hora de inicio y la duración real, y el servidor decide. Se asocia solo si
--   hay exactamente UNA candidata de esa profesora: su grabación independiente abierta o una de sus clases (±15 min).
--   Una clase y una grabación independiente a la vez es ambiguo y no se publica (no se adivina).
-- - Una sola grabación independiente esperando por profesora. Una vez asociada, la solicitud solo acepta esa grabación
--   (sus reintentos), nunca otra.
-- - Duración: entre independiente_min_seg (1 min) e independiente_max_seg (4 h), límites técnicos; no se compara con
--   ninguna clase. Topes de almacenamiento, huella única y miniatura: los mismos que las clases.
-- - Publicación, vencimiento a las 24 h desde la publicación, limpieza, reproducción, topes de consumo y permisos son
--   los mismos de siempre (misma tabla `video`, misma política). La empresa sale de la solicitud, nunca del programa.
-- - No toca reservas, asistencias, facturación, pagos, horarios, clases ni planificación.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_videos_grabaciones_independientes.sql

alter table public.video_parametros
    add column independiente_ventana_seg integer not null default 10800 check (independiente_ventana_seg between 600 and 86400),
    add column independiente_tolerancia_seg integer not null default 120 check (independiente_tolerancia_seg between 0 and 900),
    add column independiente_min_seg integer not null default 60 check (independiente_min_seg >= 1),
    add column independiente_max_seg integer not null default 14400 check (independiente_max_seg >= independiente_min_seg);

create table public.grabacion_independiente (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_profesora uuid not null references public.profiles(id),
    titulo text not null check (char_length(titulo) between 3 and 80 and titulo = btrim(titulo)),
    -- Del catálogo de Tipos de clase de la empresa (solo el nombre, la descripción y la imagen: la duración, el precio y
    -- el cupo del tipo no se aplican a las grabaciones independientes).
    id_tipo_clase bigint not null references public.tipo_clase(id),
    descripcion text check (descripcion is null or (char_length(descripcion) between 1 and 300 and descripcion = btrim(descripcion))),
    creada_en timestamptz not null default now(),
    vence_espera_en timestamptz not null,
    cancelada_en timestamptz,
    -- Hora de inicio de la grabación asociada (la que informa el programa). Vacía = todavía esperando.
    inicio_grabacion timestamptz,
    check (vence_espera_en > creada_en)
);
create index on public.grabacion_independiente (id_empresa);
create index on public.grabacion_independiente (id_tipo_clase);
create index on public.grabacion_independiente (id_profesora, creada_en desc);

alter table public.grabacion_independiente enable row level security;
revoke all on public.grabacion_independiente from public, anon, authenticated;
grant select on public.grabacion_independiente to authenticated;
-- Nadie escribe directo: solo las funciones de abajo y el servidor.
create policy "grabacion_independiente: su profesora o la administradora" on public.grabacion_independiente
    for select to authenticated
    using (id_empresa = public.empresa_activa() and (id_profesora = (select auth.uid()) or public.es_admin()));
create trigger zz_proteger_empresa before insert or update or delete on public.grabacion_independiente
    for each row execute function public._proteger_empresa();

-- Un video es de UNA clase o de UNA grabación independiente, nunca de las dos ni de ninguna.
alter table public.video alter column id_clase drop not null;
alter table public.video add column id_grabacion bigint unique references public.grabacion_independiente(id);
alter table public.video add constraint video_clase_o_grabacion check (num_nonnulls(id_clase, id_grabacion) = 1);
grant select (id_grabacion) on public.video to authenticated;
drop trigger video_coherente on public.video;
create trigger video_coherente before insert or update of id_empresa, id_clase, id_grabacion, id_profesora on public.video
    for each row execute function public._video_coherente();

-- Se puede asociar y publicar mientras no esté cancelada y su profesora siga activa como docente de esa empresa.
create or replace function public._grabacion_habilitada(p_id_grabacion bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.grabacion_independiente g
        join public.membresia m on m.id_usuario = g.id_profesora and m.id_empresa = g.id_empresa
        where g.id = p_id_grabacion and g.cancelada_en is null and m.activa and (m.es_profesora or m.es_admin)
    );
$$;
revoke execute on function public._grabacion_habilitada(bigint) from public, anon, authenticated;

create or replace function public._video_coherente()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.id_clase is not null and not exists (
           select 1 from public.clase c
           where c.id = new.id_clase and c.id_empresa = new.id_empresa and c.id_profesora = new.id_profesora) then
        raise exception 'El video no coincide con la empresa y la profesora de su clase.' using errcode = '23503';
    end if;
    if new.id_grabacion is not null and not exists (
           select 1 from public.grabacion_independiente g
           where g.id = new.id_grabacion and g.id_empresa = new.id_empresa and g.id_profesora = new.id_profesora) then
        raise exception 'El video no coincide con la empresa y la profesora de su grabación.' using errcode = '23503';
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
                   'clase', coalesce(gi.titulo, t.nombre, 'Clase'),
                   'tipo', coalesce(tg.nombre, t.nombre),
                   'independiente', gi.id is not null,
                   'fecha', coalesce(c.fecha, (gi.inicio_grabacion at time zone 'America/Montevideo')::date),
                   'hora_inicio', coalesce(c.hora_inicio, (gi.inicio_grabacion at time zone 'America/Montevideo')::time),
                   'profesora', btrim(p.nombre || ' ' || p.apellido),
                   'duracion_seg', v.duracion_seg,
                   'vence_en', v.vence_en,
                   'segundos_restantes', floor(extract(epoch from v.vence_en - now()))::bigint
               ) order by v.publicado_en desc)
        from public.video v
        left join public.clase c on c.id = v.id_clase
        left join public.grabacion_independiente gi on gi.id = v.id_grabacion
        left join public.tipo_clase tg on tg.id = gi.id_tipo_clase
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
                   'clase', coalesce(gi.titulo, t.nombre, 'Clase'),
                   'tipo', coalesce(tg.nombre, t.nombre),
                   'independiente', gi.id is not null,
                   'fecha', coalesce(c.fecha, (gi.inicio_grabacion at time zone 'America/Montevideo')::date),
                   'hora_inicio', coalesce(c.hora_inicio, (gi.inicio_grabacion at time zone 'America/Montevideo')::time),
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
        left join public.clase c on c.id = x.id_clase
        left join public.grabacion_independiente gi on gi.id = x.id_grabacion
        left join public.tipo_clase tg on tg.id = gi.id_tipo_clase
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
    if coalesce(p_duracion_seg, 0) < c.duracion * 60 - g.margen_menos_seg
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
    v_grabacion bigint;
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
    if e ? 'id_grabacion' then
        v_grabacion := (e ->> 'id_grabacion')::bigint;
        perform pg_advisory_xact_lock(hashtext('video_grabacion'), v_grabacion::integer);
        if exists (select 1 from public.video x where x.huella_sha256 = p_huella and x.id_grabacion is distinct from v_grabacion) then
            return jsonb_build_object('resultado', 'huella_repetida');
        end if;
        select * into v from public.video where id_grabacion = v_grabacion for update;
        if found and v.estado = 'publicado' then
            return jsonb_build_object('resultado', 'ya_publicado', 'id_video', v.id);
        end if;
        if found then
            update public.video set estado = 'subiendo', huella_sha256 = p_huella, duracion_seg = p_duracion_seg,
                tamano_bytes = p_tamano_bytes, partes = p_partes, lista_hls = p_lista_hls, intentos = intentos + 1,
                ultimo_error = null, archivo_eliminado_en = null, actualizado_en = now()
            where id = v.id returning * into v;
        else
            insert into public.video (id_empresa, id_grabacion, id_profesora, huella_sha256, duracion_seg, tamano_bytes,
                                      partes, lista_hls)
            values ((e ->> 'id_empresa')::bigint, v_grabacion, p_id_profesora, p_huella, p_duracion_seg, p_tamano_bytes,
                    p_partes, p_lista_hls)
            returning * into v;
        end if;
        -- Desde acá esta solicitud solo acepta esta grabación (sus reintentos), nunca otra.
        update public.grabacion_independiente set inicio_grabacion = p_inicio
        where id = v_grabacion and inicio_grabacion is null;
        return jsonb_build_object('resultado', 'ok', 'id_video', v.id, 'id_empresa', v.id_empresa, 'partes', v.partes);
    end if;
    v_clase := (e ->> 'id_clase')::bigint;
    perform pg_advisory_xact_lock(hashtext('video_clase'), v_clase::integer);
    if exists (select 1 from public.video x where x.huella_sha256 = p_huella and x.id_clase is distinct from v_clase) then
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
        if (v.id_clase is not null and not public._video_programada(v.id_clase))
           or (v.id_grabacion is not null and not public._grabacion_habilitada(v.id_grabacion)) then
            return jsonb_build_object('resultado', 'no_autorizada');
        end if;
        update public.video set estado = 'publicado', publicado_en = now(), vence_en = now() + interval '24 hours',
            ultimo_error = null, actualizado_en = now()
        where id = v.id returning * into v;
    end if;
    return jsonb_build_object('resultado', 'ok', 'publicado_en', v.publicado_en, 'vence_en', v.vence_en);
end;
$$;

-- ---------- pantallas de la profesora ----------
-- Devuelve: resultado = ok (con id y vence_espera_en) | no_autorizada | datos_invalidos | tipo_invalido |
--           sin_computadora | ya_hay_una
create or replace function public.grabacion_independiente_crear(p_titulo text, p_id_tipo_clase bigint,
                                                                p_descripcion text default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_uid uuid := (select auth.uid());
    v_titulo text := btrim(coalesce(p_titulo, ''));
    v_descripcion text := nullif(btrim(coalesce(p_descripcion, '')), '');
    v_abierta bigint;
    g public.grabacion_independiente;
begin
    if v_empresa is null or v_uid is null or not public.es_docente() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if char_length(v_titulo) not between 3 and 80 or char_length(coalesce(v_descripcion, '')) > 300 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if p_id_tipo_clase is null or not exists (select 1 from public.tipo_clase t
                                              where t.id = p_id_tipo_clase and t.id_empresa = v_empresa and t.activo) then
        return jsonb_build_object('resultado', 'tipo_invalido');
    end if;
    if not exists (select 1 from public.video_agente a where a.id_profesora = v_uid and a.activo) then
        return jsonb_build_object('resultado', 'sin_computadora');
    end if;
    perform pg_advisory_xact_lock(hashtext('grabacion_independiente'), hashtext(v_uid::text));
    select x.id into v_abierta from public.grabacion_independiente x
    where x.id_profesora = v_uid and x.cancelada_en is null and x.inicio_grabacion is null and x.vence_espera_en > now()
    limit 1;
    if found then
        return jsonb_build_object('resultado', 'ya_hay_una', 'id', v_abierta);
    end if;
    insert into public.grabacion_independiente (id_empresa, id_profesora, titulo, id_tipo_clase, descripcion, creada_en,
                                                vence_espera_en)
    values (v_empresa, v_uid, v_titulo, p_id_tipo_clase, v_descripcion, now(),
            now() + make_interval(secs => (select independiente_ventana_seg from public.video_parametros)))
    returning * into g;
    return jsonb_build_object('resultado', 'ok', 'id', g.id, 'vence_espera_en', g.vence_espera_en);
end;
$$;

-- Solo la suya y solo mientras espera (antes de que se asocie una grabación).
-- Devuelve: resultado = ok | no_encontrada | no_se_puede
create or replace function public.grabacion_independiente_cancelar(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    g public.grabacion_independiente;
begin
    select * into g from public.grabacion_independiente
    where id = p_id and id_profesora = (select auth.uid()) and id_empresa = public.empresa_activa() for update;
    if not found or not public.es_docente() then
        return jsonb_build_object('resultado', 'no_encontrada');
    end if;
    if g.cancelada_en is not null or g.inicio_grabacion is not null then
        return jsonb_build_object('resultado', 'no_se_puede');
    end if;
    update public.grabacion_independiente set cancelada_en = now() where id = g.id;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

-- Las últimas de la profesora en su empresa activa, con un estado calculado (no hay tareas programadas).
create or replace function public.mis_grabaciones_independientes()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_uid uuid := (select auth.uid());
begin
    if v_empresa is null or v_uid is null or not public.es_docente() then
        return '[]'::jsonb;
    end if;
    return coalesce((
        select jsonb_agg(jsonb_build_object(
                   'id', g.id,
                   'titulo', g.titulo,
                   'tipo', t.nombre,
                   'descripcion', g.descripcion,
                   'creada_en', g.creada_en,
                   'vence_espera_en', g.vence_espera_en,
                   'vence_en', v.vence_en,
                   'id_video', case when v.estado = 'publicado' and now() < v.vence_en then v.id end,
                   'estado', case
                       when g.cancelada_en is not null then 'cancelada'
                       when v.estado = 'publicado' and now() < v.vence_en then 'publicada'
                       when v.estado = 'publicado' or (v.estado = 'eliminado' and v.publicado_en is not null) then 'vencida'
                       when v.estado = 'subiendo' then 'procesando'
                       when v.estado = 'error' then 'con_error'
                       when v.estado = 'eliminado' then 'abandonada'
                       when g.inicio_grabacion is not null then 'procesando'
                       when g.vence_espera_en <= now() then 'sin_grabacion'
                       else 'esperando' end
               ) order by g.creada_en desc)
        from (select * from public.grabacion_independiente x
              where x.id_empresa = v_empresa and x.id_profesora = v_uid
              order by x.creada_en desc limit 20) g
        left join public.video v on v.id_grabacion = g.id
        left join public.tipo_clase t on t.id = g.id_tipo_clase
    ), '[]'::jsonb);
end;
$$;

revoke execute on function public.grabacion_independiente_crear(text, bigint, text), public.grabacion_independiente_cancelar(bigint),
    public.mis_grabaciones_independientes() from public, anon;
grant execute on function public.grabacion_independiente_crear(text, bigint, text), public.grabacion_independiente_cancelar(bigint),
    public.mis_grabaciones_independientes() to authenticated;

-- El tipo de clase tiene que ser de la misma empresa que la grabación (también ante escrituras del servidor).
create or replace function public._grabacion_tipo_coherente()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not exists (select 1 from public.tipo_clase t where t.id = new.id_tipo_clase and t.id_empresa = new.id_empresa) then
        raise exception 'El tipo de clase no es de la empresa de la grabación.' using errcode = '23503';
    end if;
    return new;
end;
$$;
revoke execute on function public._grabacion_tipo_coherente() from public, anon, authenticated;
create trigger grabacion_tipo_coherente before insert or update of id_empresa, id_tipo_clase on public.grabacion_independiente
    for each row execute function public._grabacion_tipo_coherente();
