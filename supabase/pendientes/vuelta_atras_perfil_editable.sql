-- Vuelta atrás de 20261112000000_perfil_editable.sql: deja la base como antes (no toca datos).
-- Los correos que ya se hayan cambiado quedan cambiados (en auth.users y en profiles), como corresponde.
begin;
drop trigger if exists on_auth_user_email_changed on auth.users;
drop function if exists public._sincronizar_mail_perfil();
drop function if exists public.editar_mis_datos(text, text, text, date, date);
commit;
