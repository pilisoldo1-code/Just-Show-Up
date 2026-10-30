-- Multiempresa, parte 1: estructura y datos actuales a la Empresa 1 (diseño: MULTIEMPRESA_DISENO.md).
-- No cambia ningún permiso ni función existente: el código que hoy tiene theta sigue funcionando.
-- - Tablas empresa y membresia; profiles.es_superadmin y profiles.empresa_activa.
-- - id_empresa en todas las tablas operativas (obligatoria, salvo mp_evento) y profesora asignada en
--   horarios, clases y líneas de factura.
-- - Empresa 1 «Just Show Up» (código JSU) con TODOS los datos actuales; membresías desde profiles.rol
--   (profesora → administradora y profesora; alumna → alumna); empresa activa = Empresa 1 para todas.
-- - Triggers que completan la empresa y la profesora de las filas nuevas (desde la fila madre o la
--   empresa activa de quien crea), así el código actual no necesita cambios.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_multiempresa_1.sql

create table public.empresa (
    id bigint generated always as identity primary key,
    nombre text not null check (length(btrim(nombre)) between 2 and 80),
    codigo text not null unique check (codigo ~ '^[A-Z]{2,6}$'),
    activa boolean not null default true,
    ultimo_numero_factura int not null default 0 check (ultimo_numero_factura >= 0),
    mp_habilitado boolean not null default false,
    creado_en timestamptz not null default now()
);

create table public.membresia (
    id bigint generated always as identity primary key,
    id_usuario uuid not null references public.profiles(id) on delete cascade,
    id_empresa bigint not null references public.empresa(id),
    es_admin boolean not null default false,
    es_profesora boolean not null default false,
    es_alumna boolean not null default false,
    activa boolean not null default true,
    creado_en timestamptz not null default now(),
    unique (id_usuario, id_empresa),
    check (es_admin or es_profesora or es_alumna)
);
create index on public.membresia (id_empresa);

alter table public.empresa enable row level security;
alter table public.membresia enable row level security;
revoke all on public.empresa, public.membresia from anon;
revoke insert, update, delete, truncate, references, trigger on public.empresa, public.membresia from authenticated;

alter table public.profiles
    add column es_superadmin boolean not null default false,
    add column empresa_activa bigint references public.empresa(id);

-- ---------- Empresa 1 con todos los datos actuales ----------
insert into public.empresa (nombre, codigo, mp_habilitado) values ('Just Show Up', 'JSU', true);

insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
select p.id, e.id, p.rol = 'profesora', p.rol = 'profesora', p.rol = 'alumna'
from public.profiles p cross join public.empresa e
where e.codigo = 'JSU';

update public.profiles set empresa_activa = (select id from public.empresa where codigo = 'JSU');

do $$
declare
    t text;
    v_e1 bigint := (select id from public.empresa where codigo = 'JSU');
begin
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento', 'mp_evento']
    loop
        -- con valor por defecto: completa las filas existentes sin UPDATE (no dispara triggers)
        execute format('alter table public.%I add column id_empresa bigint references public.empresa(id) default %s', t, v_e1);
        if t <> 'mp_evento' then
            execute format('alter table public.%I alter column id_empresa set not null', t);
        end if;
        execute format('create index on public.%I (id_empresa)', t);
    end loop;
end;
$$;

-- Mientras exista una sola empresa, las filas creadas sin sesión (scripts con la clave de
-- administrador) van a ella; con dos o más empresas la empresa es obligatoria.
create or replace function public._empresa_unica()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select case when count(*) = 1 then min(id) end from public.empresa;
$$;

do $$
declare
    t text;
begin
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento', 'mp_evento']
    loop
        execute format('alter table public.%I alter column id_empresa set default public._empresa_unica()', t);
    end loop;
end;
$$;

-- ---------- profesora asignada ----------
-- horario: con valor por defecto, sin UPDATE (un UPDATE dispararía _horario_desde_tipo)
do $$
declare
    v_prof uuid := (select id from public.profiles where rol = 'profesora' order by id limit 1);
begin
    execute format('alter table public.horario add column id_profesora uuid references public.profiles(id) default %L', v_prof);
    alter table public.horario alter column id_profesora drop default;
end;
$$;
alter table public.clase add column id_profesora uuid references public.profiles(id);
alter table public.resumen_mensual_detalle
    add column id_profesora uuid references public.profiles(id),
    add column profesora_nombre text;

update public.clase c set id_profesora = h.id_profesora from public.horario h where h.id = c.id_horario;
update public.resumen_mensual_detalle d
set id_profesora = c.id_profesora,
    profesora_nombre = (select btrim(p.nombre || ' ' || p.apellido) from public.profiles p where p.id = c.id_profesora)
