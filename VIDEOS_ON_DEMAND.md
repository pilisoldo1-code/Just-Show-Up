# Videos On Demand

Las alumnas pueden ver durante **24 horas** la grabación de una clase que se dio por Zoom. La profesora no sube nada a
mano: un programa en su computadora detecta la grabación, la comprime, la sube y la publica sola.

Todo funciona con **costo $0**: Zoom Básico (gratis), FFmpeg (gratis), Supabase Free, Vercel Hobby y un programa en
Python que corre en la computadora de la profesora. No hay servicios pagos ni tareas en servidores aparte.

> Documentación técnica detallada: `CLAUDE.md` (sección «Videos On Demand»). Instalación del programa local:
> `agente/README.md`. Procedimiento de pruebas reales: `agente/PRUEBAS_REALES.md`.

## Cómo funciona

```
Zoom (app de escritorio, grabación local)
  → carpeta de Zoom en la computadora de la profesora
  → programa local (agente/agente_videos.py)
      1. espera a que el archivo deje de crecer (3 minutos)
      2. pregunta al servidor si esa grabación corresponde a una clase autorizada
      3. comprime con FFmpeg (360p, ~400 kbps, ~120 MB para 40 min) y genera la miniatura
      4. pide URLs de subida firmadas y sube las partes directo a Supabase Storage (bucket privado)
      5. pide publicar
  → Just Show Up: el video aparece en «Videos On Demand» de las alumnas y docentes de esa empresa
  → a las 24 h deja de verse; la limpieza diaria borra los archivos y conserva el registro
```

### Quién hace qué

| Persona | Qué hace |
|---|---|
| **Administradora** | Elige qué horarios se graban (`/videos/configuracion`). Lo que no está activado no se procesa ni ocupa espacio. Ve las últimas grabaciones y sus errores. |
| **Profesora** | Da la clase con Zoom desde la computadora donde está instalado el programa y graba en la computadora. Nada más. |
| **Alumna** | Entra a «Videos On Demand», elige la clase y la mira. Ve cuánto tiempo le queda. |
| **Quien administra el sistema** | Crea el token de cada computadora (`scripts/video_agente.py`) y configura el entorno una sola vez. |

### Cómo se identifica la clase

El programa **no decide** la empresa ni la clase: solo informa la hora de inicio (sale del nombre de la carpeta que
crea Zoom, «AAAA-MM-DD HH.MM.SS Tema») y la duración real. El servidor busca la clase **de esa profesora** que empezó
hasta **15 minutos antes o después**. Si hay cero o más de una, no publica. La empresa sale de la clase.

### Reglas de publicación

- Solo horarios activados por la administradora, desde la fecha de activación.
- Duración entre la de la clase −6 min y +1 min (34 a 41 min para una clase de 40). Se ajusta en `video_parametros`.
- Una grabación por clase. Si la reunión dejó más de un video (se detuvo y se volvió a grabar), no se publica.
- Grabaciones de más de 72 horas no se procesan.
- Si la clase todavía no existe en la base (`sin_clase`) o no hay espacio (`sin_espacio`), reintenta cada 30 min
  durante 24 horas.

## Requisitos

### Servidor (Just Show Up)

- Migraciones `20261106000000_videos_on_demand.sql` y `20261108000000_videos_miniaturas.sql` (solo crean objetos
  nuevos: 5 tablas, funciones, 2 políticas y el bucket privado `videos-clases`; no modifican tablas existentes).
- Variables de entorno en Vercel:
  - `CRON_SECRET`: lo manda Vercel a la limpieza diaria (`vercel.json`, `17 7 * * *`, una vez por día: entra en Hobby).
  - `VIDEOS_ENTORNO`: tiene que coincidir con `video_parametros.entorno` y con el `entorno` del programa local.
    Vacía = el servidor rechaza a todos los programas (el módulo queda «dormido»).
  - Opcionales `VIDEOS_SUPABASE_URL` y `VIDEOS_SUPABASE_SERVICE_KEY`: para guardar los archivos en otro proyecto de
    Supabase y que la transferencia de video no afecte al resto de la app.
- No hay dependencias nuevas de Python en el servidor (`requirements.txt` sin cambios).

### Computadora de la profesora

- **Zoom de escritorio** (Mac o Windows) con cuenta Básica gratuita y la grabación **en la computadora** (no en la
  nube, que es paga). La carpeta por defecto en Mac es `~/Documents/Zoom`.
- **Python 3.10 o más nuevo** (el Python 3.9 que trae macOS no sirve).
- **FFmpeg** (`ffmpeg` y `ffprobe`). En las pruebas se usó la compilación firmada y notarizada de Martin Riedl
  (FFmpeg 9.0.2, Apple Silicon), sin Homebrew.
- En Mac: dar **Acceso total al disco** al `python3` que usa el programa (si no, no puede leer `~/Documents`) y dejarlo
  arrancando solo con `launchd` (`agente/com.justshowup.agente.plist.ejemplo`). En Windows: una tarea programada cada
  5 minutos con `--una-vez`.
- Que la computadora quede **prendida y con internet** después de la clase hasta que termine de subir (unos minutos).
- Zona horaria de la computadora = la de las clases (Montevideo).

El programa **no lleva claves de Supabase**: solo la URL del sitio, el `entorno` y un token propio de esa computadora,
del que la base guarda solo la huella.

## Qué tiene que hacer la profesora en cada clase

1. Abrir la reunión desde la **app de escritorio** de Zoom (no desde el celular), a la hora de la clase.
2. Grabar **en esta computadora** (botón Grabar, o la grabación automática si su cuenta la permite).
3. No detener y volver a grabar.
4. Terminar la reunión antes de los 40 minutos (Zoom Básico la corta a los 40) y dejar la computadora prendida unos
   minutos.

