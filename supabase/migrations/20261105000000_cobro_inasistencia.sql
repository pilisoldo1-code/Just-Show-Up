-- Cobro del 25 % por inasistencia sin cancelación.
-- - Una reserva confirmada de una clase dictada con asistencia «ausente» se cobra al 25 % de su precio; «presente» y
--   «sin registrar» al 100 % (nunca se presume una ausencia). Redondeo a centésimos.
-- - Rige por empresa desde empresa.cobro_inasistencia_desde (Just Show Up: 1/11/2026; vacía = no se aplica). Nunca
--   cambia facturas confirmadas: solo borradores, que se recalculan al sincronizar (al abrir Facturación).
-- - Cada línea guarda la asistencia y el porcentaje con que se cobró (foto). Los ajustes por clase tienen como tope el
--   importe base (100 % o 25 %); el descuento general se aplica sobre las líneas ya calculadas.
-- - «Anular y reemitir» copia porcentaje y asistencia; el borrador nuevo se recalcula con la asistencia actual.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_cobro_inasistencia.sql

-- ---------- estructura ----------
alter table public.empresa add column cobro_inasistencia_desde date;
update public.empresa set cobro_inasistencia_desde = date '2026-11-01' where codigo = 'JSU';

alter table public.resumen_mensual_detalle add column porcentaje integer not null default 100;
alter table public.resumen_mensual_detalle add column asistencia text;
alter table public.resumen_mensual_detalle
    add constraint resumen_mensual_detalle_porcentaje_valido check (porcentaje in (25, 100)),
    add constraint resumen_mensual_detalle_asistencia_valida check (asistencia in ('presente', 'ausente'));

create or replace function public._importe_base(p_precio numeric, p_porcentaje integer)
returns numeric
language sql
immutable
set search_path = ''
as $$
    select round(p_precio * p_porcentaje / 100.0, 2);
$$;

-- las restricciones que comparaban el importe con el precio pasan a compararlo con el importe base
do $$
declare
    r record;
    n int := 0;
begin
    for r in select conname from pg_constraint
             where conrelid = 'public.resumen_mensual_detalle'::regclass and contype = 'c'
               and (pg_get_constraintdef(oid) like '%''ninguno''::text) OR (importe_final = precio_original)%'
                    or pg_get_constraintdef(oid) like '%''importe''::text) OR (importe_final <> precio_original)%')
    loop
        execute format('alter table public.resumen_mensual_detalle drop constraint %I', r.conname);
        n := n + 1;
    end loop;
    if n <> 2 then
        raise exception 'se esperaban 2 restricciones de importe y se encontraron %', n;
    end if;
end;
$$;
alter table public.resumen_mensual_detalle
    add constraint resumen_mensual_detalle_sin_ajuste_cobra_base
        check (ajuste_tipo <> 'ninguno' or importe_final = public._importe_base(precio_original, porcentaje)),
    add constraint resumen_mensual_detalle_ajuste_distinto_de_base
        check (ajuste_tipo <> 'importe' or importe_final <> public._importe_base(precio_original, porcentaje)),
    add constraint resumen_mensual_detalle_tope_base
        check (importe_final <= public._importe_base(precio_original, porcentaje));

-- ---------- qué se cobra ----------
drop function public._cobrables(integer, integer);
create function public._cobrables(p_mes integer, p_anio integer)
returns table (id_reserva bigint, id_alumna uuid, id_clase bigint, fecha date, hora_inicio time, id_tipo_clase bigint,
               tipo_nombre text, precio numeric, asistencia text, porcentaje integer)
language sql
stable
set search_path = ''
as $$
    select r.id, r.id_alumna, c.id, c.fecha, c.hora_inicio, c.id_tipo_clase, t.nombre, c.precio, a.estado,
           case when a.estado = 'ausente' and e.cobro_inasistencia_desde is not null
                     and c.fecha >= e.cobro_inasistencia_desde then 25 else 100 end
    from public.reserva r
    join public.clase c on c.id = r.id_clase
    join public.empresa e on e.id = c.id_empresa
    left join public.tipo_clase t on t.id = c.id_tipo_clase
    left join public.asistencia a on a.id_reserva = r.id
    where r.estado = 'confirmada'
      and c.estado = 'programada'
      and c.id_empresa = public.empresa_activa()
      and c.fecha >= make_date(p_anio, p_mes, 1)
      and c.fecha < make_date(p_anio, p_mes, 1) + interval '1 month'
      and public._inicio_clase(c.fecha, c.hora_inicio) <= public._ahora();
