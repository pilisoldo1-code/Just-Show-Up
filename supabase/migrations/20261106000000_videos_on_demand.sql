-- Videos On Demand: grabaciones de clases visibles 24 h desde su publicación, solo para su empresa.
-- - video: una fila por clase grabada; la empresa y la profesora salen siempre de la clase. El registro se
--   conserva después de borrar los archivos.
-- - video_programacion: qué horarios se graban y publican (lo activa la administradora, una sola vez).
-- - video_parametros: márgenes y topes, editables sin desplegar (una sola fila).
-- - video_agente: computadoras autorizadas a subir (solo se guarda la huella del token).
-- - video_reproduccion: una fila por persona y video, con el consumo ESTIMADO. El servidor no ve la transferencia
--   real (el archivo va directo del proveedor al navegador): esto reduce el riesgo, no lo garantiza.
-- - «Vencido» no es un estado: se calcula con vence_en, así el corte no depende de la limpieza.
-- - Bucket privado SIN políticas para usuarias, a propósito: con permiso de lectura una sesión podría firmar sus
--   propias URLs con el vencimiento que quiera. Las URLs las firma solo el servidor.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_videos_on_demand.sql

create table public.video_parametros (
    id integer primary key default 1 check (id = 1),
    -- Margen técnico PROVISORIO (34 a 41 min para una clase de 40): falta validarlo con grabaciones reales.
    margen_menos_seg integer not null default 360 check (margen_menos_seg >= 0),
    margen_mas_seg integer not null default 60 check (margen_mas_seg >= 0),
    duracion_clase_min integer not null default 40 check (duracion_clase_min > 0),
    ventana_clase_seg integer not null default 900 check (ventana_clase_seg > 0),
    abandono_horas integer not null default 48 check (abandono_horas > 0),
    dia_inicio_ciclo integer not null default 1 check (dia_inicio_ciclo between 1 and 28),
    tope_ciclo_bytes bigint not null default 3000000000 check (tope_ciclo_bytes > 0),
    margen_estimacion numeric(4, 2) not null default 1.25 check (margen_estimacion >= 1),
    tope_usuaria_bytes bigint not null default 300000000 check (tope_usuaria_bytes > 0),
    almacenamiento_max_bytes bigint not null default 800000000 check (almacenamiento_max_bytes > 0),
    tamano_estimado_bytes bigint not null default 130000000 check (tamano_estimado_bytes > 0),
    -- Identifica el entorno. Vacío = nadie puede subir: el programa local, el servidor (VIDEOS_ENTORNO) y esta fila
    -- tienen que coincidir. Una base recién migrada queda cerrada hasta configurarlo a propósito.
    entorno text check (entorno ~ '^(pruebas|produccion)-[a-z0-9]{8,}$')
);
insert into public.video_parametros default values;

create table public.video_programacion (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_horario bigint not null unique references public.horario(id),
    activa boolean not null default true,
    activa_desde timestamptz not null default now(),
    actualizado_por uuid references public.profiles(id) on delete set null,
    actualizado_en timestamptz not null default now()
);
create index on public.video_programacion (id_empresa);

create table public.video (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_clase bigint not null unique references public.clase(id),
    id_profesora uuid not null references public.profiles(id),
    estado text not null default 'subiendo' check (estado in ('subiendo', 'publicado', 'eliminado', 'error')),
    huella_sha256 text not null unique check (huella_sha256 ~ '^[0-9a-f]{64}$'),
    duracion_seg integer not null check (duracion_seg > 0),
    tamano_bytes bigint not null check (tamano_bytes > 0),
    partes jsonb not null,
    lista_hls text not null,
    intentos integer not null default 1 check (intentos >= 0),
    ultimo_error text,
    creado_en timestamptz not null default now(),
    actualizado_en timestamptz not null default now(),
    publicado_en timestamptz,
    vence_en timestamptz,
    archivo_eliminado_en timestamptz,
    limpieza_intentos integer not null default 0 check (limpieza_intentos >= 0),
    limpieza_error text,
    limpieza_ultimo_intento timestamptz,
    check (estado <> 'publicado' or (publicado_en is not null and vence_en is not null))
);
create index on public.video (id_empresa);
create index on public.video (estado, vence_en);

create table public.video_agente (
    id bigint generated always as identity primary key,
    id_profesora uuid not null references public.profiles(id) on delete cascade,
    nombre text not null check (length(btrim(nombre)) between 2 and 80),
    token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
    activo boolean not null default true,
    ultimo_contacto timestamptz,
    creado_en timestamptz not null default now()
);
create index on public.video_agente (id_profesora);