from public.clase c where c.id = d.id_clase;

-- ---------- completar empresa y profesora en filas nuevas ----------
-- Empresa activa tal como está guardada (la validación de membresía llega en la parte 2).
create or replace function public._empresa_activa_guardada()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select empresa_activa from public.profiles where id = auth.uid();
$$;

create or replace function public._completar_empresa()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.id_empresa is not null then
        return new;
    end if;
    if tg_table_name = 'horario' then
        new.id_empresa := (select id_empresa from public.tipo_clase where id = new.id_tipo_clase);
    elsif tg_table_name = 'clase' then
        new.id_empresa := (select id_empresa from public.horario where id = new.id_horario);
    elsif tg_table_name in ('reserva', 'listadeespera') then
        new.id_empresa := (select id_empresa from public.clase where id = new.id_clase);
    elsif tg_table_name = 'asistencia' then
        new.id_empresa := (select id_empresa from public.reserva where id = new.id_reserva);
    elsif tg_table_name = 'bloqueo' then
        new.id_empresa := (select id_empresa from public.horario where id = new.id_horario);
    elsif tg_table_name = 'resumen_mensual' then
        new.id_empresa := (select id_empresa from public.resumen_mensual where id = new.reemplaza_a);
    elsif tg_table_name in ('resumen_mensual_detalle', 'factura_historial', 'pago', 'mp_intento') then
        new.id_empresa := (select id_empresa from public.resumen_mensual where id = new.id_resumen_mensual);
    elsif tg_table_name = 'pago_anulacion' then
        new.id_empresa := (select id_empresa from public.pago where id = new.id_pago);
    end if;
    if new.id_empresa is null then
        new.id_empresa := public._empresa_activa_guardada();
    end if;
    if new.id_empresa is null then
        raise exception 'falta la empresa de la fila nueva en %', tg_table_name using errcode = '23502';
    end if;
    return new;
end;
$$;

do $$
declare
    t text;
begin
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento']
    loop
        execute format('create trigger completar_empresa before insert on public.%I
                        for each row execute function public._completar_empresa()', t);
    end loop;
end;
$$;

create or replace function public._completar_profesora()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.id_profesora is not null then
        return new;
    end if;
    if tg_table_name = 'horario' then
        -- quien crea el horario, si es docente de esa empresa (así funciona el código actual)
        if exists (select 1 from public.membresia m
                   where m.id_usuario = auth.uid() and m.id_empresa = new.id_empresa and m.activa
                     and (m.es_profesora or m.es_admin)) then
            new.id_profesora := auth.uid();
        end if;
    elsif tg_table_name = 'clase' then
        new.id_profesora := (select id_profesora from public.horario where id = new.id_horario);
    end if;
    return new;
end;
$$;

-- después de completar la empresa (los triggers corren en orden alfabético)
create trigger completar_profesora before insert on public.horario
    for each row execute function public._completar_profesora();
create trigger completar_profesora before insert on public.clase
    for each row execute function public._completar_profesora();

-- ---------- membresía de las cuentas nuevas ----------
-- El código nuevo indica la empresa en los metadatos (id_empresa); el código actual no la indica y
-- la cuenta queda en la Empresa 1 (compatibilidad hasta que se integre el código multiempresa).
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
    insert into public.membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna)
    values (new.id, v_empresa, new.rol = 'profesora', new.rol = 'profesora', new.rol = 'alumna')
    on conflict (id_usuario, id_empresa) do nothing;
    update public.profiles set empresa_activa = v_empresa where id = new.id and empresa_activa is null;
    return new;
end;
$$;

create trigger membresia_inicial after insert on public.profiles
    for each row execute function public._membresia_inicial();

-- Compatibilidad: el script actual de alta de profesora crea la cuenta y después cambia profiles.rol.
-- Mientras exista ese flujo, el cambio de rol se refleja en la membresía de la empresa activa.
create or replace function public._membresia_desde_rol()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    update public.membresia
    set es_admin = new.rol = 'profesora', es_profesora = new.rol = 'profesora', es_alumna = new.rol = 'alumna'
    where id_usuario = new.id and id_empresa = new.empresa_activa;
    return new;
end;
$$;

create trigger membresia_desde_rol after update of rol on public.profiles
    for each row when (old.rol is distinct from new.rol) execute function public._membresia_desde_rol();

revoke execute on function public._empresa_activa_guardada(), public._completar_empresa(),
    public._completar_profesora(), public._membresia_inicial(), public._membresia_desde_rol()
    from public, anon, authenticated;
-- _empresa_unica la usan los valores por defecto de las columnas (los evalúa quien inserta).
revoke execute on function public._empresa_unica() from public, anon;
grant execute on function public._empresa_unica() to authenticated;
