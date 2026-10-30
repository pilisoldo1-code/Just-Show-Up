-- Multiempresa, parte 4: datos personales protegidos en la base.
-- La cédula, la fecha de nacimiento y el vencimiento del carné de salud dejan de poder leerse directo
-- de profiles con una sesión: solo con datos_personales(), que devuelve los propios o, a la
-- administradora, los de las personas de su empresa activa. Las profesoras no administradoras siguen
-- viendo nombre y contacto (lo permite la política de profiles de la parte 2). Además, las alumnas
-- pueden ver el nombre (solo el nombre) de las profesoras de su empresa, para saber quién da cada clase.
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

create or replace function public.datos_personales(p_ids uuid[])
returns table (id uuid, cedula text, fecha_nacimiento date, fecha_vencimiento_carne_salud date)
language sql
stable
security definer
set search_path = ''
as $$
    select p.id, p.cedula, p.fecha_nacimiento, p.fecha_vencimiento_carne_salud
    from public.profiles p
    where p.id = any(p_ids)
      and (p.id = (select auth.uid())
           or (public.es_admin()
               and exists (select 1 from public.membresia m
                           where m.id_usuario = p.id and m.id_empresa = public.empresa_activa())));
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

revoke execute on function public.datos_personales(uuid[]), public.profesoras_empresa() from public, anon;
grant execute on function public.datos_personales(uuid[]), public.profesoras_empresa() to authenticated;
