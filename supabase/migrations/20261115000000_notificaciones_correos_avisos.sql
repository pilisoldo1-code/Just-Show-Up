-- Notificaciones, fase C: los 9 correos automáticos de la alumna (los videos solo se avisan dentro de la app).
-- - _notificaciones_generar(): encola lo que corresponde (lo llama notificaciones_tick() cada minuto, solo con el
--   interruptor encendido). La clave única de notificacion_envio evita duplicados aunque corra muchas veces.
-- - _notificacion_datos(): justo antes de mandar, vuelve a comprobar que el aviso sigue correspondiendo (reserva vigente,
--   factura sin pagar, oferta sin vencer...) y arma los datos del correo con la información actual.
-- Reglas (hora de Montevideo):
--   clase_cancelada     reserva cancelada por la profesora (con bloqueo) de una clase que todavía no empezó.
--   recordatorio_clase  reserva confirmada de una clase programada que empieza dentro de la próxima hora.
--   reserva_confirmada  reserva de las últimas 2 horas de una clase que empieza en más de una hora (si empieza antes,
--                       va el recordatorio). Nunca por anotarse en la lista de espera (eso no es una reserva).
--   lugar_disponible    oferta vigente de la lista de espera.
--   factura_nueva       factura confirmada, publicada (desde el día 1 del mes siguiente) después de encender los correos,
--                       sin pagar y fuera del plazo de «próxima a vencer» (si ya está en ese plazo, va solo ese aviso).
--   factura_proxima     sin pagar, de vence_el − factura_aviso_dias a vence_el inclusive.
--   factura_vencida     sin pagar, desde el día siguiente a vence_el.
--   factura_vencida_2   sin pagar, 7 días después del primer aviso de vencida.
--   pago_registrado     pago vigente (manual o Mercado Pago aprobado). Intentos pendientes o rechazados no crean pago.
-- Los avisos de facturas salen desde correo_hora_envio (9:00). Solo se avisan hechos posteriores a correo_desde (la
-- primera vez que se encendió el interruptor). Pagada, anulada o cancelada a tiempo: el aviso se descarta.
-- No cambia datos existentes.

create or replace function public._notificaciones_generar()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_par public.notificacion_parametros;
    v_ahora timestamptz := now();
    v_hoy date := (public._ahora() at time zone 'America/Montevideo')::date;
    v_hora_ok boolean;
    v_desde timestamptz;
    v_total integer := 0;
    v_n integer;
