# Integración de las notificaciones (fases A a D) en producción

Guía paso a paso. Cada paso de producción necesita la autorización expresa de Julieta. Los interruptores
(`correo_activo` y `lista_espera_automatica`) quedan APAGADOS hasta que ella autorice encenderlos. Si una verificación
falla, no se sigue: se apaga todo (paso de emergencia) y se revisa.

Respaldo previo verificado: `~/Documents/Just Show Up - respaldos/2026-11-16 antes de notificaciones/`
(restaurado en local con todas las filas, cuentas, contadores y estructura iguales a producción; ver su `LEEME_recuperacion.md`).

## Emergencia (en cualquier momento)

En el editor SQL de Supabase, `supabase/pendientes/notificaciones_cron_desactivar.sql`: apaga los dos interruptores y
borra las tareas programadas. Los avisos dentro de la plataforma siguen. Para quitar todo:
`supabase/pendientes/vuelta_atras_notificaciones.sql` y revertir el merge en GitHub.

## Orden

| # | Paso | Quién | Verificación |
|---|------|-------|--------------|
| 1 | Comprobar que producción no cambió desde el respaldo (conteos y huella de estructura) | Claude (lectura) | iguales a `manifiesto.json` / `estructura_antes.json`; si no, respaldo nuevo |
| 2 | Aplicar las 4 migraciones, en orden: `notificaciones_parametros`, `notificaciones_correos`, `notificaciones_correos_avisos`, `lista_espera_automatica` | Claude | 28 tablas existentes con los mismos datos; 2 tablas nuevas; columnas, políticas, triggers y funciones existentes iguales (solo se agregan); interruptores apagados; theta (código anterior) sigue respondiendo |
| 3 | Push de `notificaciones-correos`, PR a `main` y merge común (conserva los 5 commits de Pilar; su rama queda «merged») | Claude, con aviso a Pilar | theta se despliega sola; `/api/health`; Novedades, campana y Mi perfil con cuentas de prueba (pantallas de solo lectura) |
| 4 | Contraseña de aplicación de Gmail | Julieta | — |
| 5 | Variables de amber (7) | Julieta | `vercel env ls production` muestra los nombres |
| 6 | Desplegar amber desde una copia limpia de `main` | Claude | `POST /tareas/notificaciones` sin secreto → 401 (no 503) |
| 7 | Secretos de Vault con `python scripts/notificaciones_secretos.py --produccion` | Julieta | la huella que muestra el script; `select count(*) from vault.secrets where name like 'notificaciones_%'` = 2 |
| 8 | Extensiones y tareas: `supabase/pendientes/notificaciones_cron_activar.sql` | Claude | `cron.job` con 2 tareas; corridas `succeeded`; `notificaciones_tick()` → «apagado» |
| 9 | Despachador antes de encender: `select public._notificaciones_llamar_despachador()` | Claude | en `net._http_response`: 200 y `{"tomados": 0}` (con el interruptor apagado no toma nada: prueba URL, secreto y configuración SMTP sin mandar correos) |
| 10 | Encender correos y pruebas controladas SOLO con las cuentas que autorice Julieta | Claude | cada aviso «enviado» en `notificacion_envio`; llegan a esas casillas; `notificaciones_resumen()` |
| 11 | Encender la lista de espera automática y probarla con una clase de prueba | Claude | la oferta vencida pasa a la siguiente sin abrir la plataforma |
| 12 | Revisión final: Just Show Up y Pilates Centro, sin duplicados ni sobrecupos, interruptores como quedaron autorizados | Claude | consultas de control |

## Comandos y consultas de control

Encender / apagar (editor SQL o conector, como `postgres`; las funciones `notificaciones_*_activar` exigen sesión de
superadministradora):

```sql
update public.notificacion_parametros set correo_activo = true, correo_desde = coalesce(correo_desde, now()), actualizado_en = now() where id = 1;
update public.notificacion_parametros set lista_espera_automatica = true, actualizado_en = now() where id = 1;
update public.notificacion_parametros set correo_activo = false, lista_espera_automatica = false, actualizado_en = now() where id = 1;
```

Estado:

```sql
select correo_activo, lista_espera_automatica, correo_desde, correo_tope_dia, correo_tope_hora from public.notificacion_parametros;
select tipo, estado, motivo, count(*) from public.notificacion_envio group by 1, 2, 3 order by 1, 2;
select jobname, schedule, active from cron.job;
select status, count(*) from cron.job_run_details where start_time > now() - interval '1 hour' group by 1;
select status_code, content, error_msg, created from net._http_response order by created desc limit 5;
```

## Configuración

- **Gmail** (la cuenta actual del proyecto): Cuenta de Google → Seguridad → Verificación en 2 pasos (activada) →
  Contraseñas de aplicación → nombre «JSU notificaciones» → copiar la contraseña de 16 letras (solo se muestra una vez).
  Revocarla después corta los correos de la app sin tocar los de inicio de sesión (si Supabase Auth usa otra).
- **amber** (Vercel → proyecto just-show-up → Settings → Environment Variables → Production, marcar «Sensitive»):
  `NOTIFICACIONES_SECRETO` (generado en la terminal, ver abajo), `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=465`,
  `SMTP_SEGURIDAD=ssl`, `SMTP_USUARIO=<gmail del proyecto>`, `SMTP_REMITENTE=<el mismo gmail>`,
  `SMTP_CONTRASENA=<contraseña de aplicación>`. Las variables toman efecto en el próximo despliegue (paso 6).
- **Secreto** (nunca por el chat): `python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" | pbcopy`
  lo deja en el portapapeles: pegarlo en Vercel y, en el paso 7, en el script (no se muestra al escribir).
- **Vault** (paso 7, desde la carpeta principal, con `main` ya integrado):
  `.venv/bin/python scripts/notificaciones_secretos.py --produccion`; dirección
  `https://just-show-up-amber.vercel.app/tareas/notificaciones` y el mismo secreto.
- **Supabase**: `pg_cron` y `pg_net` (gratis en el plan Free) los instala el paso 8. Los enlaces de los correos apuntan a
  theta (`notificacion_parametros.sitio_url`). Topes: 40 por hora y 200 cada 24 h.
