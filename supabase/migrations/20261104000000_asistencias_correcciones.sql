-- Asistencias: correcciones de la administradora, historial y pendientes.
-- - La profesora sigue marcando solo mientras dura la clase (marcar_asistencia, mismas reglas).
-- - La administradora completa o corrige asistencias de clases ya terminadas, con motivo obligatorio y todo o nada
--   (corregir_asistencias). Nunca sobre clases canceladas ni sobre reservas de un mes con la factura de esa alumna
--   confirmada. Solo presente o ausente: no se vuelve a «sin registrar» y nada se marca solo.
-- - Cada cambio (durante la clase o corrección) queda en asistencia_historial, que solo lee la administradora.
-- - Pendientes = reservas confirmadas de clases programadas ya terminadas sin asistencia (resumen_asistencias_mes,
--   asistencias_pendientes_resumen).
-- Vuelta atrás: supabase/pendientes/vuelta_atras_asistencias_correcciones.sql

create table public.asistencia_historial (
    id bigint generated always as identity primary key,
    id_empresa bigint not null references public.empresa(id),
    id_reserva bigint not null references public.reserva(id),
    estado_anterior text check (estado_anterior in ('presente', 'ausente')),
    estado_nuevo text not null check (estado_nuevo in ('presente', 'ausente')),
    origen text not null check (origen in ('clase', 'correccion')),
    motivo text check (origen = 'clase' or btrim(coalesce(motivo, '')) <> ''),
    por uuid not null references public.profiles(id),
    creado_en timestamptz not null default now()
);

create index asistencia_historial_reserva on public.asistencia_historial (id_reserva);

alter table public.asistencia_historial enable row level security;
revoke all on public.asistencia_historial from anon, authenticated;
grant select on public.asistencia_historial to authenticated;
create policy "asistencia_historial: solo la administradora de la empresa" on public.asistencia_historial
    for select to authenticated
    using (id_empresa = public.empresa_activa() and public.es_admin());

create trigger zz_proteger_empresa before insert or update or delete on public.asistencia_historial
    for each row execute function public._proteger_empresa();

-- Igual que antes (solo la docente de la clase o la administradora, solo mientras dura la clase); además deja el
-- cambio en el historial.
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
    v_empresa bigint;
    v_anterior text;
begin
    if not public.es_docente() then
        return 'no_autorizada';
    end if;
    if p_estado not in ('presente', 'ausente') then
        raise exception 'estado inválido' using errcode = '22023';
    end if;
    select public._inicio_clase(c.fecha, c.hora_inicio), c.duracion, c.id, r.id_empresa
    into v_inicio, v_duracion, v_clase, v_empresa
    from public.reserva r join public.clase c on c.id = r.id_clase
    where r.id = p_id_reserva and r.estado = 'confirmada' and r.id_empresa = public.empresa_activa()
    for no key update of r;
    if v_inicio is null then
        return 'sin_reserva';
    end if;
    if not public.puede_gestionar_clase(v_clase) then
        return 'no_autorizada';
    end if;
    if now() < v_inicio or now() > v_inicio + make_interval(mins => v_duracion) then
        return 'fuera_de_horario';
    end if;
    select estado into v_anterior from public.asistencia where id_reserva = p_id_reserva;
    if v_anterior is distinct from p_estado then
        insert into public.asistencia (id_reserva, estado) values (p_id_reserva, p_estado)
        on conflict (id_reserva) do update set estado = excluded.estado;
        insert into public.asistencia_historial (id_empresa, id_reserva, estado_anterior, estado_nuevo, origen, por)
        values (v_empresa, p_id_reserva, v_anterior, p_estado, 'clase', auth.uid());
    end if;
    return 'ok';
end;
$$;