-- id_usuario queda vacío si se borra la cuenta: el consumo del ciclo se conserva y la cuenta se puede borrar.
create table public.video_reproduccion (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_video bigint not null references public.video(id) on delete cascade,
    id_usuario uuid references public.profiles(id) on delete set null,
    segundos_vistos integer not null default 0 check (segundos_vistos >= 0),
    bytes_estimados bigint not null check (bytes_estimados >= 0),
    creado_en timestamptz not null default now(),
    ultimo_progreso_en timestamptz not null default now(),
    unique (id_video, id_usuario)
);
create index on public.video_reproduccion (creado_en);
create index on public.video_reproduccion (id_usuario);

alter table public.video_parametros enable row level security;
alter table public.video_programacion enable row level security;
alter table public.video enable row level security;
alter table public.video_agente enable row level security;
alter table public.video_reproduccion enable row level security;
revoke all on public.video_parametros, public.video_programacion, public.video, public.video_agente,
    public.video_reproduccion from public, anon, authenticated;

-- Las rutas y la lista de reproducción no se exponen a las sesiones: solo las columnas de abajo.
grant select (id, id_empresa, id_clase, id_profesora, estado, duracion_seg, publicado_en, vence_en)
    on public.video to authenticated;
grant select on public.video_programacion to authenticated;

create policy "video: empresa activa y vigente" on public.video for select to authenticated
    using (
        id_empresa = public.empresa_activa()
        and estado = 'publicado'
        and now() < vence_en
        and (public.es_alumna_activa() or public.es_docente())
    );

create policy "video_programacion: docentes de la empresa activa" on public.video_programacion for select to authenticated
    using (id_empresa = public.empresa_activa() and public.es_docente());

-- ---------- integridad ----------
-- Solo cuando cambian estas columnas: la limpieza tiene que poder marcar un video aunque su clase cambie después.
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
revoke execute on function public._video_coherente() from public, anon, authenticated;

create trigger video_coherente before insert or update of id_empresa, id_clase, id_profesora on public.video
    for each row execute function public._video_coherente();
create trigger zz_proteger_empresa before insert or update or delete on public.video
    for each row execute function public._proteger_empresa();
create trigger zz_proteger_empresa before insert or update or delete on public.video_reproduccion
    for each row execute function public._proteger_empresa();
create trigger zz_proteger_empresa before insert or update or delete on public.video_programacion
    for each row execute function public._proteger_empresa();

-- ---------- bucket ----------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('videos-clases', 'videos-clases', false, 52428800, array['video/mp2t'])
on conflict (id) do nothing;

-- ---------- selección de horarios (administradora) ----------
create or replace function public.videos_configuracion()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_duracion integer := (select duracion_clase_min from public.video_parametros);
begin
    if v_empresa is null or not public.es_admin() then
        return '[]'::jsonb;
    end if;
    return coalesce((
        select jsonb_agg(jsonb_build_object(
                   'id', h.id,
                   'dia_semana', h.dia_semana,
                   'hora_inicio', h.hora_inicio,
                   'clase', coalesce(t.nombre, 'Clase'),
                   'profesora', btrim(coalesce(p.nombre, '') || ' ' || coalesce(p.apellido, '')),
                   'duracion', h.duracion,
                   'programable', h.duracion = v_duracion,
                   'activa', coalesce(g.activa, false)
               ))
        from public.horario h
        left join public.tipo_clase t on t.id = h.id_tipo_clase
        left join public.profiles p on p.id = h.id_profesora
        left join public.video_programacion g on g.id_horario = h.id
        where h.id_empresa = v_empresa and h.activo
    ), '[]'::jsonb);
end;
$$;

