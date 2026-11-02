-- Tres roles por empresa: cada membresía tiene EXACTAMENTE UN rol (alumna, profesora o administradora).
-- * La administradora tiene los permisos docentes por es_docente() (es_admin or es_profesora): no necesita
--   también es_profesora. Puede tener clases asignadas o ninguna (el rol no depende de las clases).
-- * Solo la superadministradora da o quita el rol de administradora, y nunca deja una empresa sin una
--   administradora activa. Una administradora común gestiona alumnas y profesoras, pero no puede modificar,
--   activar, desactivar ni cambiar el rol de otra administradora.
-- * Los cambios alumna ↔ profesora se permiten solo sin nada pendiente (reservas, facturas, clases futuras).
-- Se conservan las columnas es_admin / es_profesora / es_alumna: todo lo que las lee sigue igual.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_tres_roles.sql

do $$
begin
    if exists (select 1 from public.membresia where es_alumna and (es_admin or es_profesora)) then
        raise exception 'Hay membresías de alumna combinadas con otro rol: resolverlas antes de aplicar esta migración.';
    end if;
end;
$$;

-- Qué membresías se adaptan (para que la vuelta atrás las deje exactamente como estaban). Solo lo lee el servidor.
create table public._tres_roles_respaldo (id_membresia bigint primary key);
alter table public._tres_roles_respaldo enable row level security;
revoke all on public._tres_roles_respaldo from public, anon, authenticated;
insert into public._tres_roles_respaldo select id from public.membresia where es_admin and es_profesora;

-- Las administradoras dejan de tener marcado también «profesora». Sus clases no cambian (están en horario y clase).
update public.membresia set es_profesora = false where es_admin and es_profesora;

alter table public.membresia add constraint membresia_un_solo_rol
    check ((es_admin::int + es_profesora::int + es_alumna::int) = 1);

-- ---------- qué impide cambiar de rol ----------
-- Alumna: reservas confirmadas desde el mes actual (se facturan como alumna), lista de espera de una clase que no
-- empezó, facturas en borrador o confirmadas sin pagar.
create or replace function public._pendientes_alumna(p_usuario uuid, p_empresa bigint)
returns text[]
language sql
stable
security definer
set search_path = ''
as $$
    select array_remove(array[
        case when exists (select 1 from public.reserva r join public.clase c on c.id = r.id_clase
                          where r.id_alumna = p_usuario and r.id_empresa = p_empresa and r.estado = 'confirmada'
                            and c.fecha >= date_trunc('month', public._hoy_mvd())::date) then 'reservas' end,
        case when exists (select 1 from public.listadeespera l join public.clase c on c.id = l.id_clase
                          where l.id_alumna = p_usuario and l.id_empresa = p_empresa and l.estado in ('en_espera', 'ofrecido')
                            and c.estado = 'programada' and public._inicio_clase(c.fecha, c.hora_inicio) > now()) then 'lista_espera' end,
        case when exists (select 1 from public.resumen_mensual f
                          where f.id_alumna = p_usuario and f.id_empresa = p_empresa and f.estado_factura = 'borrador') then 'borradores' end,
        case when exists (select 1 from public.resumen_mensual f
                          where f.id_alumna = p_usuario and f.id_empresa = p_empresa and f.estado_factura = 'confirmada'
                            and f.estado_pago = 'pendiente') then 'facturas_pendientes' end
    ], null);
$$;

-- Docente (profesora o administradora): horarios activos a su nombre o clases futuras programadas que siguen vigentes
-- (de un horario activo o con reservas).
create or replace function public._pendientes_docente(p_usuario uuid, p_empresa bigint)
returns text[]
language sql
stable
security definer
set search_path = ''
as $$
    select array_remove(array[
        case when exists (select 1 from public.horario h
                          where h.id_profesora = p_usuario and h.id_empresa = p_empresa and h.activo) then 'horarios' end,
        case when exists (select 1 from public.clase c join public.horario h on h.id = c.id_horario
                          where c.id_profesora = p_usuario and c.id_empresa = p_empresa and c.estado = 'programada'
                            and public._inicio_clase(c.fecha, c.hora_inicio) > now()
                            and (h.activo or exists (select 1 from public.reserva r
                                                     where r.id_clase = c.id and r.estado = 'confirmada'))) then 'clases_futuras' end
    ], null);
$$;

-- Para la pantalla de edición: qué falta resolver para que una persona de la empresa activa cambie de rol.
create or replace function public.pendientes_cambio_rol(p_id_usuario uuid)
returns text[]
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v public.membresia;
begin
    if not public.es_admin() then
        return null;
    end if;
    select * into v from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa();
    if not found or v.es_admin then
        return null;
    end if;
    return case when v.es_alumna then public._pendientes_alumna(p_id_usuario, v.id_empresa)
                else public._pendientes_docente(p_id_usuario, v.id_empresa) end;