## Seguridad

- **Acceso**: la grabación y la miniatura se leen con la sesión de cada persona por políticas de la base: solo
  alumnas activas y docentes de la **empresa activa**, solo videos publicados y sin vencer. Otra empresa, una cuenta
  sin sesión o la API directa no ven nada (probado).
- **Archivos**: bucket privado sin políticas para usuarias. Se reproducen con URLs firmadas que duran como máximo
  90 minutos y nunca más que lo que le queda al video. El video se pide recién al tocar Reproducir.
- **Programa local**: token por computadora (se puede desactivar con `scripts/video_agente.py desactivar`), consulta
  antes de comprimir, y triple coincidencia de entorno (programa, servidor y base). Una base recién migrada queda
  cerrada. Un entorno de pruebas se niega a correr en Vercel o contra producción. Producción se reconoce por huellas
  sha256, sin escribir sus identificadores en el repositorio. El modo producción exige `--produccion`, un entorno
  `produccion-...` y una URL https.
- **Mensajes de error**: se limpian de URLs firmadas, rutas y tokens antes de mostrarse.
- **Topes de consumo** (`video_parametros`): 800 MB de almacenamiento, 3 GB de transferencia por ciclo (con margen de
  1,25) y 300 MB por persona y video. Al llegar al tope se deja de servir el video, nunca se paga de más.
- No se puede impedir que alguien grabe la pantalla ni borrar lo que un dispositivo ya descargó.

## Costos ($0)

| Servicio | Plan | Uso del módulo |
|---|---|---|
| Zoom | Básico (gratis) | Grabación local, sin nube |
| Supabase | Free (1 GB de Storage, 5 GB de transferencia por mes) | Topes propios por debajo de esos límites |
| Vercel | Hobby | Un cron diario (permitido en Hobby) |
| FFmpeg, Python | Software libre | En la computadora de la profesora |

Antes de activar cualquier cosa que pueda facturar, consultar: la condición del proyecto es costo $0.

## Pruebas

- Automáticas: `python -m unittest discover tests` (incluye `test_videos`, `test_agente_videos`, `test_entorno`) y,
  en Postgres local, `tests/base_local/test_videos_base.py` (155), `test_videos_miniaturas_base.py` (33) y
  `test_videos_prueba_base.py` (27).
- **Reales, en el proyecto de prueba `jsu-videos-test`** (organización Free aparte, datos ficticios, 08 y 09/11/2026):
  FFmpeg con un video de 40 min, subida y publicación, reproducción en Chromium y Safari de iPhone, accesos entre
  empresas, vencimiento natural a las 24 h, limpieza real y una **grabación real de Zoom Básico** (72 s) detectada,
  procesada y publicada sola (con el mínimo de duración bajado temporalmente y restaurado).

### Regresión antes de integrar (09/11/2026, rama `videos-on-demand-integracion` = módulo + main `4146f5c`)

| Conjunto | Resultado |
|---|---|
| Pruebas unitarias (`unittest discover tests`) | 555/555 |
| Postgres local (`tests/base_local`, 23 archivos) | 21 sin fallas; 2 con fallas **que también ocurren en main** |
| Supabase local real con las 28 migraciones (PostgREST) | asistencias 24/24, bajas 35/35, cobro por inasistencia 43/43, dos empresas 75/75, altas 95/96 (falla de main), recorrido de pantallas 35/35 con el código del módulo **y** con el de main |
| Menú y permisos por rol contra `jsu-videos-test` | 10/10 |

**Fallas preexistentes de main (no las causa Videos On Demand; dan lo mismo en una copia limpia de `main`):**

- `tests/base_local/test_altas_cuentas_base.py`: se corta con `AttributeError: 'BaseLocal' object has no attribute
  'auth'`. Desde el PR #43, `scripts/empresa_demo.py crear` usa `db.auth`, que el doble de prueba no tiene.
- `tests/base_local/test_multiempresa_3_base.py`: 1 caso («cada línea de factura muestra la profesora que dictó esa
  clase (hay clases de las dos profesoras)») espera clases de dos profesoras y el escenario tiene una.
- `tests/supabase_local/prueba_altas_reales.py`: 1 caso («su ficha ofrece cambiar a profesora»): Mobile administración
  sacó de la pantalla el cambio de rol alumna ↔ profesora (la ruta `/roles` sigue).

**Del módulo:** la misma prueba de altas esperaba el menú de la alumna sin «Videos On Demand». Se actualizó la
expectativa (ahora exige también ese enlace, en quinto lugar), igual que ya lo hacía `tests/test_menu.py`.

## Limitaciones conocidas

- **No verificado con una grabación real de 34–41 min**: el corte de Zoom Básico a los 40 min y el rango de duración
  se probaron solo con un video generado y con la grabación corta. Si la profesora empieza a grabar más de 6 minutos
  tarde, la grabación queda corta y no se publica (se ve en «Últimas grabaciones»).
- **Android** no probado (sí iPhone y computadora).
- **Modo producción del programa** probado solo con pruebas automáticas; la primera ejecución real será en el sitio.
- La limpieza corre una vez por día: los archivos vencidos quedan en Storage hasta esa pasada (no se ven: el acceso
  lo decide `vence_en`).
- Supabase Free pausa el proyecto tras una semana sin actividad (afecta a toda la app, no solo a los videos).
- Las grabaciones muestran a quienes participan de la clase: conviene avisarles a las alumnas que la clase se graba.

## Vuelta atrás

`supabase/pendientes/vuelta_atras_videos_on_demand.sql` (se niega si quedan videos con archivos; quita también las
miniaturas) y `supabase/pendientes/vuelta_atras_videos_miniaturas.sql`. El bucket `videos-clases` se borra desde el
panel de Supabase.
