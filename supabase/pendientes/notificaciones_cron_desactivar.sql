-- NO es una migración. Apaga las notificaciones automáticas: interruptores en false y tareas programadas borradas.
-- Las extensiones pg_cron y pg_net quedan instaladas (no molestan). Los avisos dentro de la plataforma siguen.
update public.notificacion_parametros set correo_activo = false, actualizado_en = now() where id = 1;
select cron.unschedule(jobname) from cron.job where jobname in ('jsu-notificaciones', 'jsu-limpieza-cron');
