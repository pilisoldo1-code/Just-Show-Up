-- Vencimiento de facturas (decisiones de Julieta, 30/10/2026).
--
-- Regla:
-- - Una factura se paga hasta el día 10 inclusive del mes siguiente al período. Si la alumna la
--   recibe por primera vez después del día 10 (confirmación tardía), tiene 10 días desde la
--   confirmación. Nunca recibe una factura ya vencida.
-- - "Anular y reemitir" pide el tipo de reemisión: 'correccion' (error real de facturación) o
--   'administrativa'. Al confirmar la factura corregida:
--     * corrección con un cambio real (clases cobradas, ajustes, descuento o importe): 10 días desde
--       la confirmación, nunca antes del vencimiento anterior. Hasta 2 nuevos plazos por cadena de
--       reemisiones de la misma factura mensual; desde la tercera, se corrige pero conserva el plazo.
--     * corrección sin cambios reales o reemisión administrativa: conserva el vencimiento anterior.
-- - "Vencida — deuda pendiente" se muestra cuando la factura está confirmada, pendiente y pasó
--   vence_el. En la base sigue 'pendiente': se puede pagar igual (manual o Mercado Pago de prueba).
--
-- Solo agrega columnas y reemplaza _confirmar y anular_factura. anular_factura recibe p_tipo con
-- valor por defecto 'administrativa', así el código anterior (sin p_tipo) sigue funcionando.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_vencimiento_facturas.sql

alter table public.resumen_mensual
    add column tipo_reemision text,
    add column vence_el date,
    add column plazos_correccion smallint not null default 0;

alter table public.resumen_mensual
    add constraint resumen_mensual_tipo_reemision_valido
        check (tipo_reemision in ('correccion', 'administrativa')),
    add constraint resumen_mensual_plazos_correccion_rango
        check (plazos_correccion between 0 and 2);

-- Día 10 del mes siguiente al período.
create or replace function public._dia_vencimiento(p_mes int, p_anio int)
returns date
language sql
immutable
set search_path = ''
as $$
    select (make_date(p_anio, p_mes, 1) + interval '1 month' + interval '9 days')::date;
$$;

-- ¿La factura corregida difiere de la anterior en clases cobradas, ajustes, descuento o importe?
create or replace function public._correccion_con_cambios(p_nueva bigint, p_anterior bigint)
returns boolean
language sql
stable
set search_path = ''
as $$
    select exists (
               (select id_reserva, ajuste_tipo, importe_final from public.resumen_mensual_detalle where id_resumen_mensual = p_nueva
                except
                select id_reserva, ajuste_tipo, importe_final from public.resumen_mensual_detalle where id_resumen_mensual = p_anterior)
               union all
               (select id_reserva, ajuste_tipo, importe_final from public.resumen_mensual_detalle where id_resumen_mensual = p_anterior
                except
                select id_reserva, ajuste_tipo, importe_final from public.resumen_mensual_detalle where id_resumen_mensual = p_nueva))
        or exists (
               select 1 from public.resumen_mensual n, public.resumen_mensual a
               where n.id = p_nueva and a.id = p_anterior
                 and (n.descuento_tipo is distinct from a.descuento_tipo
                      or n.descuento_valor is distinct from a.descuento_valor
                      or n.total <> a.total));
$$;

-- Vencimiento de la factura p_id si se confirma en la fecha p_fecha (Montevideo).
-- regla: emision | emision_tardia | administrativa | correccion_nuevo_plazo
--        | correccion_sin_cambios | correccion_limite
create or replace function public._vencimiento(p_id bigint, p_fecha date)
returns table (vence date, plazos smallint, regla text)
language plpgsql
stable
set search_path = ''
as $$
declare
    f public.resumen_mensual;
    a public.resumen_mensual;
    v_dia10 date;
