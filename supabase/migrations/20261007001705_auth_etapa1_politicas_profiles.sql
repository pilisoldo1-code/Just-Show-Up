-- Quien es profesora (security definer: evita recursion de RLS al leer profiles).
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

revoke execute on function public.es_profesora() from public, anon;
grant execute on function public.es_profesora() to authenticated;

-- Permisos por columna: RLS limita filas, no columnas. Sin esto una alumna podria
-- editar su propio rol. Solo se pueden editar los datos personales.
revoke all on public.profiles from anon;
revoke insert, delete, truncate, references, trigger, update on public.profiles from authenticated;
grant update (nombre, apellido, telefono, fecha_nacimiento, fecha_vencimiento_carne_salud)
    on public.profiles to authenticated;

create policy "perfil propio: leer" on public.profiles
    for select to authenticated
    using (id = (select auth.uid()));

create policy "profesora: leer todos los perfiles" on public.profiles
    for select to authenticated
    using (public.es_profesora());

create policy "perfil propio: editar datos personales" on public.profiles
    for update to authenticated
    using (id = (select auth.uid()))
    with check (id = (select auth.uid()));
