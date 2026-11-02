-- Baja de docentes (profesoras y administradoras) con cancelación automática de lo futuro.
-- - Una sola operación atómica: deja inactiva la membresía, desactiva sus horarios, cancela sus clases que todavía
--   no empezaron (con sus reservas y listas de espera) y lo registra como un bloqueo de tipo 'baja' + auditoría.
-- - Lo pasado no se toca: clases empezadas o en curso, asistencias, facturas y pagos.
-- - Vista previa obligatoria con una «firma»: si algo cambió desde la vista previa, no se hace nada.
-- - Profesoras: las da de baja la administradora de su empresa. Administradoras: solo la superadministradora, y
--   nunca la última administradora activa de una empresa.
-- - Cierra los caminos que esquivaban las protecciones: dejar inactiva sin revisar, asignar o activar horarios de
--   una docente inactiva, generar clases para ella y restaurar sus clases quitando otro bloqueo.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_baja_docentes.sql

-- ---------- estructura ----------
alter table public.bloqueo drop constraint bloqueo_tipo_check;
alter table public.bloqueo add constraint bloqueo_tipo_check check (tipo in ('licencia', 'puntual', 'baja'));

create table public.baja_docente (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_usuario uuid not null references public.profiles(id),
    por uuid not null references public.profiles(id),
    era_administradora boolean not null,
    motivo text not null check (btrim(motivo) <> ''),
    id_bloqueo bigint,
    horarios bigint[] not null default '{}',
    clases integer not null default 0,
    reservas integer not null default 0,
    espera integer not null default 0,
    creada_en timestamptz not null default now()
);

alter table public.baja_docente enable row level security;
revoke all on public.baja_docente from anon, authenticated;
grant select on public.baja_docente to authenticated;
create policy "baja_docente: administradora de la empresa o superadministradora" on public.baja_docente
    for select to authenticated
    using (public.es_superadmin() or (id_empresa = public.empresa_activa() and public.es_admin()));

