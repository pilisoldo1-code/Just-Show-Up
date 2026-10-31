-- Multiempresa, parte 3: funciones por empresa y por rol (diseño: MULTIEMPRESA_DISENO.md).
-- - Las funciones que recorren datos (generar clases, licencias, cobro, borradores, confirmar el mes,
--   cupos, listas de espera) trabajan solo con la empresa activa.
-- - Administradora: todo en su empresa. Profesora no administradora: solo sus horarios, clases,
--   licencias, cancelaciones y asistencias; nada de facturación, pagos, cédulas ni tipos de clase.
-- - Una licencia general de una profesora no administradora cancela solo sus clases (bloqueo.id_profesora).
-- - Cada línea de factura guarda la profesora que dictó la clase.
-- - Mercado Pago solo en empresas con mp_habilitado (la Empresa 1), además de todas las restricciones
--   actuales (modo prueba, solo alumna@prueba.invalid).
-- - Funciones nuevas: elegir_empresa, asignar_membresia, cambiar_estado_membresia y, para la
--   superadministradora, crear_empresa, editar_empresa, cambiar_estado_empresa, resumen_empresas y
--   asignar_administradora.
-- Compatible con el código actual (mismas firmas; la cuenta actual es administradora de la Empresa 1).
-- Vuelta atrás: supabase/pendientes/vuelta_atras_multiempresa_3.sql
-- (Generado por scripts/multiempresa_3/generar.py a partir de scripts/multiempresa_3/manual.sql.)

alter table public.bloqueo add column id_profesora uuid references public.profiles(id);

-- ---------- profesora en cada línea de factura ----------
create or replace function public._completar_profesora_linea()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.id_profesora is null then
        new.id_profesora := (select id_profesora from public.clase where id = new.id_clase);
    end if;
    if new.profesora_nombre is null and new.id_profesora is not null then
        new.profesora_nombre := (select btrim(nombre || ' ' || apellido) from public.profiles where id = new.id_profesora);
    end if;
    return new;
end;
$$;
create trigger completar_profesora_linea before insert on public.resumen_mensual_detalle
    for each row execute function public._completar_profesora_linea();

-- ---------- generación de clases: solo horarios de la empresa activa ----------
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

-- Una licencia general (sin horario) cubre los horarios de su empresa; si es de una profesora,
-- solo los de esa profesora.
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

-- ---------- cobro y facturación por empresa ----------
create or replace function public._cobrables(p_mes integer, p_anio integer)
returns table (id_reserva bigint, id_alumna uuid, id_clase bigint, fecha date, hora_inicio time, id_tipo_clase bigint, tipo_nombre text, precio numeric)
language sql
stable
set search_path = ''
as $$
    select r.id, r.id_alumna, c.id, c.fecha, c.hora_inicio, c.id_tipo_clase, t.nombre, c.precio
    from public.reserva r
    join public.clase c on c.id = r.id_clase
    left join public.tipo_clase t on t.id = c.id_tipo_clase
    where r.estado = 'confirmada'
      and c.estado = 'programada'
      and c.id_empresa = public.empresa_activa()
      and c.fecha >= make_date(p_anio, p_mes, 1)
      and c.fecha < make_date(p_anio, p_mes, 1) + interval '1 month'
      and public._inicio_clase(c.fecha, c.hora_inicio) <= public._ahora();
$$;

create or replace function public._firma_mes(p_mes integer, p_anio integer)
returns text
language sql
stable
security definer
set search_path = ''
as $$
    select md5(coalesce(string_agg(id || ':' || version, ',' order by id), ''))
    from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and cantidad_clases > 0
      and id_empresa = public.empresa_activa();
$$;

-- Sin sesión (servidor) mira todas las empresas; con sesión, la empresa activa.
create or replace function public._periodo_finalizado(p_mes integer, p_anio integer)
returns boolean
language sql
stable
set search_path = ''
as $$
    select not exists (
        select 1 from public.clase c
        where c.estado = 'programada'
          and c.id_empresa = coalesce(public.empresa_activa(), c.id_empresa)
          and c.fecha >= make_date(p_anio, p_mes, 1)
          and c.fecha < make_date(p_anio, p_mes, 1) + interval '1 month'
          and public._inicio_clase(c.fecha, c.hora_inicio) + make_interval(mins => c.duracion) > public._ahora()
    );
$$;

create or replace function public.previsualizar_confirmacion_mes(p_mes integer, p_anio integer)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    return jsonb_build_object(
        'resultado', 'ok',
        'en_revision', public._mes_en_revision(p_mes, p_anio),
        'periodo_finalizado', public._periodo_finalizado(p_mes, p_anio),
        'cantidad', count(*) filter (where cantidad_clases > 0),
        'total', coalesce(sum(total) filter (where cantidad_clases > 0), 0),
        'sin_clases', count(*) filter (where cantidad_clases = 0),
        'desactualizadas', count(*) filter (where cantidad_clases > 0 and not public._factura_al_dia(id)),
        'sin_cedula', count(*) filter (where cantidad_clases > 0
                                       and (select p.cedula from public.profiles p where p.id = id_alumna) is null),
        'firma', public._firma_mes(p_mes, p_anio))
    from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and id_empresa = public.empresa_activa();
end;
$$;

