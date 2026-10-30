-- Vuelta atrás de la parte 4 de multiempresa: vuelve a permitir leer profiles completo (con las
-- políticas de la parte 2) y quita datos_personales() y profesoras_empresa(). El código nuevo sigue funcionando (tiene
-- respaldo si la función no existe). Todo o nada.
begin;
revoke select on public.profiles from authenticated;  -- también quita los permisos por columna
grant select on public.profiles to authenticated;
drop function public.datos_personales(uuid[]);
drop function public.profesoras_empresa();
delete from supabase_migrations.schema_migrations where name = 'multiempresa_4_datos_personales';
commit;
