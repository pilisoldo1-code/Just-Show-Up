-- Multiempresa, parte 3c: la empresa es de cada SESIÓN, no de la cuenta.
-- Cada token de Supabase trae `session_id` (firmado: no se puede falsificar; es la fila de auth.sessions de
-- ese dispositivo). sesion_empresa guarda qué lugar eligió cada sesión al iniciar sesión, así una persona
-- puede estar en Just Show Up en una computadora y en Pilates Centro en otro dispositivo sin que una sesión
-- cambie la otra. empresa_activa() lee la sesión; profiles.empresa_activa queda solo como respaldo para
-- las sesiones abiertas con el código anterior (lo quita la parte 4, después del merge).
-- Compatible con el código anterior: se aplica junto con las partes 1 a 3b, antes del merge.

create table public.sesion_empresa (
    session_id uuid primary key,
    id_usuario uuid not null references public.profiles(id) on delete cascade,
    id_empresa bigint not null references public.empresa(id),
    creada_en timestamptz not null default now()
);
create index on public.sesion_empresa (id_usuario);
alter table public.sesion_empresa enable row level security;  -- sin políticas: solo las funciones la usan
revoke all on public.sesion_empresa from public, anon, authenticated;

create or replace function public._sesion_actual()
returns uuid
language sql
stable
set search_path = ''
as $$
    select nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'session_id', '')::uuid;
$$;

create or replace function public.empresa_de_sesion()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select s.id_empresa
    from public.sesion_empresa s
    join public.membresia m on m.id_usuario = s.id_usuario and m.id_empresa = s.id_empresa and m.activa
    join public.empresa e on e.id = s.id_empresa and e.activa
    where s.session_id = public._sesion_actual() and s.id_usuario = (select auth.uid());
$$;

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

create or replace function public._empresa_activa_guardada()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select public.empresa_activa();
$$;

create or replace function public.entrar_a_empresa(p_lugar text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint;
    v_sesion uuid := public._sesion_actual();
begin
    if auth.uid() is null or v_sesion is null or length(public._clave_lugar(p_lugar)) < 2 then
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
    -- sesiones de esta persona que ya no existen (cerró sesión o vencieron): se limpian
    delete from public.sesion_empresa s
    where s.id_usuario = auth.uid() and s.session_id <> v_sesion
      and not exists (select 1 from auth.sessions a where a.id = s.session_id);
    insert into public.sesion_empresa (session_id, id_usuario, id_empresa) values (v_sesion, auth.uid(), v_empresa)
    on conflict (session_id) do update set id_empresa = excluded.id_empresa, creada_en = now()
        where public.sesion_empresa.id_usuario = excluded.id_usuario;
    return jsonb_build_object('resultado', 'ok', 'id', v_empresa);
end;
$$;

create or replace function public.elegir_empresa(p_id_empresa bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_sesion uuid := public._sesion_actual();
begin
    if auth.uid() is null or v_sesion is null then
        return 'no_autorizada';
    end if;
    if not exists (select 1 from public.membresia m join public.empresa e on e.id = m.id_empresa
                   where m.id_usuario = auth.uid() and m.id_empresa = p_id_empresa and m.activa and e.activa) then
        return 'no_autorizada';
    end if;
    insert into public.sesion_empresa (session_id, id_usuario, id_empresa) values (v_sesion, auth.uid(), p_id_empresa)
    on conflict (session_id) do update set id_empresa = excluded.id_empresa
        where public.sesion_empresa.id_usuario = excluded.id_usuario;
    return 'ok';
end;
$$;

create or replace function public.salir_de_empresa()
returns void
language sql
security definer
set search_path = ''
as $$
    delete from public.sesion_empresa where session_id = public._sesion_actual() and id_usuario = (select auth.uid());
$$;

revoke execute on function public.empresa_de_sesion(), public.salir_de_empresa() from public, anon;
grant execute on function public.empresa_de_sesion(), public.salir_de_empresa() to authenticated;