create or replace function public.sincronizar_borradores(p_mes integer, p_anio integer)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid;
    v_empresa bigint := public.empresa_activa();
    f public.resumen_mensual;
    v_creadas int := 0;
    v_actualizadas int := 0;
    v_quitadas int;
    v_agregadas int;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_mes is null or p_mes not between 1 and 12 or p_anio is null or p_anio < 2024 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if not public._mes_en_revision(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'fuera_de_revision');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), p_anio * 100 + p_mes);
    perform public._generar_semana_actual(p_mes, p_anio);

    for v_alumna in
        select id_alumna from public._cobrables(p_mes, p_anio)
        union
        select id_alumna from public.resumen_mensual
        where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and id_empresa = v_empresa
        order by 1
    loop
        select * into f from public.resumen_mensual
        where id_alumna = v_alumna and mes = p_mes and anio = p_anio and estado_factura <> 'anulada'
          and id_empresa = v_empresa
        for update;
        if found and f.estado_factura <> 'borrador' then
            continue;
        end if;
        if not found then
            insert into public.resumen_mensual (id_alumna, mes, anio, id_empresa) values (v_alumna, p_mes, p_anio, v_empresa)
            returning * into f;
            v_creadas := v_creadas + 1;
        end if;

        with quitadas as (
            delete from public.resumen_mensual_detalle d
            where d.id_resumen_mensual = f.id
              and d.id_reserva not in (select c.id_reserva from public._cobrables(p_mes, p_anio) c where c.id_alumna = v_alumna)
            returning d.*
        ), log as (
            insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, por)
            select f.id, q.id, 'linea_quitada',
                   jsonb_build_object('fecha', q.fecha, 'hora', q.hora_inicio, 'tipo', q.tipo_nombre,
                                      'ajuste_tipo', q.ajuste_tipo, 'importe_final', q.importe_final),
                   auth.uid()
            from quitadas q where q.ajuste_tipo <> 'ninguno'
        )
        select count(*) into v_quitadas from quitadas;

        insert into public.resumen_mensual_detalle
            (id_resumen_mensual, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre, precio_original, importe_final)
        select f.id, c.id_reserva, c.id_clase, c.fecha, c.hora_inicio, c.id_tipo_clase, c.tipo_nombre, c.precio, c.precio
        from public._cobrables(p_mes, p_anio) c
        where c.id_alumna = v_alumna
        on conflict (id_resumen_mensual, id_reserva) do nothing;
        get diagnostics v_agregadas = row_count;

        if v_quitadas + v_agregadas > 0 then
            perform public._recalcular_factura(f.id, true);
            v_actualizadas := v_actualizadas + 1;
        end if;

        if not exists (select 1 from public.resumen_mensual_detalle where id_resumen_mensual = f.id)
           and not exists (select 1 from public.factura_historial where id_resumen_mensual = f.id) then
            delete from public.resumen_mensual where id = f.id;
        end if;
    end loop;

    return jsonb_build_object('resultado', 'ok', 'creadas', v_creadas, 'actualizadas', v_actualizadas);
end;
$$;

create or replace function public.confirmar_facturas_mes(p_mes integer, p_anio integer, p_firma text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
    v_n int := 0;
    v_total numeric := 0;
    v_empresa bigint := public.empresa_activa();
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), p_anio * 100 + p_mes);
    perform public._generar_semana_actual(p_mes, p_anio);
    if not public._mes_en_revision(p_mes, p_anio) or not public._periodo_finalizado(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'periodo_abierto');
    end if;
    perform 1 from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and id_empresa = v_empresa order by id for update;
    if p_firma is distinct from public._firma_mes(p_mes, p_anio) then
        return jsonb_build_object('resultado', 'cambio');
    end if;
    for v_id in
        select id from public.resumen_mensual
        where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and cantidad_clases > 0
          and id_empresa = v_empresa
        order by id
    loop
        if not public._factura_al_dia(v_id) then
            raise exception 'desactualizada' using errcode = 'P0001';
        end if;
        v_total := v_total + (select total from public.resumen_mensual where id = v_id);
        perform public._confirmar(v_id);
        v_n := v_n + 1;
    end loop;
    if v_n = 0 then
        return jsonb_build_object('resultado', 'nada');
    end if;
    return jsonb_build_object('resultado', 'ok', 'confirmadas', v_n, 'total', v_total);
exception when raise_exception then
    if sqlerrm = 'desactualizada' then
        return jsonb_build_object('resultado', 'desactualizada');
    end if;
    raise;
end;
$$;

-- Una factura de otra empresa no existe para quien la pide.
create or replace function public._validar_borrador(p_id bigint, p_version integer)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    select * into f from public.resumen_mensual where id = p_id and id_empresa = public.empresa_activa() for update;
    if not found then
        return 'no_existe';
    end if;
    if f.estado_factura <> 'borrador' then
        return 'no_es_borrador';
    end if;
    if p_version is distinct from f.version then
        return 'version_cambio';
    end if;
    return null;
end;
$$;

-- ---------- cupos y listas de espera ----------
create or replace function public.reservas_confirmadas(p_ids bigint[])
returns table (id_clase bigint, cantidad integer)
language sql
stable
security definer
set search_path = ''
as $$
    select r.id_clase, count(*)::integer
    from public.reserva r
    where r.id_clase = any(p_ids) and r.estado = 'confirmada' and r.id_empresa = public.empresa_activa()
    group by r.id_clase;
$$;

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
      and e.id_empresa = public.empresa_activa()
    group by e.id_clase;
$$;

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
    -- Siempre en el mismo orden para no trabar dos pedidos entre sí; solo clases de la empresa activa.
    for v_id in select c.id from public.clase c
                where c.id = any(p_ids) and c.id_empresa = public.empresa_activa() order by 1 loop
        perform public._procesar_lista_espera(v_id);
    end loop;
end;
$$;