begin
    select * into f from public.resumen_mensual where id = p_id;
    v_dia10 := public._dia_vencimiento(f.mes, f.anio);
    if f.reemplaza_a is not null then
        select * into a from public.resumen_mensual where id = f.reemplaza_a;
    end if;
    -- Primera factura que ve la alumna: no hay anterior, o la anterior se anuló antes de publicarse.
    if a.id is null or a.vence_el is null or a.anulada_en < public._inicio_publicacion(f.mes, f.anio) then
        if p_fecha <= v_dia10 then
            return query select v_dia10, coalesce(a.plazos_correccion, 0::smallint), 'emision'::text;
        else
            return query select p_fecha + 10, coalesce(a.plazos_correccion, 0::smallint), 'emision_tardia'::text;
        end if;
        return;
    end if;
    if f.tipo_reemision = 'correccion' then
        if not public._correccion_con_cambios(p_id, a.id) then
            return query select a.vence_el, a.plazos_correccion, 'correccion_sin_cambios'::text;
        elsif a.plazos_correccion >= 2 then
            return query select a.vence_el, a.plazos_correccion, 'correccion_limite'::text;
        else
            return query select greatest(a.vence_el, p_fecha + 10), (a.plazos_correccion + 1)::smallint,
                                'correccion_nuevo_plazo'::text;
        end if;
        return;
    end if;
    return query select a.vence_el, a.plazos_correccion, 'administrativa'::text;
end;
$$;

create or replace function public._confirmar(p_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    f public.resumen_mensual;
    a public.profiles;
    v record;
begin
    select * into f from public.resumen_mensual where id = p_id;
    select * into a from public.profiles where id = f.id_alumna;
    select * into v from public._vencimiento(p_id, public._hoy_mvd());
    update public.resumen_mensual
    set estado_factura = 'confirmada',
        alumna_nombre = a.nombre, alumna_apellido = a.apellido, alumna_cedula = a.cedula,
        numero = 'JSU-' || f.anio || '-' || lpad(f.mes::text, 2, '0') || '-' || lpad(nextval('public.factura_numero_seq')::text, 4, '0'),
        confirmada_en = public._ahora(), confirmada_por = auth.uid(),
        estado_pago = case when f.total = 0 then 'sin_cargo' else 'pendiente' end,
        vence_el = v.vence, plazos_correccion = v.plazos,
        version = version + 1
    where id = p_id;
    insert into public.factura_historial (id_resumen_mensual, accion, detalle, por)
    values (p_id, 'confirmada', jsonb_build_object('total', f.total, 'cantidad_clases', f.cantidad_clases,
                                                   'vence_el', v.vence, 'regla_vencimiento', v.regla,
                                                   'plazos_correccion', v.plazos), auth.uid());
end;
$$;

-- Anula una factura confirmada (queda en el historial, con motivo interno) y crea un borrador
-- que la reemplaza con las mismas clases, ajustes y descuento, para corregirlo y confirmarlo.
-- p_tipo: 'correccion' (error real de facturación) o 'administrativa'.
-- Si tiene un pago vigente, primero hay que anular el pago.
-- Devuelve {"resultado": ok | no_autorizada | motivo_invalido | tipo_invalido | no_existe
--           | no_confirmada_anular | tiene_pago, "id_nueva": n}
drop function public.anular_factura(bigint, text);
create function public.anular_factura(p_id bigint, p_motivo text, p_tipo text default 'administrativa')
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
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
$$;

-- ---------- datos existentes ----------
-- Reemisiones anteriores a esta migración: administrativas. Facturas confirmadas o anuladas:
-- vencimiento calculado con la fecha en que se confirmaron, en orden (la anterior primero).
update public.resumen_mensual set tipo_reemision = 'administrativa'
where reemplaza_a is not null and tipo_reemision is null;

do $$
declare
    r record;
    v record;
begin
    for r in select id, confirmada_en from public.resumen_mensual
             where estado_factura <> 'borrador' and vence_el is null order by id
    loop
        select * into v from public._vencimiento(r.id, (r.confirmada_en at time zone 'America/Montevideo')::date);
        update public.resumen_mensual set vence_el = v.vence, plazos_correccion = v.plazos where id = r.id;
    end loop;
end;
$$;

alter table public.resumen_mensual
    add constraint resumen_mensual_tipo_si_reemplaza check ((reemplaza_a is null) = (tipo_reemision is null)),
    add constraint resumen_mensual_vence_si_confirmada check ((estado_factura = 'borrador') = (vence_el is null));

-- ---------- permisos ----------
revoke execute on function public._dia_vencimiento(int, int), public._correccion_con_cambios(bigint, bigint),
    public._vencimiento(bigint, date), public._confirmar(bigint) from public, anon, authenticated;
revoke execute on function public.anular_factura(bigint, text, text) from public, anon;
grant execute on function public.anular_factura(bigint, text, text) to authenticated;
