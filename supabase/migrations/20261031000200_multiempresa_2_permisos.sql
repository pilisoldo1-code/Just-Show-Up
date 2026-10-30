-- Multiempresa, parte 2: permisos por empresa (diseño: MULTIEMPRESA_DISENO.md).
-- - Funciones de ayuda: empresa_activa() (solo si la membresía y la empresa están activas),
--   es_superadmin(), es_admin(), es_docente(), es_alumna_activa(); es_profesora() pasa a ser es_docente()
--   (mismo nombre para no tocar las ~40 funciones que la usan).
-- - Políticas de lectura por empresa activa y rol en todas las tablas; facturación y pagos solo para la
--   administradora; la profesora no administradora ve lo de sus clases.
-- - Protección general (_proteger_empresa): toda alta, modificación o baja con sesión sobre una fila de
--   otra empresa se rechaza, aunque venga de una función security definer.
-- - Integridad: cada fila hija pertenece a la misma empresa que su fila madre (claves compuestas).
-- - Índices únicos por empresa (nombre de tipo de clase; una factura vigente por alumna, empresa y mes).
-- - Contador de facturas propio de cada empresa (la Empresa 1 continúa el contador global actual).
-- - Cierre del acceso sin sesión (anon) a horario, clase, reserva y listadeespera.
-- Compatible con el código actual: todas las cuentas tienen la Empresa 1 como empresa activa.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_multiempresa_2.sql

-- ---------- funciones de ayuda ----------
create or replace function public.empresa_activa()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select p.empresa_activa
    from public.profiles p
    join public.membresia m on m.id_usuario = p.id and m.id_empresa = p.empresa_activa and m.activa
    join public.empresa e on e.id = p.empresa_activa and e.activa
    where p.id = (select auth.uid());
$$;

create or replace function public.es_superadmin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce((select es_superadmin from public.profiles where id = (select auth.uid())), false);
$$;

create or replace function public._membresia_activa()
returns public.membresia
language sql
stable
security definer
set search_path = ''
as $$
    select m.* from public.membresia m
    where m.id_usuario = (select auth.uid()) and m.id_empresa = public.empresa_activa() and m.activa;
$$;

create or replace function public.es_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce((public._membresia_activa()).es_admin, false);
$$;

create or replace function public.es_docente()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce((public._membresia_activa()).es_admin or (public._membresia_activa()).es_profesora, false);
$$;

create or replace function public.es_alumna_activa()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce((public._membresia_activa()).es_alumna, false);
$$;

-- Antes: profiles.rol = 'profesora'. Ahora: administradora o profesora de la empresa activa.
create or replace function public.es_profesora()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.es_docente();
$$;