begin
    select * into v_par from public.notificacion_parametros where id = 1;
    if not found or not v_par.correo_activo or v_par.correo_desde is null then
        return 0;
    end if;
    v_hora_ok := extract(hour from public._ahora() at time zone 'America/Montevideo') >= v_par.correo_hora_envio;
    v_desde := greatest(v_par.correo_desde, v_ahora - interval '24 hours');

    create temporary table if not exists pg_temp._candidatos (
        id_empresa bigint, id_usuario uuid, tipo text, referencia bigint, prioridad integer
    ) on commit drop;
    truncate pg_temp._candidatos;

    -- clases y reservas
    insert into pg_temp._candidatos
    select r.id_empresa, r.id_alumna, 'clase_cancelada', r.id, 3
      from public.reserva r join public.clase c on c.id = r.id_clase
     where r.estado = 'cancelada' and r.id_bloqueo is not null and r.fecha_cancelacion >= v_desde
       and public._inicio_clase(c.fecha, c.hora_inicio) > v_ahora;
    insert into pg_temp._candidatos
    select r.id_empresa, r.id_alumna, 'recordatorio_clase', r.id, 1
      from public.reserva r join public.clase c on c.id = r.id_clase
     where r.estado = 'confirmada' and c.estado = 'programada'
       and public._inicio_clase(c.fecha, c.hora_inicio) > v_ahora
       and public._inicio_clase(c.fecha, c.hora_inicio) <= v_ahora + interval '60 minutes';
    insert into pg_temp._candidatos
    select r.id_empresa, r.id_alumna, 'reserva_confirmada', r.id, 4
      from public.reserva r join public.clase c on c.id = r.id_clase
     where r.estado = 'confirmada' and c.estado = 'programada'
       and r.fecha_reserva >= greatest(v_desde, v_ahora - interval '2 hours')
       and public._inicio_clase(c.fecha, c.hora_inicio) > v_ahora + interval '60 minutes';
    insert into pg_temp._candidatos
    select le.id_empresa, le.id_alumna, 'lugar_disponible', le.id, 0
      from public.listadeespera le join public.clase c on c.id = le.id_clase
     where le.estado = 'ofrecido' and le.oferta_vence_en > v_ahora and c.estado = 'programada'
       and public._inicio_clase(c.fecha, c.hora_inicio) > v_ahora;
    insert into pg_temp._candidatos
    select rm.id_empresa, rm.id_alumna, 'pago_registrado', p.id, 4
      from public.pago p join public.resumen_mensual rm on rm.id = p.id_resumen_mensual
     where p.estado = 'vigente' and p.creado_en >= v_desde and rm.estado_factura = 'confirmada';

    -- facturas (desde la hora de envío)
    if v_hora_ok then
        insert into pg_temp._candidatos
        select rm.id_empresa, rm.id_alumna, 'factura_nueva', rm.id, 5
          from public.resumen_mensual rm
         where rm.estado_factura = 'confirmada' and rm.estado_pago = 'pendiente'
           and public._ahora() >= public._inicio_publicacion(rm.mes, rm.anio)
           and greatest(rm.confirmada_en, public._inicio_publicacion(rm.mes, rm.anio)) >= v_par.correo_desde
           and (rm.vence_el is null or v_hoy < rm.vence_el - v_par.factura_aviso_dias);
        insert into pg_temp._candidatos
        select rm.id_empresa, rm.id_alumna, 'factura_vencida', rm.id, 5
          from public.resumen_mensual rm
         where rm.estado_factura = 'confirmada' and rm.estado_pago = 'pendiente'
           and public._ahora() >= public._inicio_publicacion(rm.mes, rm.anio)
           and rm.vence_el is not null and v_hoy > rm.vence_el;
        insert into pg_temp._candidatos
        select rm.id_empresa, rm.id_alumna, 'factura_vencida_2', rm.id, 5
          from public.resumen_mensual rm
          join public.notificacion_envio n on n.tipo = 'factura_vencida' and n.referencia = rm.id
         where rm.estado_factura = 'confirmada' and rm.estado_pago = 'pendiente'
           and rm.vence_el is not null and v_hoy > rm.vence_el
           and n.estado in ('enviado', 'omitido', 'fallido')
           and (coalesce(n.enviado_en, n.creado_en) at time zone 'America/Montevideo')::date + 7 <= v_hoy;
    end if;

    insert into public.notificacion_envio (id_empresa, id_usuario, tipo, referencia, prioridad, estado, motivo)
    select x.id_empresa, x.id_usuario, x.tipo, x.referencia, x.prioridad,
           case when p.recibir_correos then 'pendiente' else 'omitido' end,
           case when p.recibir_correos then null else 'preferencia' end
      from pg_temp._candidatos x
      join public.profiles p on p.id = x.id_usuario
      join public.membresia m on m.id_usuario = x.id_usuario and m.id_empresa = x.id_empresa and m.activa and m.es_alumna
    on conflict (tipo, referencia, canal) do nothing;
    get diagnostics v_n = row_count;
    v_total := v_total + v_n;

    if v_hora_ok then
        insert into public.notificacion_envio (id_empresa, id_usuario, tipo, referencia, prioridad, estado, motivo)
        select rm.id_empresa, rm.id_alumna, 'factura_proxima', rm.id, 5,
               case when p.recibir_correos then 'pendiente' else 'omitido' end,
               case when p.recibir_correos then null else 'preferencia' end
          from public.resumen_mensual rm
          join public.profiles p on p.id = rm.id_alumna
          join public.membresia m on m.id_usuario = rm.id_alumna and m.id_empresa = rm.id_empresa and m.activa and m.es_alumna
         where rm.estado_factura = 'confirmada' and rm.estado_pago = 'pendiente'
           and public._ahora() >= public._inicio_publicacion(rm.mes, rm.anio)
           and rm.vence_el is not null and v_hoy between rm.vence_el - v_par.factura_aviso_dias and rm.vence_el
        on conflict (tipo, referencia, canal) do nothing;
        get diagnostics v_n = row_count;
        v_total := v_total + v_n;
    end if;
    return v_total;
end;
$$;

