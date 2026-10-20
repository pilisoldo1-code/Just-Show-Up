-- Módulo 4: lista de espera completa.
-- Cuando se libera un lugar se le ofrece a la primera de la lista, que tiene 30 minutos
-- (o hasta el inicio de la clase) para confirmar; si no, pasa a la siguiente. Mientras la
-- oferta está vigente, el lugar queda guardado para ella. La alumna puede rechazar la
-- oferta o bajarse de la lista ('retirado'). Todo se procesa al consultar o actuar (no hay
-- tareas programadas) y siempre con la clase bloqueada, como reservar.
-- Compatible con el código anterior: las funciones existentes mantienen nombre,
-- parámetros y códigos de respuesta. No modifica filas existentes.

alter table public.listadeespera drop constraint listadeespera_estado_check;
alter table public.listadeespera add constraint listadeespera_estado_check
    check (estado in ('en_espera', 'ofrecido', 'confirmado', 'expirado', 'retirado'));

-- Vence ofertas pasadas y ofrece cada lugar libre a la siguiente de la lista.
-- No toca clases canceladas ni clases que ya empezaron.
create or replace function public._procesar_lista_espera(p_id_clase bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_c record;
    v_inicio timestamptz;
    v_libres integer;
    v_sig bigint;
begin
    select c.estado, c.fecha, c.hora_inicio, h.cupo_max into v_c
    from public.clase c join public.horario h on h.id = c.id_horario
    where c.id = p_id_clase
    for update of c;
    if not found or v_c.estado <> 'programada' then
        return;
    end if;
    v_inicio := public._inicio_clase(v_c.fecha, v_c.hora_inicio);
    if v_inicio <= now() then
        return;
    end if;

    update public.listadeespera set estado = 'expirado'
    where id_clase = p_id_clase and estado = 'ofrecido' and oferta_vence_en <= now();

    v_libres := v_c.cupo_max
        - (select count(*) from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
        - (select count(*) from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido');
    while v_libres > 0 loop
        v_sig := null;
        select id into v_sig from public.listadeespera
        where id_clase = p_id_clase and estado = 'en_espera'
        order by posicion, fecha_ingreso, id
        limit 1;
        exit when v_sig is null;
        update public.listadeespera
        set estado = 'ofrecido', oferta_vence_en = least(now() + interval '30 minutes', v_inicio)
        where id = v_sig;
        v_libres := v_libres - 1;
    end loop;
end;
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
    v_su_oferta boolean;
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
        return 'no_disponible';
    end if;
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return 'ya_reservada';
    end if;
    perform public._procesar_lista_espera(p_id_clase);

    -- Si tiene una oferta vigente, el lugar guardado es suyo.
    v_su_oferta := exists (select 1 from public.listadeespera
                           where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido');
    if not v_su_oferta and
       (select count(*) from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
       + (select count(*) from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido')
       >= v_clase.cupo_max then
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
-- Al cancelar, el lugar se ofrece a la lista de espera.
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
    perform public._procesar_lista_espera(p_id_clase);
    return 'ok';
end;
$$;

-- Devuelve {"resultado": ok | no_disponible | hay_lugar | ya_reservada | ya_en_espera, "posicion": n}
-- La posición devuelta es la actual (entre las que siguen en la lista).
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
    perform public._procesar_lista_espera(p_id_clase);
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return jsonb_build_object('resultado', 'ya_reservada');
    end if;
    if exists (select 1 from public.listadeespera
               where id_alumna = v_alumna and id_clase = p_id_clase and estado in ('en_espera', 'ofrecido')) then
        return jsonb_build_object('resultado', 'ya_en_espera');
    end if;
    if (select count(*) from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
       + (select count(*) from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido')
       < v_clase.cupo_max then
        return jsonb_build_object('resultado', 'hay_lugar');
    end if;
    -- La posición guardada nunca se repite (máximo histórico + 1): define el orden.
    select coalesce(max(posicion), 0) + 1 into v_posicion
    from public.listadeespera where id_clase = p_id_clase;
    insert into public.listadeespera (id_alumna, id_clase, posicion)
    values (v_alumna, p_id_clase, v_posicion);
    return jsonb_build_object('resultado', 'ok', 'posicion',
        (select count(*) from public.listadeespera
         where id_clase = p_id_clase and estado in ('en_espera', 'ofrecido') and posicion <= v_posicion));
end;
$$;

-- Devuelve: ok | sin_oferta | vencida | no_disponible | ya_reservada
create or replace function public.confirmar_oferta(p_id_clase bigint)
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
    select c.*, h.cupo_max into v_clase
    from public.clase c join public.horario h on h.id = c.id_horario
    where c.id = p_id_clase
    for update of c;
    if not found or v_clase.estado <> 'programada'
       or public._inicio_clase(v_clase.fecha, v_clase.hora_inicio) <= now() then
        return 'no_disponible';
    end if;
    perform public._procesar_lista_espera(p_id_clase);
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return 'ya_reservada';
    end if;
    if not exists (select 1 from public.listadeespera
                   where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido') then
        if exists (select 1 from public.listadeespera
                   where id_alumna = v_alumna and id_clase = p_id_clase
                     and estado = 'expirado' and oferta_vence_en is not null) then
            return 'vencida';
        end if;
        return 'sin_oferta';
    end if;
    insert into public.reserva (id_alumna, id_clase) values (v_alumna, p_id_clase);
    perform public._sincronizar_inscriptas(p_id_clase);
    update public.listadeespera set estado = 'confirmado'
    where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido';
    return 'ok';
end;
$$;

-- Rechazar la oferta: pasa enseguida a la siguiente. Devuelve: ok | sin_oferta
create or replace function public.rechazar_oferta(p_id_clase bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_n integer;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    perform 1 from public.clase where id = p_id_clase for update;
    perform public._procesar_lista_espera(p_id_clase);
    with r as (
        update public.listadeespera set estado = 'retirado'
        where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido'
        returning 1
    )
    select count(*) into v_n from r;
    if v_n = 0 then
        return 'sin_oferta';
    end if;
    perform public._procesar_lista_espera(p_id_clase);
    return 'ok';
end;
$$;

-- Bajarse de la lista (con o sin oferta). Devuelve: ok | no_estaba
create or replace function public.salir_lista_espera(p_id_clase bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_n integer;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    perform 1 from public.clase where id = p_id_clase for update;
    with r as (
        update public.listadeespera set estado = 'retirado'
        where id_alumna = v_alumna and id_clase = p_id_clase and estado in ('en_espera', 'ofrecido')
        returning 1
    )
    select count(*) into v_n from r;
    if v_n = 0 then
        return 'no_estaba';
    end if;
    perform public._procesar_lista_espera(p_id_clase);
    return 'ok';
end;
$$;

-- Procesa vencimientos y ofertas de varias clases (se llama al abrir una pantalla).
create or replace function public.actualizar_listas_espera(p_ids bigint[])
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
begin
    if auth.uid() is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    if p_ids is null or array_length(p_ids, 1) > 100 then
        return;
    end if;
    -- Siempre en el mismo orden para no trabar dos pedidos entre sí.
    for v_id in select distinct x from unnest(p_ids) as x order by 1 loop
        perform public._procesar_lista_espera(v_id);
    end loop;
end;
$$;

-- La lista de espera de quien consulta: estado, posición actual y vencimiento de la oferta.
create or replace function public.mi_lista_espera(p_ids bigint[] default null)
returns table (id_clase bigint, estado text, posicion integer, oferta_vence_en timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
    select e.id_clase, e.estado,
           (select count(*)::integer from public.listadeespera o
            where o.id_clase = e.id_clase and o.estado in ('en_espera', 'ofrecido')
              and o.posicion <= e.posicion),
           e.oferta_vence_en
    from public.listadeespera e
    where e.id_alumna = (select auth.uid())
      and e.estado in ('en_espera', 'ofrecido')
      and (p_ids is null or e.id_clase = any(p_ids));
$$;

-- Lugares guardados por ofertas vigentes (sin revelar para quién).
create or replace function public.ofertas_vigentes(p_ids bigint[])
returns table (id_clase bigint, cantidad integer)
language sql
stable
security definer
set search_path = ''
as $$
    select e.id_clase, count(*)::integer
    from public.listadeespera e
    where e.id_clase = any(p_ids) and e.estado = 'ofrecido' and e.oferta_vence_en > now()
    group by e.id_clase;
$$;

revoke execute on function public._procesar_lista_espera(bigint) from public, anon, authenticated;
revoke execute on function public.confirmar_oferta(bigint) from public, anon;
revoke execute on function public.rechazar_oferta(bigint) from public, anon;
revoke execute on function public.salir_lista_espera(bigint) from public, anon;
revoke execute on function public.actualizar_listas_espera(bigint[]) from public, anon;
revoke execute on function public.mi_lista_espera(bigint[]) from public, anon;
revoke execute on function public.ofertas_vigentes(bigint[]) from public, anon;
grant execute on function public.confirmar_oferta(bigint) to authenticated;
grant execute on function public.rechazar_oferta(bigint) to authenticated;
grant execute on function public.salir_lista_espera(bigint) to authenticated;
grant execute on function public.actualizar_listas_espera(bigint[]) to authenticated;
grant execute on function public.mi_lista_espera(bigint[]) to authenticated;
grant execute on function public.ofertas_vigentes(bigint[]) to authenticated;
