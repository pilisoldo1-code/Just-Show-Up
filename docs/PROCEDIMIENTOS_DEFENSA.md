# Procedimientos para cerrar antes de la defensa (17/11/2026)

> Preparado el **10/11/2026**. §1, §5 y §6 ya se ejecutaron (con autorización de Julieta); §2–§4 esperan su OK
> (**[autorización]**). Nunca se escriben contraseñas, tokens ni claves en este archivo ni en el chat.

## 1. Regla de los 15 minutos — HECHO (10/11)

**Qué cambió**: solo la función `public._video_evaluar`. Una clase presencial **sin grabación activada** ya no vuelve
«ambigua» una grabación registrada con «Empezar a grabar». No tocó tablas ni datos.

**Cómo se hizo**:
1. Respaldo de producción (§5).
2. Compatibilidad (solo lectura): la función tenía la huella esperada (`c789cf0e693ee53d2b1dbed2691d533f`), 0 horarios
   activados, 0 videos, 0 grabaciones esperando.
3. Migración `20261111000000_videos_regla_15_minutos.sql` aplicada con el conector (registrada como
   `20261110163824 videos_regla_15_minutos`). Huella nueva `c7e3ea3ac4954a748eed3e24734d592f` (igual a la calculada en
   Postgres local); las otras 153 funciones, RLS y políticas, idénticas; `authenticated` y `anon` siguen sin poder
   ejecutarla.
4. Merge del PR #51 (`main` = `3f765ee`); theta desplegada y verificada (Videos muestra el texto nuevo; `/api/health`,
   inicio de sesión, `/admin/login`, `/activar-cuenta`, `/olvide-contrasena` y amber responden).

**Vuelta atrás** (si hiciera falta): `supabase/pendientes/vuelta_atras_videos_regla_15_minutos.sql` deja la función con
la huella anterior (`c789cf0e…`), probado en local; revertir también el merge.

## 2. Prueba completa de Videos On Demand (grabación corta, unos 2 minutos)

**Estado del programa local (10/11)**: corriendo con `launchd` (`com.justshowup.agente`), configuración de producción con
permisos 600 que apunta a amber (amber responde), FFmpeg instalado, lee `~/Documents/Zoom` (tiene acceso total al disco).
Hoy reintenta una reunión de prueba del 09/11 (`sin_clase`); deja de reintentar sola a las 24 h.

**Requisitos / impedimentos a tener en cuenta**
- La única computadora autorizada es la Mac de Julieta y su token es de **`profesora@prueba.invalid`** (Laura
  Profesora, administradora de Just Show Up): la prueba se hace entrando a theta con esa cuenta.
- Desde el 10/11 (regla de los 15 minutos) se puede grabar a cualquier hora, también cerca de las clases de las 8:00 y
  9:00 de esa cuenta.
- **La Mac se duerme al minuto** (`pmset sleep 1`): dejarla enchufada y despierta hasta que termine la subida (por
  ejemplo, con `caffeinate -di` en una terminal o la app abierta), o la subida espera a que se despierte.
- Zoom de escritorio con la cuenta de Julieta iniciada; grabar **en la computadora**; detener la grabación una sola vez.
- Registrar la clase **antes** de empezar a grabar (tolerancia de 2 minutos) y empezar dentro de las 3 horas.
- Una sola clase esperando por profesora: no registrar otra hasta que la primera pase a «Procesando».
- Si Zoom pregunta por convertir la grabación, tocar «Convertir ahora» (si se cierra Zoom antes de convertir, el archivo
  no aparece).

**Pasos**
1. Julieta entra a theta como `profesora@prueba.invalid` → Videos → completa nombre (por ejemplo «Prueba de grabación»),
   tipo y su ID personal de Zoom → **Empezar a grabar**.
2. Acepta «Abrir zoom.us» → **Unirse** → **Grabar** (⌘⇧R) → espera unos 2 minutos → **Finalizar la reunión para todos**.
3. Claude sigue el registro del programa (`~/.config/jsu/agente-trabajo-produccion/agente.log`) y la base, solo leyendo:
   archivo estable 3 minutos → `autorizada` → compresión → subida → `publicado`.
4. La grabación pasa a «Publicada» en «Mis clases grabadas»; como alumna (`alumna@prueba.invalid` o Sofía) se ve en la
   biblioteca y se reproduce; muestra cuánto le queda.
5. Al día siguiente, pasada la hora de publicación + 24 h: ya no se ve; después de la limpieza diaria (cron de amber,
   07:17 UTC) Claude verifica que los archivos se borraron y el registro quedó (`video.estado`, `archivo_eliminado_en`).

