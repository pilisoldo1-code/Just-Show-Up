-- Videos On Demand: grabaciones de clases visibles 24 h desde su publicación, solo para su empresa.
-- - video: una fila por clase grabada; la empresa y la profesora salen siempre de la clase. El registro se
--   conserva después de borrar los archivos.
-- - video_agente: computadoras autorizadas a subir (solo se guarda la huella del token).
-- - video_reproduccion: inicios de reproducción, para no pasar la cuota de transferencia de Supabase (un exceso
--   puede bloquear con 402 todos los proyectos de la organización).
-- - «Vencido» no es un estado: se calcula con vence_en, así el corte es exacto aunque la limpieza se atrase.
-- - Bucket privado SIN políticas para usuarias, a propósito: con permiso de lectura una sesión podría firmar sus
--   propias URLs con el vencimiento que quiera. Las URLs las firma solo el servidor.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_videos_on_demand.sql

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
    publicado_en timestamptz,
    vence_en timestamptz,
    archivo_eliminado_en timestamptz,
    check (estado <> 'publicado' or (publicado_en is not null and vence_en is not null))
);
create index on public.video (id_empresa);
create index on public.video (estado, vence_en);

create table public.video_agente (
    id bigint generated always as identity primary key,
    id_profesora uuid not null references public.profiles(id),
    nombre text not null check (length(btrim(nombre)) between 2 and 80),
    token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
    activo boolean not null default true,
    ultimo_contacto timestamptz,
    creado_en timestamptz not null default now()
);
create index on public.video_agente (id_profesora);

create table public.video_reproduccion (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_video bigint not null references public.video(id),
    id_usuario uuid not null references public.profiles(id),
    bytes_estimados bigint not null check (bytes_estimados >= 0),
    creado_en timestamptz not null default now()
);
create index on public.video_reproduccion (id_video, id_usuario, creado_en);
create index on public.video_reproduccion (creado_en);

alter table public.video enable row level security;
alter table public.video_agente enable row level security;
alter table public.video_reproduccion enable row level security;
revoke all on public.video, public.video_agente, public.video_reproduccion from public, anon, authenticated;

-- Las rutas y la lista de reproducción no se exponen a las sesiones: solo las columnas de abajo.
grant select (id, id_empresa, id_clase, id_profesora, estado, duracion_seg, publicado_en, vence_en)
    on public.video to authenticated;

create policy "video: empresa activa y vigente" on public.video for select to authenticated
    using (
        id_empresa = public.empresa_activa()
        and estado = 'publicado'
        and now() < vence_en
        and (public.es_alumna_activa() or public.es_docente())
    );

-- ---------- integridad ----------
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

create trigger video_coherente before insert or update on public.video
    for each row execute function public._video_coherente();
create trigger zz_proteger_empresa before insert or update or delete on public.video
    for each row execute function public._proteger_empresa();
create trigger zz_proteger_empresa before insert or update or delete on public.video_reproduccion
    for each row execute function public._proteger_empresa();

-- ---------- bucket ----------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('videos-clases', 'videos-clases', false, 52428800, array['video/mp2t'])
on conflict (id) do nothing;

-- ---------- lectura con sesión ----------
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

