-- «Eliminar alumna» (distinto de «Desactivar», que no cambia): la administradora quita a una alumna de su empresa para
-- que, si vuelve, se registre como una alumna NUEVA (cuenta nueva, invitación original de Supabase, historial vacío).
-- - La cuenta anterior NO se borra: sus reservas, asistencias, facturas y pagos la referencian con claves que impiden
--   borrarla (integridad y facturación). Queda archivada con su historial; el servidor después le cambia el correo en
--   Supabase Auth por uno interno (.invalid) y la bloquea, y así el correo original queda libre para una cuenta nueva.
-- - eliminar_alumna (todo o nada, con la misma vista previa y «firma» que la baja de docentes): cancela sus reservas de
--   clases que todavía no empezaron (sin cobro; el lugar pasa a la lista de espera con la función de siempre), la retira
--   de las listas de espera, desactiva y oculta su membresía (membresia.eliminada_en), pasa su cédula a la auditoría y
--   registra todo en alumna_eliminada.
-- - Se niega si la alumna pertenece a otra empresa (liberar su correo le cortaría el acceso allá): en ese caso se usa
--   «Desactivar».
-- - alumna_eliminada_correo_liberado: la llama el servidor (service_role) cuando el archivado en Auth terminó; mientras
--   no se llame, la pantalla muestra la eliminación como pendiente y ofrece reintentar.
-- No cambia datos existentes ni ninguna función existente.

alter table public.membresia add column eliminada_en timestamptz;
-- membresia da permiso de lectura columna por columna (la cédula no se lee directo): solo esta columna nueva
grant select (eliminada_en) on public.membresia to authenticated;

create table public.alumna_eliminada (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_usuario uuid not null references public.profiles(id),
    mail_original text not null,
    nombre text not null,
    apellido text not null,
    cedula text,
    motivo text not null check (length(btrim(motivo)) between 1 and 300),
    por uuid references public.profiles(id),
    eliminada_en timestamptz not null default now(),
    reservas_canceladas integer not null default 0,
    esperas_retiradas integer not null default 0,
    facturas_pendientes integer not null default 0,
    correo_liberado_en timestamptz
);
create index on public.alumna_eliminada (id_empresa, eliminada_en desc);
create index on public.alumna_eliminada (id_usuario);

alter table public.alumna_eliminada enable row level security;
revoke all on public.alumna_eliminada from public, anon, authenticated;
grant select on public.alumna_eliminada to authenticated;
create policy "alumna_eliminada: administradora de la empresa" on public.alumna_eliminada for select to authenticated
    using (id_empresa = public.empresa_activa() and public.es_admin());

-- ---------- vista previa ----------

create or replace function public._resumen_eliminar_alumna(p_usuario uuid, p_empresa bigint)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    with futuras as (
        select r.id, r.id_clase, c.fecha, c.hora_inicio, t.nombre as tipo
        from public.reserva r
        join public.clase c on c.id = r.id_clase
        left join public.tipo_clase t on t.id = c.id_tipo_clase
        where r.id_alumna = p_usuario and r.id_empresa = p_empresa and r.estado = 'confirmada'
          and c.estado = 'programada' and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    ),
    espera as (
        select l.id, l.id_clase, l.estado, c.fecha, c.hora_inicio, t.nombre as tipo
        from public.listadeespera l
        join public.clase c on c.id = l.id_clase
        left join public.tipo_clase t on t.id = c.id_tipo_clase
        where l.id_alumna = p_usuario and l.id_empresa = p_empresa and l.estado in ('en_espera', 'ofrecido')
    ),
    pendientes as (
        select f.id, f.numero, f.mes, f.anio, f.total
        from public.resumen_mensual f
        where f.id_alumna = p_usuario and f.id_empresa = p_empresa and f.estado_factura = 'confirmada'
          and f.estado_pago = 'pendiente'
    )
    select jsonb_build_object(
        'reservas', coalesce((select jsonb_agg(jsonb_build_object('fecha', fecha, 'hora_inicio', to_char(hora_inicio, 'HH24:MI'),
                              'tipo', tipo) order by fecha, hora_inicio) from futuras), '[]'),
        'espera', coalesce((select jsonb_agg(jsonb_build_object('fecha', fecha, 'hora_inicio', to_char(hora_inicio, 'HH24:MI'),
                            'tipo', tipo, 'estado', estado) order by fecha, hora_inicio) from espera), '[]'),
        'facturas_pendientes', coalesce((select jsonb_agg(jsonb_build_object('numero', numero, 'mes', mes, 'anio', anio,
                                         'total', total) order by anio, mes) from pendientes), '[]'),
        'historial', jsonb_build_object(
            'reservas', (select count(*) from public.reserva where id_alumna = p_usuario and id_empresa = p_empresa),
            'asistencias', (select count(*) from public.asistencia a join public.reserva r on r.id = a.id_reserva
                            where r.id_alumna = p_usuario and r.id_empresa = p_empresa),
            'facturas', (select count(*) from public.resumen_mensual where id_alumna = p_usuario and id_empresa = p_empresa
                         and estado_factura <> 'borrador'),
            'pagos', (select count(*) from public.pago p join public.resumen_mensual f on f.id = p.id_resumen_mensual
                      where f.id_alumna = p_usuario and f.id_empresa = p_empresa)),
        'firma', md5(concat_ws('|',
            (select string_agg(id::text, ',' order by id) from futuras),
            (select string_agg(id::text || estado, ',' order by id) from espera),
            (select string_agg(id::text, ',' order by id) from pendientes)))
    );
$$;

