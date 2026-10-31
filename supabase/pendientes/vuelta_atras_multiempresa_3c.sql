-- Vuelta atrás de la parte 3c de multiempresa: la empresa vuelve a ser de la cuenta (profiles.empresa_activa).
begin;
create or replace function public.empresa_activa()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select p.empresa_activa
    from public.profiles p
    join public.membresia m on m.id_usuario = p.id and m.id_empresa = p.empresa_activa and m.activa
    join public.empresa e on e.id = p.empresa_activa and e.activa
    where p.id = (select auth.uid());
$$;

create or replace function public._empresa_activa_guardada()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select empresa_activa from public.profiles where id = auth.uid();
$$;

create or replace function public.elegir_empresa(p_id_empresa bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if auth.uid() is null then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.membresia m join public.empresa e on e.id = m.id_empresa
                   where m.id_usuario = auth.uid() and m.id_empresa = p_id_empresa and m.activa and e.activa) then
        return 'no_autorizada';
    end if;
    update public.profiles set empresa_activa = p_id_empresa where id = auth.uid();
    return 'ok';
end;
$$;

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

drop function public.salir_de_empresa();
drop function public.empresa_de_sesion();
drop function public._sesion_actual();
drop table public.sesion_empresa;
delete from supabase_migrations.schema_migrations where name = 'multiempresa_3c_sesiones';
commit;