create or replace function public._notificacion_datos(p_envio public.notificacion_envio)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_hoy date := (public._ahora() at time zone 'America/Montevideo')::date;
    v_nombre text := (select p.nombre from public.profiles p where p.id = p_envio.id_usuario);
    v jsonb;
begin
    if p_envio.tipo in ('clase_cancelada', 'recordatorio_clase', 'reserva_confirmada') then
        select jsonb_build_object('tipo_clase', t.nombre, 'fecha', c.fecha, 'hora_inicio', c.hora_inicio) into v
          from public.reserva r
          join public.clase c on c.id = r.id_clase
          left join public.tipo_clase t on t.id = c.id_tipo_clase
         where r.id = p_envio.referencia and r.id_alumna = p_envio.id_usuario and r.id_empresa = p_envio.id_empresa
           and public._inicio_clase(c.fecha, c.hora_inicio) > now()
           and case p_envio.tipo
                   when 'clase_cancelada' then r.estado = 'cancelada' and r.id_bloqueo is not null
                   else r.estado = 'confirmada' and c.estado = 'programada' end;
    elsif p_envio.tipo = 'lugar_disponible' then
        select jsonb_build_object('tipo_clase', t.nombre, 'fecha', c.fecha, 'hora_inicio', c.hora_inicio,
                                  'oferta_vence_en', le.oferta_vence_en) into v
          from public.listadeespera le
          join public.clase c on c.id = le.id_clase
          left join public.tipo_clase t on t.id = c.id_tipo_clase
         where le.id = p_envio.referencia and le.id_alumna = p_envio.id_usuario and le.id_empresa = p_envio.id_empresa
           and le.estado = 'ofrecido' and le.oferta_vence_en > now() and c.estado = 'programada'
           and public._inicio_clase(c.fecha, c.hora_inicio) > now();
    elsif p_envio.tipo in ('factura_nueva', 'factura_proxima', 'factura_vencida', 'factura_vencida_2') then
        select jsonb_build_object('numero', rm.numero, 'mes', rm.mes, 'anio', rm.anio, 'total', rm.total,
                                  'vence_el', rm.vence_el) into v
          from public.resumen_mensual rm
         where rm.id = p_envio.referencia and rm.id_alumna = p_envio.id_usuario and rm.id_empresa = p_envio.id_empresa
           and rm.estado_factura = 'confirmada' and rm.estado_pago = 'pendiente'
           and public._ahora() >= public._inicio_publicacion(rm.mes, rm.anio)
           and case p_envio.tipo
                   when 'factura_nueva' then true
                   when 'factura_proxima' then rm.vence_el is not null and v_hoy <= rm.vence_el
                   else rm.vence_el is not null and v_hoy > rm.vence_el end;
    elsif p_envio.tipo = 'pago_registrado' then
        select jsonb_build_object('numero', rm.numero, 'mes', rm.mes, 'anio', rm.anio, 'monto', p.monto,
                                  'medio_pago', p.medio_pago) into v
          from public.pago p
          join public.resumen_mensual rm on rm.id = p.id_resumen_mensual
         where p.id = p_envio.referencia and rm.id_alumna = p_envio.id_usuario and rm.id_empresa = p_envio.id_empresa
           and p.estado = 'vigente' and rm.estado_factura = 'confirmada';
    end if;
    return case when v is null then null else v || jsonb_build_object('nombre', v_nombre) end;
end;
$$;

create or replace function public.notificaciones_tick()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_par public.notificacion_parametros;
    v_nuevos integer := 0;
    v_despachador text := 'nada_pendiente';
begin
    if not pg_try_advisory_xact_lock(hashtext('notificaciones_tick')) then
        return jsonb_build_object('resultado', 'ocupado');
    end if;
    select * into v_par from public.notificacion_parametros where id = 1;
    if not v_par.correo_activo then
        return jsonb_build_object('resultado', 'apagado');
    end if;
    v_nuevos := public._notificaciones_generar();
    if exists (select 1 from public.notificacion_envio where estado = 'pendiente' and proximo_intento <= now()) then
        v_despachador := public._notificaciones_llamar_despachador();
    end if;
    return jsonb_build_object('resultado', 'ok', 'nuevos', v_nuevos, 'despachador', v_despachador);
end;
$$;

revoke execute on function public._notificaciones_generar(), public._notificacion_datos(public.notificacion_envio),
    public.notificaciones_tick() from public, anon, authenticated, service_role;
