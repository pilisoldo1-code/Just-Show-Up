-- Vuelta atrás de 20261101000000_tres_roles.sql: deja membresías, funciones y permisos como antes.
-- Todo o nada (una transacción). Las administradoras que tenían también «profesora» la recuperan (las
-- guardadas en _tres_roles_respaldo). Las personas creadas o cambiadas después de la migración quedan con su
-- único rol, que el modelo anterior también acepta.
begin;

alter table public.membresia drop constraint membresia_un_solo_rol;
update public.membresia set es_profesora = true where id in (select id_membresia from public._tres_roles_respaldo);
drop table public._tres_roles_respaldo;

drop function public.administradoras_empresa(bigint);
drop function public.quitar_administradora(bigint, uuid, text);
drop function public.editar_datos_persona(uuid, text, text, text, date, date);
drop function public.pendientes_cambio_rol(uuid);
drop function public.puede_gestionar_persona(uuid);
drop function public._pendientes_docente(uuid, bigint);
drop function public._pendientes_alumna(uuid, bigint);

create or replace function public.asignar_membresia(p_id_usuario uuid, p_es_admin boolean, p_es_profesora boolean, p_es_alumna boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if not (coalesce(p_es_admin, false) or coalesce(p_es_profesora, false) or coalesce(p_es_alumna, false)) then
        return 'sin_rol';
    end if;
    if p_id_usuario = auth.uid() and not coalesce(p_es_admin, false) then
        return 'no_puede_quitarse_admin';
    end if;
    if not exists (select 1 from public.profiles where id = p_id_usuario and not es_superadmin) then
        return 'no_existe';
    end if;
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
    values (p_id_usuario, v_empresa, coalesce(p_es_admin, false), coalesce(p_es_profesora, false), coalesce(p_es_alumna, false))
    on conflict (id_usuario, id_empresa) do update
        set es_admin = excluded.es_admin, es_profesora = excluded.es_profesora, es_alumna = excluded.es_alumna, activa = true;
    update public.profiles set empresa_activa = v_empresa where id = p_id_usuario and empresa_activa is null;
    return 'ok';
end;
$$;

create or replace function public.cambiar_estado_membresia(p_id_usuario uuid, p_activa boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if p_id_usuario = auth.uid() then
        return 'no_puede_desactivarse';
    end if;
    update public.membresia set activa = coalesce(p_activa, false)
    where id_usuario = p_id_usuario and id_empresa = public.empresa_activa();
    if not found then
        return 'no_existe';
    end if;
    return 'ok';
end;
$$;

create or replace function public.asignar_administradora(p_id_empresa bigint, p_id_usuario uuid, p_es_profesora boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.empresa where id = p_id_empresa) then
        return 'no_existe';
    end if;
    if not exists (select 1 from public.profiles where id = p_id_usuario and not es_superadmin) then
        return 'no_existe';
    end if;
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora)
    values (p_id_usuario, p_id_empresa, true, coalesce(p_es_profesora, false))
    on conflict (id_usuario, id_empresa) do update
        set es_admin = true, es_profesora = membresia.es_profesora or coalesce(p_es_profesora, false), activa = true;
    update public.profiles set empresa_activa = p_id_empresa where id = p_id_usuario and empresa_activa is null;
    return 'ok';
end;
$$;

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

create or replace function public._membresia_inicial()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_meta jsonb := (select raw_user_meta_data from auth.users where id = new.id);
    v_empresa bigint;
begin
    if coalesce((v_meta ->> 'sin_empresa')::boolean, false) then
        return new;
    end if;
    v_empresa := coalesce((v_meta ->> 'id_empresa')::bigint, (select id from public.empresa where codigo = 'JSU'));
    if v_empresa is null then
        return new;
    end if;
    if v_meta ? 'rol_empresa' then
        insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
        values (new.id, v_empresa, v_meta ->> 'rol_empresa' in ('admin', 'admin_profesora'),
                v_meta ->> 'rol_empresa' in ('profesora', 'admin_profesora'), v_meta ->> 'rol_empresa' = 'alumna')
        on conflict (id_usuario, id_empresa) do nothing;
    else
        insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
        values (new.id, v_empresa, new.rol = 'profesora', new.rol = 'profesora', new.rol = 'alumna')
        on conflict (id_usuario, id_empresa) do nothing;
    end if;
    update public.profiles set empresa_activa = v_empresa where id = new.id and empresa_activa is null;
    return new;
end;
$$;

commit;
