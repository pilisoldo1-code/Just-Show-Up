-- Multiempresa, parte 3b: acceso por nombre del lugar.
-- La persona entra escribiendo el nombre del lugar, su mail y su contraseña. El nombre se compara por una
-- clave normalizada (sin mayúsculas, tildes, espacios ni signos), que es única entre empresas: «Just Show Up»,
-- «just show up» y «Just-Show Up» son el mismo lugar y no se puede crear otro con un nombre que coincida.
-- entrar_a_empresa() valida en un solo paso que el lugar exista y esté activo y que la persona tenga una
-- membresía activa con algún rol; ante cualquier falla devuelve lo mismo (no revela qué falló).
-- Compatible con el código anterior: se aplica junto con las partes 1 a 3, antes del merge.

create or replace function public._clave_lugar(p_nombre text)
returns text
language sql
immutable
parallel safe
set search_path = ''
as $$
    select regexp_replace(
        lower(translate(coalesce(p_nombre, ''),
                        'ÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇáàäâãéèëêíìïîóòöôõúùüûñç',
                        'AAAAAEEEEIIIIOOOOOUUUUNCaaaaaeeeeiiiiooooouuuunc')),
        '[^a-z0-9]', '', 'g');
$$;

alter table public.empresa add constraint empresa_nombre_con_letras check (length(public._clave_lugar(nombre)) >= 2);
create unique index empresa_nombre_lugar_unico on public.empresa (public._clave_lugar(nombre));

create or replace function public.entrar_a_empresa(p_lugar text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint;
begin
    if auth.uid() is null or length(public._clave_lugar(p_lugar)) < 2 then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select e.id into v_empresa
    from public.empresa e
    join public.membresia m on m.id_empresa = e.id and m.id_usuario = auth.uid() and m.activa
                           and (m.es_admin or m.es_profesora or m.es_alumna)
    where e.activa and public._clave_lugar(e.nombre) = public._clave_lugar(p_lugar);
    if v_empresa is null then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    update public.profiles set empresa_activa = v_empresa where id = auth.uid();
    return jsonb_build_object('resultado', 'ok', 'id', v_empresa);
end;
$$;

-- Crear y editar empresas: el nombre del lugar también tiene que ser único (por su clave).
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
    if length(public._clave_lugar(p_nombre)) < 2 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    if exists (select 1 from public.empresa where public._clave_lugar(nombre) = public._clave_lugar(p_nombre)) then
        return jsonb_build_object('resultado', 'nombre_repetido');
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
    if length(public._clave_lugar(p_nombre)) < 2 then
        return 'datos_invalidos';
    end if;
    if exists (select 1 from public.empresa where id <> p_id and public._clave_lugar(nombre) = public._clave_lugar(p_nombre)) then
        return 'nombre_repetido';
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

revoke execute on function public.entrar_a_empresa(text) from public, anon;
grant execute on function public.entrar_a_empresa(text) to authenticated;
