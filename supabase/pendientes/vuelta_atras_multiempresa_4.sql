-- Vuelta atrás de la parte 4 de multiempresa: vuelve a permitir leer profiles y membresia completos (con
-- las políticas de la parte 2), la cédula vuelve a profiles (la de la Empresa 1, si no choca con otra), la
-- empresa vuelve a poder salir de profiles para sesiones viejas y se quitan las funciones nuevas. El código
-- nuevo sigue funcionando (tiene respaldo si las funciones no existen). Todo o nada.
begin;
revoke select on public.profiles from authenticated;  -- también quita los permisos por columna
grant select on public.profiles to authenticated;
revoke select on public.membresia from authenticated;
grant select on public.membresia to authenticated;
update public.profiles p set cedula = m.cedula
from public.membresia m
where m.id_usuario = p.id and m.id_empresa = (select id from public.empresa where codigo = 'JSU')
  and m.cedula is not null and p.cedula is distinct from m.cedula
  and not exists (select 1 from public.profiles q where q.cedula = m.cedula and q.id <> p.id);
drop function public.datos_personales(uuid[]);
drop function public.profesoras_empresa();
drop function public.cedula_disponible(text);

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

create or replace function public.empresa_activa()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select case
        when exists (select 1 from public.sesion_empresa s where s.session_id = public._sesion_actual())
            then public.empresa_de_sesion()
        else (select p.empresa_activa  -- sesiones abiertas con el código anterior
              from public.profiles p
              join public.membresia m on m.id_usuario = p.id and m.id_empresa = p.empresa_activa and m.activa
              join public.empresa e on e.id = p.empresa_activa and e.activa
              where p.id = (select auth.uid()))
    end;
$$;

create or replace function public._membresia_desde_rol()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if exists (select 1 from auth.users where id = new.id and raw_user_meta_data ? 'rol_empresa') then
        return new;
    end if;
    update public.membresia
    set es_admin = new.rol = 'profesora', es_profesora = new.rol = 'profesora', es_alumna = new.rol = 'alumna'
    where id_usuario = new.id and id_empresa = new.empresa_activa;
    return new;
end;
$$;

create trigger membresia_desde_rol after update of rol on public.profiles
    for each row when (old.rol is distinct from new.rol) execute function public._membresia_desde_rol();
revoke execute on function public._membresia_desde_rol() from public, anon, authenticated;

drop index public.membresia_cedula_por_empresa;
alter table public.membresia drop column cedula;
delete from supabase_migrations.schema_migrations where name = 'multiempresa_4_datos_personales';
commit;
