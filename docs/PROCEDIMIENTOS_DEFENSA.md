# Procedimientos para cerrar antes de la defensa (17/11/2026)

> Preparado el **10/11/2026**. Nada de esto se ejecutó: cada paso marcado **[autorización]** espera el OK de Julieta.
> Nunca se escriben contraseñas, tokens ni claves en este archivo ni en el chat.

## 1. Regla de los 15 minutos (PR #51, rama `videos-regla-15-min`, commit `a0b8e64`)

**Qué cambia**: solo la función `public._video_evaluar`. Una clase presencial **sin grabación activada** deja de volver
«ambigua» una grabación registrada con «Empezar a grabar». No toca tablas ni datos.

**Compatibilidad comprobada (10/11, solo lectura)**: la función de producción tiene la huella que espera la migración
(`c789cf0e693ee53d2b1dbed2691d533f`); `_video_programada` existe; 0 horarios activados, 0 videos, 0 grabaciones
esperando; `authenticated` no puede ejecutar `_video_evaluar` (sigue así después). Pruebas: 27/27 de la regla, 155/155,
124/124, 33/33 y 27/27 de Videos en Postgres local; 596/596 generales.

**Pasos** (en este orden; el código actual de theta funciona antes y después de la migración):
1. **[autorización]** Respaldo de producción (§5).
2. **[autorización]** Aplicar `supabase/migrations/20261111000000_videos_regla_15_minutos.sql` con el conector de Supabase.
3. Verificar (solo lectura): la huella nueva de `_video_evaluar` tiene que ser `c7e3ea3ac4954a748eed3e24734d592f`
   (calculada en Postgres local con todas las migraciones) y el resto de las funciones, iguales.
4. **[autorización]** Merge del PR #51 (cambia el texto de la pantalla de Videos). theta se despliega sola.

**Vuelta atrás**: `supabase/pendientes/vuelta_atras_videos_regla_15_minutos.sql` deja la función con la huella de hoy
(`c789cf0e…`), probado en local. Si hiciera falta, revertir también el merge.

## 2. Prueba completa de Videos On Demand (grabación corta, unos 2 minutos)

**Estado del programa local (10/11)**: corriendo con `launchd` (`com.justshowup.agente`), configuración de producción con
permisos 600 que apunta a amber (amber responde), FFmpeg instalado, lee `~/Documents/Zoom` (tiene acceso total al disco).
Hoy reintenta una reunión de prueba del 09/11 (`sin_clase`); deja de reintentar sola a las 24 h.

**Requisitos / impedimentos a tener en cuenta**
- La única computadora autorizada es la Mac de Julieta y su token es de **`profesora@prueba.invalid`** (Laura
  Profesora, administradora de Just Show Up): la prueba se hace entrando a theta con esa cuenta.
- **Hasta aplicar la regla de los 15 minutos**, no grabar entre las 7:45 y las 9:15 de lunes a viernes (esa cuenta da las
  clases de las 8:00 y 9:00): el video no se publicaría (`clase_ambigua`).
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

## 5. Respaldo de producción

**Procedimiento (solo lectura)**: copiar `descargar_datos.py` del respaldo del 09/11 a una carpeta nueva
`~/Documents/Just Show Up - respaldos/2026-11-10 antes de la regla de 15 minutos/` y agregar a su lista las tablas de
Videos (`grabacion_independiente`, `video` sin la columna `miniatura`, `video_reproduccion`, `video_programacion`,
`video_parametros`; `video_agente` queda afuera a propósito). El script solo hace consultas `select`, lista las cuentas
(sin contraseñas) y descarga las imágenes de tipos de clase; guarda con permisos 600 y muestra solo cantidades. Además,
se guardan las huellas de las funciones (`pg_get_functiondef`) y la lista de migraciones aplicadas.
**[autorización]** para ejecutarlo.

## 6. Decisiones pendientes de Julieta: asistencias y facturas

Investigado el 10/11 (no repetir):
- **11 asistencias pendientes, todas de noviembre** (1 en Just Show Up: Marta 06/11 8:00; 10 en Pilates Centro: 04/11,
  05/11 y 09/11). No afectan octubre. Si quedan sin registrar, en noviembre se cobran al 100 %.
- **8 facturas de octubre de Just Show Up en borrador** ($87.800 en total), **al día** con las reservas y sin asistencias
  pendientes (105 presentes al 100 %, 19 ausentes al 25 %), sin ajustes. Las alumnas no las ven hasta confirmarlas.
- Si se confirman: números JSU-2026-10-0003 en adelante, vencen 10 días después de la confirmación, quedan visibles para
  las alumnas y ya no se editan (solo «Anular y reemitir»). Marta tendría una segunda factura pendiente ($800) además
  de la de la demostración de Mercado Pago.
- **Cómo**: Facturación → octubre → «Confirmar facturas del mes» (administradora de JSU). Asistencias → noviembre →
  Pendientes (administradora de cada empresa, con motivo), o para Pilates `scripts/empresa_demo.py actualizar`.
- **Decisión pendiente de Julieta**: confirmar o no las facturas de octubre y registrar o no las asistencias de noviembre.