## 3. Claves legacy de Supabase

**Riesgo**: theta y amber usan las claves legacy (`eyJ…`). La legacy de servicio se pegó en el chat el 06/10 y nunca se
desactivó: con ella se puede leer y modificar toda la base sin pasar por RLS. Las claves nuevas ya existen (la secreta
está en el `.env` local de Julieta y la app pasa todas las pruebas con ella).

**Procedimiento sin cortes** (legacy y nuevas funcionan a la vez hasta el último paso):
| Paso | Quién | Detalle |
|---|---|---|
| 1 | Julieta | En Supabase → API Keys: confirmar la clave **publicable** (`sb_publishable_…`) y la **secreta** del servidor (crear una propia para theta y otra para amber, así se pueden revocar por separado). |
| 2 | **Pilar** (Vercel de theta) | Settings → Environment Variables: `SUPABASE_KEY` = publicable, `SUPABASE_SERVICE_KEY` = secreta de theta. **Redeploy**. Pasarle la clave por un medio privado, nunca por el grupo. |
| 3 | Julieta / Claude | Verificar theta (solo lectura): `/api/health`, inicio de sesión de las cuentas de demostración, Mis facturas (usa la clave de servidor para Mercado Pago) y la ficha de una alumna (lee el estado de invitación). |
| 4 | Julieta (Vercel de amber) | Lo mismo en amber + Redeploy. Verificar `/api/health`; el programa local no usa claves de Supabase. |
| 5 | Julieta | Recién con los dos sitios verificados: API Keys → Legacy API keys → **Disable**. |
| 6 | Julieta / Claude | Verificar otra vez los dos sitios. Si algo falla: volver a habilitar las legacy (se puede) y revisar las variables. |

Si Pilar no puede antes de la defensa, **no desactivar** las legacy (theta dejaría de funcionar entero) y dejarlo para
después; el riesgo es conocido y acotado a quien tenga esa clave.

## 4. Contraseñas de las cuentas de demostración

**Riesgo**: `profesora@prueba.invalid` administra Just Show Up en un sitio público y las contraseñas de prueba
aparecieron el 10/11 en la salida interna de una sesión de trabajo (no en el chat ni en el repo).

**Procedimiento** (Claude, con autorización; nada se muestra):
1. Un script local genera contraseñas nuevas al azar y las aplica con la clave de administrador
   (`auth.admin.update_user_by_id`) a las cuentas de `.env.cuentas-prueba`.
2. Escribe las nuevas solo en `.env.cuentas-prueba` (permisos 600) antes de aplicarlas; si algo falla, no aplica ninguna.
3. Verifica que cada cuenta entre a su lugar en theta y cierra esas sesiones.
4. Efecto: se cierran las sesiones abiertas de esas cuentas; no cambia nada más. Hacerlo **antes** del último ensayo.
La de la superadministradora la eligió Julieta y no está en el archivo: la cambia ella (Mi perfil) si quiere.

## 3. Claves legacy de Supabase

**Riesgo**: theta y amber usan las claves legacy (`eyJ…`). La legacy de servicio se pegó en el chat el 06/10 y nunca se
desactivó: con ella se puede leer y modificar toda la base sin pasar por RLS. Las claves nuevas ya existen (la secreta
está en el `.env` local de Julieta y la app pasa todas las pruebas con ella).

**Procedimiento sin cortes** (legacy y nuevas funcionan a la vez hasta el último paso):
| Paso | Quién | Detalle |
|---|---|---|
| 1 | Julieta | En Supabase → API Keys: confirmar la clave **publicable** (`sb_publishable_…`) y la **secreta** del servidor (crear una propia para theta y otra para amber, así se pueden revocar por separado). |
| 2 | **Pilar** (Vercel de theta) | Settings → Environment Variables: `SUPABASE_KEY` = publicable, `SUPABASE_SERVICE_KEY` = secreta de theta. **Redeploy**. Pasarle la clave por un medio privado, nunca por el grupo. |
| 3 | Julieta / Claude | Verificar theta (solo lectura): `/api/health`, inicio de sesión de las cuentas de demostración, Mis facturas (usa la clave de servidor para Mercado Pago) y la ficha de una alumna (lee el estado de invitación). |
| 4 | Julieta (Vercel de amber) | Lo mismo en amber + Redeploy. Verificar `/api/health`; el programa local no usa claves de Supabase. |
| 5 | Julieta | Recién con los dos sitios verificados: API Keys → Legacy API keys → **Disable**. |
| 6 | Julieta / Claude | Verificar otra vez los dos sitios. Si algo falla: volver a habilitar las legacy (se puede) y revisar las variables. |