-- Autoriza una reproducción. Reabrir el mismo video dentro de 90 minutos (lo que dura una URL firmada) no cuenta
-- como una nueva. Topes fijos acá y no como parámetro: esta función la llama la sesión de la usuaria.
-- Devuelve: resultado = ok | no_disponible | limite_usuaria | sin_presupuesto
create or replace function public.video_reproducir(p_id_video bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    c_tope_mensual constant bigint := 4000000000;
    c_inicios_por_usuaria constant integer := 3;
    v_empresa bigint := public.empresa_activa();
    v_usuario uuid := (select auth.uid());
    v public.video;
    v_mes timestamptz := date_trunc('month', now() at time zone 'America/Montevideo') at time zone 'America/Montevideo';
begin
    if v_usuario is null or v_empresa is null or not (public.es_alumna_activa() or public.es_docente()) then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    select * into v from public.video
    where id = p_id_video and id_empresa = v_empresa and estado = 'publicado' and now() < vence_en;
    if not found then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;

    perform pg_advisory_xact_lock(hashtext('video_presupuesto'));
    if not exists (select 1 from public.video_reproduccion r
                   where r.id_video = v.id and r.id_usuario = v_usuario and r.creado_en > now() - interval '90 minutes') then
        if (select count(*) from public.video_reproduccion r
            where r.id_video = v.id and r.id_usuario = v_usuario) >= c_inicios_por_usuaria then
            return jsonb_build_object('resultado', 'limite_usuaria');
        end if;
        if (select coalesce(sum(r.bytes_estimados), 0) from public.video_reproduccion r where r.creado_en >= v_mes)
           + v.tamano_bytes > c_tope_mensual then
            return jsonb_build_object('resultado', 'sin_presupuesto');
        end if;
        insert into public.video_reproduccion (id_empresa, id_video, id_usuario, bytes_estimados)
        values (v.id_empresa, v.id, v_usuario, v.tamano_bytes);
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

revoke execute on function public.videos_disponibles(bigint), public.video_reproducir(bigint) from public, anon;
grant execute on function public.videos_disponibles(bigint), public.video_reproducir(bigint) to authenticated;

-- ---------- programa local y limpieza (solo el servidor, con la clave de administrador) ----------
create or replace function public.video_agente_identificar(p_token_hash text)
returns uuid
language sql
security definer
set search_path = ''
as $$
    update public.video_agente a set ultimo_contacto = now()
    where a.token_hash = p_token_hash and a.activo
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

-- Identifica la clase de esa profesora que empezó hasta 30 minutos antes o después de la grabación. La empresa la
-- define la clase, nunca el programa local. Se puede repetir sin duplicar: una fila por clase y por archivo.
-- Devuelve: resultado = ok | ya_publicado | ya_eliminado | sin_clase | clase_ambigua | huella_repetida | datos_invalidos
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
    v_clases bigint[];
    v_empresa bigint;
    v public.video;
    v_fecha date := (p_inicio at time zone 'America/Montevideo')::date;
begin
    if p_huella !~ '^[0-9a-f]{64}$' or coalesce(p_duracion_seg, 0) <= 0 or coalesce(btrim(p_lista_hls), '') = ''
       or not public._video_partes_validas(p_partes, p_tamano_bytes) then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    select array_agg(c.id) into v_clases
    from public.clase c
    where c.id_profesora = p_id_profesora and c.estado <> 'cancelada'
      and c.fecha between v_fecha - 1 and v_fecha + 1
      and abs(extract(epoch from public._inicio_clase(c.fecha, c.hora_inicio) - p_inicio)) <= 1800;
    if v_clases is null then
        return jsonb_build_object('resultado', 'sin_clase');
    end if;
    if array_length(v_clases, 1) > 1 then
        return jsonb_build_object('resultado', 'clase_ambigua');
    end if;

    perform pg_advisory_xact_lock(hashtext('video_clase'), v_clases[1]::integer);
    if exists (select 1 from public.video x where x.huella_sha256 = p_huella and x.id_clase <> v_clases[1]) then
        return jsonb_build_object('resultado', 'huella_repetida');
    end if;
    select * into v from public.video where id_clase = v_clases[1] for update;
    if found and v.estado in ('publicado', 'eliminado') then
        return jsonb_build_object('resultado', 'ya_' || v.estado, 'id_video', v.id);
    end if;
    if found then
        update public.video set estado = 'subiendo', huella_sha256 = p_huella, duracion_seg = p_duracion_seg,
            tamano_bytes = p_tamano_bytes, partes = p_partes, lista_hls = p_lista_hls, intentos = intentos + 1,
            ultimo_error = null
        where id = v.id returning * into v;
    else
        select c.id_empresa into v_empresa from public.clase c where c.id = v_clases[1];
        insert into public.video (id_empresa, id_clase, id_profesora, huella_sha256, duracion_seg, tamano_bytes,
                                  partes, lista_hls)
        values (v_empresa, v_clases[1], p_id_profesora, p_huella, p_duracion_seg, p_tamano_bytes, p_partes, p_lista_hls)
        returning * into v;
    end if;
    return jsonb_build_object('resultado', 'ok', 'id_video', v.id, 'id_empresa', v.id_empresa, 'partes', v.partes);
end;
$$;

create or replace function public.video_de_agente(p_id_video bigint, p_id_profesora uuid)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object('id', v.id, 'id_empresa', v.id_empresa, 'estado', v.estado, 'partes', v.partes)
    from public.video v
    where v.id = p_id_video and v.id_profesora = p_id_profesora;
$$;

-- publicado_en y vence_en los fija la hora de la base. Repetirla no corre el vencimiento.
-- Devuelve: resultado = ok | no_encontrado | ya_eliminado
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
    if v.estado <> 'publicado' then
        update public.video set estado = 'publicado', publicado_en = now(), vence_en = now() + interval '24 hours',
            ultimo_error = null
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
    update public.video set estado = 'error', ultimo_error = left(coalesce(p_mensaje, ''), 500)
    where id = p_id_video and id_profesora = p_id_profesora and estado = 'subiendo';
$$;

-- Vencidos y subidas abandonadas hace más de 48 h, con sus archivos todavía guardados.
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
             or (v.estado in ('subiendo', 'error') and v.creado_en < now() - interval '48 hours')
          order by v.id
          limit p_limite) x;
$$;

create or replace function public.video_marcar_eliminado(p_id_video bigint)
returns void
language sql
security definer
set search_path = ''
as $$
    update public.video set estado = 'eliminado', archivo_eliminado_en = now()
    where id = p_id_video and estado <> 'eliminado'
      and ((estado = 'publicado' and vence_en <= now()) or estado in ('subiendo', 'error'));
$$;

revoke execute on function
    public.video_agente_identificar(text),
    public._video_partes_validas(jsonb, bigint),
    public.video_registrar(uuid, timestamptz, text, integer, bigint, jsonb, text),
    public.video_de_agente(bigint, uuid),
    public.video_publicar(bigint, uuid),
    public.video_error(bigint, uuid, text),
    public.videos_a_limpiar(integer),
    public.video_marcar_eliminado(bigint)
from public, anon, authenticated;
grant execute on function
    public.video_agente_identificar(text),
    public.video_registrar(uuid, timestamptz, text, integer, bigint, jsonb, text),
    public.video_de_agente(bigint, uuid),
    public.video_publicar(bigint, uuid),
    public.video_error(bigint, uuid, text),
    public.videos_a_limpiar(integer),
    public.video_marcar_eliminado(bigint)
to service_role;
grant select, insert, update on public.video_agente to service_role;