-- La clase la dicta quien la llama (o es la administradora).
create or replace function public.puede_gestionar_clase(p_id_clase bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.es_admin()
        or (public.es_docente() and exists (select 1 from public.clase c
                                           where c.id = p_id_clase and c.id_empresa = public.empresa_activa()
                                             and c.id_profesora = (select auth.uid())));
$$;

create or replace function public.puede_gestionar_horario(p_id_horario bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.es_admin()
        or (public.es_docente() and exists (select 1 from public.horario h
                                           where h.id = p_id_horario and h.id_empresa = public.empresa_activa()
                                             and h.id_profesora = (select auth.uid())));
$$;

-- ---------- integridad: la fila hija es de la misma empresa que la madre ----------
alter table public.tipo_clase add constraint tipo_clase_id_empresa_unico unique (id, id_empresa);
alter table public.horario add constraint horario_id_empresa_unico unique (id, id_empresa);
alter table public.clase add constraint clase_id_empresa_unico unique (id, id_empresa);
alter table public.reserva add constraint reserva_id_empresa_unico unique (id, id_empresa);
alter table public.resumen_mensual add constraint resumen_mensual_id_empresa_unico unique (id, id_empresa);
alter table public.pago add constraint pago_id_empresa_unico unique (id, id_empresa);

alter table public.horario add constraint horario_tipo_misma_empresa
    foreign key (id_tipo_clase, id_empresa) references public.tipo_clase (id, id_empresa);
alter table public.clase add constraint clase_horario_misma_empresa
    foreign key (id_horario, id_empresa) references public.horario (id, id_empresa);
alter table public.clase add constraint clase_tipo_misma_empresa
    foreign key (id_tipo_clase, id_empresa) references public.tipo_clase (id, id_empresa);
alter table public.reserva add constraint reserva_clase_misma_empresa
    foreign key (id_clase, id_empresa) references public.clase (id, id_empresa);
alter table public.listadeespera add constraint listadeespera_clase_misma_empresa
    foreign key (id_clase, id_empresa) references public.clase (id, id_empresa);
alter table public.asistencia add constraint asistencia_reserva_misma_empresa
    foreign key (id_reserva, id_empresa) references public.reserva (id, id_empresa);
alter table public.bloqueo add constraint bloqueo_horario_misma_empresa
    foreign key (id_horario, id_empresa) references public.horario (id, id_empresa);
alter table public.resumen_mensual add constraint resumen_mensual_reemplaza_misma_empresa
    foreign key (reemplaza_a, id_empresa) references public.resumen_mensual (id, id_empresa);
alter table public.resumen_mensual_detalle add constraint detalle_factura_misma_empresa
    foreign key (id_resumen_mensual, id_empresa) references public.resumen_mensual (id, id_empresa);
alter table public.resumen_mensual_detalle add constraint detalle_reserva_misma_empresa
    foreign key (id_reserva, id_empresa) references public.reserva (id, id_empresa);
alter table public.factura_historial add constraint historial_factura_misma_empresa
    foreign key (id_resumen_mensual, id_empresa) references public.resumen_mensual (id, id_empresa);
alter table public.pago add constraint pago_factura_misma_empresa
    foreign key (id_resumen_mensual, id_empresa) references public.resumen_mensual (id, id_empresa);
alter table public.pago_anulacion add constraint pago_anulacion_pago_misma_empresa
    foreign key (id_pago, id_empresa) references public.pago (id, id_empresa);
alter table public.mp_intento add constraint mp_intento_factura_misma_empresa
    foreign key (id_resumen_mensual, id_empresa) references public.resumen_mensual (id, id_empresa);

-- ---------- únicos por empresa ----------
drop index public.tipo_clase_nombre_unico;
create unique index tipo_clase_nombre_unico on public.tipo_clase (id_empresa, lower(btrim(nombre)));
drop index public.resumen_mensual_una_vigente;
create unique index resumen_mensual_una_vigente on public.resumen_mensual (id_empresa, id_alumna, mes, anio)
    where estado_factura <> 'anulada';

-- ---------- protección general ----------
-- Con sesión de usuaria (auth.uid() no nulo), solo se escriben filas de la empresa activa. Sin sesión
-- (servidor con la clave de administrador, migraciones) no se aplica: esos caminos no los usa una alumna.
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

do $$
declare
    t text;
begin
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento']
    loop
        -- "zz_" para que corra después de los triggers que completan la empresa
        execute format('create trigger zz_proteger_empresa before insert or update or delete on public.%I
                        for each row execute function public._proteger_empresa()', t);
    end loop;
end;
$$;

-- ---------- cierre del acceso sin sesión ----------
drop policy "Allow anon read" on public.horario;
drop policy "Allow anon insert" on public.horario;
drop policy "Allow anon update" on public.horario;
drop policy "Allow anon delete" on public.horario;
drop policy "anon select" on public.clase;
drop policy "anon insert" on public.clase;
drop policy "anon update" on public.clase;
drop policy "anon select" on public.reserva;
drop policy "anon insert" on public.reserva;
drop policy "anon update" on public.reserva;
drop policy "anon select" on public.listadeespera;
drop policy "anon insert" on public.listadeespera;
drop policy "anon update" on public.listadeespera;
revoke all on public.horario, public.clase, public.reserva, public.listadeespera, public.tipo_clase,
    public.asistencia, public.bloqueo, public.profiles from anon;
revoke insert, update, delete, truncate on public.reserva, public.listadeespera from authenticated;

-- ---------- políticas por empresa ----------
drop policy "logueadas: leer tipos de clase" on public.tipo_clase;
create policy "tipo_clase: empresa activa" on public.tipo_clase for select to authenticated
    using (id_empresa = public.empresa_activa());

drop policy "logueadas: leer horarios" on public.horario;
drop policy "profesora: crear horarios" on public.horario;
drop policy "profesora: editar horarios" on public.horario;
drop policy "profesora: borrar horarios" on public.horario;
create policy "horario: empresa activa" on public.horario for select to authenticated
    using (id_empresa = public.empresa_activa());
create policy "horario: crear (admin o la profesora asignada)" on public.horario for insert to authenticated
    with check (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))));