end;
$$;

-- ¿La administradora activa puede gestionar a esta persona? (alta, datos, contraseña). Otra administradora: no.
create or replace function public.puede_gestionar_persona(p_id_usuario uuid)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v public.membresia;
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    select * into v from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa();
    if not found then
        return 'no_existe';
    end if;
    if v.es_admin and p_id_usuario <> auth.uid() then
        return 'es_administradora';
    end if;
    return 'ok';
end;
$$;

-- ---------- administradora de la empresa ----------
-- Solo alumna o profesora. Nunca da, quita ni modifica el rol de administradora (tampoco el propio).
create or replace function public.asignar_membresia(p_id_usuario uuid, p_es_admin boolean, p_es_profesora boolean, p_es_alumna boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_empresa bigint := public.empresa_activa();
    v_profesora boolean := coalesce(p_es_profesora, false);
    v_alumna boolean := coalesce(p_es_alumna, false);
    v_actual public.membresia;
    v_pendientes text[];
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if coalesce(p_es_admin, false) then
        return 'solo_superadmin';
    end if;
    if v_profesora = v_alumna then
        return case when v_profesora then 'un_solo_rol' else 'sin_rol' end;
    end if;
    if not exists (select 1 from public.profiles where id = p_id_usuario and not es_superadmin) then
        return 'no_existe';
    end if;
    select * into v_actual from public.membresia where id_usuario = p_id_usuario and id_empresa = v_empresa for update;
    if found then
        if v_actual.es_admin then
            return 'es_administradora';
        end if;
        if v_actual.es_alumna and v_profesora then
            v_pendientes := public._pendientes_alumna(p_id_usuario, v_empresa);
        elsif v_actual.es_profesora and v_alumna then
            v_pendientes := public._pendientes_docente(p_id_usuario, v_empresa);
        end if;
        if cardinality(v_pendientes) > 0 then
            return 'pendientes:' || array_to_string(v_pendientes, ',');
        end if;
        update public.membresia set es_admin = false, es_profesora = v_profesora, es_alumna = v_alumna, activa = true
        where id = v_actual.id;
    else
        insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
        values (p_id_usuario, v_empresa, false, v_profesora, v_alumna);
    end if;
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
declare
    v public.membresia;
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    if p_id_usuario = auth.uid() then
        return 'no_puede_desactivarse';
    end if;
    select * into v from public.membresia where id_usuario = p_id_usuario and id_empresa = public.empresa_activa() for update;
    if not found then
        return 'no_existe';
    end if;
    if v.es_admin then
        return 'es_administradora';
    end if;
    update public.membresia set activa = coalesce(p_activa, false) where id = v.id;
    return 'ok';
end;
$$;

-- Datos de la cuenta de una persona de la empresa activa (la cédula va aparte, por empresa). Otra administradora: no.
create or replace function public.editar_datos_persona(p_id_usuario uuid, p_nombre text, p_apellido text, p_telefono text,
                                                       p_fecha_nacimiento date, p_fecha_vencimiento_carne_salud date)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_permiso text := public.puede_gestionar_persona(p_id_usuario);
begin
    if v_permiso <> 'ok' then
        return v_permiso;
    end if;
    if coalesce(btrim(p_nombre), '') = '' or coalesce(btrim(p_apellido), '') = '' then
        return 'datos_invalidos';
    end if;
    update public.profiles
    set nombre = btrim(p_nombre), apellido = btrim(p_apellido), telefono = nullif(btrim(coalesce(p_telefono, '')), ''),
        fecha_nacimiento = p_fecha_nacimiento, fecha_vencimiento_carne_salud = p_fecha_vencimiento_carne_salud
    where id = p_id_usuario;
    return 'ok';
end;
$$;

-- Cédula: como antes, pero tampoco la de otra administradora.
create or replace function public.actualizar_cedula(p_id_alumna uuid, p_cedula text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_cedula text := nullif(regexp_replace(coalesce(p_cedula, ''), '[\s.\-]', '', 'g'), '');
    v_permiso text;
begin
    if not public.es_admin() then
        return 'no_autorizada';
    end if;
    v_permiso := public.puede_gestionar_persona(p_id_alumna);
    if v_permiso <> 'ok' then
        return v_permiso;
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

-- ---------- superadministradora ----------
-- Designa administradora a una cuenta (nueva o existente) o reactiva a una administradora inactiva. Si era alumna de
-- esa empresa, solo sin nada pendiente.
create or replace function public.asignar_administradora(p_id_empresa bigint, p_id_usuario uuid, p_es_profesora boolean)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.membresia;
    v_pendientes text[];
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
    select * into v from public.membresia where id_usuario = p_id_usuario and id_empresa = p_id_empresa for update;
    if found and v.es_alumna then
        v_pendientes := public._pendientes_alumna(p_id_usuario, p_id_empresa);
        if cardinality(v_pendientes) > 0 then
            return 'pendientes:' || array_to_string(v_pendientes, ',');
        end if;
    end if;
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
    values (p_id_usuario, p_id_empresa, true, false, false)
    on conflict (id_usuario, id_empresa) do update
        set es_admin = true, es_profesora = false, es_alumna = false, activa = true;
    update public.profiles set empresa_activa = p_id_empresa where id = p_id_usuario and empresa_activa is null;
    return 'ok';
end;
$$;

-- Quita el rol de administradora: pasa a profesora (conserva sus clases) o queda inactiva (como profesora inactiva,
-- con todo su historial). Nunca deja la empresa sin una administradora activa; para dejarla inactiva no puede tener
-- horarios activos ni clases futuras a su nombre.
create or replace function public.quitar_administradora(p_id_empresa bigint, p_id_usuario uuid, p_accion text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v public.membresia;
    v_pendientes text[];
begin
    if not public.es_superadmin() then
        return 'no_autorizada';
    end if;
    if p_accion is null or p_accion not in ('profesora', 'inactiva') then
        return 'accion_invalida';
    end if;
    -- todas las administradoras de la empresa, en orden: dos pedidos a la vez no pueden dejarla sin ninguna
    perform 1 from public.membresia where id_empresa = p_id_empresa and es_admin order by id for update;
    select * into v from public.membresia where id_usuario = p_id_usuario and id_empresa = p_id_empresa;
    if not found or not v.es_admin then
        return 'no_es_administradora';
    end if;
    if v.activa and not exists (select 1 from public.membresia
                                where id_empresa = p_id_empresa and es_admin and activa and id <> v.id) then
        return 'ultima_administradora';
    end if;
    if p_accion = 'inactiva' then
        v_pendientes := public._pendientes_docente(p_id_usuario, p_id_empresa);
        if cardinality(v_pendientes) > 0 then
            return 'pendientes:' || array_to_string(v_pendientes, ',');
        end if;
    end if;
    update public.membresia
    set es_admin = false, es_profesora = true, activa = case when p_accion = 'inactiva' then false else activa end
    where id = v.id;
    return 'ok';
end;
$$;

-- Administradoras de una empresa (activas e inactivas), con lo que impediría dejarlas inactivas.
create or replace function public.administradoras_empresa(p_id_empresa bigint)
returns table (id uuid, nombre text, apellido text, mail text, activa boolean, pendientes text[])
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return;
    end if;
    return query
    select p.id, p.nombre, p.apellido, p.mail, m.activa, public._pendientes_docente(p.id, p_id_empresa)
    from public.membresia m join public.profiles p on p.id = m.id_usuario
    where m.id_empresa = p_id_empresa and m.es_admin
    order by m.activa desc, p.apellido, p.nombre;
end;
$$;

-- ---------- cuentas nuevas ----------
-- Un solo rol: «admin» y el valor viejo «admin_profesora» crean una administradora; sin rol de empresa, el
-- profiles.rol de siempre («profesora» = administradora).
create or replace function public._membresia_inicial()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_meta jsonb := (select raw_user_meta_data from auth.users where id = new.id);
    v_empresa bigint;
    v_rol text;
begin
    if coalesce((v_meta ->> 'sin_empresa')::boolean, false) then
        return new;
    end if;
    v_empresa := coalesce((v_meta ->> 'id_empresa')::bigint, (select id from public.empresa where codigo = 'JSU'));
    if v_empresa is null then
        return new;
    end if;
    v_rol := case when v_meta ? 'rol_empresa' then
                      case when v_meta ->> 'rol_empresa' in ('admin', 'admin_profesora') then 'admin'
                           when v_meta ->> 'rol_empresa' = 'profesora' then 'profesora' else 'alumna' end
                  when new.rol = 'profesora' then 'admin' else 'alumna' end;
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
    values (new.id, v_empresa, v_rol = 'admin', v_rol = 'profesora', v_rol = 'alumna')
    on conflict (id_usuario, id_empresa) do nothing;
    update public.profiles set empresa_activa = v_empresa where id = new.id and empresa_activa is null;
    return new;
end;
$$;

revoke execute on function public._pendientes_alumna(uuid, bigint), public._pendientes_docente(uuid, bigint)
    from public, anon, authenticated;
revoke execute on function public.pendientes_cambio_rol(uuid), public.puede_gestionar_persona(uuid),
    public.editar_datos_persona(uuid, text, text, text, date, date), public.quitar_administradora(bigint, uuid, text),
    public.administradoras_empresa(bigint) from public, anon;
grant execute on function public.pendientes_cambio_rol(uuid), public.puede_gestionar_persona(uuid),
    public.editar_datos_persona(uuid, text, text, text, date, date), public.quitar_administradora(bigint, uuid, text),
    public.administradoras_empresa(bigint) to authenticated;
