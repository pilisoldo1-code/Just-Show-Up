-- Vuelta atrás de 20261105000000_cobro_inasistencia.sql: las 6 funciones de facturación vuelven a ser idénticas byte a
-- byte a las de producción (03/11/2026), se quitan la función de configuración, las columnas nuevas y el auxiliar
-- _importe_base, y vuelven las restricciones originales (con sus nombres). Se niega si alguna línea ya se cobró al
-- 25 % (confirmada o borrador): esa información se perdería. Todo o nada.
begin;

do $$
begin
    if exists (select 1 from public.resumen_mensual_detalle where porcentaje <> 100) then
        raise exception 'hay líneas cobradas al 25 %%: la vuelta atrás se cancela';
    end if;
end;
$$;

drop function public.configurar_cobro_inasistencia(bigint, date);
drop function public._cobrables(integer, integer);

CREATE OR REPLACE FUNCTION public._cobrables(p_mes integer, p_anio integer)
 RETURNS TABLE(id_reserva bigint, id_alumna uuid, id_clase bigint, fecha date, hora_inicio time without time zone, id_tipo_clase bigint, tipo_nombre text, precio numeric)
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
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
$function$
;

CREATE OR REPLACE FUNCTION public._factura_al_dia(p_id bigint)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
    select not exists (
        (select c.id_reserva from public.resumen_mensual f, public._cobrables(f.mes, f.anio) c
         where f.id = p_id and c.id_alumna = f.id_alumna)
        except
        (select id_reserva from public.resumen_mensual_detalle where id_resumen_mensual = p_id)
    ) and not exists (
        (select id_reserva from public.resumen_mensual_detalle where id_resumen_mensual = p_id)
        except
        (select c.id_reserva from public.resumen_mensual f, public._cobrables(f.mes, f.anio) c
         where f.id = p_id and c.id_alumna = f.id_alumna)
    );
$function$
;

CREATE OR REPLACE FUNCTION public.sincronizar_borradores(p_mes integer, p_anio integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
$function$
;

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
$function$
;

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
$function$
;

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
$function$
;

revoke execute on function public._cobrables(integer, integer) from public, anon, authenticated;
grant execute on function public._cobrables(integer, integer) to service_role;

alter table public.resumen_mensual_detalle
    drop constraint resumen_mensual_detalle_sin_ajuste_cobra_base,
    drop constraint resumen_mensual_detalle_ajuste_distinto_de_base,
    drop constraint resumen_mensual_detalle_tope_base,
    drop constraint resumen_mensual_detalle_porcentaje_valido,
    drop constraint resumen_mensual_detalle_asistencia_valida,
    add constraint resumen_mensual_detalle_check1 check (ajuste_tipo <> 'ninguno' or importe_final = precio_original),
    add constraint resumen_mensual_detalle_check3 check (ajuste_tipo <> 'importe' or importe_final <> precio_original);
alter table public.resumen_mensual_detalle drop column porcentaje, drop column asistencia;
drop function public._importe_base(numeric, integer);
alter table public.empresa drop column cobro_inasistencia_desde;

commit;