create policy "horario: editar (admin o la profesora asignada)" on public.horario for update to authenticated
    using (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))))
    with check (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))));
create policy "horario: borrar (admin o la profesora asignada)" on public.horario for delete to authenticated
    using (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))));

drop policy "logueadas: leer clases" on public.clase;
drop policy "profesora: crear clases" on public.clase;
drop policy "profesora: editar clases" on public.clase;
create policy "clase: empresa activa" on public.clase for select to authenticated
    using (id_empresa = public.empresa_activa());
create policy "clase: crear (admin o la profesora asignada)" on public.clase for insert to authenticated
    with check (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))));
create policy "clase: editar (admin o la profesora asignada)" on public.clase for update to authenticated
    using (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))))
    with check (id_empresa = public.empresa_activa() and (public.es_admin() or (public.es_docente() and id_profesora = (select auth.uid()))));

drop policy "reserva: leer propias o profesora" on public.reserva;
create policy "reserva: propias, de mis clases o admin" on public.reserva for select to authenticated
    using (id_empresa = public.empresa_activa()
           and (id_alumna = (select auth.uid()) or public.es_admin()
                or (public.es_docente() and exists (select 1 from public.clase c where c.id = id_clase and c.id_profesora = (select auth.uid())))));

drop policy "lista de espera: leer propias o profesora" on public.listadeespera;
create policy "listadeespera: propias, de mis clases o admin" on public.listadeespera for select to authenticated
    using (id_empresa = public.empresa_activa()
           and (id_alumna = (select auth.uid()) or public.es_admin()
                or (public.es_docente() and exists (select 1 from public.clase c where c.id = id_clase and c.id_profesora = (select auth.uid())))));

drop policy "asistencia: leer propias o profesora" on public.asistencia;
create policy "asistencia: propias, de mis clases o admin" on public.asistencia for select to authenticated
    using (id_empresa = public.empresa_activa()
           and (public.es_admin()
                or exists (select 1 from public.reserva r join public.clase c on c.id = r.id_clase
                           where r.id = id_reserva
                             and (r.id_alumna = (select auth.uid())
                                  or (public.es_docente() and c.id_profesora = (select auth.uid()))))));

drop policy "bloqueo: leer profesora" on public.bloqueo;
create policy "bloqueo: admin o de mis horarios" on public.bloqueo for select to authenticated
    using (id_empresa = public.empresa_activa()
           and (public.es_admin()
                or (public.es_docente() and exists (select 1 from public.horario h where h.id = id_horario and h.id_profesora = (select auth.uid())))));

drop policy "resumen_mensual: profesora o propia publicada" on public.resumen_mensual;
create policy "resumen_mensual: admin o propia publicada" on public.resumen_mensual for select to authenticated
    using (id_empresa = public.empresa_activa()
           and (public.es_admin()
                or (id_alumna = (select auth.uid())
                    and ((estado_factura = 'confirmada' and public._ahora() >= public._inicio_publicacion(mes, anio))
                         or (estado_factura = 'anulada' and anulada_en >= public._inicio_publicacion(mes, anio))))));

