-- Multiempresa, parte 4: datos personales protegidos en la base.
-- La cédula, la fecha de nacimiento y el vencimiento del carné de salud dejan de poder leerse directo
-- de profiles con una sesión: solo con datos_personales(), que devuelve los propios o, a la
-- administradora, los de las personas de su empresa activa. Las profesoras no administradoras siguen
-- viendo nombre y contacto (lo permite la política de profiles de la parte 2). Además, las alumnas
-- pueden ver el nombre (solo el nombre) de las profesoras de su empresa, para saber quién da cada clase.
-- Además:
-- * La CÉDULA pasa a ser de cada empresa (membresia.cedula): una sola cuenta por persona, pero cada
--   administradora carga y ve solo la cédula registrada en su empresa, y la cédula es única solo dentro de
--   la empresa, así un aviso de «cédula repetida» nunca revela que la persona está en otro lugar. Las
--   cédulas actuales pasan a la Empresa 1 (todos los datos de hoy son de ella). profiles.cedula queda sin
--   uso (respaldo para la vuelta atrás).
-- * La empresa sale SOLO de la sesión (se quita el respaldo de profiles.empresa_activa de la parte 3c).
-- Se aplica DESPUÉS de integrar el código nuevo (el viejo lee esas columnas directo).

revoke select on public.profiles from authenticated;
do $$
declare
    v_columnas text;
begin
    select string_agg(quote_ident(attname), ', ' order by attnum) into v_columnas
    from pg_attribute
    where attrelid = 'public.profiles'::regclass and attnum > 0 and not attisdropped
      and attname not in ('cedula', 'fecha_nacimiento', 'fecha_vencimiento_carne_salud');
    execute format('grant select (%s) on public.profiles to authenticated', v_columnas);
end;
$$;

alter table public.membresia add column cedula text check (cedula is null or public._cedula_valida(cedula));
create unique index membresia_cedula_por_empresa on public.membresia (id_empresa, cedula) where cedula is not null;
update public.membresia m set cedula = p.cedula
from public.profiles p
where p.id = m.id_usuario and p.cedula is not null and m.id_empresa = (select id from public.empresa where codigo = 'JSU');
-- membresia: las docentes leen las de su empresa (política de la parte 2), pero la cédula no
revoke select on public.membresia from authenticated;
do $$
declare
    v_columnas text;
begin
    select string_agg(quote_ident(attname), ', ' order by attnum) into v_columnas
    from pg_attribute
    where attrelid = 'public.membresia'::regclass and attnum > 0 and not attisdropped and attname <> 'cedula';
    execute format('grant select (%s) on public.membresia to authenticated', v_columnas);
end;
$$;

create or replace function public.datos_personales(p_ids uuid[])
returns table (id uuid, cedula text, fecha_nacimiento date, fecha_vencimiento_carne_salud date)
language sql
stable
security definer
set search_path = ''
as $$
    select p.id, m.cedula, p.fecha_nacimiento, p.fecha_vencimiento_carne_salud
    from public.profiles p
    left join public.membresia m on m.id_usuario = p.id and m.id_empresa = public.empresa_activa()
    where p.id = any(p_ids)
      and (p.id = (select auth.uid())
           or (public.es_admin()
               and exists (select 1 from public.membresia x
                           where x.id_usuario = p.id and x.id_empresa = public.empresa_activa())));
$$;

-- Cédula de una persona en la empresa activa (solo la administradora). Repetida = solo dentro de la empresa.
create or replace function public.actualizar_cedula(p_id_alumna uuid, p_cedula text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_cedula text := nullif(regexp_replace(coalesce(p_cedula, ''), '[\s.\-]', '', 'g'), '');
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.membresia where id_usuario = p_id_alumna and id_empresa = public.empresa_activa()) then
        return 'no_existe';
    end if;
    if v_cedula is not null and not public._cedula_valida(v_cedula) then
        return 'cedula_invalida';
    end if;
    update public.membresia set cedula = v_cedula where id_usuario = p_id_alumna and id_empresa = public.empresa_activa();
    return 'ok';
exception when unique_violation then
    return 'cedula_repetida';
end;
$$;

-- Para el alta: ¿la cédula ya la tiene otra persona de ESTA empresa? (solo la administradora)
create or replace function public.cedula_disponible(p_cedula text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.es_admin() and not exists (
        select 1 from public.membresia
        where id_empresa = public.empresa_activa()
          and cedula = nullif(regexp_replace(coalesce(p_cedula, ''), '[\s.\-]', '', 'g'), ''));
$$;

-- Al confirmar, la factura copia la cédula registrada en la empresa que factura.
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
        alumna_nombre = a.nombre, alumna_apellido = a.apellido, alumna_cedula = (select m.cedula from public.membresia m where m.id_usuario = f.id_alumna and m.id_empresa = f.id_empresa),
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
                                       and (select m.cedula from public.membresia m where m.id_usuario = id_alumna and m.id_empresa = public.empresa_activa()) is null),
        'firma', public._firma_mes(p_mes, p_anio))
    from public.resumen_mensual
    where mes = p_mes and anio = p_anio and estado_factura = 'borrador' and id_empresa = public.empresa_activa();
end;
$$;

-- Sin la compatibilidad con profiles.rol: desde el código nuevo los roles salen solo de la membresía (los da la
-- administradora o la superadministradora), así ningún cambio en profiles puede dar permisos.
drop trigger membresia_desde_rol on public.profiles;
drop function public._membresia_desde_rol();

-- La empresa sale solo de la sesión (todas las sesiones del código nuevo eligen su lugar al entrar).
create or replace function public.empresa_activa()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select public.empresa_de_sesion();
$$;

create or replace function public.profesoras_empresa()
returns table (id uuid, nombre text)
language sql
stable
security definer
set search_path = ''
as $$
    select p.id, btrim(p.nombre || ' ' || p.apellido)
    from public.membresia m
    join public.profiles p on p.id = m.id_usuario
    where m.id_empresa = public.empresa_activa()
      and (m.es_profesora or m.es_admin)
      and exists (select 1 from public.membresia yo
                  where yo.id_usuario = (select auth.uid()) and yo.id_empresa = m.id_empresa and yo.activa);
$$;

revoke execute on function public.datos_personales(uuid[]), public.profesoras_empresa(), public.cedula_disponible(text)
    from public, anon;
grant execute on function public.datos_personales(uuid[]), public.profesoras_empresa(), public.cedula_disponible(text)
    to authenticated;
