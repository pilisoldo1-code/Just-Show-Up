-- VUELTA ATRÁS de 20261030120000_vencimiento_facturas.sql (NO es una migración).
-- Deja _confirmar y anular_factura exactamente como estaban en producción el 30/10/2026
-- (copias de pg_get_functiondef: huellas md5 467dedb2… y 3239c032…), quita las funciones nuevas,
-- las restricciones y las columnas tipo_reemision, vence_el y plazos_correccion.
-- Se pierden esos tres datos (el tipo de cada reemisión y los vencimientos calculados); el resto
-- de las facturas, líneas, pagos e historial no se toca. Todo o nada.
-- Correr en el SQL Editor de Supabase solo si Julieta lo autoriza.

begin;

drop function public.anular_factura(bigint, text, text);

CREATE OR REPLACE FUNCTION public.anular_factura(p_id bigint, p_motivo text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    f public.resumen_mensual;
    v_nueva bigint;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if not public._motivo_valido(p_motivo) then
        return jsonb_build_object('resultado', 'motivo_invalido');
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

    insert into public.resumen_mensual (id_alumna, mes, anio, reemplaza_a, descuento_tipo, descuento_valor)
    values (f.id_alumna, f.mes, f.anio, p_id, f.descuento_tipo, f.descuento_valor)
    returning id into v_nueva;

    insert into public.resumen_mensual_detalle
        (id_resumen_mensual, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre,
         precio_original, ajuste_tipo, importe_final)
    select v_nueva, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre,
           precio_original, ajuste_tipo, importe_final
    from public.resumen_mensual_detalle where id_resumen_mensual = p_id;

    -- los motivos de los ajustes que siguen vigentes pasan a las líneas nuevas
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
    values (p_id, 'anulada', jsonb_build_object('texto', 'Reemplazada por un borrador nuevo', 'id_nueva', v_nueva),
            btrim(p_motivo), auth.uid()),
           (v_nueva, 'reemision', jsonb_build_object('texto', 'Reemplaza a la factura ' || f.numero, 'id_anterior', p_id),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_nueva, true);
    return jsonb_build_object('resultado', 'ok', 'id_nueva', v_nueva);
end;
$function$
;

CREATE OR REPLACE FUNCTION public._confirmar(p_id bigint)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
    f public.resumen_mensual;
    a public.profiles;
begin
    select * into f from public.resumen_mensual where id = p_id;
    select * into a from public.profiles where id = f.id_alumna;
    update public.resumen_mensual
    set estado_factura = 'confirmada',
        alumna_nombre = a.nombre, alumna_apellido = a.apellido, alumna_cedula = a.cedula,
        numero = 'JSU-' || f.anio || '-' || lpad(f.mes::text, 2, '0') || '-' || lpad(nextval('public.factura_numero_seq')::text, 4, '0'),
        confirmada_en = public._ahora(), confirmada_por = auth.uid(),
        estado_pago = case when f.total = 0 then 'sin_cargo' else 'pendiente' end,
        version = version + 1
    where id = p_id;
    insert into public.factura_historial (id_resumen_mensual, accion, detalle, por)
    values (p_id, 'confirmada', jsonb_build_object('total', f.total, 'cantidad_clases', f.cantidad_clases), auth.uid());
end;
$function$
;

drop function public._vencimiento(bigint, date);
drop function public._correccion_con_cambios(bigint, bigint);
drop function public._dia_vencimiento(int, int);

alter table public.resumen_mensual
    drop constraint resumen_mensual_tipo_si_reemplaza,
    drop constraint resumen_mensual_vence_si_confirmada,
    drop constraint resumen_mensual_tipo_reemision_valido,
    drop constraint resumen_mensual_plazos_correccion_rango,
    drop column tipo_reemision,
    drop column vence_el,
    drop column plazos_correccion;

revoke execute on function public._confirmar(bigint) from public, anon, authenticated;
revoke execute on function public.anular_factura(bigint, text) from public, anon;
grant execute on function public.anular_factura(bigint, text) to authenticated;

delete from supabase_migrations.schema_migrations where name = 'vencimiento_facturas';

commit;