$$;
revoke execute on function public._cobrables(integer, integer) from public, anon, authenticated;
grant execute on function public._cobrables(integer, integer) to service_role;

-- Un borrador está al día si tiene exactamente las reservas cobrables, con la asistencia y el porcentaje actuales.
create or replace function public._factura_al_dia(p_id bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select not exists (
        (select c.id_reserva, c.porcentaje, c.asistencia from public.resumen_mensual f, public._cobrables(f.mes, f.anio) c
         where f.id = p_id and c.id_alumna = f.id_alumna)
        except
        (select id_reserva, porcentaje, asistencia from public.resumen_mensual_detalle where id_resumen_mensual = p_id)
    ) and not exists (
        (select id_reserva, porcentaje, asistencia from public.resumen_mensual_detalle where id_resumen_mensual = p_id)
        except
        (select c.id_reserva, c.porcentaje, c.asistencia from public.resumen_mensual f, public._cobrables(f.mes, f.anio) c
         where f.id = p_id and c.id_alumna = f.id_alumna)
    );
$$;

-- Igual que antes; además actualiza las líneas cuya asistencia (o porcentaje) cambió: sin ajuste, cobran el importe
-- base nuevo; bonificadas siguen en 0; «cobrar menos» se baja al tope si lo supera. Queda en el historial.
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
    v_cambiadas int;
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

        -- líneas existentes con otra asistencia o porcentaje
        with nuevas as (
            select d.id, d.importe_final as antes_importe, d.ajuste_tipo as antes_ajuste, d.porcentaje as antes_pct,
                   d.asistencia as antes_asistencia, c.porcentaje, c.asistencia,
                   public._importe_base(d.precio_original, c.porcentaje) as base, d.fecha, d.hora_inicio, d.tipo_nombre
            from public.resumen_mensual_detalle d
            join public._cobrables(p_mes, p_anio) c on c.id_reserva = d.id_reserva and c.id_alumna = v_alumna
            where d.id_resumen_mensual = f.id
              and (d.porcentaje is distinct from c.porcentaje or d.asistencia is distinct from c.asistencia)
        ), cambiadas as (
            update public.resumen_mensual_detalle d
            set porcentaje = n.porcentaje, asistencia = n.asistencia,
                ajuste_tipo = case when n.antes_ajuste = 'importe' and n.antes_importe >= n.base then 'ninguno'
                                   else n.antes_ajuste end,
                importe_final = case when n.antes_ajuste = 'ninguno' then n.base
                                     when n.antes_ajuste = 'bonificada' then 0
                                     else least(n.antes_importe, n.base) end
            from nuevas n
            where d.id = n.id
            returning d.id, d.importe_final, n.antes_importe, n.antes_pct, n.porcentaje, n.antes_asistencia, n.asistencia,
                      n.fecha, n.hora_inicio, n.tipo_nombre
        ), log2 as (
            insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, por)
            select f.id, x.id, 'cambio_asistencia',
                   jsonb_build_object('fecha', x.fecha, 'hora', x.hora_inicio, 'tipo', x.tipo_nombre,
                                      'asistencia_antes', x.antes_asistencia, 'asistencia_despues', x.asistencia,
                                      'porcentaje_antes', x.antes_pct, 'porcentaje_despues', x.porcentaje,
                                      'antes', x.antes_importe, 'despues', x.importe_final),
                   auth.uid()
            from cambiadas x where x.antes_importe <> x.importe_final
        )
        select count(*) into v_cambiadas from cambiadas;

        insert into public.resumen_mensual_detalle
            (id_resumen_mensual, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre, precio_original,
             importe_final, porcentaje, asistencia)
        select f.id, c.id_reserva, c.id_clase, c.fecha, c.hora_inicio, c.id_tipo_clase, c.tipo_nombre, c.precio,
               public._importe_base(c.precio, c.porcentaje), c.porcentaje, c.asistencia
        from public._cobrables(p_mes, p_anio) c
        where c.id_alumna = v_alumna
        on conflict (id_resumen_mensual, id_reserva) do nothing;
        get diagnostics v_agregadas = row_count;

        if v_quitadas + v_agregadas + v_cambiadas > 0 then
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