drop policy "resumen_mensual_detalle: si se ve la factura" on public.resumen_mensual_detalle;
create policy "resumen_mensual_detalle: si se ve la factura" on public.resumen_mensual_detalle for select to authenticated
    using (id_empresa = public.empresa_activa()
           and exists (select 1 from public.resumen_mensual rm where rm.id = id_resumen_mensual));

drop policy "pago: profesora o vigente de una factura visible" on public.pago;
create policy "pago: admin o vigente de una factura visible" on public.pago for select to authenticated
    using (id_empresa = public.empresa_activa()
           and (public.es_admin()
                or (estado = 'vigente' and exists (select 1 from public.resumen_mensual rm where rm.id = id_resumen_mensual))));

drop policy "factura_historial: solo profesora" on public.factura_historial;
create policy "factura_historial: admin" on public.factura_historial for select to authenticated
    using (id_empresa = public.empresa_activa() and public.es_admin());

drop policy "pago_anulacion: solo profesora" on public.pago_anulacion;
create policy "pago_anulacion: admin" on public.pago_anulacion for select to authenticated
    using (id_empresa = public.empresa_activa() and public.es_admin());

drop policy "mp_intento: profesora o propio" on public.mp_intento;
create policy "mp_intento: admin o propio" on public.mp_intento for select to authenticated
    using (id_empresa = public.empresa_activa() and (public.es_admin() or id_alumna = (select auth.uid())));

drop policy "mp_evento: solo profesora" on public.mp_evento;
create policy "mp_evento: admin" on public.mp_evento for select to authenticated
    using (id_empresa = public.empresa_activa() and public.es_admin());

drop policy "profesora: leer todos los perfiles" on public.profiles;
create policy "perfiles: docentes leen a las personas de su empresa" on public.profiles for select to authenticated
    using (public.es_docente()
           and exists (select 1 from public.membresia m where m.id_usuario = profiles.id and m.id_empresa = public.empresa_activa()));

create policy "empresa: superadmin o mis empresas" on public.empresa for select to authenticated
    using (public.es_superadmin()
           or exists (select 1 from public.membresia m where m.id_empresa = empresa.id and m.id_usuario = (select auth.uid()) and m.activa));

create policy "membresia: propias o de mi empresa (docentes)" on public.membresia for select to authenticated
    using (id_usuario = (select auth.uid()) or (public.es_docente() and id_empresa = public.empresa_activa()));

-- ---------- contador de facturas por empresa ----------
-- La Empresa 1 continúa el contador global (la próxima factura conserva el número que tendría hoy).
update public.empresa
set ultimo_numero_factura = (select case when is_called then last_value else last_value - 1 end from public.factura_numero_seq)
where codigo = 'JSU';

create or replace function public._siguiente_numero_factura(p_id_empresa bigint)
returns int
language sql
security definer
set search_path = ''
as $$
    update public.empresa set ultimo_numero_factura = ultimo_numero_factura + 1
    where id = p_id_empresa
    returning ultimo_numero_factura;
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
        numero = (select codigo from public.empresa where id = f.id_empresa) || '-' || f.anio || '-'
                 || lpad(f.mes::text, 2, '0') || '-' || lpad(public._siguiente_numero_factura(f.id_empresa)::text, 4, '0'),
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

-- ---------- permisos de las funciones ----------
revoke execute on function public.empresa_activa(), public.es_superadmin(), public._membresia_activa(),
    public.es_admin(), public.es_docente(), public.es_alumna_activa(), public.puede_gestionar_clase(bigint),
    public.puede_gestionar_horario(bigint) from public, anon;
grant execute on function public.empresa_activa(), public.es_superadmin(), public._membresia_activa(),
    public.es_admin(), public.es_docente(), public.es_alumna_activa(), public.puede_gestionar_clase(bigint),
    public.puede_gestionar_horario(bigint) to authenticated;
revoke execute on function public._proteger_empresa(), public._siguiente_numero_factura(bigint),
    public._confirmar(bigint) from public, anon, authenticated;