-- ---------- asistencia: la profesora de la clase o la administradora ----------
create or replace function public.marcar_asistencia(p_id_reserva bigint, p_estado text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_inicio timestamptz;
    v_duracion integer;
    v_clase bigint;
begin
    if not public.es_docente() then
        return 'no_autorizada';
    end if;
    if p_estado not in ('presente', 'ausente') then
        raise exception 'estado inválido' using errcode = '22023';
    end if;
    select public._inicio_clase(c.fecha, c.hora_inicio), c.duracion, c.id into v_inicio, v_duracion, v_clase
    from public.reserva r join public.clase c on c.id = r.id_clase
    where r.id = p_id_reserva and r.estado = 'confirmada' and r.id_empresa = public.empresa_activa();
    if v_inicio is null then
        return 'sin_reserva';
    end if;
    if not public.puede_gestionar_clase(v_clase) then
        return 'no_autorizada';
    end if;
    if now() < v_inicio or now() > v_inicio + make_interval(mins => v_duracion) then
        return 'fuera_de_horario';
    end if;
    insert into public.asistencia (id_reserva, estado) values (p_id_reserva, p_estado)
    on conflict (id_reserva) do update set estado = excluded.estado;
    return 'ok';
end;
$$;

-- ---------- licencias y cancelaciones ----------
-- Administradora: un horario de su empresa o todos. Profesora no administradora: un horario suyo o
-- todos los suyos (la licencia general queda a su nombre y no toca clases de otras profesoras).
create or replace function public.crear_bloqueo(p_tipo text, p_desde date, p_hasta date, p_id_horario bigint, p_motivo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_id bigint;
    v_clases bigint[];
    v_reservas integer;
    v_espera integer;
    v_empresa bigint := public.empresa_activa();
    v_admin boolean := public.es_admin();
    v_profesora uuid;
begin
    if not public.es_docente() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_tipo not in ('licencia', 'puntual') or p_desde is null or p_hasta is null
       or p_desde < v_hoy or p_hasta < p_desde then
        return jsonb_build_object('resultado', 'fechas_invalidas');
    end if;
    if coalesce(trim(p_motivo), '') = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;
    if p_id_horario is not null and not exists (select 1 from public.horario where id = p_id_horario and id_empresa = v_empresa) then
        return jsonb_build_object('resultado', 'horario_invalido');
    end if;
    if p_id_horario is not null and not public.puede_gestionar_horario(p_id_horario) then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    v_profesora := case when v_admin or p_id_horario is not null then null else auth.uid() end;

    insert into public.bloqueo (tipo, fecha_desde, fecha_hasta, id_horario, motivo, id_profesora, id_empresa)
    values (p_tipo, p_desde, p_hasta, p_id_horario, trim(p_motivo), v_profesora, v_empresa)
    returning id into v_id;

    perform 1 from public.clase c
    where c.estado = 'programada'
      and c.id_empresa = v_empresa
      and c.fecha between p_desde and p_hasta
      and (p_id_horario is null or c.id_horario = p_id_horario)
      and (v_profesora is null or c.id_profesora = v_profesora)
      and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    order by c.id
    for update;

    with canceladas as (
        update public.clase c set estado = 'cancelada', cantidad_inscriptas = 0
        where c.estado = 'programada'
          and c.id_empresa = v_empresa
          and c.fecha between p_desde and p_hasta
          and (p_id_horario is null or c.id_horario = p_id_horario)
          and (v_profesora is null or c.id_profesora = v_profesora)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from canceladas;

    with r as (
        update public.reserva
        set estado = 'cancelada', fecha_cancelacion = now(), id_bloqueo = v_id
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

    return jsonb_build_object('resultado', 'ok', 'id', v_id,
                              'clases', coalesce(array_length(v_clases, 1), 0),
                              'reservas', v_reservas, 'espera', v_espera);
end;
$$;

create or replace function public.crear_bloqueos_puntuales(p_ids_clase bigint[], p_motivo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_motivo text := trim(coalesce(p_motivo, ''));
    v_ids bigint[];
    v_total integer;
    v_validas integer;
    v_reservas integer := 0;
    v_espera integer := 0;
    v_bloqueo_id bigint;
    rec record;
begin
    if not public.es_docente() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;

    select array_agg(distinct x) into v_ids from unnest(coalesce(p_ids_clase, '{}')) x;
    v_total := coalesce(array_length(v_ids, 1), 0);
    if v_total = 0 then
        return jsonb_build_object('resultado', 'sin_clases');
    end if;
    if v_motivo = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;

    perform 1 from public.clase c where c.id = any(v_ids) and c.id_empresa = public.empresa_activa() order by c.id for update;

    -- Todas tienen que existir en la empresa activa, estar programadas, no haber empezado, ser de un
    -- horario activo y poder gestionarlas quien cancela. Si una sola no cumple, no se cancela ninguna.
    select count(*) into v_validas from public.clase c
    join public.horario h on h.id = c.id_horario
    where c.id = any(v_ids) and c.estado = 'programada' and h.activo
      and c.id_empresa = public.empresa_activa()
      and public.puede_gestionar_clase(c.id)
      and public._inicio_clase(c.fecha, c.hora_inicio) > now();
    if v_validas <> v_total then
        return jsonb_build_object('resultado', 'clases_invalidas');
    end if;

    for rec in select c.id, c.fecha, c.id_horario from public.clase c where c.id = any(v_ids)
    loop
        insert into public.bloqueo (tipo, fecha_desde, fecha_hasta, id_horario, motivo)
        values ('puntual', rec.fecha, rec.fecha, rec.id_horario, v_motivo)
        returning id into v_bloqueo_id;

        update public.clase set estado = 'cancelada', cantidad_inscriptas = 0 where id = rec.id;

        with r as (
            update public.reserva set estado = 'cancelada', fecha_cancelacion = now(), id_bloqueo = v_bloqueo_id
            where id_clase = rec.id and estado = 'confirmada'
            returning 1
        )
        select v_reservas + count(*) into v_reservas from r;

        with e as (
            update public.listadeespera set estado = 'expirado'
            where id_clase = rec.id and estado in ('en_espera', 'ofrecido')
            returning 1
        )
        select v_espera + count(*) into v_espera from e;
    end loop;

    return jsonb_build_object('resultado', 'ok', 'clases', v_total, 'reservas', v_reservas, 'espera', v_espera);
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

-- ---------- tipo de clase de un horario: admin o la profesora del horario ----------
create or replace function public.cambiar_tipo_horario(p_id_horario bigint, p_id_tipo bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_t public.tipo_clase;
    v_ids bigint[];
    v_tomadas bigint[];
    v_id bigint;
begin
    if not public.puede_gestionar_horario(p_id_horario) then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_t from public.tipo_clase where id = p_id_tipo and id_empresa = public.empresa_activa();
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if not v_t.activo then
        return jsonb_build_object('resultado', 'tipo_inactivo');
    end if;
    update public.horario set id_tipo_clase = p_id_tipo where id = p_id_horario;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;

    select coalesce(array_agg(id order by id), '{}') into v_ids from (
        select c.id from public.clase c
        where c.id_horario = p_id_horario and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        order by c.id
        for update
    ) x;
    select coalesce(array_agg(id), '{}') into v_tomadas
    from unnest(v_ids) as id where public._clase_tomada(id);

    update public.clase
    set id_tipo_clase = v_t.id, precio = v_t.precio_presencial, duracion = v_t.duracion, cupo_max = v_t.cupo_max
    where id = any(v_ids) and not (id = any(v_tomadas));

    foreach v_id in array v_ids loop
        perform public._procesar_lista_espera(v_id);
    end loop;

    return jsonb_build_object('resultado', 'ok',
        'cambian', coalesce(array_length(v_ids, 1), 0) - coalesce(array_length(v_tomadas, 1), 0),
        'conservan', coalesce(array_length(v_tomadas, 1), 0));
end;
$$;

create or replace function public.previsualizar_cambio_tipo_horario(p_id_horario bigint, p_id_tipo bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not public.puede_gestionar_horario(p_id_horario) then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    return (
        with futuras as (
            select c.id, public._clase_tomada(c.id) as tomada
            from public.clase c
            where c.id_horario = p_id_horario and c.estado = 'programada'
              and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        )
        select jsonb_build_object('resultado', 'ok',
            'futuras', (select count(*) from futuras),
            'cambian', (select count(*) from futuras where not tomada),
            'conservan', (select count(*) from futuras where tomada))
    );
end;
$$;

-- ---------- Mercado Pago: solo empresas habilitadas (la Empresa 1), resto igual ----------
create or replace function public.crear_intento_mp(p_id_resumen bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_mail text;
    f public.resumen_mensual;
    v_ref text;
    v_id bigint;
begin
    if not coalesce((select habilitado from public.mp_configuracion where id = 1), false) then
        return jsonb_build_object('resultado', 'deshabilitado');
    end if;
    if v_uid is null or public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select lower(mail) into v_mail from public.profiles where id = v_uid;
    if v_mail is distinct from 'alumna@prueba.invalid' then
        return jsonb_build_object('resultado', 'alumna_no_habilitada');
    end if;
    select * into f from public.resumen_mensual where id = p_id_resumen and id_empresa = public.empresa_activa() for update;
    if not found or f.id_alumna <> v_uid then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if not coalesce((select mp_habilitado and activa from public.empresa where id = f.id_empresa), false) then
        return jsonb_build_object('resultado', 'deshabilitado');
    end if;
    if f.estado_factura <> 'confirmada' or public._ahora() < public._inicio_publicacion(f.mes, f.anio) then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    if f.estado_pago = 'sin_cargo' or f.total <= 0 then
        return jsonb_build_object('resultado', 'sin_cargo');
    end if;
    if f.estado_pago <> 'pendiente'
       or exists (select 1 from public.pago where id_resumen_mensual = f.id and estado = 'vigente') then
        return jsonb_build_object('resultado', 'ya_pagada');
    end if;
    v_ref := 'JSU-' || f.id || '-' || replace(gen_random_uuid()::text, '-', '');
    insert into public.mp_intento (id_resumen_mensual, id_alumna, referencia, monto)
    values (f.id, v_uid, v_ref, f.total)
    returning id into v_id;
    return jsonb_build_object('resultado', 'ok', 'id_intento', v_id, 'referencia', v_ref,
                              'monto', f.total, 'numero', f.numero, 'mes', f.mes, 'anio', f.anio);
end;
$$;

create or replace function public.registrar_pago_mercado_pago(p_referencia text, p_id_pago_mp text, p_estado text, p_monto numeric, p_moneda text, p_fecha date)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id_resumen bigint;
    i public.mp_intento;
    f public.resumen_mensual;
    v_id_pago bigint;
    v_motivo text;
begin
    select id_resumen_mensual into v_id_resumen from public.mp_intento where referencia = p_referencia;
    if not found or coalesce(p_id_pago_mp, '') = '' then
        return jsonb_build_object('resultado', 'intento_desconocido');
    end if;
    select * into f from public.resumen_mensual where id = v_id_resumen for update;
    select * into i from public.mp_intento where referencia = p_referencia for update;

    if exists (select 1 from public.pago where id_transaccion = p_id_pago_mp) then
        update public.mp_intento set estado = 'aprobado', actualizado_en = now()
        where id = i.id and estado <> 'aprobado' and id_pago_mp = p_id_pago_mp;
        return jsonb_build_object('resultado', 'ya_registrado');
    end if;

    if p_estado is distinct from 'approved' then
        if i.estado in ('creado', 'rechazado') then
            update public.mp_intento
            set estado = case when p_estado in ('rejected', 'cancelled') then 'rechazado' else estado end,
                id_pago_mp = p_id_pago_mp, detalle = left(p_estado, 40), actualizado_en = now()
            where id = i.id;
        end if;
        return jsonb_build_object('resultado',
            case when p_estado in ('rejected', 'cancelled') then 'rechazado' else 'pendiente' end);
    end if;

    v_motivo := case
        when not coalesce((select mp_habilitado from public.empresa where id = f.id_empresa), false)
            then 'la empresa no tiene Mercado Pago habilitado'
        when i.estado in ('aprobado', 'a_revisar') then 'el intento ya tenía otro pago'
        when p_moneda is distinct from 'UYU' or p_monto is distinct from i.monto then 'importe o moneda distintos al intento'
        when f.estado_factura <> 'confirmada' then 'la factura ya no está confirmada'
        when f.estado_pago <> 'pendiente'
             or exists (select 1 from public.pago where id_resumen_mensual = f.id and estado = 'vigente')
            then 'la factura ya tenía un pago'
        when f.total <> p_monto then 'el total de la factura cambió'
    end;
    if v_motivo is not null then
        if i.estado <> 'aprobado' then
            update public.mp_intento set estado = 'a_revisar', id_pago_mp = p_id_pago_mp, detalle = v_motivo,
                                         actualizado_en = now()
            where id = i.id;
        end if;
        return jsonb_build_object('resultado', 'a_revisar', 'motivo', v_motivo);
    end if;

    insert into public.pago (id_resumen_mensual, monto, medio_pago, fecha, registrado_por, id_transaccion)
    values (f.id, p_monto, 'mercado_pago', least(coalesce(p_fecha, public._hoy_mvd()), public._hoy_mvd()),
            i.id_alumna, p_id_pago_mp)
    returning id into v_id_pago;
    update public.resumen_mensual set estado_pago = 'pagado' where id = f.id;
    update public.mp_intento set estado = 'aprobado', id_pago_mp = p_id_pago_mp, id_pago = v_id_pago,
                                 detalle = null, actualizado_en = now()
    where id = i.id;
    return jsonb_build_object('resultado', 'ok', 'id_pago', v_id_pago);
exception when unique_violation then
    return jsonb_build_object('resultado', 'ya_registrado');
end;
$$;

-- ---------- empresa activa y membresías ----------
create or replace function public.elegir_empresa(p_id_empresa bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if auth.uid() is null then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.membresia m join public.empresa e on e.id = m.id_empresa
                   where m.id_usuario = auth.uid() and m.id_empresa = p_id_empresa and m.activa and e.activa) then
        return 'no_autorizada';
    end if;
    update public.profiles set empresa_activa = p_id_empresa where id = auth.uid();
    return 'ok';
end;
$$;

-- La administradora agrega o cambia los roles de una persona en su empresa (la cuenta ya existe; si es
-- nueva, la crea el servidor). No puede quitarse a sí misma el rol de administradora.
create or replace function public.asignar_membresia(p_id_usuario uuid, p_es_admin boolean, p_es_profesora boolean, p_es_alumna boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if not (coalesce(p_es_admin, false) or coalesce(p_es_profesora, false) or coalesce(p_es_alumna, false)) then
        return 'sin_rol';
    end if;
    if p_id_usuario = auth.uid() and not coalesce(p_es_admin, false) then
        return 'no_puede_quitarse_admin';
    end if;
    if not exists (select 1 from public.profiles where id = p_id_usuario and not es_superadmin) then
        return 'no_existe';
    end if;
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
    values (p_id_usuario, v_empresa, coalesce(p_es_admin, false), coalesce(p_es_profesora, false), coalesce(p_es_alumna, false))
    on conflict (id_usuario, id_empresa) do update
        set es_admin = excluded.es_admin, es_profesora = excluded.es_profesora, es_alumna = excluded.es_alumna, activa = true;
    update public.profiles set empresa_activa = v_empresa where id = p_id_usuario and empresa_activa is null;
    return 'ok';
end;
$$;

create or replace function public.cambiar_estado_membresia(p_id_usuario uuid, p_activa boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if p_id_usuario = auth.uid() then
        return 'no_puede_desactivarse';
    end if;
    update public.membresia set activa = coalesce(p_activa, false)
    where id_usuario = p_id_usuario and id_empresa = public.empresa_activa();
    if not found then
        return 'no_existe';
    end if;
    return 'ok';
end;
$$;

-- ---------- superadministradora ----------
create or replace function public.crear_empresa(p_nombre text, p_codigo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
begin
    if not public.es_superadmin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if length(btrim(coalesce(p_nombre, ''))) not between 2 and 80 or coalesce(p_codigo, '') !~ '^[A-Z]{2,6}$' then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if exists (select 1 from public.empresa where codigo = p_codigo) then
        return jsonb_build_object('resultado', 'codigo_repetido');
    end if;
    insert into public.empresa (nombre, codigo) values (btrim(p_nombre), p_codigo) returning id into v_id;
    return jsonb_build_object('resultado', 'ok', 'id', v_id);
end;
$$;

-- El código queda fijo desde la primera factura numerada.
create or replace function public.editar_empresa(p_id bigint, p_nombre text, p_codigo text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    e public.empresa;
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    select * into e from public.empresa where id = p_id for update;
    if not found then
        return 'no_existe';
    end if;
    if length(btrim(coalesce(p_nombre, ''))) not between 2 and 80 or coalesce(p_codigo, '') !~ '^[A-Z]{2,6}$' then
        return 'datos_invalidos';
    end if;
    if p_codigo <> e.codigo and (e.ultimo_numero_factura > 0
                                 or exists (select 1 from public.resumen_mensual where id_empresa = p_id and numero is not null)) then
        return 'codigo_fijo';
    end if;
    if p_codigo <> e.codigo and exists (select 1 from public.empresa where codigo = p_codigo) then
        return 'codigo_repetido';
    end if;
    update public.empresa set nombre = btrim(p_nombre), codigo = p_codigo where id = p_id;
    return 'ok';
end;
$$;

create or replace function public.cambiar_estado_empresa(p_id bigint, p_activa boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    update public.empresa set activa = coalesce(p_activa, false) where id = p_id;
    if not found then
        return 'no_existe';
    end if;
    return 'ok';
end;
$$;

-- Información general, sin datos operativos ni personales (solo nombre y mail de las administradoras).
create or replace function public.resumen_empresas()
returns table (id bigint, nombre text, codigo text, activa boolean, profesoras integer, alumnas integer, administradoras text)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return;
    end if;
    return query
    select e.id, e.nombre, e.codigo, e.activa,
           (select count(*)::integer from public.membresia m where m.id_empresa = e.id and m.activa and m.es_profesora),
           (select count(*)::integer from public.membresia m where m.id_empresa = e.id and m.activa and m.es_alumna),
           (select string_agg(btrim(p.nombre || ' ' || p.apellido) || ' <' || p.mail || '>', ', ' order by p.apellido)
            from public.membresia m join public.profiles p on p.id = m.id_usuario
            where m.id_empresa = e.id and m.activa and m.es_admin)
    from public.empresa e
    order by e.id;
end;
$$;

-- La superadministradora asigna la administradora de una empresa (la cuenta la crea el servidor).
create or replace function public.asignar_administradora(p_id_empresa bigint, p_id_usuario uuid, p_es_profesora boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.empresa where id = p_id_empresa) then
        return 'no_existe';
    end if;
    if not exists (select 1 from public.profiles where id = p_id_usuario and not es_superadmin) then
        return 'no_existe';
    end if;
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora)
    values (p_id_usuario, p_id_empresa, true, coalesce(p_es_profesora, false))
    on conflict (id_usuario, id_empresa) do update
        set es_admin = true, es_profesora = membresia.es_profesora or coalesce(p_es_profesora, false), activa = true;
    update public.profiles set empresa_activa = p_id_empresa where id = p_id_usuario and empresa_activa is null;
    return 'ok';
end;
$$;

revoke execute on function public._completar_profesora_linea() from public, anon, authenticated;
revoke execute on function public.elegir_empresa(bigint), public.asignar_membresia(uuid, boolean, boolean, boolean),
    public.cambiar_estado_membresia(uuid, boolean), public.crear_empresa(text, text), public.editar_empresa(bigint, text, text),
    public.cambiar_estado_empresa(bigint, boolean), public.resumen_empresas(),
    public.asignar_administradora(bigint, uuid, boolean) from public, anon;
grant execute on function public.elegir_empresa(bigint), public.asignar_membresia(uuid, boolean, boolean, boolean),
    public.cambiar_estado_membresia(uuid, boolean), public.crear_empresa(text, text), public.editar_empresa(bigint, text, text),
    public.cambiar_estado_empresa(bigint, boolean), public.resumen_empresas(),
    public.asignar_administradora(bigint, uuid, boolean) to authenticated;

-- ---------- solo la administradora (generadas) ----------

-- confirmar_factura: solo la administradora
CREATE OR REPLACE FUNCTION public.confirmar_factura(p_id bigint, p_version integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    f public.resumen_mensual;
    v_err text;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into f from public.resumen_mensual where id = p_id;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), f.anio * 100 + f.mes);
    perform public._generar_semana_actual(f.mes, f.anio);
    v_err := public._validar_borrador(p_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._mes_en_revision(f.mes, f.anio) or not public._periodo_finalizado(f.mes, f.anio) then
        return jsonb_build_object('resultado', 'periodo_abierto');
    end if;
    select * into f from public.resumen_mensual where id = p_id;
    if f.cantidad_clases = 0 then
        return jsonb_build_object('resultado', 'sin_clases');
    end if;
    if not public._factura_al_dia(p_id) then
        return jsonb_build_object('resultado', 'desactualizada');
    end if;
    perform public._confirmar(p_id);
    return jsonb_build_object('resultado', 'ok', 'numero', (select numero from public.resumen_mensual where id = p_id));
end;
$function$;

-- registrar_pago: solo la administradora
CREATE OR REPLACE FUNCTION public.registrar_pago(p_id_resumen bigint, p_monto numeric, p_medio text, p_fecha date)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    f public.resumen_mensual;
    v_id bigint;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if p_monto is null or p_medio is null or p_medio not in ('efectivo', 'transferencia') or p_fecha is null then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if p_fecha > public._hoy_mvd() then
        return jsonb_build_object('resultado', 'fecha_futura');
    end if;
    select * into f from public.resumen_mensual where id = p_id_resumen for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if f.estado_factura <> 'confirmada' then
        return jsonb_build_object('resultado', 'no_confirmada');
    end if;
    if f.estado_pago = 'pagado' then
        return jsonb_build_object('resultado', 'ya_pagado');
    end if;
    if f.estado_pago = 'sin_cargo' then
        return jsonb_build_object('resultado', 'sin_cargo');
    end if;
    if p_monto <> f.total then
        return jsonb_build_object('resultado', 'monto_distinto', 'total', f.total);
    end if;
    insert into public.pago (id_resumen_mensual, monto, medio_pago, fecha, registrado_por)
    values (p_id_resumen, f.total, p_medio, p_fecha, auth.uid())
    returning id into v_id;
    update public.resumen_mensual set estado_pago = 'pagado' where id = p_id_resumen;
    return jsonb_build_object('resultado', 'ok', 'id_pago', v_id);
exception when unique_violation then
    return jsonb_build_object('resultado', 'ya_pagado');
end;
$function$;

-- anular_pago: solo la administradora
CREATE OR REPLACE FUNCTION public.anular_pago(p_id_pago bigint, p_motivo text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_id_resumen bigint;
    v_estado text;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select id_resumen_mensual into v_id_resumen from public.pago where id = p_id_pago;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    perform 1 from public.resumen_mensual where id = v_id_resumen for update;
    select estado into v_estado from public.pago where id = p_id_pago for update;
    if v_estado <> 'vigente' then
        return jsonb_build_object('resultado', 'ya_anulado');
    end if;
    update public.pago set estado = 'anulado' where id = p_id_pago;
    insert into public.pago_anulacion (id_pago, motivo, anulado_por) values (p_id_pago, btrim(p_motivo), auth.uid());
    update public.resumen_mensual set estado_pago = 'pendiente' where id = v_id_resumen;
    return jsonb_build_object('resultado', 'ok');
end;
$function$;

-- anular_factura: solo la administradora
CREATE OR REPLACE FUNCTION public.anular_factura(p_id bigint, p_motivo text, p_tipo text DEFAULT 'administrativa'::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    f public.resumen_mensual;
    v_nueva bigint;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    if p_tipo is null or p_tipo not in ('correccion', 'administrativa') then
        return jsonb_build_object('resultado', 'tipo_invalido');
    end if;
    select * into f from public.resumen_mensual where id = p_id;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), f.anio * 100 + f.mes);
    select * into f from public.resumen_mensual where id = p_id for update;
    if f.estado_factura <> 'confirmada' then
        return jsonb_build_object('resultado', 'no_confirmada_anular');
    end if;
    if f.estado_pago = 'pagado' or exists (select 1 from public.pago where id_resumen_mensual = p_id and estado = 'vigente') then
        return jsonb_build_object('resultado', 'tiene_pago');
    end if;

    update public.resumen_mensual
    set estado_factura = 'anulada', anulada_en = public._ahora(), anulada_por = auth.uid(), version = version + 1
    where id = p_id;

    insert into public.resumen_mensual (id_alumna, mes, anio, reemplaza_a, tipo_reemision, descuento_tipo, descuento_valor)
    values (f.id_alumna, f.mes, f.anio, p_id, p_tipo, f.descuento_tipo, f.descuento_valor)
    returning id into v_nueva;

    insert into public.resumen_mensual_detalle
        (id_resumen_mensual, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre,
         precio_original, ajuste_tipo, importe_final)
    select v_nueva, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre,
           precio_original, ajuste_tipo, importe_final
    from public.resumen_mensual_detalle where id_resumen_mensual = p_id;

    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por, en)
    select v_nueva, nueva.id, h.accion, h.detalle || jsonb_build_object('copiado_de', f.numero), h.motivo, h.por, h.en
    from public.factura_historial h
    join public.resumen_mensual_detalle vieja on vieja.id = h.id_linea
    join public.resumen_mensual_detalle nueva on nueva.id_resumen_mensual = v_nueva and nueva.id_reserva = vieja.id_reserva
    where h.id_resumen_mensual = p_id and h.accion in ('ajuste_linea', 'ajuste_quitado')
    order by h.id;
    insert into public.factura_historial (id_resumen_mensual, accion, detalle, motivo, por, en)
    select v_nueva, h.accion, h.detalle || jsonb_build_object('copiado_de', f.numero), h.motivo, h.por, h.en
    from public.factura_historial h
    where h.id_resumen_mensual = p_id and h.accion in ('descuento_general', 'descuento_quitado')
    order by h.id;

    insert into public.factura_historial (id_resumen_mensual, accion, detalle, motivo, por)
    values (p_id, 'anulada', jsonb_build_object('texto', 'Reemplazada por un borrador nuevo', 'id_nueva', v_nueva,
                                                'tipo', p_tipo),
            btrim(p_motivo), auth.uid()),
           (v_nueva, 'reemision', jsonb_build_object('texto', 'Reemplaza a la factura ' || f.numero, 'id_anterior', p_id,
                                                     'tipo', p_tipo),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_nueva, true);
    return jsonb_build_object('resultado', 'ok', 'id_nueva', v_nueva);
end;
$function$;

-- ajustar_linea: solo la administradora
CREATE OR REPLACE FUNCTION public.ajustar_linea(p_id_linea bigint, p_tipo text, p_importe numeric, p_motivo text, p_version integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_id bigint;
    v_err text;
    l public.resumen_mensual_detalle;
    v_importe numeric;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select id_resumen_mensual into v_id from public.resumen_mensual_detalle where id = p_id_linea;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    v_err := public._validar_borrador(v_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select * into l from public.resumen_mensual_detalle where id = p_id_linea for update;
    if p_tipo = 'bonificada' then
        v_importe := 0;
    elsif p_tipo = 'importe' and p_importe is not null and p_importe >= 0
          and p_importe < l.precio_original and p_importe = round(p_importe, 2) then
        v_importe := p_importe;
    else
        return jsonb_build_object('resultado', 'importe_invalido');
    end if;
    if l.precio_original = 0 then
        return jsonb_build_object('resultado', 'importe_invalido');
    end if;
    update public.resumen_mensual_detalle
    set ajuste_tipo = case when v_importe = 0 then 'bonificada' else 'importe' end, importe_final = v_importe
    where id = p_id_linea;
    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por)
    values (v_id, p_id_linea, 'ajuste_linea',
            jsonb_build_object('fecha', l.fecha, 'hora', l.hora_inicio, 'tipo', l.tipo_nombre,
                               'precio_original', l.precio_original,
                               'antes', l.importe_final, 'despues', v_importe),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = v_id));
end;
$function$;

-- quitar_ajuste_linea: solo la administradora
CREATE OR REPLACE FUNCTION public.quitar_ajuste_linea(p_id_linea bigint, p_motivo text, p_version integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_id bigint;
    v_err text;
    l public.resumen_mensual_detalle;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select id_resumen_mensual into v_id from public.resumen_mensual_detalle where id = p_id_linea;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    v_err := public._validar_borrador(v_id, p_version);
    if v_err is not null then
        return jsonb_build_object('resultado', v_err);
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
    end if;
    select * into l from public.resumen_mensual_detalle where id = p_id_linea for update;
    if l.ajuste_tipo = 'ninguno' then
        return jsonb_build_object('resultado', 'sin_ajuste');
    end if;
    update public.resumen_mensual_detalle set ajuste_tipo = 'ninguno', importe_final = precio_original
    where id = p_id_linea;
    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por)
    values (v_id, p_id_linea, 'ajuste_quitado',
            jsonb_build_object('fecha', l.fecha, 'hora', l.hora_inicio, 'tipo', l.tipo_nombre,
                               'precio_original', l.precio_original, 'antes', l.importe_final, 'despues', l.precio_original),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = v_id));
end;
$function$;

-- actualizar_cedula: solo la administradora
CREATE OR REPLACE FUNCTION public.actualizar_cedula(p_id_alumna uuid, p_cedula text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_cedula text := nullif(regexp_replace(coalesce(p_cedula, ''), '[\s.\-]', '', 'g'), '');
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.profiles where id = p_id_alumna and rol = 'alumna') then
        return 'no_existe';
    end if;
    if v_cedula is not null and not public._cedula_valida(v_cedula) then
        return 'cedula_invalida';
    end if;
    update public.profiles set cedula = v_cedula where id = p_id_alumna;
    return 'ok';
exception when unique_violation then
    return 'cedula_repetida';
end;
$function$;

-- crear_tipo_clase: solo la administradora
CREATE OR REPLACE FUNCTION public.crear_tipo_clase(p_nombre text, p_descripcion text, p_duracion integer, p_precio numeric, p_cupo integer, p_imagen_url text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_id bigint;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(length(trim(p_nombre)), 0) not between 1 and 60 or p_duracion is null or p_duracion <= 0
       or p_precio is null or p_precio < 0 or p_cupo is null or p_cupo <= 0 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    begin
        insert into public.tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max, imagen_url)
        values (trim(p_nombre), coalesce(trim(p_descripcion), ''), p_duracion, p_precio, p_cupo, p_imagen_url)
        returning id into v_id;
    exception when unique_violation then
        return jsonb_build_object('resultado', 'nombre_repetido');
    end;
    return jsonb_build_object('resultado', 'ok', 'id', v_id);
end;
$function$;

-- aplicar_cambio_tipo: solo la administradora
CREATE OR REPLACE FUNCTION public.aplicar_cambio_tipo(p_id bigint, p_nombre text, p_descripcion text, p_duracion integer, p_precio numeric, p_cupo integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_ids bigint[];
    v_tomadas bigint[];
    v_id bigint;
    v_horarios integer;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(length(trim(p_nombre)), 0) not between 1 and 60 or p_duracion is null or p_duracion <= 0
       or p_precio is null or p_precio < 0 or p_cupo is null or p_cupo <= 0 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    perform 1 from public.tipo_clase where id = p_id for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    begin
        update public.tipo_clase
        set nombre = trim(p_nombre), descripcion = coalesce(trim(p_descripcion), ''),
            duracion = p_duracion, precio_presencial = p_precio, cupo_max = p_cupo
        where id = p_id;
    exception when unique_violation then
        return jsonb_build_object('resultado', 'nombre_repetido');
    end;

    -- Las columnas espejo de horario se recalculan con el trigger.
    with h as (update public.horario set id_tipo_clase = id_tipo_clase where id_tipo_clase = p_id returning 1)
    select count(*) into v_horarios from h;

    -- Clases futuras del tipo, bloqueadas en orden (como reservar).
    select coalesce(array_agg(id order by id), '{}') into v_ids from (
        select c.id from public.clase c
        where c.id_tipo_clase = p_id and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        order by c.id
        for update
    ) x;
    select coalesce(array_agg(id), '{}') into v_tomadas
    from unnest(v_ids) as id where public._clase_tomada(id);

    update public.clase set precio = p_precio, duracion = p_duracion, cupo_max = p_cupo
    where id = any(v_ids) and not (id = any(v_tomadas));
    update public.clase set cupo_max = p_cupo where id = any(v_tomadas);

    foreach v_id in array v_ids loop
        perform public._procesar_lista_espera(v_id);
    end loop;

    return jsonb_build_object('resultado', 'ok', 'horarios', v_horarios,
        'actualizan_todo', coalesce(array_length(v_ids, 1), 0) - coalesce(array_length(v_tomadas, 1), 0),
        'conservan_precio', coalesce(array_length(v_tomadas, 1), 0));
end;
$function$;

-- previsualizar_cambio_tipo: solo la administradora
CREATE OR REPLACE FUNCTION public.previsualizar_cambio_tipo(p_id bigint, p_duracion integer, p_precio numeric, p_cupo integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_t public.tipo_clase;
    v_resultado jsonb;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_t from public.tipo_clase where id = p_id;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    with futuras as (
        select c.id, c.fecha, c.hora_inicio, c.precio, c.duracion, c.cupo_max,
               (select count(*) from public.reserva r where r.id_clase = c.id and r.estado = 'confirmada') as confirmadas,
               (select count(*) from public.listadeespera e where e.id_clase = c.id and e.estado = 'ofrecido'
                  and e.oferta_vence_en > now()) as ofertas,
               (select count(*) from public.listadeespera e where e.id_clase = c.id and e.estado = 'en_espera') as en_espera,
               public._clase_tomada(c.id) as tomada
        from public.clase c
        where c.id_tipo_clase = p_id and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    )
    select jsonb_build_object(
        'resultado', 'ok',
        'horarios', (select count(*) from public.horario where id_tipo_clase = p_id),
        'futuras', (select count(*) from futuras),
        'actualizan_todo', (select count(*) from futuras where not tomada),
        'conservan_precio', (select count(*) from futuras where tomada
                             and (precio <> p_precio or duracion <> p_duracion)),
        'cambia_cupo', p_cupo <> v_t.cupo_max,
        'excedidas', coalesce((select jsonb_agg(jsonb_build_object('fecha', fecha, 'hora', hora_inicio,
                                   'confirmadas', confirmadas, 'ofertas', ofertas) order by fecha, hora_inicio)
                               from futuras where confirmadas + ofertas > p_cupo), '[]'),
        'con_lista_espera', (select count(*) from futuras where en_espera > 0 and p_cupo > cupo_max)
    ) into v_resultado;
    return v_resultado;
end;
$function$;

-- cambiar_estado_tipo_clase: solo la administradora
CREATE OR REPLACE FUNCTION public.cambiar_estado_tipo_clase(p_id bigint, p_activo boolean)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    v_activos integer;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    perform 1 from public.tipo_clase where id = p_id for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if not p_activo then
        select count(*) into v_activos from public.horario where id_tipo_clase = p_id and activo;
        if v_activos > 0 then
            return jsonb_build_object('resultado', 'tiene_horarios_activos', 'horarios', v_activos);
        end if;
    end if;
    update public.tipo_clase set activo = p_activo where id = p_id;
    return jsonb_build_object('resultado', 'ok');
end;
$function$;

-- cambiar_imagen_tipo_clase: solo la administradora
CREATE OR REPLACE FUNCTION public.cambiar_imagen_tipo_clase(p_id bigint, p_imagen_url text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    update public.tipo_clase set imagen_url = p_imagen_url where id = p_id;
    return case when found then 'ok' else 'no_existe' end;
end;
$function$;
