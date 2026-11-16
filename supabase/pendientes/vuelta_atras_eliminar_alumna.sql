-- Vuelta atrás de 20261117000000_eliminar_alumna.sql. Todo o nada. Se niega si ya se eliminó alguna alumna (sus
-- cuentas archivadas y la auditoría deben conservarse). «Dejar inactiva» no cambia.
begin;
do $$
begin
    if exists (select 1 from public.alumna_eliminada) or exists (select 1 from public.membresia where eliminada_en is not null) then
        raise exception 'Ya hay alumnas eliminadas: no se deshace (se perdería la auditoría).';
    end if;
end;
$$;
drop function if exists public.alumna_eliminada_correo_liberado(bigint);
drop function if exists public.eliminar_alumna(uuid, text, text);
drop function if exists public.previsualizar_eliminar_alumna(uuid);
drop function if exists public._validar_eliminar_alumna(uuid);
drop function if exists public._resumen_eliminar_alumna(uuid, bigint);
drop table if exists public.alumna_eliminada;
alter table public.membresia drop column if exists eliminada_en;
commit;
