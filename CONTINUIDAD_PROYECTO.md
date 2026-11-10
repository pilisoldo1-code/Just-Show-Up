# Continuidad del proyecto Just Show Up — traspaso entre sesiones

> Actualizado: **10/11/2026** (`main` = `8cc250f`, desplegado en theta). Para la defensa del **17/11** ver además
> `docs/GUIA_DEMOSTRACION.md` (recorrido por rol), `docs/PROCEDIMIENTOS_DEFENSA.md` (lo que falta autorizar),
> `docs/ARQUITECTURA.md` (diagramas) y `docs/MER.md` (modelo real de la base).
> Leer junto con `CLAUDE.md` (reglas, gotchas, detalle de cada módulo). Este archivo dice
> **dónde quedamos y qué sigue**; `CLAUDE.md` es la referencia permanente.
> No contiene contraseñas, tokens ni claves: esas viven solo en `.env` y `.env.cuentas-prueba`
> (ignorados por git) y en las variables de Vercel.

---

## 0. Resumen en 30 segundos (10/11/2026)

- **En `main` y en theta**: reservas, lista de espera, horarios, tipos de clase, planificación, asistencias (con
  correcciones), facturación (vencimiento, 25 % por inasistencia, PDF), Mercado Pago de prueba, estadísticas,
  **multiempresa** (Just Show Up y Pilates Centro, tres roles, superadministradora, bajas de docentes),
  **Videos On Demand** («Empezar a grabar», PR #45–#46 y #48), **invitaciones por correo** (PR #49: ninguna pantalla
  muestra contraseñas; Magic Link retirado) y pantallas de celular (PR #42, #44, #50).
- **Base de producción**: 29 migraciones aplicadas, iguales a `supabase/migrations/`. Todas las tablas con RLS y sin
  acceso `anon`.
- **Abierto**: PR #51 (regla de los 15 minutos de Videos; necesita migración, ver `docs/PROCEDIMIENTOS_DEFENSA.md` §1).
- **Falta antes de la defensa**: aplicar la regla de 15 minutos, prueba completa de Videos en producción, respaldo nuevo,
  cambiar contraseñas de demostración, decidir facturas de octubre y asistencias de noviembre, ensayos. Claves legacy:
  con Pilar o después de la defensa.
- **Theta es la plataforma de la defensa.** Cualquier push a `main` despliega solo en theta: consultar siempre antes.
- Entrar al sitio entre semana: Supabase Free se pausa tras ~7 días sin uso.

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
    sola a producción cada vez que se actualiza `main`** (cualquier commit, incluso solo de
    documentación). Cada PR genera además un deploy *Preview* automático.
    **Hoy: producción = `7183efc`** (merge del PR #20, deploy OK).
  - **amber** `https://just-show-up-amber.vercel.app` (cuenta de Julieta): **no** se despliega
    sola; solo por CLI desde una copia limpia (sin `.env`). **Sigue con la versión anterior**
    (Planificación, sin Tipos de clase ni facturación). Actualizarla queda pendiente.
- **Estructura**: `app/main.py` (rutas), `app/auth.py` (sesión por cookies `sb_acceso`/`sb_refresco`,
  roles), `app/clases.py` (grilla, reservas, lista de espera, planificación, asistencia),
  `app/tipos_clase.py`, `app/estadisticas.py`, `app/facturas.py` (facturación),
  `app/factura_pdf.py` (PDF con fpdf2), `app/validaciones.py` (incluye cédula),
  `app/templates/` (menú en `_menu.html`), `app/static/style.css`, `supabase/migrations/`,
  `supabase/pendientes/`, `tests/`.
- **Roles**: una profesora (administradora) y alumnas. No hay registro público: la profesora da de
  alta a las alumnas. La seguridad real está en la base (RLS + funciones `security definer`).
- **Módulos hechos y en `main`**: autenticación (etapa 1 + fase 1 de etapa 2), olvidé mi
  contraseña, horarios, grilla y reservas, cancelación (hasta 1 h antes), lista de espera con
  ofertas de 30 min, asistencia, Mi historial, Planificación (licencias por período y
  **cancelación de clases puntuales**), Estadísticas, Tipos de clase, **Facturación mensual +
  pagos simulados + cédula (módulo 6)** y **menú nuevo de la profesora**.

## 2. Entorno local (también para una computadora nueva)

- Clonar: `git clone https://github.com/pilisoldo1-code/Just-Show-Up.git` y trabajar en `main`
  (o en una rama nueva por módulo).
- `.venv` con Python 3.12: `uv venv --python 3.12 .venv && uv pip install -r requirements-dev.txt`
  (`requirements-dev.txt` = `requirements.txt` + httpx, pgserver, psycopg2-binary). Python ≥ 3.10
  para la app; **`pgserver` (Postgres local de las pruebas) solo instala con Python 3.12** (ver
  Gotchas de `CLAUDE.md`).
- **Archivos que NO están en git y hay que copiar a mano por un medio privado** (nunca por chat
  ni por el repo): `.env` (`SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_KEY`) y
  `.env.cuentas-prueba` (formato "mail contraseña" por línea). Sin `.env` la app no arranca; sin
  `.env.cuentas-prueba` no se puede entrar con las cuentas de prueba. Se puede leer y editar el
  código sin ellos; solo hacen falta para probar.
- Servidor: `.venv/bin/uvicorn app.main:app --port 8000 --env-file .env`.
- Supabase se administra con el **conector MCP de Supabase** en Claude Code (hay que tenerlo
  conectado en la sesión nueva para consultar la base; sin él, solo lectura de código).
- Cuentas de prueba: `profesora@prueba.invalid`, `alumna@prueba.invalid` (con contraseña en
  `.env.cuentas-prueba`) y `alumna.uno/dos/tres@prueba.invalid` (sin contraseña: se entra con
  `auth.admin.generate_link` magic link + `verify_otp(token_hash)`). Hay 8 alumnas de demo
  `@demo.invalid` (no tocar). **Nunca mostrar ni escribir contraseñas en el chat.**

## 3. GitHub

- **Al 10/11/2026: `main` = `8cc250f`** (merge del PR #50). Integrados del #25 al #50; abierto el **PR #51** (regla de
  los 15 minutos de Videos, sin integrar). Lo que sigue en esta sección es el registro hasta el 30/10.
- Repo: `pilisoldo1-code/Just-Show-Up`. **`main` = `4fa97ba`** (merge del PR #24, 30/10). Integrados
  por Julieta y desplegados en theta: **PR #21** (Mercado Pago de prueba, 29/10), **PR #22** (guía de la
  defensa y script de la factura de demostración, 29/10), **PR #23** (Mercado Pago solo para
  `alumna@prueba.invalid`, 30/10) y **PR #24** (rediseño de la sección Pago, 30/10). Antes: **PR #19**
  (traspaso del 28/10) y **PR #20** (header responsive), integrados el 28/10.
- **PR #16** "Planificación: cancelar clases puntuales y mejoras del menú de la profesora"
  (rama `mejoras-planificacion-menu`), **PR #17** "Ajuste visual: cupos dinámicos y formulario de
  cancelación puntual" (`ajuste-cupos-tipos-clase`) y **PR #18** "Corregir alineación de los
  checkboxes en Cancelar clases puntuales" (`fix-alineacion-checkboxes-puntual`): autora Josefina
  Bello, **integrados el 27/10** entre las 23:13 y las 23:54 UTC (merge común), desplegados en theta.
- **PR #15** (módulo 6) **integrado el 27/10 a las 21:52 UTC** por `julietaalvez-lgtm`, con
  **merge común (no Squash and merge)**: `main` incluye los 10 commits del PR tal cual.
  - El mensaje del merge **no** tiene la línea `Co-authored-by` de Josefina, pero **sus commits
    originales (`e8f4184`, `a8a52b9`) están en `main` con su autoría** ("Josefina Bello").
  - Checks de Vercel en verde antes del merge (último commit del PR: `60331f1`, documentación).
- **PR #13** (Josefina, rama `modulo-6-resumen` en `a8a52b9`): **GitHub lo marcó automáticamente
  como "merged"** porque sus commits quedaron dentro de `main`. Su rama no se modificó. Su
  migración `20261021000000_resumen_mensual_pagos.sql` **no está aplicada y no debe aplicarse**
  (la reemplaza la del PR #15). Avisarles a Josefina y Pilar por qué figura como integrado.
- Ramas remotas viejas: `clases-proxima-semana`, `modulo-estadisticas`, `modulo-6-resumen`, y las
  ya integradas `modulo-6-resumen-correcciones`, `mejoras-planificacion-menu`,
  `ajuste-cupos-tipos-clase`, `fix-alineacion-checkboxes-puntual`. No borrarlas sin consultar.
- **Rama local de respaldo** (solo en la computadora de Julieta, no en GitHub):
  `respaldo-traspaso-2713384` = el commit de documentación del 27/10 que nunca se subió; su
  contenido quedó incorporado en `docs-traspaso-28-10`.
- La rama `doc-ideas-pendientes` de la computadora de Jose **nunca se subió**; sus cinco ideas
  se reconstruyeron en §11.

## 4. Supabase

- Proyecto activo (`ACTIVE_HEALTHY`) al 28/10. El plan Free **se pausa tras ~1 semana sin uso**:
  si todo da 500, revisar `get_project`; si está pausado, avisar a Julieta y reactivarlo desde el
  panel (Restore). No crear otro proyecto ni cambiar credenciales.
- **13 migraciones aplicadas** (Supabase registra su propia versión al aplicar; los nombres del
  repo difieren en el timestamp, es normal):
  1–10: horario … tipos_de_clase (`20261021002249`).
  11. `20261027205550 profiles_cedula` ← archivo `20261021140000_profiles_cedula.sql`
  12. `20261027205754 resumen_mensual_pagos` ← archivo `20261021150000_resumen_mensual_pagos.sql`
  13. `20261028003643 planificacion_cancelacion_puntual` ← archivo
      `20261027220000_planificacion_cancelacion_puntual.sql`. Verificado el 28/10 (solo lectura):
      `crear_bloqueos_puntuales` existe, es `security definer`, su código es **idéntico** al del
      archivo (huella sin comentarios), la ejecutan `authenticated` y no `anon`.
- Estructura del módulo 6 verificada **idéntica** a la que generan los archivos del repo.
- **Datos al 28/10 (sin cambios: no se probó nada todavía contra la base)**: bloqueo 0, profiles 16
  (ninguna con cédula), tipo_clase 1 (Gimnasia), horario 10, clase 50 (oct–6/11), reserva 144,
  listadeespera 1, asistencia 71; los 10 horarios están activos. **Tablas de facturación vacías** y
  `factura_numero_seq` reiniciada (la primera factura real será 0001).
- **Desde el 31/10**, cuando alguien con rol profesora abra Facturación en octubre, se crean
  automáticamente los **borradores de octubre de las alumnas reales** (funcionamiento previsto).
  Las pruebas no deben abrir meses con datos reales que ya estén en revisión.
- **Respaldos** (fuera del repo, en la Mac de Julieta, con datos personales):
  `~/Documents/Just Show Up - respaldos/2026-10-27 antes de facturación (módulo 6)/`
  (`datos.json`, `restaurar_datos.sql`, huellas de estructura y datos, `vuelta_atras_modulo6.sql`
  — **no usar sin evaluar**: borra facturación y cédulas —, `LEEME.txt`). Respaldo anterior:
  `2026-10-20 antes de Tipos de clase/`. **No están en la otra computadora.**
- **Seguridad pendiente (fase 2)**: `supabase/pendientes/auth_etapa2_cerrar_anon.sql` sigue SIN
  aplicar: sin sesión todavía se puede leer/escribir `horario`, `clase`, `reserva`, `listadeespera`
  (puede alterar lo que entra en un borrador de factura; las confirmadas no). Recomendado antes de
  usar facturación con alumnas reales. Rotación de claves también pendiente (ver `CLAUDE.md`).

## 5. Módulo 6 — facturación y cédula (resumen)

Detalle completo en `CLAUDE.md` (sección "Facturación mensual y pagos") y en la descripción del PR #15.

- **Profesora** (`/profesora/resumenes`, menú "Facturación"): provisorio durante el mes; desde el
  **último día del mes** borradores automáticos; ajustes con motivo; confirmación individual y
  masiva con vista previa (todo o nada); pagos simulados; anular y reemitir; PDF.
- **Alumna** ("Mis facturas"): solo sus facturas confirmadas **desde el día 1 del mes siguiente**
  (lo aplica RLS); PDF "Factura mensual – Just Show Up".
- **Cédula**: `profiles.cedula` opcional, dígito verificador uruguayo, única; la carga la profesora.
  Las facturas guardan foto fija (nombre, apellido, cédula) al confirmar.
- **Pruebas**: `python -m unittest discover tests` (69) · `python tests/base_local/test_facturacion_base.py`
  (180, Postgres local) · `tests/supabase_real/prueba_modulo6_real.py` (73, contra Supabase real
  con cuentas de prueba; necesita la carpeta de respaldo de la Mac de Julieta).
- **Verificación de theta después del merge (27/10, 17/17 OK, solo lectura)**: health, login,
  redirecciones sin sesión; profesora de prueba: Horarios, Facturación (octubre, provisorio),
  Estadísticas, Tipos de clase, Alumnas, Mi perfil, logout; alumna de prueba: Mis facturas, Mi
  perfil (cédula), bloqueo de la facturación de la profesora, logout. Huella de 12 tablas idéntica
  antes y después. A propósito **no** se abrieron la grilla, el día, `/profesora/clases` ni meses
  pasados de Facturación (escriben en la base).
- **No probado contra la base real**: confirmar con clases pendientes, publicación el día 1,
  cambio de año, PDF de una factura real (todavía no hay facturas).

## 6. Estado de tareas

### Estado al 10/11/2026 (lo vigente)
- **Integrado y desplegado del 31/10 al 10/11**: multiempresa (#27–#29), bajas de docentes (#32), correcciones de
  asistencia (#34), cobro del 25 % (#35), escenario de Pilates Centro (#36–#39, #43), estadísticas con gráficas
  (#40–#41), celular de profesora/administradora/alumna (#42, #44), Videos On Demand (#45, #46, #48), Magic Links
  (#47, retirado en #49), invitaciones por correo (#49), tarjetas de días terminados (#50).
- **Correos**: Gmail SMTP de la cuenta exclusiva del proyecto en Supabase Auth (plantillas «Invite user» y
  «Reset Password» en español). Invitación y recuperación probadas por Julieta en theta.
- **Revisión de celular (10/11)**: 27 pantallas de alumna, profesora y administradoras leídas de theta + pantallas de la
  superadministración con datos simulados, a 320/375/390/430 px: sin desbordes ni textos cortados.
- **Abierto**: PR #51 (regla de 15 minutos), sin aplicar ni integrar.
- **Ramas viejas ya integradas** (se pueden archivar): `mobile-administracion` (en #44), `escenario-pilates-asignaciones`
  (en #43), `magic-links` local (reemplazada). `documentacion-mer` quedó reemplazada por `docs/MER.md`.
- **Decisiones pendientes de Julieta** (detalle en `docs/PROCEDIMIENTOS_DEFENSA.md` §6): confirmar las 8 facturas de
  octubre de Just Show Up (al día, $87.800) y registrar las 11 asistencias de noviembre (1 JSU, 10 PIL).
- **Pendiente antes de la defensa**: §1–§5 de `docs/PROCEDIMIENTOS_DEFENSA.md` (regla de 15 minutos, prueba completa de
  Videos, claves legacy, contraseñas de demostración, respaldo) y los ensayos con `docs/GUIA_DEMOSTRACION.md`.
- **Para después de la defensa**: notificaciones por correo (facturas, recordatorios), regla de 48 h para asistencias,
  reasignar horarios de una profesora inactiva, instalador del programa de videos para otras computadoras, estadística
  comparativa entre empresas.

### Histórico (hasta el 30/10)

### Terminado
- Módulo 6 integrado en `main`, desplegado en theta y verificado (§3 y §5).
- Cancelación de clases puntuales y menú nuevo de la profesora: código en `main` y theta (PR
  #16–#18), migración aplicada y verificada, pruebas finales opción A (§9).
- Header responsive y menú de la alumna (PR #20), en theta.
- **Mercado Pago de prueba, completo** (§7): integración, prueba de punta a punta en theta, factura de
  demostración, restricción a una sola alumna y guía de la defensa (`GUIA_DEFENSA_MERCADO_PAGO.md`).
- **Rediseño de la pantalla de factura de la profesora** (PR #24, solo plantilla y CSS): pago en
  tarjeta, pagos anulados e intentos de Mercado Pago en desplegables con etiquetas, «Corregir la factura»
  compacta con «Cómo funciona» e historial en línea de tiempo. Desplegado y verificado en theta.
- Investigación de Videos On Demand (§8).
- Rotación de la clave secreta de Supabase (28/10): nueva `just_show_up_nueva_clave` en el `.env`
  local; la anterior (`servidor_app`) eliminada y verificada revocada. theta y amber usan todavía
  las claves viejas `eyJ…` (legacy), sin cambios.

### Pendiente antes de la defensa (17/11/2026) — lo único necesario
- **Julieta entra al sitio al menos una vez por semana y el día anterior** (Supabase Free se pausa tras
  ~7 días sin uso; si pasa, *Restore* en el panel de Supabase).
- **Unos días antes**, las comprobaciones de `GUIA_DEFENSA_MERCADO_PAGO.md` §2: la compradora de prueba
  inicia sesión en Mercado Pago y tiene saldo ficticio; la factura de septiembre sigue *Pendiente de
  pago* con el botón (si un ensayo la dejó pagada, anular **el pago**, nunca «Anular y reemitir»).
- Desde el 31/10, abrir Facturación en octubre arma los borradores de las alumnas (es lo previsto). No
  hace falta confirmarlos para la defensa.

### Orden de trabajo (actualizado 30/10/2026)
1. ~~Integrar el PR #15~~ — **hecho**.
2. ~~Planificación: cancelar clases puntuales + menú~~ — **hecho** (§9, §10).
3. ~~**Mercado Pago Checkout Pro, exclusivamente en modo de prueba**~~ — **hecho** (§7), con el rediseño
   de la pantalla de pagos.
4. **Videos On Demand**: Zoom gratuito (clases de ~40 min), carga manual y alojamiento gratuito
   sujeto a validación (§8).

**Orden aprobado por Julieta el 30/10 (las etapas siguientes, de a una y con su autorización):**
1. **Vencimiento de facturas** — PR #26. Migración **aplicada en Supabase el 30/10** (`20261030153545
   vencimiento_facturas`): estructura idéntica a la calculada en local, datos idénticos al respaldo, theta y Mercado
   Pago de prueba funcionando con el código anterior. Incidente: una verificación que confirmaba una factura dentro
   de un bloque que se deshace dejó el contador de facturas en `1 / true`; se restauró a `1 / false` con autorización
   (la próxima factura de octubre es `JSU-2026-10-0001`, datos sin cambios). Ver Gotchas de `CLAUDE.md`. Respaldo previo (solo lectura):
   `~/Documents/Just Show Up - respaldos/2026-10-30 antes de vencimiento de facturas/`. Regla en `CLAUDE.md`
   (módulo 6). Al aplicarla, la factura de demostración (confirmada el 29/10, después del 10/10) vence el
   **08/11/2026** y desde el 09/11 se ve «Vencida — deuda pendiente», pero se sigue pudiendo pagar.
2. **Multiempresa** (obligatoria para la defensa; antes, presentar el diseño completo).
3. Cobro del 25 % por ausencias. 4. Mejoras de Estadísticas. 5. Videos On Demand (Supabase Storage con
   interruptor y topes de consumo). 6. Magic Link y notificaciones gratuitas. 7. Revisión de celulares y
   pruebas generales. 8. Preparación de la defensa (última).

### Mejoras futuras (sin fecha, no necesarias para la defensa)
- **Seguridad, etapa 2**: quitar el acceso sin sesión (`anon`) a `horario`, `clase`, `reserva` y
  `listadeespera` (`supabase/pendientes/auth_etapa2_cerrar_anon.sql`) y desactivar las claves legacy de
  Supabase. Ojo: amber tiene código viejo y dejaría de funcionar; hay que decidir antes qué pasa con amber.
- Mails con Resend, actualizar amber, fase 2 de Tipos de clase (borrar columnas viejas de `horario`),
  borrar cuentas y datos de prueba antes del uso real.

### Necesita autorización explícita de Julieta (no hacerlo por iniciativa propia)
- Cualquier **merge**, **push** (un push a `main` despliega theta), **deploy**, **migración** o
  escritura en Supabase real, crear tablas/columnas/env vars, nuevas dependencias, crear cuentas en
  servicios externos, aplicar la fase 2 de seguridad.

## 7. Mercado Pago Checkout Pro — SOLO MODO DE PRUEBA (paso 3)

**Condiciones de Julieta (siempre):** solo cuentas, tarjetas y pagos ficticios; ningún costo ni
dinero real; nunca credenciales productivas ni tarjetas reales; theta sigue siendo la plataforma
principal y de la defensa; no modificar datos existentes; no exponer credenciales; ningún pago se
marca aprobado sin verificarlo con Mercado Pago. **Cada etapa necesita su autorización.**

**Decisiones tomadas**
- **Plataforma: theta** (no amber, sin base duplicada). El código llega a theta por PR, como siempre.
- **Credenciales en Supabase Vault (opción B)**, no en Vercel: theta es de Pilar y el plan Hobby no
  permite sumar integrantes. Se comprobó (28/10) que theta tiene su `SUPABASE_SERVICE_KEY`
  (el logout anula la sesión; control sin logout: la sesión sigue válida).
- Interruptor `mp_configuracion.habilitado` (arranca **apagado**). Solo alumnas `@demo.invalid` y
  `@prueba.invalid` (desde la restricción del ítem 7, solo `alumna@prueba.invalid`). Botón blanco con el logo oficial (reglas de marca: no sobre celeste/azul).
- Sin costos: Mercado Pago de prueba, Supabase Free (Vault incluido) y Vercel Hobby son gratis.

**Etapa 1 — prueba técnica (hecha, 28/10, sin tocar base ni código)**
- Aplicación Checkout Pro de Julieta (cuenta personal, Uruguay). La **vendedora de prueba** se crea
  sola con la app (sus credenciales son las "de prueba"; el token empieza con `APP_USR`); Julieta
  creó una **compradora de prueba** (Uruguay, saldo ficticio). Ambas en su nota privada.
- `GET /users/me`: `test_user` + `user_product_seller`, `MLU`, coincide con `MP_USER_ID`.
- Pago aprobado de $1 (operación 186586783238, saldo ficticio) y rechazado (185540909609, tarjeta
  Mastercard de prueba 5031 7557 3453 0604, titular `OTHE`, documento **Otro 123456789**). La
  actividad de la cuenta personal de Julieta quedó vacía.
- **`live_mode` viene `true`** con estas cuentas (ver Gotchas de `CLAUDE.md`); el link correcto es
  `init_point`; comisión simulada ($0,06) a cargo de la vendedora de prueba.

**Etapa 2 — desarrollo (hecha, 29/10, rama `mercado-pago-prueba`; integrada con el PR #21)**
- `app/mercadopago.py`, rutas al final de `app/main.py`, botón en `mis_facturas.html`, pago e
  intentos en `profesora_factura.html`, migración `20261029120000_mercado_pago_prueba.sql`
  (**sin aplicar**), logo oficial en `app/static/img/`. Detalle técnico en `CLAUDE.md`.
- Pruebas: 73/73 de la migración en Postgres local, 166/166 de la app (49 de Mercado Pago),
  180/180 facturación y 33/33 cancelación puntual locales, navegador escritorio/celular con datos
  simulados.

**Etapas que faltan (cada una con autorización)**
3. **Base**, en 5 pasos con autorización de a uno:
   - ~~Paso 1: respaldo~~ — **hecho 29/10** (solo lectura): `~/Documents/Just Show Up - respaldos/
     2026-10-29 antes de Mercado Pago/` (13 tablas, 16 usuarias sin contraseñas, huellas de
     estructura y datos, secuencia de facturas; bucket `tipos-clase` vacío). Restauración verificada
     en Postgres local: idéntica. Ver su `LEEME.txt`.
   - ~~Paso 2: vuelta atrás~~ — **hecho 29/10** (solo local): `supabase/pendientes/
     vuelta_atras_mercado_pago.sql`, probado con `tests/base_local/vuelta_atras_mercado_pago.py`
     sobre la copia del respaldo: 24/24 en 3 corridas (aplicar → usar → deshacer ×3 = idéntico).
   - ~~Paso 3: aplicar la migración~~ — **hecho 29/10** como `20261029134035 mercado_pago_prueba`
     (14 migraciones). Antes: planes verificados (Supabase Free, Vercel Hobby, GitHub Free) y base
     idéntica al respaldo. Después: datos de las 13 tablas y secuencia de facturas **idénticos**,
     estructura **idéntica** a la calculada en local, interruptor apagado, Vault vacío, sin intentos
     ni eventos; sin sesión todo lo nuevo da 401; theta 17/17. Vuelta atrás: el script del paso 2.
   - ~~Paso 4: guardar el token~~ — **hecho 29/10**: antes se verificó con `GET /users/me` que es de la
     vendedora de prueba (test_user, MLU); un script local lo leyó del `.env` y lo guardó con
     `mp_guardar_secreto`. En Vault queda **cifrado** (secreto `mp_access_token`); la huella sha256 de lo
     guardado = la del `.env`. Sin clave de avisos (opcional).
   - ~~Paso 5 (comprobaciones)~~ — hechas junto con el paso 4: sin sesión, alumna y profesora de prueba
     (sesiones reales) no pueden leer la configuración, el Vault ni la tabla, ni guardar el token ni
     encender Mercado Pago (401/403/406), y el token no aparece en ninguna respuesta; iniciar un pago
     devuelve `deshabilitado`. Datos idénticos antes/después, interruptor apagado, theta 17/17, el token
     no está en ningún archivo fuera del `.env`.
4. ~~**PR #21**~~ — **integrado el 29/10** por Julieta (`0810ee0`); theta desplegada con Mercado Pago apagado.
5. ~~Prueba de punta a punta en theta~~ — **hecha el 29/10** (no hizo falta esperar al 1/11): factura
   ficticia de $1 de septiembre para `alumna@prueba.invalid`; Julieta confirmó que theta está en el
   plan gratuito de Vercel; interruptor **encendido** desde las 11:24. Pago rechazado (186675619836,
   registrado por el aviso y por la vuelta) y aprobado (185628106611: aviso `ok`, vuelta
   `ya_registrado`, sin duplicado), vista de la profesora correcta y anulación del pago (factura otra
   vez pendiente). Datos reales idénticos al respaldo.
6. ~~Factura de demostración para la defensa~~ — **hecha el 29/10**: se borró solo lo de la prueba de $1
   (factura 18, pago 9 y su anulación, intentos 1–4, avisos 1–4, tipo 80, horario 203, clase 6480,
   reserva 1191) y se creó con `scripts/factura_demo_mp.py` la factura **19 = JSU-2026-09-0001, $800**,
   confirmada, publicada y pendiente (tipo «Gimnasia (demostración)» 81 y horario lunes 10:00 204,
   **inactivos**; clase 6531 del 7/9; reserva 1192). Manifiesto en
   `~/Documents/Just Show Up - respaldos/registro_demo_mp.json`. Contador de facturas reiniciado
   (`setval(..., 1, false)`) antes y después: la primera factura real de octubre será
   **JSU-2026-10-0001** (el número incluye año y mes: no chocan). Comparación fila por fila con el
   respaldo antes y después: idéntica. En theta (solo lectura, sin pagar): la alumna ve la factura y
   el botón; la profesora la ve sin botón. **Mercado Pago queda encendido hasta la defensa.**
   Guía: `GUIA_DEFENSA_MERCADO_PAGO.md`. **Nunca «Anular y reemitir» la factura de demostración**
   (con el contador en 1 chocaría el número): solo anular el pago.
7. **Restricción a una sola alumna** (autorizada a preparar el 29/10; rama `mercado-pago-solo-alumna-demo`):
   solo `alumna@prueba.invalid` puede iniciar un pago (las 8 alumnas demo, las demás `@prueba.invalid` y
   las reales no). Servidor (`mercadopago.ALUMNA_HABILITADA`) y base (migración
   `20261029180000_mercado_pago_solo_alumna_demo.sql`, solo cambia la condición del mail de
   `crear_intento_mp`). Respaldo de la función de producción en
   `~/Documents/Just Show Up - respaldos/2026-10-29 antes de restringir Mercado Pago/`; vuelta atrás
   `supabase/pendientes/vuelta_atras_solo_alumna_demo.sql` (probada: byte a byte igual a producción).
   Pruebas: 29/29 nueva, 73/73, 180/180, 33/33 locales y 170 unitarias. **Migración APLICADA en Supabase
   el 29/10** como `20261029152415 mercado_pago_solo_alumna_demo` (autorizada por Julieta), después de
   verificar respaldo, función idéntica a la respaldada y datos reales idénticos. Después: función distinta
   solo en la línea del mail, mismos permisos; simulando cada una de las 16 cuentas en un bloque que se
   deshace, solo `alumna@prueba.invalid` obtiene `ok` (8 demo, 3 de prueba y 3 reales:
   `alumna_no_habilitada`; profesora: `no_autorizada`); en theta (todavía con el código anterior)
   `sofia@demo.invalid` forzando el pago queda rechazada por la base; la alumna autorizada ve la factura
   y el botón; 0 intentos, 0 pagos, contador y datos reales idénticos; credencial de prueba intacta.
   **Código del servidor integrado (PR #23, `42aef09`, 30/10)** y verificado en theta: `sofia@demo.invalid`
   forzando el pago queda rechazada **por el servidor** (los registros de Supabase muestran que no llega
   a llamar a `crear_intento_mp`); la base mantiene su propio control.
8. **Rediseño de la pantalla de factura de la profesora** (PR #24, `4fa97ba`, 30/10): solo plantilla y CSS,
   formularios idénticos a los anteriores; probado con 7 escenarios simulados en computadora y celular
   y verificado en theta. Ajuste posterior de «Cómo funciona» (caja que contiene título y explicación).
   Un intento «Sin completar» del 29/10 en la factura de demostración es de Julieta (abrió Mercado Pago
   sin pagar): queda como historial, no afecta nada.

**Datos para pagar en las pruebas:** compradora de prueba (usuario y contraseña en la nota de
Julieta) en ventana de incógnito; tarjeta Mastercard 5031 7557 3453 0604, venc. 11/30, CVV 123,
documento **Otro 123456789**; titular `APRO` = aprobado, `OTHE` = rechazado. O pagar con el saldo
ficticio de la compradora (siempre aprueba).

## 8. Videos On Demand (paso 4) — decisiones e investigación

> **Actualizado 03/11 (Pilar): el pedido cambió.** Ya no es carga manual: la grabación de Zoom Basic se sube sola con un
> programa local y, por ahora, los archivos van a Supabase Storage divididos en partes. Mux gratuito sigue en evaluación
> y sin activar. Rama `feature/videos-on-demand`; estado y detalle en `CLAUDE.md` («Videos On
> Demand»). Lo que sigue abajo es la decisión anterior del 27/10 y queda como referencia.

- **Objetivo**: la profesora sube la grabación de una clase; **todas las usuarias con rol alumna**
  la ven **dentro de Just Show Up durante 24 h desde la publicación**; al vencer se oculta, se
  corta el acceso (no alcanza con guardar el enlace) y el archivo se elimina automáticamente, sin
  intervención de la profesora. Costo $0, sin tarjetas.
- **Grabación (decidido)**: **Zoom gratuito**, carga **manual** del archivo. Zoom gratis corta
  las reuniones con participantes a **40 min** y graba solo localmente; grabación en la nube y
  webhooks requieren Zoom Pro. Referencia ~40 min por video, sin límite técnico de duración.
- **Alojamiento (decidido, sujeto a validación)**: **Mux plan gratuito** (sin tarjeta; hasta 10
  videos a la vez, 100.000 min de reproducción/mes, marca "Mux"). **Antes de implementar, prueba
  corta obligatoria**: reproducción firmada (JWT con `exp`) en el plan gratuito, subir un archivo
  grande, que un token vencido corte la reproducción, borrar por API, qué pasa con el video 11.
  Si falla: respaldo con Supabase Storage (50 MB por archivo en Free → solo videos cortos).
- **Descartados**: Supabase Storage como principal (50 MB), Cloudinary (100 MB, créditos), Vimeo
  gratis (privacidad paga), YouTube no listado (enlace público; OAuth en modo prueba vence a los 7 días).
- **Diseño propuesto**: subida **directa navegador→Mux** (Vercel limita los pedidos a 4,5 MB);
  estados subiendo/procesando/listo/publicado/retirado/vencido/error (consulta periódica en vez de
  webhooks); `publicar_video` fija `publicado_en` y `vence_en = +24 h` con la hora de la base;
  `retirar_video`; ocultación por RLS/consulta (`vence_en > now()`); token firmado con
  `exp = vence_en`; limpieza: pg_cron cada 10 min + pg_net → API de Mux (clave en Vault) o, de
  respaldo, limpieza al abrir la sección + cron diario de Vercel (Hobby: solo 1 por día).
- **Tabla propuesta `video`**: titulo, descripcion, id_tipo_clase, estado, mux_upload_id,
  mux_asset_id, mux_playback_id, duracion_seg, subido_por, creado_en, publicado_en, vence_en,
  retirado_en, retirado_por, archivo_eliminado_en. Nueva dependencia `PyJWT[crypto]`; claves de Mux
  en `.env`/Vercel. Todo a autorizar.
- **Limitaciones**: no se puede impedir grabar la pantalla; un token emitido sigue valiendo hasta su
  `exp` (por eso retirar también borra el video en Mux); marca "Mux"; máximo 10 videos a la vez;
  el plan gratuito puede cambiar (**regla: nunca registrar tarjeta**).
- **Segunda etapa**: integración automática con Zoom (requiere Pro), videos solo para quienes
  reservaron, avisos por mail, estadísticas, subtítulos, duración configurable.

## 9. Planificación — cancelar clases puntuales (terminada)

**Implementado e integrado** (PR #16, con ajustes visuales en #17 y #18; detalle técnico en
`CLAUDE.md`, "Planificación: cancelar clases puntuales").

- `/profesora/planificacion` tiene dos pestañas: **"Licencia / ausencia"** (lo de antes, sin
  cambios: rango de fechas, motivo, vista previa, confirmar) y **"Cancelar clases puntuales"**:
  elegir fecha → ver las clases del día con hora y tipo → checkboxes (una, varias o todas) →
  motivo → diálogo de confirmación con fecha, horarios y cantidad → cancelar. Las ya canceladas o
  empezadas aparecen identificadas y sin checkbox.
- **Modelo**: un `bloqueo` de tipo `puntual` por clase elegida (`fecha_desde = fecha_hasta`, con su
  `id_horario`), así "Bloqueos registrados" y "Eliminar" (recuperación de reservas) funcionan igual
  que con las licencias. Función `crear_bloqueos_puntuales(p_ids_clase, p_motivo)`: solo profesora,
  todo o nada (`clases_invalidas` si alguna no existe, no está `programada`, ya empezó o su
  horario no está activo), bloquea las filas de `clase` (sin carreras), cancela clase + reservas
  confirmadas (con `id_bloqueo`, así no se cobran en la facturación) + lista de espera (`expirado`).
- **Probado sin la base real**: 33 casos en Postgres local
  (`tests/base_local/test_planificacion_puntual_base.py`), 24 de rutas/lógica con datos simulados
  (`tests/test_planificacion.py`), `tests/test_cupos_tipos_clase.py` y navegador con datos
  simulados. El PR #17 informa 99/99 en `unittest discover tests`.
- **Pruebas finales contra Supabase real: HECHAS el 28/10 con la opción A (solo lectura, sin crear
  datos):** las 6 funciones de Planificación idénticas al repo; alumna → `no_autorizada`, anon →
  sin permiso (por SQL y por la API pública); profesora con id inexistente → `clases_invalidas`,
  sin clases y motivo vacío rechazados; 33/33 locales (incluye horario inactivo); licencias
  (`crear_bloqueo`/`quitar_bloqueo`) sin cambios; huella de las tablas idéntica antes y después.
  Revisión visual en theta (escritorio y celular) sin elegir fecha. No se probó una cancelación
  real (la primera la hará la profesora).

### Contradicción detectada en el plan de pruebas (resolver antes de probar)

- Las pruebas reales anteriores (p. ej. `tests/supabase_real/prueba_modulo6_real.py`) crean
  **horarios de prueba inactivos** para que las alumnas reales no los vean ni los reserven.
- Pero `crear_bloqueos_puntuales` **exige horario activo** (`h.activo`) y `generar_clases` solo
  genera clases de horarios **y** tipos de clase activos. Con un horario de prueba inactivo, la
  cancelación puntual **siempre** devuelve `clases_invalidas`: el camino feliz no se puede probar así.
- Un horario de prueba **activo** sí aparece en la grilla de las alumnas reales mientras exista.
- **Opciones (decide Julieta)**:
  - **A (recomendada)**: el camino feliz queda cubierto por las 33 pruebas del Postgres local;
    contra la base real probar solo lo que no se ve: permisos (alumna y sin sesión rechazadas),
    motivo vacío, sin clases, y que un horario inactivo dé `clases_invalidas`.
  - **B**: además, una prueba corta y controlada con un horario **activo** de un tipo `PRUEBA-…`, en
    un día y hora sin clases reales: crear la clase, cancelarla, quitar el bloqueo, desactivar y
    borrar todo, con respaldo y huella de las tablas antes y después.
- Precauciones: abrir la pestaña puntual con una `fecha` ejecuta `generar_clases` para esa semana
  (escribe en la base). Identificar lo propio por marcadores (`PRUEBA-…`, `@prueba.invalid`),
  nunca "la primera fila".

## 10. Menús y header (hechos, PR #16 y PR #20)

Orden actual (`app/templates/_menu.html`): **Horarios · Tipos de clase · Asistencias ·
Planificación · Alumnas · Facturación · Estadísticas**.

- «Clases» pasó a **«Asistencias»** (la URL sigue siendo `/profesora/clases`).
- **Tipos de clase** es una opción propia del menú (el botón duplicado dentro de Horarios se quitó en el PR #20).
- **Facturación** sigue visible en el menú principal.
- La pestaña activa se resalta en **verde menta** (`--accent`, clase `activo` según
  `request.url.path`), también en las pantallas internas de cada sección.
- **Header (PR #20)**: fuera de `<main>`, mismo ancho (hasta 1200 px) en todas las pantallas:
  «Just Show Up» · secciones · perfil. **Hasta 900 px**: botón ☰ con lista vertical (se cierra con ✕,
  tocando afuera, Escape o eligiendo una sección). Perfil = Mi perfil + Cerrar sesión (los dos roles).
- **Alumna**: Clases · Mis inscripciones · Mi historial · Mis facturas en el menú principal.
- Horarios: tabla con scroll propio en celular. Una revisión integral para celulares sigue como
  idea a evaluar (§11, E).

## 11. Ideas pendientes para evaluar (NO aprobadas para implementar)

Anotadas el 27/10 en la computadora de Jose (rama local `doc-ideas-pendientes`, nunca subida) y
reconstruidas acá el 28/10. **Son ideas para evaluar con Julieta, no funcionalidades aprobadas.**
Cada una necesita su propuesta y autorización antes de programar.

- **A. Cobro por inasistencia**: evaluar cobrar el **25 % del precio de la clase** a una alumna que
  reservó y no asistió, en lugar del 100 %. Hoy la regla es que se cobra igual (reserva confirmada
  de clase dictada, presente, ausente o sin registrar). Afecta la facturación (`resumen_mensual_detalle`
  y sus funciones) y las reglas de negocio de la especificación.
- **B. Notificaciones**: investigar avisos automáticos cuando la profesora cancela una clase
  (correo electrónico, notificaciones push o WhatsApp), pensando sobre todo en el uso desde
  celulares. Hoy los avisos son solo dentro de la app; el correo depende de Resend (falta cuenta y
  dominio verificado).
- **C. Magic Links**: evaluar enlaces por correo para iniciar sesión o recuperar el acceso a la
  cuenta. Hoy hay login con contraseña y recuperación por link de Supabase; el mailer gratuito de
  Supabase tiene límites (ver Gotchas de `CLAUDE.md`).
- **D. Estadísticas**: agregar gráficas e indicadores visuales al módulo de Estadísticas (hoy
  muestra números y listas; sin framework JS).
- **E. Mobile**: revisar y mejorar integralmente el diseño y la experiencia de uso desde celulares,
  tanto para alumnas como para la profesora.

## 12. Reglas de seguridad y precauciones

- **Nunca** contraseñas, tokens ni claves en el chat, el repo o el HTML. No imprimirlas ni en
  scripts. Credenciales solo en `.env`, `.env.cuentas-prueba` y Vercel.
- **No afectar datos reales de alumnas**: pruebas solo con `@prueba.invalid`, datos marcados
  (`PRUEBA-…`), meses o fechas sin datos reales; registrar lo creado, limpiar y comparar con una
  huella de las tablas antes/después. Nunca elegir "la primera fila" de una tabla en una prueba.
- Con datos reales **no visitar** la grilla (`/`), el día (`/dia/…`), Mis inscripciones,
  `/profesora/clases` ni la pestaña puntual de Planificación con una fecha en pruebas: generan
  clases nuevas y/o procesan listas de espera reales. Al probar con requests, no seguir la
  redirección del login de alumna (va a `/`).
- Antes de una migración: respaldo verificado + script de vuelta atrás probado en el Postgres local.
- **Push o merge a `main` = deploy automático en theta.** Consultar siempre antes.
- No aplicar la migración del PR #13.
- No implementar Mercado Pago con credenciales de producción; no registrar tarjetas en Mux ni en
  ningún servicio.
- Si Supabase da errores de conexión, primero revisar si está pausado (no asumir error de código).
- No guardar en `/tmp` nada que haya que conservar (macOS lo limpia).

## 13. Al retomar (próxima sesión)

1. Leer este archivo y `CLAUDE.md`; verificar con `git` y `gh` que `main` coincide con §3, que
   no hay PR abiertos pendientes de integrar y que Supabase
   está activo (sin modificar nada).
2. Avisar si faltan `.env` (con `MP_ACCESS_TOKEN` y `MP_USER_ID`) / `.env.cuentas-prueba` o el
   conector de Supabase en la computadora.
3. Mercado Pago está **encendido** para la defensa del 17/11 (§7, `GUIA_DEFENSA_MERCADO_PAGO.md`):
   no hacer pagos, no tocar la factura de demostración ni el interruptor sin pedido de Julieta.
   Lo pendiente antes de la defensa y las mejoras futuras están en §6.
4. Las ideas de §11 solo se evalúan si Julieta lo pide.
