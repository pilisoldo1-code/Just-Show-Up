-- Cédula de identidad de las alumnas (opcional: las usuarias existentes quedan sin cédula
-- hasta que la profesora la cargue; no se completa nada automáticamente).
-- * Se guarda solo con dígitos (7 u 8, el último es el dígito verificador) y se valida.
-- * No se repite entre usuarias.
-- * Lectura: la protección actual de profiles (cada usuaria su fila, la profesora todas).
-- * Escritura: la alumna NO puede cambiarla (no se agrega al permiso de UPDATE por
--   columna); la carga la profesora con actualizar_cedula o el servidor al dar el alta.

-- Dígito verificador de la cédula uruguaya: pesos 2 9 8 7 6 3 4 sobre los 7 dígitos del
-- número (con ceros a la izquierda); verificador = (10 - suma mod 10) mod 10.
create or replace function public._cedula_valida(p_cedula text)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select p_cedula ~ '^[0-9]{7,8}$'
       and (10 - (
             select sum(substr(lpad(left(p_cedula, -1), 7, '0'), i, 1)::int * (array[2, 9, 8, 7, 6, 3, 4])[i])
             from generate_series(1, 7) i
           )::int % 10) % 10 = right(p_cedula, 1)::int;
$$;

alter table public.profiles add column cedula text check (cedula is null or public._cedula_valida(cedula));
create unique index profiles_cedula_unica on public.profiles (cedula) where cedula is not null;

-- La profesora carga o corrige la cédula de una alumna (p_cedula vacía o null = quitarla).
-- Devuelve: ok | no_autorizada | no_existe | cedula_invalida | cedula_repetida
create or replace function public.actualizar_cedula(p_id_alumna uuid, p_cedula text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_cedula text := nullif(regexp_replace(coalesce(p_cedula, ''), '[\s.\-]', '', 'g'), '');
begin
    if not public.es_profesora() then
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
$$;

revoke execute on function public.actualizar_cedula(uuid, text) from public, anon;
grant execute on function public.actualizar_cedula(uuid, text) to authenticated;
