-- VUELTA ATRÁS de 20261031000200_multiempresa_2_permisos.sql (NO es una migración).
-- Restaura las políticas, permisos, índices y funciones anteriores (incluido el acceso sin sesión que
-- existía antes) y quita la protección general y las claves compuestas. Devuelve el contador global de
-- facturas al valor del contador de la Empresa 1. Se niega si hay más de una empresa (sin la parte 2,
-- sus datos quedarían visibles para las demás). Todo o nada.
-- Correr en el SQL Editor de Supabase solo si Julieta lo autoriza.

begin;

do $$
begin
    if (select count(*) from public.empresa) > 1 then
        raise exception 'Hay más de una empresa: sin la parte 2 sus datos quedarían expuestos. No se hizo nada.';
    end if;
end;
$$;

-- contador: el global vuelve a continuar donde quedó la Empresa 1
select case when ultimo_numero_factura > 0 then setval('public.factura_numero_seq', ultimo_numero_factura, true)
            else setval('public.factura_numero_seq', 1, false) end
from public.empresa where codigo = 'JSU';
update public.empresa set ultimo_numero_factura = 0;

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

create or replace function public.es_profesora()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.profiles
        where id = (select auth.uid()) and rol = 'profesora'
    );
$$;

do $$
declare
    t text;
begin
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento']
    loop
        execute format('drop trigger zz_proteger_empresa on public.%I', t);
    end loop;
end;
$$;

-- políticas nuevas
drop policy "tipo_clase: empresa activa" on public.tipo_clase;
drop policy "horario: empresa activa" on public.horario;
drop policy "horario: crear (admin o la profesora asignada)" on public.horario;
drop policy "horario: editar (admin o la profesora asignada)" on public.horario;
drop policy "horario: borrar (admin o la profesora asignada)" on public.horario;
drop policy "clase: empresa activa" on public.clase;
drop policy "clase: crear (admin o la profesora asignada)" on public.clase;
drop policy "clase: editar (admin o la profesora asignada)" on public.clase;
drop policy "reserva: propias, de mis clases o admin" on public.reserva;
drop policy "listadeespera: propias, de mis clases o admin" on public.listadeespera;
drop policy "asistencia: propias, de mis clases o admin" on public.asistencia;
drop policy "bloqueo: admin o de mis horarios" on public.bloqueo;
drop policy "resumen_mensual: admin o propia publicada" on public.resumen_mensual;
drop policy "resumen_mensual_detalle: si se ve la factura" on public.resumen_mensual_detalle;
drop policy "pago: admin o vigente de una factura visible" on public.pago;
drop policy "factura_historial: admin" on public.factura_historial;
drop policy "pago_anulacion: admin" on public.pago_anulacion;
drop policy "mp_intento: admin o propio" on public.mp_intento;
drop policy "mp_evento: admin" on public.mp_evento;
drop policy "perfiles: docentes leen a las personas de su empresa" on public.profiles;
drop policy "empresa: superadmin o mis empresas" on public.empresa;
drop policy "membresia: propias o de mi empresa (docentes)" on public.membresia;

-- políticas anteriores
create policy "logueadas: leer tipos de clase" on public.tipo_clase for select to authenticated using (true);
create policy "logueadas: leer horarios" on public.horario for select to authenticated using (true);
create policy "profesora: crear horarios" on public.horario for insert to authenticated with check (public.es_profesora());
create policy "profesora: editar horarios" on public.horario for update to authenticated using (public.es_profesora()) with check (public.es_profesora());
create policy "profesora: borrar horarios" on public.horario for delete to authenticated using (public.es_profesora());
create policy "logueadas: leer clases" on public.clase for select to authenticated using (true);
create policy "profesora: crear clases" on public.clase for insert to authenticated with check (public.es_profesora());
create policy "profesora: editar clases" on public.clase for update to authenticated using (public.es_profesora()) with check (public.es_profesora());
create policy "reserva: leer propias o profesora" on public.reserva for select to authenticated
    using (id_alumna = (select auth.uid()) or public.es_profesora());
create policy "lista de espera: leer propias o profesora" on public.listadeespera for select to authenticated
    using (id_alumna = (select auth.uid()) or public.es_profesora());
