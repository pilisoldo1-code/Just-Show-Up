-- VUELTA ATRÁS de la migración 20261029120000_mercado_pago_prueba.sql (Mercado Pago, solo prueba).
-- Usar SOLO si hace falta deshacerla. Deja la base exactamente como antes de aplicarla
-- (probado en un Postgres local con el respaldo del 29/10: tests/base_local/vuelta_atras_mercado_pago.py).
--
-- Borra: las tablas mp_configuracion, mp_intento y mp_evento (con lo que tengan), las 7 funciones
-- de Mercado Pago, la columna pago.id_transaccion, el medio 'mercado_pago' y los secretos de
-- Mercado Pago del Vault. No toca ningún otro dato.
-- Si ya hay pagos registrados con Mercado Pago, NO hace nada (se detiene con un error): primero
-- hay que decidir qué hacer con esos pagos (por ejemplo, anularlos y borrarlos a mano).
-- Todo o nada: si algo falla, no se aplica ningún cambio.

begin;

do $$
begin
    if to_regclass('public.pago') is not null
       and exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'pago' and column_name = 'id_transaccion')
       and exists (select 1 from public.pago where medio_pago = 'mercado_pago') then
        raise exception 'Hay pagos registrados con Mercado Pago: la vuelta atrás no se aplicó. Revisarlos antes.';
    end if;
end;
$$;

drop function if exists public.crear_intento_mp(bigint);
drop function if exists public.registrar_pago_mercado_pago(text, text, text, numeric, text, date);
drop function if exists public.mp_registrar_evento(text, text, text, text, text);
drop function if exists public.mp_guardar_preferencia(bigint, text);
drop function if exists public.mp_cambiar_habilitado(boolean);
drop function if exists public.mp_guardar_secreto(text, text);
drop function if exists public.mp_configuracion_servidor();

drop table if exists public.mp_evento;
drop table if exists public.mp_intento;
drop table if exists public.mp_configuracion;

alter table public.pago drop constraint if exists pago_mercado_pago_con_operacion;
drop index if exists public.pago_id_transaccion_unico;
alter table public.pago drop column if exists id_transaccion;
alter table public.pago drop constraint if exists pago_medio_pago_check;
alter table public.pago add constraint pago_medio_pago_check check (medio_pago in ('efectivo', 'transferencia'));

delete from vault.secrets where name in ('mp_access_token', 'mp_webhook_secret');

-- El registro de la migración que guarda Supabase (no existe en un Postgres local).
do $$
begin
    if to_regclass('supabase_migrations.schema_migrations') is not null then
        delete from supabase_migrations.schema_migrations where name = 'mercado_pago_prueba';
    end if;
end;
$$;

commit;