-- Correcciones de la administradora: [{"id_reserva": 1, "estado": "presente"}, ...] con un motivo. Todo o nada.
-- Devuelve {"resultado": ok | no_autorizada | motivo_vacio | sin_cambios | estado_invalido | no_existe |
--           clase_cancelada | no_termino | factura_confirmada, "id_reserva": la que falló, "cambios": n}
create or replace function public.corregir_asistencias(p_cambios jsonb, p_motivo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_item record;
    v_fila record;
    v_anterior text;
    v_cambios integer := 0;
begin
    if not public.es_admin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(btrim(p_motivo), '') = '' then
        return jsonb_build_object('resultado', 'motivo_vacio');
    end if;
    if p_cambios is null or jsonb_typeof(p_cambios) <> 'array' or jsonb_array_length(p_cambios) = 0 then
        return jsonb_build_object('resultado', 'sin_cambios');
    end if;
    for v_item in select x ->> 'estado' as estado from jsonb_array_elements(p_cambios) x loop
        if v_item.estado is null or v_item.estado not in ('presente', 'ausente') then
            return jsonb_build_object('resultado', 'estado_invalido');
        end if;
    end loop;

    -- el mismo candado por mes que la facturación (sincronizar, confirmar, anular): una corrección no se cruza con
    -- la confirmación de las facturas de ese mes
    perform pg_advisory_xact_lock(hashtext('facturas_mes'), m.periodo)
    from (select distinct extract(year from c.fecha)::int * 100 + extract(month from c.fecha)::int as periodo
          from jsonb_array_elements(p_cambios) x
          join public.reserva r on r.id = (x ->> 'id_reserva')::bigint and r.id_empresa = v_empresa
          join public.clase c on c.id = r.id_clase
          order by 1) m;
    perform 1 from public.reserva r
    where r.id in (select (x ->> 'id_reserva')::bigint from jsonb_array_elements(p_cambios) x) and r.id_empresa = v_empresa
    order by r.id for no key update;

    for v_item in select distinct on ((x ->> 'id_reserva')::bigint) (x ->> 'id_reserva')::bigint as id_reserva,
                         x ->> 'estado' as estado
                  from jsonb_array_elements(p_cambios) x order by (x ->> 'id_reserva')::bigint loop
        select r.id, r.estado, r.id_alumna, c.estado as estado_clase, c.fecha,
               public._inicio_clase(c.fecha, c.hora_inicio) + make_interval(mins => c.duracion) as fin
        into v_fila
        from public.reserva r join public.clase c on c.id = r.id_clase
        where r.id = v_item.id_reserva and r.id_empresa = v_empresa;
        if v_fila.id is null or v_fila.estado <> 'confirmada' then
            return jsonb_build_object('resultado', 'no_existe', 'id_reserva', v_item.id_reserva);
        end if;
        if v_fila.estado_clase <> 'programada' then
            return jsonb_build_object('resultado', 'clase_cancelada', 'id_reserva', v_item.id_reserva);
        end if;
        if now() <= v_fila.fin then
            return jsonb_build_object('resultado', 'no_termino', 'id_reserva', v_item.id_reserva);
        end if;
        if exists (select 1 from public.resumen_mensual f
                   where f.id_alumna = v_fila.id_alumna and f.id_empresa = v_empresa
                     and f.anio = extract(year from v_fila.fecha) and f.mes = extract(month from v_fila.fecha)
                     and f.estado_factura = 'confirmada') then
            return jsonb_build_object('resultado', 'factura_confirmada', 'id_reserva', v_item.id_reserva);
        end if;
    end loop;

    for v_item in select distinct on ((x ->> 'id_reserva')::bigint) (x ->> 'id_reserva')::bigint as id_reserva,
                         x ->> 'estado' as estado
                  from jsonb_array_elements(p_cambios) x order by (x ->> 'id_reserva')::bigint loop
        v_anterior := (select estado from public.asistencia where id_reserva = v_item.id_reserva);
        if v_anterior is distinct from v_item.estado then
            insert into public.asistencia (id_reserva, estado, id_empresa) values (v_item.id_reserva, v_item.estado, v_empresa)
            on conflict (id_reserva) do update set estado = excluded.estado;
            insert into public.asistencia_historial (id_empresa, id_reserva, estado_anterior, estado_nuevo, origen, motivo, por)
            values (v_empresa, v_item.id_reserva, v_anterior, v_item.estado, 'correccion', btrim(p_motivo), auth.uid());
            v_cambios := v_cambios + 1;
        end if;
    end loop;
    if v_cambios = 0 then
        return jsonb_build_object('resultado', 'sin_cambios');
    end if;
    return jsonb_build_object('resultado', 'ok', 'cambios', v_cambios);
end;
$$;

-- Clases terminadas de un mes con cuántas reservas tienen y cuántas faltan registrar. La administradora ve las de
-- su empresa; la profesora, solo las suyas; nadie más.
create or replace function public.resumen_asistencias_mes(p_anio integer, p_mes integer)
returns table (id_clase bigint, fecha date, hora_inicio time, duracion integer, tipo text, id_profesora uuid,
               reservas integer, sin_registrar integer)
language sql
stable
security definer
set search_path = ''
as $$
    select c.id, c.fecha, c.hora_inicio, c.duracion, t.nombre, c.id_profesora,
           count(r.id)::integer,
           count(r.id) filter (where not exists (select 1 from public.asistencia a where a.id_reserva = r.id))::integer
    from public.clase c
    left join public.tipo_clase t on t.id = c.id_tipo_clase
    left join public.reserva r on r.id_clase = c.id and r.estado = 'confirmada'
    where c.id_empresa = public.empresa_activa() and c.estado = 'programada'
      and extract(year from c.fecha) = p_anio and extract(month from c.fecha) = p_mes
      and public._inicio_clase(c.fecha, c.hora_inicio) + make_interval(mins => c.duracion) < now()
      and (public.es_admin() or (public.es_docente() and c.id_profesora = auth.uid()))
    group by c.id, c.fecha, c.hora_inicio, c.duracion, t.nombre, c.id_profesora;
$$;

-- Total de pendientes (para el aviso de Inicio), con los mismos permisos.
create or replace function public.asistencias_pendientes_resumen()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object('clases', count(distinct c.id), 'asistencias', count(r.id), 'ultima_fecha', max(c.fecha))
    from public.clase c
    join public.reserva r on r.id_clase = c.id and r.estado = 'confirmada'
    where c.id_empresa = public.empresa_activa() and c.estado = 'programada'
      and public._inicio_clase(c.fecha, c.hora_inicio) + make_interval(mins => c.duracion) < now()
      and not exists (select 1 from public.asistencia a where a.id_reserva = r.id)
      and (public.es_admin() or (public.es_docente() and c.id_profesora = auth.uid()));
$$;

-- De estas reservas, cuáles no se pueden corregir porque la factura de ese mes de la alumna ya está confirmada.
create or replace function public.reservas_con_factura_confirmada(p_ids bigint[])
returns setof bigint
language sql
stable
security definer
set search_path = ''
as $$
    select r.id from public.reserva r join public.clase c on c.id = r.id_clase
    where public.es_admin() and r.id = any(p_ids) and r.id_empresa = public.empresa_activa()
      and exists (select 1 from public.resumen_mensual f
                  where f.id_alumna = r.id_alumna and f.id_empresa = r.id_empresa and f.estado_factura = 'confirmada'
                    and f.anio = extract(year from c.fecha) and f.mes = extract(month from c.fecha));
$$;

-- Historial de una clase con nombres (solo la administradora).
create or replace function public.historial_asistencias_clase(p_id_clase bigint)
returns table (creado_en timestamptz, alumna text, estado_anterior text, estado_nuevo text, origen text, motivo text,
               por text)
language sql
stable
security definer
set search_path = ''
as $$
    select h.creado_en, btrim(pa.nombre || ' ' || pa.apellido), h.estado_anterior, h.estado_nuevo, h.origen, h.motivo,
           btrim(pp.nombre || ' ' || pp.apellido)
    from public.asistencia_historial h
    join public.reserva r on r.id = h.id_reserva
    join public.profiles pa on pa.id = r.id_alumna
    join public.profiles pp on pp.id = h.por
    where public.es_admin() and r.id_clase = p_id_clase and h.id_empresa = public.empresa_activa()
    order by h.creado_en desc, h.id desc;
$$;

revoke execute on function public.corregir_asistencias(jsonb, text), public.resumen_asistencias_mes(integer, integer),
    public.asistencias_pendientes_resumen(), public.reservas_con_factura_confirmada(bigint[]),
    public.historial_asistencias_clase(bigint) from public, anon;
grant execute on function public.corregir_asistencias(jsonb, text), public.resumen_asistencias_mes(integer, integer),
    public.asistencias_pendientes_resumen(), public.reservas_con_factura_confirmada(bigint[]),
    public.historial_asistencias_clase(bigint) to authenticated;
