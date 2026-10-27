# Continuidad del proyecto Just Show Up — traspaso entre sesiones

> Actualizado: **27/10/2026** (fin de la sesión que hizo el módulo 6).
> Leer junto con `CLAUDE.md` (reglas, gotchas, detalle de cada módulo). Este archivo dice
> **dónde quedamos y qué sigue**; `CLAUDE.md` es la referencia permanente.
> No contiene contraseñas, tokens ni claves: esas viven solo en `.env` y `.env.cuentas-prueba`
> (ignorados por git) y en las variables de Vercel.

---

## 1. Qué es y cómo está armado

- **Just Show Up**: plataforma para una profesora de clases presenciales (Gimnasia, etc.) y sus
  alumnas. Reemplaza la gestión por WhatsApp. Proyecto **universitario** (UCU, "Obligatorio
  Incorporación Estratégica"): costo $0, sin cobros reales, sin requisitos fiscales.
- **Equipo**: Julieta (dueña de esta línea de trabajo, `julietaalvez-lgtm`), Pilar
  (`pilisoldo1-code`, dueña del repo y del Vercel oficial "theta") y Josefina
  (`josefinabellonan03-coder`, autora del PR #13).
- **Stack**: Python **FastAPI + Jinja2** (server-rendered, sin framework JS) · **Supabase**
  (Postgres + Auth + Storage), proyecto "Just Show Up" `nspgafucytcjqkqnmkry` (sa-east-1, plan
  Free) · **Vercel** (`api/index.py` importa `app.main:app`).
- **Deploys**:
  - **theta** `https://just-show-up-theta.vercel.app` (cuenta Vercel de Pilar): **se despliega
    sola a producción cada vez que se actualiza `main`** (verificado en el historial de
    deployments de GitHub). Cada PR genera además un deploy *Preview* automático.
  - **amber** `https://just-show-up-amber.vercel.app` (cuenta de Julieta): **no** se despliega
    sola; solo por CLI desde una copia limpia (sin `.env`). Hoy tiene un código viejo (Planificación).
- **Estructura**: `app/main.py` (rutas), `app/auth.py` (sesión por cookies `sb_acceso`/`sb_refresco`,
  roles), `app/clases.py` (grilla, reservas, lista de espera, planificación, asistencia),
  `app/tipos_clase.py`, `app/estadisticas.py`, **`app/facturas.py`** (facturación),
  **`app/factura_pdf.py`** (PDF con fpdf2), `app/validaciones.py` (incluye cédula),
  `app/templates/`, `app/static/style.css`, `supabase/migrations/`, `supabase/pendientes/`,
  `tests/`.
- **Roles**: una profesora (administradora) y alumnas. No hay registro público: la profesora da de
  alta a las alumnas. La seguridad real está en la base (RLS + funciones `security definer`).
- **Módulos hechos y en `main`**: autenticación (etapa 1 + fase 1 de etapa 2), olvidé mi
  contraseña, horarios, grilla y reservas, cancelación (hasta 1 h antes), lista de espera con
  ofertas de 30 min, asistencia, Mi historial, Planificación (bloqueos por período), Estadísticas,
  Tipos de clase.
- **Módulo 6 (facturación + cédula)**: terminado y probado, en el **PR #15 sin integrar** (ver §3).

## 2. Entorno local

- `.venv` con Python 3.12: `uv venv --python 3.12 .venv && uv pip install -r requirements-dev.txt`
  (`requirements-dev.txt` = `requirements.txt` + httpx, pgserver, psycopg2-binary).
- Servidor: `.venv/bin/uvicorn app.main:app --port 8000 --env-file .env`.
  Al cerrar esta sesión había procesos uvicorn viejos escuchando en el puerto 8000; si molestan,
  cerrarlos antes de levantar otro.
- Cuentas de prueba: `profesora@prueba.invalid`, `alumna@prueba.invalid` (con contraseña en
  `.env.cuentas-prueba`, formato "mail contraseña" por línea) y `alumna.uno/dos/tres@prueba.invalid`
  (sin contraseña: se entra con `auth.admin.generate_link` magic link + `verify_otp(token_hash)`).
  Hay 8 alumnas de demo `@demo.invalid` (no tocar). **Nunca mostrar ni escribir contraseñas en el chat.**

## 3. GitHub

- Repo: `pilisoldo1-code/Just-Show-Up`. `main` = **`f1536c2`** (merge del PR #14, Tipos de clase).
- Rama de trabajo: **`modulo-6-resumen-correcciones`**, pusheada hasta **`692be2d`**.
  Commits sobre `main`:
  - `e8f4184`, `a8a52b9` — trabajo original de Josefina (autoría conservada).
  - `72aa78a` merge del trabajo de #13 · `a732fb7` correcciones · `2c52777` etapa 1 ·
    `fa370d6` etapas 2 y 3 · `3fa93b8` cédula · `af29073` docs · `692be2d` pruebas contra Supabase real.
  - Más un commit **local, sin pushear**, con este archivo y la actualización de `CLAUDE.md`
    (ver §9: decidir si se pushea al PR antes de integrarlo).
- **PR #15** https://github.com/pilisoldo1-code/Just-Show-Up/pull/15 — "Módulo 6: facturación
  mensual, pagos simulados y cédula de las alumnas". Estado al 27/10: abierto, `MERGEABLE`/`CLEAN`,
  checks de Vercel en verde (Preview), sin revisiones ni comentarios pendientes. La descripción
  incluye funcionalidades, migraciones (ya aplicadas), pruebas y riesgos.
- **PR #13** (Josefina, rama `modulo-6-resumen` en `a8a52b9`): abierto. **No modificarlo ni cerrarlo.**
  Su migración `20261021000000_resumen_mensual_pagos.sql` **no está aplicada y no debe aplicarse**
  (la reemplaza la del PR #15).
- **Integración decidida**: **Squash and merge** del PR #15 (así #13 no queda marcado "merged"
  automáticamente), con esta línea en el mensaje:
  `Co-authored-by: Josefina Bello <338869384+josefinabellonan03-coder@users.noreply.github.com>`
- Otras ramas remotas: `clases-proxima-semana`, `modulo-estadisticas` (viejas, no tocar).

## 4. Supabase

- Proyecto activo (`ACTIVE_HEALTHY`) al 27/10. El plan Free **se pausa tras ~1 semana sin uso**:
  si todo da 500, revisar `get_project`; si está pausado, avisar a Julieta y reactivarlo desde el
  panel (Restore). No crear otro proyecto ni cambiar credenciales.
- **12 migraciones aplicadas** (Supabase registra su propia versión al aplicar; los nombres del
  repo difieren en el timestamp, es normal):
  1–10: horario … tipos_de_clase (`20261021002249`).
  11. `20261027205550 profiles_cedula` ← archivo `20261021140000_profiles_cedula.sql`
  12. `20261027205754 resumen_mensual_pagos` ← archivo `20261021150000_resumen_mensual_pagos.sql`
- Después de aplicarlas se verificó: estructura de producción **idéntica** a la que generan los
  archivos del repo (columnas, restricciones, índices, 56 funciones, 36 políticas, permisos, RLS,
  triggers); datos existentes sin cambios; permisos probados con cuentas de prueba.
- **Datos al 27/10**: profiles 16 (ninguna con cédula), tipo_clase 1 (Gimnasia), horario 10,
  clase 50 (oct–6/11), reserva 144, listadeespera 1, asistencia 71, bloqueo 0, auth.users 16.
  **Tablas de facturación vacías** y `factura_numero_seq` reiniciada (la primera factura real será 0001).
- **Respaldos** (fuera del repo, con datos personales, permisos 600/700):
  `~/Documents/Just Show Up - respaldos/2026-10-27 antes de facturación (módulo 6)/`
  - `datos.json` (8 tablas + usuarias de auth sin contraseñas), `restaurar_datos.sql`,
    `huellas_produccion.json` (estructura antes), `huellas_post_migracion.json` (después),
    `huellas_datos_produccion.json`, `huellas_estructura.sql` (la consulta), `vuelta_atras_modulo6.sql`
    (deshace las 2 migraciones; **no usar sin evaluar**: borra facturación y cédulas),
    `verificar_restauracion.py`, `registro_pruebas_paso3.json`, `LEEME.txt`, `sumas_sha256.txt`.
  - Respaldo anterior: `2026-10-20 antes de Tipos de clase/`.
- **Seguridad pendiente (fase 2)**: `supabase/pendientes/auth_etapa2_cerrar_anon.sql` sigue SIN
  aplicar: sin sesión todavía se puede leer/escribir `horario`, `clase`, `reserva`, `listadeespera`
  (puede alterar lo que entra en un borrador de factura; las confirmadas no). Recomendado antes de
  usar facturación con alumnas reales. Rotación de claves también pendiente (ver `CLAUDE.md`).

## 5. Módulo 6 — facturación y cédula (resumen)

Detalle completo en `CLAUDE.md` (sección "Facturación mensual y pagos") y en la descripción del PR #15.

- **Profesora** (`/profesora/resumenes`, menú "Facturación"): provisorio durante el mes; desde el
  **último día del mes** borradores automáticos (solo clases ya empezadas, sin perder ajustes);
  ajustes con motivo (bonificar, importe menor, descuento general monto/%); confirmación individual
  y **masiva con vista previa** (todo o nada, "firma" de ids+versiones); confirmar exige que haya
  terminado la última clase del mes; estados separados factura (borrador/confirmada/anulada) y pago
  (pendiente/pagado/sin_cargo); pagos simulados (total exacto, fecha no futura, uno vigente,
  anulación con motivo en `pago_anulacion`); **anular y reemitir**; PDF.
- **Alumna** ("Mis facturas"): solo sus facturas confirmadas **desde el día 1 del mes siguiente**
  (lo aplica RLS); detalle, descuentos, total, estado de pago; PDF "Factura mensual – Just Show Up".
- **Cédula**: `profiles.cedula` opcional, dígito verificador uruguayo, única; la carga la profesora
  (alta o "Cargar/Corregir cédula" en Alumnas, función `actualizar_cedula`); la alumna la ve en Mi
  perfil y no puede cambiarla. Las facturas guardan **foto fija** (nombre, apellido, cédula) al
  confirmar; sin cédula se confirma igual ("Cédula: no registrada") y la vista previa masiva avisa.
- **Pruebas** (todas en verde):
  - `python -m unittest discover tests` → 69 (49 de facturación/cédula + 20 de estadísticas).
  - `python tests/base_local/test_facturacion_base.py` → 180 casos en Postgres local con reloj
    simulado y datos sintéticos (fechas, concurrencia, permisos, PDF, código real de la app).
  - `python tests/supabase_real/prueba_modulo6_real.py <carpeta respaldo> <archivo registro>` → 73
    casos contra Supabase real vía servidor local en el puerto 8018, solo cuentas `@prueba.invalid`
    y septiembre 2026 (sin datos reales); limpia y compara con el respaldo. Corrida 3 veces, base
    idéntica al respaldo al final. Si se vuelve a correr: reiniciar después
    `select setval('public.factura_numero_seq', 1, false)` (solo si no hay facturas).
- **No probado contra la base real** (sí en local con reloj simulado): confirmar con clases
  pendientes, publicación el día 1, cambio de año; marcar asistencia en horario de clase.

## 6. Estado de tareas

### Terminado
- Módulo 6 completo (código, tests, migraciones aplicadas y verificadas, pruebas reales, PR #15 abierto).
- Respaldo verificado y restaurable del 27/10.
- Investigación de Mercado Pago (Checkout Pro de prueba) y de Videos On Demand (§7 y §8).

### Pendiente — orden de trabajo DEFINITIVO (27/10/2026)
1. **Integrar el PR #15** (facturación).
2. **Planificación: cancelar clases puntuales con checkboxes** (§9).
3. **Mercado Pago Checkout Pro en modo de prueba** (§7).
4. **Videos On Demand** con Zoom gratuito + alojamiento gratuito sujeto a validación (§8).

Después (sin fecha): fase 2 de seguridad + rotación de claves, mails con Resend, actualizar amber,
fase 2 de Tipos de clase (borrar columnas viejas de `horario`).

### Necesita autorización explícita de Julieta (no hacerlo por iniciativa propia)
- Cualquier **merge**, **push**, **deploy** (theta se despliega sola al actualizar `main`),
  **migración** o escritura en Supabase real, crear tablas/columnas/env vars, nuevas dependencias,
  crear cuentas en servicios externos, aplicar la fase 2 de seguridad.
- **Paso 1 (PR #15) quedó DETENIDO a la espera de su decisión**: todas las verificaciones dieron
  bien, pero el merge desplegaría automáticamente en **theta**. Opciones presentadas:
  **A** integrar aceptando el deploy (avisar a Pilar; verificar theta con lectura y cuentas de
  prueba; Pilar puede hacer rollback en su Vercel) — recomendada · **B** que Pilar desactive el
  auto-deploy antes · **C** esperar. Con A: Squash and merge + coautoría de Josefina (§3), sin tocar #13.
  Después del merge: verificar que `main` tiene el módulo 6 y revisar theta (sin escribir datos reales).
  Ojo: desde el **31/10** al abrir la facturación de octubre se crean borradores de alumnas REALES
  (es el funcionamiento previsto).

## 7. Mercado Pago (paso 3) — decisiones y propuesta

- **Solo modo de prueba**, sin cobros reales ni costos; se **conserva** el registro manual de pagos.
  Flujo: Mis facturas → factura confirmada, publicada y pendiente → "Pagar con Mercado Pago
  (prueba)" → Checkout Pro de prueba → pago simulado → Mercado Pago informa → si aprobado, la
  factura pasa a "Pagada"; rechazado/pendiente no la marca.
- **Cuentas**: alguien con cuenta real de Mercado Pago Uruguay crea una aplicación Checkout Pro;
  se genera una cuenta **vendedora de prueba** (sus credenciales usa el servidor) + crear una
  **compradora de prueba** (mismo país). Comprar en incógnito con tarjetas de prueba de Uruguay
  (titular `APRO` aprobado, `OTHE` rechazado, `CONT` pendiente; CVV 123; venc. 11/30; CI 12345678).
- **A confirmar con una prueba corta antes de programar**: qué enlace abrir con credenciales de
  prueba (`init_point` vs `sandbox_init_point`; las fuentes se contradicen) y cómo detectar que la
  cuenta vendedora es de prueba.
- **Diseño propuesto**: preferencia con importe de la base, UYU, `external_reference` = código
  propio de un solo uso por intento, `binary_mode` (solo aprobado/rechazado), sin Abitab/Red Pagos,
  vencimiento 30 min, `back_urls` + `auto_return`, `notification_url`. Verificación **siempre**
  consultando `GET /v1/payments/{id}` con credencial del servidor (estado approved, código propio,
  importe = total, UYU, `live_mode` false). Webhook: validar `x-signature` (HMAC-SHA256 sobre
  `id:{data.id};request-id:{x-request-id};ts:{ts};` con la clave secreta), responder 200 rápido.
  Sin duplicados: `pago.id_transaccion` único + un pago vigente por factura + bloqueo de la factura;
  si ya hay pago manual → "duplicado a revisar". Pago MP = `medio_pago='mercado_pago'` en la misma
  tabla `pago`.
- **Cambios previstos (a autorizar)**: migración (`medio_pago` acepta `mercado_pago`,
  `pago.id_transaccion` único, tablas `mp_intento` y `mp_evento` solo profesora, funciones
  `crear_intento_mp` y `registrar_pago_mercado_pago` solo servidor); `app/mercadopago.py` con
  `httpx`; rutas iniciar pago / vuelta / webhook público; env vars `MP_MODO=prueba`, credencial de
  prueba, clave del webhook, URL pública. Protecciones: sin `MP_MODO=prueba` no hay botón; rechazar
  `live_mode` true; nunca credenciales de producción. Mercado Pago no acepta `localhost`: propuesta
  de probar y demostrar en **amber** (Julieta controla sus env vars).
- **Decisiones pendientes**: quién crea la cuenta de desarrollador; usar amber para la demo;
  `binary_mode` y excluir Abitab/Red Pagos; autorizar migración y env vars.
- Fuentes: docs de Mercado Pago Uruguay (Checkout Pro: cuentas de prueba, tarjetas de prueba,
  compras de prueba, notificaciones, back URLs) y docs de webhooks (firma).

## 8. Videos On Demand (paso 4) — decisiones e investigación

- **Objetivo**: la profesora sube la grabación de una clase; **todas las usuarias con rol alumna**
  la ven **dentro de Just Show Up durante 24 h desde la publicación** (no desde la grabación ni la
  subida); al vencer se oculta, se corta el acceso (no alcanza con guardar el enlace) y el archivo
  se elimina automáticamente, sin intervención de la profesora. Costo $0, sin tarjetas.
- **Grabación (decidido)**: **Zoom gratuito**, carga **manual** del archivo (sin integración
  Zoom→alojamiento en la v1). Verificado: Zoom gratis corta las reuniones con participantes a
  **40 min** y graba solo localmente en la compu; grabación en la nube y webhooks requieren Zoom
  Pro. Referencia ~40 min por video, **sin límite técnico** de duración.
- **Alojamiento (decidido, sujeto a validación)**: **Mux plan gratuito** (sin tarjeta; hasta 10
  videos a la vez, 100.000 min de reproducción/mes, marca "Mux"). **Antes de implementar, prueba
  corta obligatoria**: confirmar que el plan gratuito permite reproducción firmada (JWT con `exp`;
  la doc no lo excluye pero no lo afirma), subir un archivo grande, que un token vencido corte la
  reproducción, borrar por API, qué pasa con el video 11. Si falla: respaldo con Supabase Storage
  (límite 50 MB por archivo en Free → solo videos cortos/comprimidos).
- **Descartados**: Supabase Storage como principal (50 MB), Cloudinary (100 MB, créditos), Vimeo
  gratis (privacidad paga), YouTube no listado (enlace público; OAuth en modo prueba vence a los 7 días).
- **Diseño propuesto**: subida **directa navegador→Mux** (Vercel limita los pedidos a 4,5 MB);
  estados subiendo/procesando/listo/publicado/retirado/vencido/error (consulta periódica en vez de
  webhooks, así funciona en local); `publicar_video` fija `publicado_en` y `vence_en = +24 h` con la
  hora de la base; `retirar_video`; ocultación por RLS/consulta (`vence_en > now()`); token firmado
  con `exp = vence_en`; limpieza: **pg_cron cada 10 min + pg_net → API de Mux** (clave en Vault) o,
  de respaldo, limpieza al abrir la sección + cron diario de Vercel (Hobby: solo 1 por día).
- **Tabla propuesta `video`**: titulo, descripcion, id_tipo_clase, estado, mux_upload_id,
  mux_asset_id, mux_playback_id, duracion_seg, subido_por, creado_en, publicado_en, vence_en,
  retirado_en, retirado_por, archivo_eliminado_en. Nueva dependencia `PyJWT[crypto]`; claves de Mux
  en `.env`/Vercel. Todo a autorizar.
- **Limitaciones**: no se puede impedir grabar la pantalla; un token emitido sigue valiendo hasta su
  `exp` (por eso retirar también borra el video en Mux); marca "Mux"; máximo 10 videos a la vez;
  el plan gratuito puede cambiar (**regla: nunca registrar tarjeta**).
- **Segunda etapa**: integración automática con Zoom (requiere Pro), videos solo para quienes
  reservaron, avisos por mail, estadísticas, subtítulos, duración configurable.

## 9. Planificación — cancelar clases puntuales (paso 2): especificación completa

**No implementar sin antes revisar el código actual de Planificación y presentar una propuesta
técnica a Julieta.**

- Dentro de **Planificación**, dos opciones claramente diferenciadas (pestañas o selector):
  - **"Licencia / ausencia"** = lo que existe hoy (conservar tal cual): fecha de inicio, fecha de
    finalización, motivo, vista previa de clases afectadas, confirmar.
  - **"Cancelar clases puntuales"** (nuevo):
    1. Elegir una fecha en un calendario.
    2. Ver automáticamente todas las clases programadas de ese día, con **horario y tipo**.
    3. Seleccionarlas con **checkboxes**: una, varias o todas.
    4. Indicar el **motivo**.
    5. Botón **"Cancelar clases seleccionadas"**.
    6. Confirmación mostrando **fecha, horarios y cantidad** de clases seleccionadas.
    7. Confirmar la operación.
- **Ejemplo**: lunes 2/11 con 08:00–09:00, 09:00–10:00 y 17:00–18:00 (Gimnasia); por una consulta
  médica a la mañana se marcan las dos primeras; la de las 17:00 sigue activa. También debe poder
  cancelarse una sola.
- **Reglas**:
  - Solo se cancelan las seleccionadas; las no seleccionadas quedan activas.
  - Las ya canceladas aparecen identificadas y **no se pueden seleccionar**.
  - Con alumnas inscriptas se aplica el **mismo procedimiento de cancelación** que ya usa el
    sistema (reservas canceladas y marcadas, lista de espera cerrada, avisos dentro de la app) y se
    respetan reservas, lista de espera, notificaciones y **facturación** (las reservas canceladas
    no se cobran).
  - **Sin cancelaciones duplicadas**.
  - Varias a la vez = **todo o nada** (sin quedar a medias ante un error).
  - Funciona en **computadora y celular**.
- **Contexto técnico útil** (verificar en el código): hoy Planificación crea un **bloqueo**
  (`crear_bloqueo(p_tipo, p_desde, p_hasta, p_id_horario, p_motivo)`, con vista previa que no
  escribe y `quitar_bloqueo` que recupera reservas). Las reservas que cancela la profesora quedan con
  `reserva.id_bloqueo` (sin FK a propósito). Hay rutas `/profesora/planificacion`,
  `/revisar`, `/confirmar`, `/{id}/eliminar` en `app/main.py` y lógica en `app/clases.py`.
  Probablemente convenga un bloqueo por clase o un bloqueo con lista de clases; definirlo en la propuesta.

## 10. Reglas de seguridad y precauciones

- **Nunca** contraseñas, tokens ni claves en el chat, el repo o el HTML. No imprimirlas ni en
  scripts (el entorno lo bloquea). Credenciales solo en `.env`, `.env.cuentas-prueba` y Vercel.
- **No afectar datos reales de alumnas**: pruebas solo con `@prueba.invalid`, datos marcados
  (`PRUEBA-…`), meses o fechas sin datos reales; registrar lo creado, limpiar y comparar con un
  respaldo. Nunca elegir "la primera fila" de una tabla en una prueba.
- Con datos reales **no visitar** la grilla (`/`), el día (`/dia/…`) ni Mis inscripciones en
  pruebas: generan clases nuevas y procesan listas de espera reales.
- Antes de una migración: respaldo verificado + script de vuelta atrás probado en el Postgres local.
- **Merge a `main` = deploy automático en theta.** Consultar siempre antes.
- No modificar ni cerrar el PR #13. No aplicar la migración de #13.
- No implementar Mercado Pago con credenciales de producción; no registrar tarjetas en Mux ni en
  ningún servicio.
- Si Supabase da errores de conexión, primero revisar si está pausado (no asumir error de código).
- No guardar en `/tmp` nada que haya que conservar (macOS lo limpia).

## 11. Pendiente de decidir al retomar

1. Opción A/B/C para integrar el PR #15 (theta se despliega solo).
2. Si se pushea al PR #15 el commit local con este archivo y `CLAUDE.md` antes de integrarlo
   (recomendado: sí, así la documentación queda en `main`).
3. Luego, propuesta técnica de Planificación (paso 2).
