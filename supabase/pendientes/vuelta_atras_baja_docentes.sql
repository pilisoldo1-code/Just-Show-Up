-- Vuelta atrás de 20261103000000_baja_docentes.sql: deja funciones, triggers y estructura como antes.
-- Se niega si ya hay bajas registradas (no se borra historial). Todo o nada.
begin;

do $$
begin
    if exists (select 1 from public.baja_docente) or exists (select 1 from public.bloqueo where tipo = 'baja') then
        raise exception 'Hay bajas registradas: no se deshace la migración para no perder historial.';
    end if;
end;
$$;

drop trigger validar_docente on public.horario;
drop trigger validar_docente on public.clase;
drop function public.dar_de_baja_administradora(bigint, uuid, text, text);
drop function public.previsualizar_baja_administradora(bigint, uuid);
drop function public._validar_baja_administradora(bigint, uuid);
drop function public.dar_de_baja_docente(uuid, text, text);
drop function public.previsualizar_baja_docente(uuid);
drop function public._validar_baja_docente(uuid);
drop function public._ejecutar_baja(uuid, bigint, text, text, boolean);
drop function public._resumen_baja(uuid, bigint, boolean);
drop function public._validar_docente_clase();
drop function public._validar_docente_horario();
drop table public.baja_docente;
alter table public.bloqueo drop constraint bloqueo_tipo_check;
alter table public.bloqueo add constraint bloqueo_tipo_check check (tipo in ('licencia', 'puntual'));

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
    on conflict (id_horario, fecha) do nothing;
end;
$$;

create or replace function public.cambiar_estado_membresia(p_id_usuario uuid, p_activa boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.membresia;
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
    update public.membresia set activa = coalesce(p_activa, false) where id = v.id;
    return 'ok';
end;
$$;

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

-- _docente_activa la usaban las funciones restauradas arriba: se borra al final
drop function public._docente_activa(uuid, bigint);

commit;