-- ---------- docente activa ----------
create or replace function public._docente_activa(p_usuario uuid, p_empresa bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (select 1 from public.membresia m
                   where m.id_usuario = p_usuario and m.id_empresa = p_empresa and m.activa
                     and (m.es_profesora or m.es_admin));
$$;

-- Un horario activo solo puede ser de una docente activa de su empresa. El bloqueo compartido sobre la membresía
-- espera a una baja en curso (que la bloquea para modificarla): no hay carrera entre las dos.
create or replace function public._validar_docente_horario()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not new.activo or new.id_profesora is null then
        return new;
    end if;
    if tg_op = 'UPDATE' and old.activo and new.id_profesora is not distinct from old.id_profesora then
        return new;
    end if;
    perform 1 from public.membresia m
    where m.id_usuario = new.id_profesora and m.id_empresa = new.id_empresa and m.activa and (m.es_profesora or m.es_admin)
    for share;
    if not found then
        raise exception 'profesora_inactiva' using errcode = 'P0001',
            hint = 'El horario es de una profesora inactiva o que no es docente de la empresa.';
    end if;
    return new;
end;
$$;

create trigger validar_docente before insert or update of activo, id_profesora on public.horario
    for each row execute function public._validar_docente_horario();

-- Una clase programada solo puede ser de una docente activa. Al generarla nace cancelada (así una generación que
-- coincide con una baja no falla); volver a programarla a mano se rechaza.
create or replace function public._validar_docente_clase()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.estado <> 'programada' or new.id_profesora is null then
        return new;
    end if;
    if tg_op = 'UPDATE' and old.estado = 'programada' and new.id_profesora is not distinct from old.id_profesora then
        return new;
    end if;
    perform 1 from public.membresia m
    where m.id_usuario = new.id_profesora and m.id_empresa = new.id_empresa and m.activa and (m.es_profesora or m.es_admin)
    for share;
    if found then
        return new;
    end if;
    if tg_op = 'INSERT' then
        new.estado := 'cancelada';
        return new;
    end if;
    raise exception 'profesora_inactiva' using errcode = 'P0001',
        hint = 'La clase es de una profesora inactiva.';
end;
$$;

create trigger validar_docente before insert or update of estado, id_profesora on public.clase
    for each row execute function public._validar_docente_clase();

-- ---------- la superadministradora puede operar sobre una empresa solo dentro de una baja ----------
create or replace function public._proteger_empresa()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint;
begin
    if (select auth.uid()) is null then
        return coalesce(new, old);
    end if;
    -- dar_de_baja_administradora marca la empresa para esta transacción (solo vale para la superadministradora)
    if coalesce(current_setting('jsu.baja_empresa', true), '') <> '' then
        if current_setting('jsu.baja_empresa', true) = coalesce(new.id_empresa, old.id_empresa)::text
           and (tg_op <> 'UPDATE' or old.id_empresa = new.id_empresa) and public.es_superadmin() then
            return coalesce(new, old);
        end if;
    end if;
    v_empresa := public.empresa_activa();
    if tg_op in ('UPDATE', 'DELETE') and (v_empresa is null or old.id_empresa is distinct from v_empresa) then
        raise exception 'No se puede modificar información de otra empresa' using errcode = '42501';
    end if;
    if tg_op in ('INSERT', 'UPDATE') and (v_empresa is null or new.id_empresa is distinct from v_empresa) then
        raise exception 'No se puede modificar información de otra empresa' using errcode = '42501';
    end if;
    return coalesce(new, old);
end;
$$;

-- ---------- generación y bloqueos ----------
-- Una baja cubre las clases de esa docente mientras siga inactiva (si se la reactiva, sus horarios vuelven a
-- generar clases normalmente).
create or replace function public._clase_bloqueada(p_fecha date, p_id_horario bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.bloqueo b
        join public.horario h on h.id = p_id_horario
        where p_fecha between b.fecha_desde and b.fecha_hasta
          and b.id_empresa = h.id_empresa
          and (b.id_horario = p_id_horario
               or (b.id_horario is null and (b.id_profesora is null or b.id_profesora = h.id_profesora)))
          and (b.tipo <> 'baja' or not public._docente_activa(b.id_profesora, b.id_empresa))
    );
$$;

create or replace function public.generar_clases(p_lunes date)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
begin
    if auth.uid() is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    if extract(isodow from p_lunes) <> 1 or p_lunes < v_hoy - 7 or p_lunes > v_hoy + 28 then
        return;
    end if;
    insert into public.clase (id_horario, fecha, hora_inicio, duracion, precio, cupo_max, id_tipo_clase, estado)
    select h.id, d.fecha, h.hora_inicio, t.duracion, t.precio_presencial, t.cupo_max, t.id,
           case when public._clase_bloqueada(d.fecha, h.id) then 'cancelada' else 'programada' end
    from public.horario h
    join public.tipo_clase t on t.id = h.id_tipo_clase
    cross join lateral (
        select p_lunes + (array_position(
            array['lunes','martes','miercoles','jueves','viernes','sabado','domingo'],
            h.dia_semana) - 1) as fecha
    ) d
    where h.activo and t.activo and h.id_empresa = public.empresa_activa()
      and (h.id_profesora is null or public._docente_activa(h.id_profesora, h.id_empresa))
    on conflict (id_horario, fecha) do nothing;
end;
$$;

-- ---------- qué se cancela (vista previa y confirmación usan lo mismo) ----------
create or replace function public._resumen_baja(p_usuario uuid, p_empresa bigint, p_con_nombres boolean)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    with horarios as (
        select h.id, h.dia_semana, h.hora_inicio, t.nombre as tipo
        from public.horario h join public.tipo_clase t on t.id = h.id_tipo_clase
        where h.id_profesora = p_usuario and h.id_empresa = p_empresa and h.activo
    ),
    clases as (
        select c.id, c.fecha, c.hora_inicio, t.nombre as tipo
        from public.clase c join public.tipo_clase t on t.id = c.id_tipo_clase
        where c.id_profesora = p_usuario and c.id_empresa = p_empresa and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    ),
    reservas as (
        select r.id, r.id_clase, r.id_alumna from public.reserva r
        where r.id_clase in (select id from clases) and r.estado = 'confirmada'
    ),
    espera as (
        select l.id, l.id_clase from public.listadeespera l
        where l.id_clase in (select id from clases) and l.estado in ('en_espera', 'ofrecido')
    ),
    en_curso as (
        select count(*) as n from public.clase c
        where c.id_profesora = p_usuario and c.id_empresa = p_empresa and c.estado = 'programada'
          and now() >= public._inicio_clase(c.fecha, c.hora_inicio)
          and now() < public._inicio_clase(c.fecha, c.hora_inicio) + make_interval(mins => c.duracion)
    )
    select jsonb_build_object(
        'horarios', coalesce((select jsonb_agg(jsonb_build_object('id', id, 'dia_semana', dia_semana,
                              'hora_inicio', to_char(hora_inicio, 'HH24:MI'), 'tipo', tipo) order by id) from horarios), '[]'),
        'clases', coalesce((select jsonb_agg(jsonb_build_object('id', c.id, 'fecha', c.fecha,
                              'hora_inicio', to_char(c.hora_inicio, 'HH24:MI'), 'tipo', c.tipo,
                              'reservas', (select count(*) from reservas r where r.id_clase = c.id),
                              'espera', (select count(*) from espera e where e.id_clase = c.id))
                              order by c.fecha, c.hora_inicio, c.id) from clases c), '[]'),
        'reservas', (select count(*) from reservas),
        'alumnas', (select count(distinct id_alumna) from reservas),
        'nombres', case when p_con_nombres then coalesce((select jsonb_agg(n order by n) from (
                        select distinct btrim(p.nombre || ' ' || p.apellido) as n
                        from reservas r join public.profiles p on p.id = r.id_alumna) x), '[]') else '[]' end,
        'espera', (select count(*) from espera),
        'en_curso', (select n from en_curso),
        'firma', md5(concat_ws('|',
            (select string_agg(id::text, ',' order by id) from horarios),
            (select string_agg(id::text, ',' order by id) from clases),
            (select string_agg(id::text, ',' order by id) from reservas),
            (select string_agg(id::text, ',' order by id) from espera)))
    );
$$;

-- Hace la baja. La membresía de la docente ya está bloqueada por quien llama.
create or replace function public._ejecutar_baja(p_usuario uuid, p_empresa bigint, p_motivo text, p_firma text,
                                                 p_quitar_admin boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := public._hoy_mvd();
    v_horarios bigint[];
    v_clases bigint[];
    v_reservas integer;
    v_espera integer;
    v_bloqueo bigint;
begin
    -- mismo orden de bloqueo que reservar y crear_bloqueo: primero las clases
    perform 1 from public.horario
    where id_profesora = p_usuario and id_empresa = p_empresa and activo order by id for update;
    perform 1 from public.clase c
    where c.id_profesora = p_usuario and c.id_empresa = p_empresa and c.estado = 'programada'
      and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    order by c.id for update;

    if public._resumen_baja(p_usuario, p_empresa, false) ->> 'firma' is distinct from p_firma then
        return jsonb_build_object('resultado', 'cambio');
    end if;

    with h as (
        update public.horario set activo = false
        where id_profesora = p_usuario and id_empresa = p_empresa and activo
        returning id
    )
    select coalesce(array_agg(id order by id), '{}') into v_horarios from h;

    -- el bloqueo marca sus reservas canceladas y cubre sus clases futuras (también las que ya canceló una licencia):
    -- si no tiene ninguna clase futura, no hace falta (y no aparece una desactivación vacía en Planificación)
    if exists (select 1 from public.clase where id_profesora = p_usuario and id_empresa = p_empresa and fecha >= v_hoy) then
        insert into public.bloqueo (tipo, fecha_desde, fecha_hasta, id_horario, motivo, id_profesora, id_empresa)
        values ('baja', v_hoy,
                greatest(v_hoy + 60, coalesce((select max(fecha) from public.clase
                                               where id_profesora = p_usuario and id_empresa = p_empresa), v_hoy)),
                null, btrim(p_motivo), p_usuario, p_empresa)
        returning id into v_bloqueo;
    end if;

    -- primero la membresía: desde acá ningún horario ni clase de ella puede volver a quedar activo
    update public.membresia
    set activa = false,
        es_admin = case when p_quitar_admin then false else es_admin end,
        es_profesora = case when p_quitar_admin then true else es_profesora end
    where id_usuario = p_usuario and id_empresa = p_empresa;

    with canceladas as (
        update public.clase c set estado = 'cancelada', cantidad_inscriptas = 0
        where c.id_profesora = p_usuario and c.id_empresa = p_empresa and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from canceladas;

    with r as (
        update public.reserva set estado = 'cancelada', fecha_cancelacion = now(), id_bloqueo = v_bloqueo
        where id_clase = any(v_clases) and estado = 'confirmada'
        returning 1
    )
    select count(*) into v_reservas from r;

    with e as (
        update public.listadeespera set estado = 'expirado'
        where id_clase = any(v_clases) and estado in ('en_espera', 'ofrecido')
        returning 1
    )
    select count(*) into v_espera from e;

    insert into public.baja_docente (id_empresa, id_usuario, por, era_administradora, motivo, id_bloqueo, horarios,
                                     clases, reservas, espera)
    values (p_empresa, p_usuario, auth.uid(), p_quitar_admin, btrim(p_motivo), v_bloqueo, v_horarios,
            coalesce(array_length(v_clases, 1), 0), v_reservas, v_espera);

    return jsonb_build_object('resultado', 'ok', 'horarios', coalesce(array_length(v_horarios, 1), 0),
                              'clases', coalesce(array_length(v_clases, 1), 0), 'reservas', v_reservas, 'espera', v_espera);
end;
$$;

-- ---------- profesoras: la administradora de su empresa ----------
create or replace function public._validar_baja_docente(p_id_usuario uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select case
        when not public.es_admin() then 'no_autorizada'
        when p_id_usuario = auth.uid() then 'no_puede_desactivarse'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa())
            then 'no_existe'
        when exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa()
                     and es_admin) then 'es_administradora'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa()
                         and es_profesora) then 'no_es_profesora'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa()
                         and activa) then 'ya_inactiva'
        else 'ok'
    end;