create policy "asistencia: leer propias o profesora" on public.asistencia for select to authenticated
    using (public.es_profesora() or exists (select 1 from public.reserva r where r.id = asistencia.id_reserva and r.id_alumna = (select auth.uid())));
create policy "bloqueo: leer profesora" on public.bloqueo for select to authenticated using (public.es_profesora());
create policy "resumen_mensual: profesora o propia publicada" on public.resumen_mensual for select to authenticated
    using (public.es_profesora()
           or (id_alumna = (select auth.uid())
               and ((estado_factura = 'confirmada' and public._ahora() >= public._inicio_publicacion(mes, anio))
                    or (estado_factura = 'anulada' and anulada_en >= public._inicio_publicacion(mes, anio)))));
create policy "resumen_mensual_detalle: si se ve la factura" on public.resumen_mensual_detalle for select to authenticated
    using (exists (select 1 from public.resumen_mensual rm where rm.id = id_resumen_mensual));
create policy "pago: profesora o vigente de una factura visible" on public.pago for select to authenticated
    using (public.es_profesora()
           or (estado = 'vigente' and exists (select 1 from public.resumen_mensual rm where rm.id = id_resumen_mensual)));
create policy "factura_historial: solo profesora" on public.factura_historial for select to authenticated using (public.es_profesora());
create policy "pago_anulacion: solo profesora" on public.pago_anulacion for select to authenticated using (public.es_profesora());
create policy "mp_intento: profesora o propio" on public.mp_intento for select to authenticated
    using (public.es_profesora() or id_alumna = (select auth.uid()));
create policy "mp_evento: solo profesora" on public.mp_evento for select to authenticated using (public.es_profesora());
create policy "profesora: leer todos los perfiles" on public.profiles for select to authenticated using (public.es_profesora());

-- acceso sin sesión anterior
create policy "Allow anon read" on public.horario for select to anon using (true);
create policy "Allow anon insert" on public.horario for insert to anon with check (true);
create policy "Allow anon update" on public.horario for update to anon using (true) with check (true);
create policy "Allow anon delete" on public.horario for delete to anon using (true);
create policy "anon select" on public.clase for select to anon using (true);
create policy "anon insert" on public.clase for insert to anon with check (true);
create policy "anon update" on public.clase for update to anon using (true) with check (true);
create policy "anon select" on public.reserva for select to anon using (true);
create policy "anon insert" on public.reserva for insert to anon with check (true);
create policy "anon update" on public.reserva for update to anon using (true) with check (true);
create policy "anon select" on public.listadeespera for select to anon using (true);
create policy "anon insert" on public.listadeespera for insert to anon with check (true);
create policy "anon update" on public.listadeespera for update to anon using (true) with check (true);
grant all on public.horario, public.clase, public.reserva, public.listadeespera to anon;
grant insert, update, delete, truncate on public.reserva, public.listadeespera to authenticated;

-- índices únicos anteriores
drop index public.tipo_clase_nombre_unico;
create unique index tipo_clase_nombre_unico on public.tipo_clase (lower(trim(nombre)));
drop index public.resumen_mensual_una_vigente;
create unique index resumen_mensual_una_vigente on public.resumen_mensual (id_alumna, mes, anio)
    where estado_factura <> 'anulada';

-- claves compuestas
do $$
declare
    t text;
begin
    foreach t in array array['horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo', 'resumen_mensual',
                             'resumen_mensual_detalle', 'factura_historial', 'pago', 'mp_intento', 'pago_anulacion'] loop
        execute format('drop trigger validar_empresa_madre on public.%I', t);
    end loop;
end;
$$;
drop function public._validar_empresa_madre();

drop function public._proteger_empresa();
drop function public._siguiente_numero_factura(bigint);
drop function public.puede_gestionar_horario(bigint);
drop function public.puede_gestionar_clase(bigint);
drop function public.es_alumna_activa();
drop function public.es_docente();
drop function public.es_admin();
drop function public._membresia_activa();
drop function public.es_superadmin();
drop function public.empresa_activa();

revoke execute on function public._confirmar(bigint) from public, anon, authenticated;

delete from supabase_migrations.schema_migrations where name = 'multiempresa_2_permisos';

commit;
