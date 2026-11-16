-- NO es una migración. Crea las tareas programadas de las notificaciones (pg_cron) y habilita pg_net.
-- En producción, SOLO con autorización expresa de Julieta y después de:
--   1) guardar en Vault `notificaciones_url` (https://<sitio>/tareas/notificaciones) y `notificaciones_secreto`
--      con scripts/notificaciones_secretos.py (nunca escribiendo el secreto en el editor SQL);
--   2) cargar en el servidor las variables NOTIFICACIONES_SECRETO y SMTP_* (ver app/correos.py).
-- Las tareas no mandan nada mientras los interruptores estén apagados (correo_activo y lista_espera_automatica en
-- notificacion_parametros). Se deshace con notificaciones_cron_desactivar.sql.
-- como indica Supabase (Integrations > Cron): pg_cron en pg_catalog y permisos sobre su esquema para postgres
create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;
-- pg_net crea su propio esquema «net»
create extension if not exists pg_net;

-- cada minuto: lista de espera automática (si está encendida), generar avisos y avisar al despachador
select cron.schedule('jsu-notificaciones', '* * * * *', $$select public.notificaciones_tick()$$);
-- todos los días: el historial de pg_cron no se borra solo (una fila por minuto)
select cron.schedule('jsu-limpieza-cron', '17 3 * * *',
                     $$delete from cron.job_run_details where end_time < now() - interval '7 days'$$);
