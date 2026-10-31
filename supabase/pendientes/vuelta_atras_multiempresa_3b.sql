-- Vuelta atrás de la parte 3b de multiempresa (acceso por nombre del lugar): deja crear_empresa y
-- editar_empresa como en la parte 3 y quita la clave del lugar. Todo o nada.
begin;
drop function public.entrar_a_empresa(text);
drop index public.empresa_nombre_lugar_unico;
alter table public.empresa drop constraint empresa_nombre_con_letras;

create or replace function public.crear_empresa(p_nombre text, p_codigo text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
begin
    if not public.es_superadmin() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if length(btrim(coalesce(p_nombre, ''))) not between 2 and 80 or coalesce(p_codigo, '') !~ '^[A-Z]{2,6}$' then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if exists (select 1 from public.empresa where codigo = p_codigo) then
        return jsonb_build_object('resultado', 'codigo_repetido');
    end if;
    insert into public.empresa (nombre, codigo) values (btrim(p_nombre), p_codigo) returning id into v_id;
    return jsonb_build_object('resultado', 'ok', 'id', v_id);
end;
$$;

create or replace function public.editar_empresa(p_id bigint, p_nombre text, p_codigo text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    e public.empresa;
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    select * into e from public.empresa where id = p_id for update;
    if not found then
        return 'no_existe';
    end if;
    if length(btrim(coalesce(p_nombre, ''))) not between 2 and 80 or coalesce(p_codigo, '') !~ '^[A-Z]{2,6}$' then
        return 'datos_invalidos';
    end if;
    if p_codigo <> e.codigo and (e.ultimo_numero_factura > 0
                                 or exists (select 1 from public.resumen_mensual where id_empresa = p_id and numero is not null)) then
        return 'codigo_fijo';
    end if;
    if p_codigo <> e.codigo and exists (select 1 from public.empresa where codigo = p_codigo) then
        return 'codigo_repetido';
    end if;
    update public.empresa set nombre = btrim(p_nombre), codigo = p_codigo where id = p_id;
    return 'ok';
end;
$$;

drop function public._clave_lugar(text);
delete from supabase_migrations.schema_migrations where name = 'multiempresa_3b_acceso';
commit;