$$;

create or replace function public.previsualizar_baja_docente(p_id_usuario uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_estado text := public._validar_baja_docente(p_id_usuario);
begin
    if v_estado <> 'ok' then
        return jsonb_build_object('resultado', v_estado);
    end if;
    return jsonb_build_object('resultado', 'ok') || public._resumen_baja(p_id_usuario, public.empresa_activa(), true);
end;
$$;

create or replace function public.dar_de_baja_docente(p_id_usuario uuid, p_motivo text, p_firma text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_estado text;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(btrim(p_motivo), '') = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;
    perform 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa() for update;
    v_estado := public._validar_baja_docente(p_id_usuario);
    if v_estado <> 'ok' then
        return jsonb_build_object('resultado', v_estado);
    end if;
    return public._ejecutar_baja(p_id_usuario, public.empresa_activa(), p_motivo, p_firma, false);
end;
$$;

-- ---------- administradoras: solo la superadministradora ----------
create or replace function public._validar_baja_administradora(p_id_empresa bigint, p_id_usuario uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select case
        when not public.es_superadmin() then 'no_autorizada'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = p_id_empresa
                         and es_admin) then 'no_es_administradora'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = p_id_empresa
                         and activa) then 'ya_inactiva'
        when not exists (select 1 from public.membresia where id_empresa = p_id_empresa and es_admin and activa
                         and id_usuario <> p_id_usuario) then 'ultima_administradora'
        else 'ok'
    end;
$$;

create or replace function public.previsualizar_baja_administradora(p_id_empresa bigint, p_id_usuario uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_estado text := public._validar_baja_administradora(p_id_empresa, p_id_usuario);
begin
    if v_estado <> 'ok' then
        return jsonb_build_object('resultado', v_estado);
    end if;
    -- la superadministradora ve cantidades, no los nombres de las alumnas de la empresa
    return jsonb_build_object('resultado', 'ok') || public._resumen_baja(p_id_usuario, p_id_empresa, false);
end;
$$;

create or replace function public.dar_de_baja_administradora(p_id_empresa bigint, p_id_usuario uuid, p_motivo text,
                                                             p_firma text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_estado text;
    v_resultado jsonb;
begin
    if not public.es_superadmin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(btrim(p_motivo), '') = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;
    -- las administradoras de la empresa en orden fijo (como quitar_administradora): dos bajas simultáneas no
    -- pueden dejar la empresa sin administradora activa
    perform 1 from public.membresia where id_empresa = p_id_empresa and es_admin order by id for update;
    v_estado := public._validar_baja_administradora(p_id_empresa, p_id_usuario);
    if v_estado <> 'ok' then
        return jsonb_build_object('resultado', v_estado);
    end if;
    perform set_config('jsu.baja_empresa', p_id_empresa::text, true);
    v_resultado := public._ejecutar_baja(p_id_usuario, p_id_empresa, p_motivo, p_firma, true);
    perform set_config('jsu.baja_empresa', '', true);
    return v_resultado;
end;
$$;

-- ---------- caminos viejos ----------
-- Dejar inactiva a una profesora con horarios activos o clases futuras ya no se puede sin la baja (vista previa).
create or replace function public.cambiar_estado_membresia(p_id_usuario uuid, p_activa boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.membresia;
    v_pendientes text[];
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if p_id_usuario = auth.uid() then
        return 'no_puede_desactivarse';
    end if;
    select * into v from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa() for update;
    if not found then
        return 'no_existe';
    end if;
    if v.es_admin then
        return 'es_administradora';
    end if;
    if not coalesce(p_activa, false) and v.activa and v.es_profesora then
        v_pendientes := public._pendientes_docente(p_id_usuario, v.id_empresa);
        if cardinality(v_pendientes) > 0 then
            return 'pendientes:' || array_to_string(v_pendientes, ',');
        end if;
    end if;
    update public.membresia set activa = coalesce(p_activa, false) where id = v.id;
    return 'ok';
end;
$$;

-- Las bajas no se eliminan desde Planificación (las clases de una docente inactiva no se reabren).
create or replace function public.quitar_bloqueo(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_b public.bloqueo;
    v_clases bigint[];
    v_recuperadas integer;
    v_sin_cupo integer;
begin
    if not public.es_docente() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_b from public.bloqueo where id = p_id and id_empresa = public.empresa_activa() for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if v_b.tipo = 'baja' then
        return jsonb_build_object('resultado', 'es_baja');
    end if;
    -- (con "is not distinct from" y coalesce: una licencia sin profesora asignada no es de nadie)
    if not public.es_admin()
       and not (v_b.id_profesora is not distinct from auth.uid() and v_b.id_profesora is not null)
       and not coalesce(v_b.id_horario is not null and public.puede_gestionar_horario(v_b.id_horario), false) then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if v_b.fecha_hasta < v_hoy then
        return jsonb_build_object('resultado', 'finalizado');
    end if;
    delete from public.bloqueo where id = p_id;

    perform 1 from public.clase c
    where c.estado = 'cancelada'
      and c.id_empresa = v_b.id_empresa
      and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
      and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
      and (v_b.id_profesora is null or c.id_profesora = v_b.id_profesora)
      and public._inicio_clase(c.fecha, c.hora_inicio) > now()
      and not public._clase_bloqueada(c.fecha, c.id_horario)
      and (c.id_profesora is null or public._docente_activa(c.id_profesora, c.id_empresa))
    order by c.id
    for update;

    with r as (
        update public.clase c set estado = 'programada'
        where c.estado = 'cancelada'
          and c.id_empresa = v_b.id_empresa
          and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
          and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
          and (v_b.id_profesora is null or c.id_profesora = v_b.id_profesora)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
          and not public._clase_bloqueada(c.fecha, c.id_horario)
          and (c.id_profesora is null or public._docente_activa(c.id_profesora, c.id_empresa))
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from r;

    with candidatas as (
        select r.id, r.id_clase,
               row_number() over (partition by r.id_clase order by r.fecha_reserva, r.id) as orden,
               row_number() over (partition by r.id_alumna, r.id_clase order by r.fecha_reserva desc, r.id desc) as unica,
               c.cupo_max - (select count(*) from public.reserva x
                             where x.id_clase = r.id_clase and x.estado = 'confirmada') as libres
        from public.reserva r
        join public.clase c on c.id = r.id_clase
        where r.id_bloqueo = p_id and r.estado = 'cancelada' and r.id_clase = any(v_clases)
          and not exists (select 1 from public.reserva y
                          where y.id_alumna = r.id_alumna and y.id_clase = r.id_clase
                            and y.estado = 'confirmada')
    ),
    elegidas as (
        select id, id_clase, row_number() over (partition by id_clase order by orden) as n, libres
        from candidatas where unica = 1
    ),
    ok as (
        update public.reserva r
        set estado = 'confirmada', fecha_cancelacion = null, id_bloqueo = null
        from elegidas e
        where e.id = r.id and e.n <= e.libres
        returning 1
    )
    select count(*) into v_recuperadas from ok;

    select count(*) into v_sin_cupo from public.reserva
    where id_bloqueo = p_id and estado = 'cancelada' and id_clase = any(v_clases);

    update public.reserva r
    set id_bloqueo = (
        select b.id from public.bloqueo b join public.clase c on c.id = r.id_clase
        join public.horario h on h.id = c.id_horario
        where c.fecha between b.fecha_desde and b.fecha_hasta
          and b.id_empresa = c.id_empresa
          and (b.id_horario = c.id_horario
               or (b.id_horario is null and (b.id_profesora is null or b.id_profesora = h.id_profesora)))
        order by b.id limit 1)
    where r.id_bloqueo = p_id and r.estado = 'cancelada'
      and exists (select 1 from public.clase c
                  where c.id = r.id_clase and public._clase_bloqueada(c.fecha, c.id_horario));

    update public.clase c
    set cantidad_inscriptas = (select count(*) from public.reserva x
                               where x.id_clase = c.id and x.estado = 'confirmada')
    where c.id = any(v_clases);

    return jsonb_build_object('resultado', 'ok', 'clases', coalesce(array_length(v_clases, 1), 0),
                              'reservas', v_recuperadas, 'sin_cupo', v_sin_cupo);
end;
$$;

-- ---------- permisos ----------
revoke execute on function public._docente_activa(uuid, bigint), public._resumen_baja(uuid, bigint, boolean),
    public._ejecutar_baja(uuid, bigint, text, text, boolean), public._validar_baja_docente(uuid),
    public._validar_baja_administradora(bigint, uuid), public._validar_docente_horario(),
    public._validar_docente_clase() from public, anon, authenticated;
revoke execute on function public.previsualizar_baja_docente(uuid), public.dar_de_baja_docente(uuid, text, text),
    public.previsualizar_baja_administradora(bigint, uuid), public.dar_de_baja_administradora(bigint, uuid, text, text)
    from public, anon;
grant execute on function public.previsualizar_baja_docente(uuid), public.dar_de_baja_docente(uuid, text, text),
    public.previsualizar_baja_administradora(bigint, uuid), public.dar_de_baja_administradora(bigint, uuid, text, text)
    to authenticated;
