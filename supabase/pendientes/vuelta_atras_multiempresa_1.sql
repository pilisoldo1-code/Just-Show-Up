-- VUELTA ATRÁS de 20261031000100_multiempresa_1_estructura.sql (NO es una migración).
-- Quita las tablas empresa y membresia, las columnas id_empresa, id_profesora, profesora_nombre,
-- es_superadmin y empresa_activa, y los triggers y funciones de la parte 1. Los datos anteriores a la
-- migración no se tocan. Se niega si ya hay más de una empresa (habría datos de otra empresa que se
-- mezclarían con los de la Empresa 1). Todo o nada.
-- Correr en el SQL Editor de Supabase solo si Julieta lo autoriza.

begin;

do $$
begin
    if (select count(*) from public.empresa) > 1 then
        raise exception 'Hay más de una empresa: la vuelta atrás mezclaría sus datos. No se hizo nada.';
    end if;
end;
$$;

drop trigger membresia_desde_rol on public.profiles;
drop trigger membresia_inicial on public.profiles;
drop trigger completar_profesora on public.horario;
drop trigger completar_profesora on public.clase;
do $$
declare
    t text;
begin
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento']
    loop
        execute format('drop trigger completar_empresa on public.%I', t);
    end loop;
    foreach t in array array['tipo_clase', 'horario', 'clase', 'reserva', 'listadeespera', 'asistencia', 'bloqueo',
                             'resumen_mensual', 'resumen_mensual_detalle', 'factura_historial', 'pago',
                             'pago_anulacion', 'mp_intento', 'mp_evento']
    loop
        execute format('alter table public.%I drop column id_empresa', t);
    end loop;
end;
$$;

drop function public._membresia_desde_rol();
drop function public._empresa_unica();
drop function public._membresia_inicial();
drop function public._completar_profesora();
drop function public._completar_empresa();
drop function public._empresa_activa_guardada();

alter table public.resumen_mensual_detalle drop column id_profesora, drop column profesora_nombre;
alter table public.clase drop column id_profesora;
alter table public.horario drop column id_profesora;
alter table public.profiles drop column empresa_activa, drop column es_superadmin;

drop table public.membresia;
drop table public.empresa;

delete from supabase_migrations.schema_migrations where name = 'multiempresa_1_estructura';

commit;