-- Devuelve: resultado = ok | no_autorizada | no_encontrado | duracion_no_valida
create or replace function public.video_programar(p_id_horario bigint, p_activa boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_duracion integer;
begin
    if v_empresa is null or not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select h.duracion into v_duracion from public.horario h where h.id = p_id_horario and h.id_empresa = v_empresa;
    if not found then
        return jsonb_build_object('resultado', 'no_encontrado');
    end if;
    if p_activa and v_duracion <> (select duracion_clase_min from public.video_parametros) then
        return jsonb_build_object('resultado', 'duracion_no_valida');
    end if;
    insert into public.video_programacion (id_empresa, id_horario, activa, actualizado_por)
    values (v_empresa, p_id_horario, p_activa, (select auth.uid()))
    on conflict (id_horario) do update
        set activa = excluded.activa, actualizado_por = excluded.actualizado_por, actualizado_en = now(),
            activa_desde = case when public.video_programacion.activa or not excluded.activa
                                then public.video_programacion.activa_desde else now() end;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

-- ---------- lectura y reproducción con sesión ----------
-- Lista lo vigente de la empresa activa, o un solo video con p_id_video. Los nombres de la clase y la profesora
-- salen de sus tablas: una alumna no puede leer el perfil de la profesora directamente.
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

create or replace function public._video_inicio_ciclo(p_dia integer)
returns timestamptz
language plpgsql
stable
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_inicio date := make_date(extract(year from v_hoy)::integer, extract(month from v_hoy)::integer, p_dia);
begin
    if v_inicio > v_hoy then
        v_inicio := (v_inicio - interval '1 month')::date;
    end if;
    return v_inicio::timestamp at time zone 'America/Montevideo';
end;
$$;

create or replace function public._video_vigente(p_id_video bigint)
returns public.video
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v public.video;
begin
    if (select auth.uid()) is null or v_empresa is null or not (public.es_alumna_activa() or public.es_docente()) then
        return null;
    end if;
    select * into v from public.video
    where id = p_id_video and id_empresa = v_empresa and estado = 'publicado' and now() < vence_en;
    return v;
end;
$$;

-- Se llama al tocar Play, no al abrir la página. Una sola fila por persona y video: pausar, adelantar, retroceder o
-- recargar no cuenta como otro inicio. La reserva inicial (un cuarto del archivo) evita que muchos inicios simultáneos
-- pasen el tope con consumo cero; después la reemplaza el progreso informado.
-- Devuelve: resultado = ok | no_disponible | limite_usuaria | sin_presupuesto
create or replace function public.video_iniciar(p_id_video bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.video := public._video_vigente(p_id_video);
    v_usuario uuid := (select auth.uid());
    g public.video_parametros;
    r public.video_reproduccion;
begin
    if v.id is null then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    select * into g from public.video_parametros;
    perform pg_advisory_xact_lock(hashtext('video_presupuesto'));
    select * into r from public.video_reproduccion where id_video = v.id and id_usuario = v_usuario;
    if found then
        if r.bytes_estimados >= g.tope_usuaria_bytes then
            return jsonb_build_object('resultado', 'limite_usuaria');
        end if;
        return jsonb_build_object('resultado', 'ok');
    end if;
    if (select coalesce(sum(x.bytes_estimados), 0) from public.video_reproduccion x
        where x.creado_en >= public._video_inicio_ciclo(g.dia_inicio_ciclo)) * g.margen_estimacion
       + v.tamano_bytes > g.tope_ciclo_bytes then
        return jsonb_build_object('resultado', 'sin_presupuesto');
    end if;
    insert into public.video_reproduccion (id_empresa, id_video, id_usuario, bytes_estimados)
    values (v.id_empresa, v.id, v_usuario, ceil(v.tamano_bytes / 4.0)::bigint);
    return jsonb_build_object('resultado', 'ok');
end;
$$;

-- Lo que entrega el navegador es una estimación: se acota al tiempo real transcurrido desde el último aviso.
-- Devuelve: resultado = ok | no_disponible | limite_usuaria
create or replace function public.video_progreso(p_id_video bigint, p_segundos integer)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.video := public._video_vigente(p_id_video);
    r public.video_reproduccion;
    v_tope bigint := (select tope_usuaria_bytes from public.video_parametros);
    v_segundos integer;
begin
    if v.id is null then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    select * into r from public.video_reproduccion
    where id_video = v.id and id_usuario = (select auth.uid()) for update;
    if not found then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    v_segundos := greatest(0, least(coalesce(p_segundos, 0), ceil(extract(epoch from now() - r.ultimo_progreso_en))::integer + 5));
    update public.video_reproduccion
    set segundos_vistos = segundos_vistos + v_segundos,
        bytes_estimados = greatest(bytes_estimados, ((segundos_vistos + v_segundos)::numeric * v.tamano_bytes / v.duracion_seg)::bigint),
        ultimo_progreso_en = now()
    where id = r.id returning * into r;
    return jsonb_build_object('resultado', case when r.bytes_estimados >= v_tope then 'limite_usuaria' else 'ok' end);
end;
$$;

-- Entrega lo necesario para firmar. Exige haber tocado Play: abrir la página nunca llega acá.
-- Devuelve: resultado = ok | no_disponible | sin_iniciar | limite_usuaria
create or replace function public.video_para_firmar(p_id_video bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v public.video := public._video_vigente(p_id_video);
    r public.video_reproduccion;
begin
    if v.id is null then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    select * into r from public.video_reproduccion where id_video = v.id and id_usuario = (select auth.uid());
    if not found then
        return jsonb_build_object('resultado', 'sin_iniciar');
    end if;
    if r.bytes_estimados >= (select tope_usuaria_bytes from public.video_parametros) then
        return jsonb_build_object('resultado', 'limite_usuaria');
    end if;
    return jsonb_build_object(
        'resultado', 'ok',
        'id_empresa', v.id_empresa,
        'partes', v.partes,
        'lista_hls', v.lista_hls,
        'segundos_restantes', floor(extract(epoch from v.vence_en - now()))::bigint
    );
end;
$$;

-- Para la administradora: estado del procesamiento de su empresa. Sin huellas, rutas ni listas de reproducción.
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

revoke execute on function public._video_vigente(bigint), public._video_inicio_ciclo(integer)
    from public, anon, authenticated;
revoke execute on function public.videos_configuracion(), public.video_programar(bigint, boolean),
    public.videos_ultimas_grabaciones(integer), public.videos_disponibles(bigint), public.video_iniciar(bigint), public.video_progreso(bigint, integer),
    public.video_para_firmar(bigint) from public, anon;
grant execute on function public.videos_configuracion(), public.video_programar(bigint, boolean),
    public.videos_ultimas_grabaciones(integer), public.videos_disponibles(bigint), public.video_iniciar(bigint), public.video_progreso(bigint, integer),
    public.video_para_firmar(bigint) to authenticated;

-- ---------- programa local y limpieza (solo el servidor, con la clave de administrador) ----------
create or replace function public.video_agente_identificar(p_token_hash text, p_entorno text)
returns uuid
language sql
security definer
set search_path = ''
as $$
    update public.video_agente a set ultimo_contacto = now()
    where a.token_hash = p_token_hash and a.activo
      and exists (select 1 from public.video_parametros g where g.entorno = p_entorno)
      and exists (select 1 from public.membresia m
                  where m.id_usuario = a.id_profesora and m.activa and (m.es_profesora or m.es_admin))
    returning a.id_profesora;
$$;

create or replace function public._video_partes_validas(p_partes jsonb, p_tamano bigint)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    v_parte jsonb;
    v_suma bigint := 0;
begin
    if jsonb_typeof(p_partes) <> 'array' or jsonb_array_length(p_partes) not between 1 and 100 then
        return false;
    end if;
    for v_parte in select * from jsonb_array_elements(p_partes) loop
        if coalesce(v_parte ->> 'nombre', '') !~ '^parte-[0-9]{2}\.ts$'
           or jsonb_typeof(v_parte -> 'bytes') is distinct from 'number'
           or (v_parte ->> 'bytes')::bigint not between 1 and 52428800 then
            return false;
        end if;
        v_suma := v_suma + (v_parte ->> 'bytes')::bigint;
    end loop;
    return v_suma = p_tamano;
end;
$$;

create or replace function public._video_programada(p_id_clase bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.clase c
        join public.video_programacion g on g.id_horario = c.id_horario and g.activa
        where c.id = p_id_clase and public._inicio_clase(c.fecha, c.hora_inicio) >= g.activa_desde
    );
$$;

-- Decide si una grabación se procesa. Solo una clase de esa profesora puede estar dentro de la ventana: si hay cero o
-- más de una, no se adivina. La empresa la define la clase, nunca el programa local.
-- Devuelve: resultado = autorizada | sin_clase | clase_ambigua | no_autorizada | duracion_fuera_de_rango |
--           ya_publicado | ya_eliminado | sin_espacio
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

-- Lo consulta el programa local ANTES de comprimir: lo no autorizado no se procesa ni ocupa espacio.
create or replace function public.video_consultar(p_id_profesora uuid, p_inicio timestamptz, p_duracion_seg integer)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select public._video_evaluar(p_id_profesora, p_inicio, p_duracion_seg,
                                 (select tamano_estimado_bytes from public.video_parametros));
$$;

-- Vuelve a evaluar todo con el tamaño real. Se puede repetir sin duplicar: una fila por clase y por archivo.
-- Devuelve: resultado = ok | huella_repetida | datos_invalidos | los de _video_evaluar
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

-- Cada contacto del programa cuenta como actividad: una subida en curso no se limpia por abandonada.
create or replace function public.video_de_agente(p_id_video bigint, p_id_profesora uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.video;
begin
    update public.video set actualizado_en = now()
    where id = p_id_video and id_profesora = p_id_profesora and estado in ('subiendo', 'error');
    select * into v from public.video where id = p_id_video and id_profesora = p_id_profesora;
    if not found then
        return null;
    end if;
    return jsonb_build_object('id', v.id, 'id_empresa', v.id_empresa, 'estado', v.estado, 'partes', v.partes);
end;
$$;

-- Publica solo desde «subiendo»: el servidor ya comprobó las partes. publicado_en y vence_en los fija la hora de la
-- base y repetir no corre el vencimiento.
-- Devuelve: resultado = ok | no_encontrado | ya_eliminado | no_subiendo | no_autorizada
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

create or replace function public.video_error(p_id_video bigint, p_id_profesora uuid, p_mensaje text)
returns void
language sql
security definer
set search_path = ''
as $$
    update public.video set estado = 'error', ultimo_error = left(coalesce(p_mensaje, ''), 500), actualizado_en = now()
    where id = p_id_video and id_profesora = p_id_profesora and estado = 'subiendo';
$$;

-- Vencidos y subidas sin actividad reciente. Primero los que hace más que no se intentan: un video que falla
-- siempre no frena a los demás.
create or replace function public.videos_a_limpiar(p_limite integer default 20)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(jsonb_agg(jsonb_build_object('id', x.id, 'id_empresa', x.id_empresa, 'partes', x.partes)), '[]'::jsonb)
    from (select v.id, v.id_empresa, v.partes from public.video v
          where (v.estado = 'publicado' and v.vence_en <= now())
             or (v.estado in ('subiendo', 'error')
                 and v.actualizado_en < now() - make_interval(hours => (select abandono_horas from public.video_parametros)))
          order by v.limpieza_ultimo_intento nulls first, v.id
          limit p_limite) x;
$$;

create or replace function public.video_marcar_eliminado(p_id_video bigint)
returns void
language sql
security definer
set search_path = ''
as $$
    update public.video set estado = 'eliminado', archivo_eliminado_en = now(), limpieza_error = null,
        limpieza_ultimo_intento = now(), limpieza_intentos = limpieza_intentos + 1
    where id = p_id_video
      and ((estado = 'publicado' and vence_en <= now())
           or (estado in ('subiendo', 'error')
               and actualizado_en < now() - make_interval(hours => (select abandono_horas from public.video_parametros))));
$$;

create or replace function public.video_limpieza_error(p_id_video bigint, p_mensaje text)
returns void
language sql
security definer
set search_path = ''
as $$
    update public.video set limpieza_error = left(coalesce(p_mensaje, ''), 500), limpieza_ultimo_intento = now(),
        limpieza_intentos = limpieza_intentos + 1
    where id = p_id_video and estado <> 'eliminado';
$$;

revoke execute on function
    public.video_agente_identificar(text, text),
    public._video_partes_validas(jsonb, bigint),
    public._video_programada(bigint),
    public._video_evaluar(uuid, timestamptz, integer, bigint),
    public.video_consultar(uuid, timestamptz, integer),
    public.video_registrar(uuid, timestamptz, text, integer, bigint, jsonb, text),
    public.video_de_agente(bigint, uuid),
    public.video_publicar(bigint, uuid),
    public.video_error(bigint, uuid, text),
    public.videos_a_limpiar(integer),
    public.video_marcar_eliminado(bigint),
    public.video_limpieza_error(bigint, text)
from public, anon, authenticated;
grant execute on function
    public.video_agente_identificar(text, text),
    public.video_consultar(uuid, timestamptz, integer),
    public.video_registrar(uuid, timestamptz, text, integer, bigint, jsonb, text),
    public.video_de_agente(bigint, uuid),
    public.video_publicar(bigint, uuid),
    public.video_error(bigint, uuid, text),
    public.videos_a_limpiar(integer),
    public.video_marcar_eliminado(bigint),
    public.video_limpieza_error(bigint, text)
to service_role;
grant select, insert, update on public.video_agente to service_role;