Si Pilar no puede antes de la defensa, **no desactivar** las legacy (theta dejaría de funcionar entero) y dejarlo para
después; el riesgo es conocido y acotado a quien tenga esa clave.

## 4. Contraseñas de las cuentas de demostración

**Riesgo**: `profesora@prueba.invalid` administra Just Show Up en un sitio público y las contraseñas de prueba
aparecieron el 10/11 en la salida interna de una sesión de trabajo (no en el chat ni en el repo).

**Procedimiento** (Claude, con autorización; nada se muestra):
1. Un script local genera contraseñas nuevas al azar y las aplica con la clave de administrador
   (`auth.admin.update_user_by_id`) a las cuentas de `.env.cuentas-prueba`.
2. Escribe las nuevas solo en `.env.cuentas-prueba` (permisos 600) antes de aplicarlas; si algo falla, no aplica ninguna.
3. Verifica que cada cuenta entre a su lugar en theta y cierra esas sesiones.
4. Efecto: se cierran las sesiones abiertas de esas cuentas; no cambia nada más. Hacerlo **antes** del último ensayo.
La de la superadministradora la eligió Julieta y no está en el archivo: la cambia ella (Mi perfil) si quiere.

## 5. Respaldo de producción — HECHO (10/11)

`~/Documents/Just Show Up - respaldos/2026-11-10 antes de la regla de 15 minutos/` (permisos 600, solo consultas
`select`): `descargar_datos.py` del 09/11 más las tablas de Videos (`grabacion_independiente`, `video`,
`video_reproduccion`, `video_programacion`, `video_parametros`; `video_agente` afuera a propósito), `datos.json`
(27 conjuntos, cantidades iguales a producción), huellas de funciones, migración y vuelta atrás.

## 6. Facturas de octubre y asistencias de noviembre — HECHO (10/11, autorizado por Julieta)

**Facturas (Just Show Up, octubre)**: confirmadas **una por una** con la sesión de la administradora (nunca la
confirmación masiva, que habría confirmado las 8). Sin cambios de importes, ajustes ni pagos; Pilates Centro sin tocar.

| Factura | Alumna | Total |
|---|---|---|
| JSU-2026-10-0003 | Valentina Rodríguez | $15.600 |
| JSU-2026-10-0004 | Camila Fernández | $14.200 |
| JSU-2026-10-0005 | Martina Sosa | $14.400 |
| JSU-2026-10-0006 | Paula Castro | $12.200 |
| JSU-2026-10-0007 | Lucía Martínez | $14.800 |
| JSU-2026-10-0008 | Florencia Silva | $9.000 |
| JSU-2026-10-0009 | Sofía Pereira | $6.800 |

- Vencen el **10/11/2026** (confirmadas el día 10: regla «día 10 del mes siguiente»), así que desde el 11/11 se ven
  «Vencida — deuda pendiente» (se pueden pagar igual).
- **Queda en borrador a propósito: la de Marta Gómez (id 32, una clase, $800)**, para mostrar en la defensa la
  confirmación (Facturación → octubre → abrir la factura → «Confirmar»; saldría JSU-2026-10-0010). Se eligió porque es
  corta y porque Marta es la alumna de Mercado Pago: así no tiene una segunda factura pendiente antes de la demostración.
  Usar la confirmación **individual**: la masiva de octubre confirmaría solo esa (es la única en borrador).

**Asistencias (noviembre)**: las 11 pendientes, con «Clases terminadas» (`corregir_asistencias`) de la administradora
de cada empresa y el motivo «Regularización de datos ficticios de demostración: asistencia asignada, no verificada».
Quedó 0 pendientes; las 198 asistencias anteriores, reservas, clases y facturas, idénticas.

| Empresa | Clase | Presentes | Ausentes |
|---|---|---|---|
| Just Show Up | 06/11 8:00 | Marta | — |
| Pilates Centro | 04/11 19:00 | Julia, Noelia | Inés |
| Pilates Centro | 05/11 18:00 | Rocío | Sabrina |
| Pilates Centro | 09/11 19:00 | Rocío, Inés, Sabrina | Julia, Marta |

Total: Just Show Up 1 presente / 0 ausentes; Pilates Centro 6 presentes / 4 ausentes (las ausencias de noviembre se
cobran al 25 % en las facturas de noviembre).