create or replace function public._validar_eliminar_alumna(p_id_usuario uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select case
        when not public.es_admin() then 'no_autorizada'
        when p_id_usuario = auth.uid() then 'no_puede_eliminarse'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa()
                         and eliminada_en is null) then 'no_existe'
        when not exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa()
                         and es_alumna and not es_admin and not es_profesora) then 'no_es_alumna'
        when exists (select 1 from public.profiles where id = p_id_usuario and es_superadmin) then 'no_es_alumna'
        -- liberar su correo le cortaría el acceso en la otra empresa: ahí se usa «Desactivar»
        when exists (select 1 from public.membresia where id_usuario = p_id_usuario and id_empresa <> public.empresa_activa()
                     and eliminada_en is null) then 'otra_empresa'
        else 'ok'
    end;
$$;

create or replace function public.previsualizar_eliminar_alumna(p_id_usuario uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_estado text := public._validar_eliminar_alumna(p_id_usuario);
begin
    if v_estado <> 'ok' then
        return jsonb_build_object('resultado', v_estado);
    end if;
    return jsonb_build_object('resultado', 'ok') || public._resumen_eliminar_alumna(p_id_usuario, public.empresa_activa());
end;
$$;

-- ---------- eliminar ----------

create or replace function public.eliminar_alumna(p_id_usuario uuid, p_motivo text, p_firma text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_estado text;
    v_clases bigint[];
    v_clase bigint;
    v_reservas integer;
    v_espera integer;
    v_pendientes integer;
    v_p public.profiles;
    v_cedula text;
    v_registro bigint;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(btrim(p_motivo), '') = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;
    perform 1 from public.membresia where id_usuario = p_id_usuario order by id_empresa for update;
    v_estado := public._validar_eliminar_alumna(p_id_usuario);
    if v_estado <> 'ok' then
        return jsonb_build_object('resultado', v_estado);
    end if;

    -- mismo orden de bloqueo que reservar y la lista de espera: primero las clases afectadas
    select coalesce(array_agg(distinct x.id_clase), '{}') into v_clases from (
        select r.id_clase from public.reserva r join public.clase c on c.id = r.id_clase
        where r.id_alumna = p_id_usuario and r.id_empresa = v_empresa and r.estado = 'confirmada'
          and c.estado = 'programada' and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        union
        select l.id_clase from public.listadeespera l
        where l.id_alumna = p_id_usuario and l.id_empresa = v_empresa and l.estado in ('en_espera', 'ofrecido')) x;
    perform 1 from public.clase where id = any(v_clases) order by id for update;

    if public._resumen_eliminar_alumna(p_id_usuario, v_empresa) ->> 'firma' is distinct from p_firma then
        return jsonb_build_object('resultado', 'cambio');
    end if;

    with r as (
        update public.reserva r set estado = 'cancelada', fecha_cancelacion = now()
        from public.clase c
        where c.id = r.id_clase and r.id_alumna = p_id_usuario and r.id_empresa = v_empresa and r.estado = 'confirmada'
          and c.estado = 'programada' and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        returning 1
    )
    select count(*) into v_reservas from r;

    with e as (
        update public.listadeespera set estado = 'retirado'
        where id_alumna = p_id_usuario and id_empresa = v_empresa and estado in ('en_espera', 'ofrecido')
        returning 1
    )
    select count(*) into v_espera from e;

    -- cupos al día y los lugares liberados pasan a la siguiente de la lista (misma función que una cancelación)
    foreach v_clase in array v_clases loop
        perform public._sincronizar_inscriptas(v_clase);
        perform public._procesar_lista_espera(v_clase);
    end loop;

    select count(*) into v_pendientes from public.resumen_mensual
    where id_alumna = p_id_usuario and id_empresa = v_empresa and estado_factura = 'confirmada' and estado_pago = 'pendiente';

    select * into v_p from public.profiles where id = p_id_usuario;
    select cedula into v_cedula from public.membresia where id_usuario = p_id_usuario and id_empresa = v_empresa;

    update public.membresia set activa = false, eliminada_en = now(), cedula = null
    where id_usuario = p_id_usuario and id_empresa = v_empresa;

    insert into public.alumna_eliminada (id_empresa, id_usuario, mail_original, nombre, apellido, cedula, motivo, por,
                                         reservas_canceladas, esperas_retiradas, facturas_pendientes)
    values (v_empresa, p_id_usuario, v_p.mail, v_p.nombre, v_p.apellido, v_cedula, btrim(p_motivo), auth.uid(),
            v_reservas, v_espera, v_pendientes)
    returning id into v_registro;

    return jsonb_build_object('resultado', 'ok', 'registro', v_registro, 'reservas', v_reservas, 'espera', v_espera,
                              'facturas_pendientes', v_pendientes);
end;
$$;

-- El servidor la llama cuando el correo de la cuenta archivada ya no es el original (archivado en Auth terminado).
create or replace function public.alumna_eliminada_correo_liberado(p_id bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.alumna_eliminada;
begin
    select * into v from public.alumna_eliminada where id = p_id for update;
    if not found then
        return 'no_existe';
    end if;
    if exists (select 1 from public.profiles where id = v.id_usuario and lower(mail) = lower(v.mail_original)) then
        return 'correo_sin_liberar';
    end if;
    update public.alumna_eliminada set correo_liberado_en = coalesce(correo_liberado_en, now()) where id = p_id;
    return 'ok';
end;
$$;

revoke execute on function public._resumen_eliminar_alumna(uuid, bigint), public._validar_eliminar_alumna(uuid),
    public.previsualizar_eliminar_alumna(uuid), public.eliminar_alumna(uuid, text, text),
    public.alumna_eliminada_correo_liberado(bigint) from public, anon, authenticated;
grant execute on function public.previsualizar_eliminar_alumna(uuid), public.eliminar_alumna(uuid, text, text)
    to authenticated;
grant execute on function public.alumna_eliminada_correo_liberado(bigint) to service_role;
