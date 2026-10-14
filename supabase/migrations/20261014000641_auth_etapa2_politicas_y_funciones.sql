-- Etapa 2 de autenticación, fase 1 (aditiva): políticas para usuarias con sesión
-- y funciones que hacen las escrituras de alumnas. Las políticas abiertas a anon
-- se quitan en la fase 2, cuando todos los deploys usen el código nuevo.

-- ---------- políticas para authenticated ----------

create policy "logueadas: leer horarios" on public.horario
    for select to authenticated using (true);
create policy "profesora: crear horarios" on public.horario
    for insert to authenticated with check (public.es_profesora());
create policy "profesora: editar horarios" on public.horario
    for update to authenticated using (public.es_profesora()) with check (public.es_profesora());
create policy "profesora: borrar horarios" on public.horario
    for delete to authenticated using (public.es_profesora());

create policy "logueadas: leer clases" on public.clase
    for select to authenticated using (true);
create policy "profesora: crear clases" on public.clase
    for insert to authenticated with check (public.es_profesora());
create policy "profesora: editar clases" on public.clase
    for update to authenticated using (public.es_profesora()) with check (public.es_profesora());

-- Las alumnas solo leen lo propio; reservar, cancelar y anotarse en espera van
-- por las funciones de abajo (que validan cupo y plazos dentro de la base).
create policy "reserva: leer propias o profesora" on public.reserva
    for select to authenticated
    using (id_alumna = (select auth.uid()) or public.es_profesora());

create policy "lista de espera: leer propias o profesora" on public.listadeespera
    for select to authenticated
    using (id_alumna = (select auth.uid()) or public.es_profesora());

-- ---------- funciones ----------

-- Inicio de la clase en hora de Montevideo (las horas se guardan locales).
create or replace function public._inicio_clase(p_fecha date, p_hora time)
returns timestamptz
language sql
immutable
set search_path = ''
as $$
    select (p_fecha + p_hora) at time zone 'America/Montevideo';
$$;

create or replace function public._sincronizar_inscriptas(p_id_clase bigint)
returns void
language sql
security definer
set search_path = ''
as $$
    update public.clase
    set cantidad_inscriptas = (
        select count(*) from public.reserva
        where id_clase = p_id_clase and estado = 'confirmada'
    )
    where id = p_id_clase;
$$;

-- Crea (si faltan) las clases de la semana que empieza en p_lunes a partir de los
-- horarios activos. Idempotente. Solo semanas cercanas, para no llenar la tabla.
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
    insert into public.clase (id_horario, fecha, hora_inicio, duracion, precio)
    select h.id,
           p_lunes + (array_position(
               array['lunes','martes','miercoles','jueves','viernes','sabado','domingo'],
               h.dia_semana) - 1),
           h.hora_inicio, h.duracion, h.precio
    from public.horario h
    where h.activo
    on conflict (id_horario, fecha) do nothing;
end;
$$;

-- Cantidad de reservas confirmadas por clase, sin revelar quiénes son.
create or replace function public.reservas_confirmadas(p_ids bigint[])
returns table (id_clase bigint, cantidad integer)
language sql
stable
security definer
set search_path = ''
as $$
    select r.id_clase, count(*)::integer
    from public.reserva r
    where r.id_clase = any(p_ids) and r.estado = 'confirmada'
    group by r.id_clase;
$$;

-- Devuelve: ok | no_disponible | ya_reservada | completa
create or replace function public.reservar(p_id_clase bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_clase record;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    -- El lock serializa reservas simultáneas de la misma clase: no hay sobreventa.
    select c.*, h.cupo_max into v_clase
    from public.clase c join public.horario h on h.id = c.id_horario
    where c.id = p_id_clase
    for update of c;
    if not found or v_clase.estado <> 'programada'
       or public._inicio_clase(v_clase.fecha, v_clase.hora_inicio) <= now() then
        return 'no_disponible';
    end if;
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return 'ya_reservada';
    end if;
    if (select count(*) from public.reserva
        where id_clase = p_id_clase and estado = 'confirmada') >= v_clase.cupo_max then
        return 'completa';
    end if;
    insert into public.reserva (id_alumna, id_clase) values (v_alumna, p_id_clase);
    perform public._sincronizar_inscriptas(p_id_clase);
    update public.listadeespera set estado = 'confirmado'
    where id_alumna = v_alumna and id_clase = p_id_clase and estado in ('en_espera', 'ofrecido');
    return 'ok';
end;
$$;

-- Devuelve: ok | sin_reserva | iniciada | plazo_vencido
-- Se puede cancelar hasta 1 hora antes del inicio (exactamente 1 hora sí).
create or replace function public.cancelar_reserva(p_id_clase bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_inicio timestamptz;
    v_reserva bigint;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    select public._inicio_clase(fecha, hora_inicio) into v_inicio
    from public.clase where id = p_id_clase
    for update;
    select id into v_reserva from public.reserva
    where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada';
    if v_reserva is null then
        return 'sin_reserva';
    end if;
    if now() >= v_inicio then
        return 'iniciada';
    end if;
    if now() > v_inicio - interval '1 hour' then
        return 'plazo_vencido';
    end if;
    update public.reserva set estado = 'cancelada', fecha_cancelacion = now() where id = v_reserva;
    perform public._sincronizar_inscriptas(p_id_clase);
    return 'ok';
end;
$$;

-- Devuelve {"resultado": ok | no_disponible | hay_lugar | ya_reservada | ya_en_espera, "posicion": n}
create or replace function public.anotar_en_espera(p_id_clase bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_clase record;
    v_posicion integer;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    select c.*, h.cupo_max into v_clase
    from public.clase c join public.horario h on h.id = c.id_horario
    where c.id = p_id_clase
    for update of c;
    if not found or v_clase.estado <> 'programada'
       or public._inicio_clase(v_clase.fecha, v_clase.hora_inicio) <= now() then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    if (select count(*) from public.reserva
        where id_clase = p_id_clase and estado = 'confirmada') < v_clase.cupo_max then
        return jsonb_build_object('resultado', 'hay_lugar');
    end if;
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return jsonb_build_object('resultado', 'ya_reservada');
    end if;
    if exists (select 1 from public.listadeespera
               where id_alumna = v_alumna and id_clase = p_id_clase and estado in ('en_espera', 'ofrecido')) then
        return jsonb_build_object('resultado', 'ya_en_espera');
    end if;
    select count(*) + 1 into v_posicion from public.listadeespera
    where id_clase = p_id_clase and estado in ('en_espera', 'ofrecido');
    insert into public.listadeespera (id_alumna, id_clase, posicion)
    values (v_alumna, p_id_clase, v_posicion);
    return jsonb_build_object('resultado', 'ok', 'posicion', v_posicion);
end;
$$;

revoke execute on function public._sincronizar_inscriptas(bigint) from public, anon, authenticated;
revoke execute on function public.generar_clases(date) from public, anon;
revoke execute on function public.reservas_confirmadas(bigint[]) from public, anon;
revoke execute on function public.reservar(bigint) from public, anon;
revoke execute on function public.cancelar_reserva(bigint) from public, anon;
revoke execute on function public.anotar_en_espera(bigint) from public, anon;
grant execute on function public.generar_clases(date) to authenticated;
grant execute on function public.reservas_confirmadas(bigint[]) to authenticated;
grant execute on function public.reservar(bigint) to authenticated;
grant execute on function public.cancelar_reserva(bigint) to authenticated;
grant execute on function public.anotar_en_espera(bigint) to authenticated;