-- «Cobrar menos» tiene como tope el importe base de la línea (100 % o 25 %).
create or replace function public.ajustar_linea(p_id_linea bigint, p_tipo text, p_importe numeric, p_motivo text, p_version integer)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
    v_err text;
    l public.resumen_mensual_detalle;
    v_importe numeric;
    v_base numeric;
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
    v_base := public._importe_base(l.precio_original, l.porcentaje);
    if p_tipo = 'bonificada' then
        v_importe := 0;
    elsif p_tipo = 'importe' and p_importe is not null and p_importe >= 0
          and p_importe < v_base and p_importe = round(p_importe, 2) then
        v_importe := p_importe;
    else
        return jsonb_build_object('resultado', 'importe_invalido');
    end if;
    if v_base = 0 then
        return jsonb_build_object('resultado', 'importe_invalido');
    end if;
    update public.resumen_mensual_detalle
    set ajuste_tipo = case when v_importe = 0 then 'bonificada' else 'importe' end, importe_final = v_importe
    where id = p_id_linea;
    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por)
    values (v_id, p_id_linea, 'ajuste_linea',
            jsonb_build_object('fecha', l.fecha, 'hora', l.hora_inicio, 'tipo', l.tipo_nombre,
                               'precio_original', l.precio_original, 'porcentaje', l.porcentaje,
                               'antes', l.importe_final, 'despues', v_importe),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = v_id));
end;
$$;

-- Quitar un ajuste vuelve al importe base de la línea.
create or replace function public.quitar_ajuste_linea(p_id_linea bigint, p_motivo text, p_version integer)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
    v_err text;
    l public.resumen_mensual_detalle;
    v_base numeric;
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
    v_base := public._importe_base(l.precio_original, l.porcentaje);
    update public.resumen_mensual_detalle set ajuste_tipo = 'ninguno', importe_final = v_base
    where id = p_id_linea;
    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por)
    values (v_id, p_id_linea, 'ajuste_quitado',
            jsonb_build_object('fecha', l.fecha, 'hora', l.hora_inicio, 'tipo', l.tipo_nombre,
                               'precio_original', l.precio_original, 'porcentaje', l.porcentaje,
                               'antes', l.importe_final, 'despues', v_base),
            btrim(p_motivo), auth.uid());
    perform public._recalcular_factura(v_id, true);
    return jsonb_build_object('resultado', 'ok', 'version', (select version from public.resumen_mensual where id = v_id));
end;
$$;

-- Igual que antes; además copia el porcentaje y la asistencia de cada línea (el borrador nuevo se pone al día al
-- sincronizar, con la asistencia corregida si la hubo).
create or replace function public.anular_factura(p_id bigint, p_motivo text, p_tipo text default 'administrativa')
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
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
         precio_original, ajuste_tipo, importe_final, porcentaje, asistencia)
    select v_nueva, id_reserva, id_clase, fecha, hora_inicio, id_tipo_clase, tipo_nombre,
           precio_original, ajuste_tipo, importe_final, porcentaje, asistencia
    from public.resumen_mensual_detalle where id_resumen_mensual = p_id;

    insert into public.factura_historial (id_resumen_mensual, id_linea, accion, detalle, motivo, por, en)
    select v_nueva, nueva.id, h.accion, h.detalle || jsonb_build_object('copiado_de', f.numero), h.motivo, h.por, h.en
    from public.factura_historial h
    join public.resumen_mensual_detalle vieja on vieja.id = h.id_linea
    join public.resumen_mensual_detalle nueva on nueva.id_resumen_mensual = v_nueva and nueva.id_reserva = vieja.id_reserva
    where h.id_resumen_mensual = p_id and h.accion in ('ajuste_linea', 'ajuste_quitado', 'cambio_asistencia')
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

-- La superadministradora configura desde cuándo rige el cobro en cada empresa. Nunca hacia atrás: la fecha no puede
-- ser anterior al primer día del mes en curso (lo ya facturado o en revisión no cambia). Vacía = no se aplica.
create or replace function public.configurar_cobro_inasistencia(p_id_empresa bigint, p_desde date)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_actual date;
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    select cobro_inasistencia_desde into v_actual from public.empresa where id = p_id_empresa for update;
    if not found then
        return 'no_existe';
    end if;
    if p_desde is not distinct from v_actual then
        return 'sin_cambios';
    end if;
    if p_desde is not null and p_desde < date_trunc('month', public._hoy_mvd())::date then
        return 'fecha_retroactiva';
    end if;
    if v_actual is not null and v_actual < date_trunc('month', public._hoy_mvd())::date then
        return 'ya_vigente';
    end if;
    update public.empresa set cobro_inasistencia_desde = p_desde where id = p_id_empresa;
    return 'ok';
end;
$$;

revoke execute on function public.configurar_cobro_inasistencia(bigint, date) from public, anon;
grant execute on function public.configurar_cobro_inasistencia(bigint, date) to authenticated;
