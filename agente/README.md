# Programa local de Videos On Demand

Corre en la computadora donde la profesora da la clase con Zoom, porque Zoom gratuito guarda la grabación solo ahí.

**Estado:** probado de verdad en el proyecto de prueba `jsu-videos-test` (FFmpeg, Supabase Storage, iPhone y una
grabación real de Zoom Básico de 72 s publicada sola). No se probó una grabación real de 34–41 min ni el modo
`--produccion` fuera de las pruebas automáticas. Descripción completa del módulo: `../VIDEOS_ON_DEMAND.md`.

## Qué tiene que hacer la profesora

1. Iniciar la reunión desde la **app de escritorio** de Zoom. Desde el celular no se graba en la computadora.
2. Empezar a la hora de la clase y terminar antes de los 40 minutos, que es cuando Zoom corta.
3. No detener y volver a grabar: una reunión con más de un video no se publica.
4. Dejar la computadora prendida y con internet hasta que termine de subir.

## Antes de instalar

La administradora activa en Just Show Up qué horarios se graban: Videos On Demand > "Elegir qué horarios se graban".
Lo que no esté activado no se comprime ni se sube.

## Instalación (una sola vez)

1. Instalar Python 3.10 o más nuevo y FFmpeg (`ffmpeg` y `ffprobe`).
2. En Zoom web, Settings > Recording: activar **Automatic recording** en **On the local computer**.
3. Revisar que la computadora tenga la zona horaria de las clases: se usa para saber a qué clase corresponde.
4. Crear el token de esa computadora. Lo hace quien administra el sistema, contra el entorno que corresponda:

   ```bash
   python scripts/video_agente.py crear profesora@ejemplo.com "Notebook de la profesora"
   ```

   Se muestra una sola vez y en la base queda solo su huella. No mandarlo por chat. Contra un proyecto que no sea
   local, el script exige `--remoto`.
5. Copiar `config.ejemplo.json` como `config.json`, pegar el token y completar `entorno`. En Mac o Linux:
   `chmod 600 config.json`. En pruebas, `scripts/videos_prueba_datos.py` genera este archivo solo.

   El programa solo necesita `url`, `entorno` y `token`. **No lleva ninguna clave de Supabase.** Si `ffmpeg` no está en
   el PATH, indicar su ruta completa en `ffmpeg` y `ffprobe`.
6. Hacer que arranque solo.

### Mac

1. Copiar `com.justshowup.agente.plist.ejemplo` a `~/Library/LaunchAgents/com.justshowup.agente.plist` y cambiar las
   rutas. `launchd` no usa el PATH de la terminal: por eso el ejemplo lo define.
2. En Ajustes del Sistema > Privacidad y seguridad > **Acceso total al disco**, agregar el `python3` que se va a usar.
   Sin esto no puede leer `Documentos/Zoom`, y queda registrado en `trabajo/agente.log`.
3. Cargarlo:

   ```bash
   launchctl load ~/Library/LaunchAgents/com.justshowup.agente.plist
   ```

### Windows

Crear una tarea en el Programador de tareas que ejecute `python agente_videos.py --una-vez` cada 5 minutos. Si Documentos
está en OneDrive, indicar la carpeta real en `carpeta_zoom`.

## Protección contra producción

El programa no sube nada si no coinciden tres cosas: `entorno` en `config.json`, `VIDEOS_ENTORNO` en el servidor y
`video_parametros.entorno` en la base. Una base recién migrada queda cerrada hasta configurarlo a propósito.

- Sin `--produccion`, el entorno tiene que ser `pruebas-...` y el servidor, de la red local. Rechaza producción aunque
  el entorno diga pruebas.
- Un servidor de pruebas se niega a correr en Vercel o contra la base de producción.
- Si el servidor rechaza al programa, se detiene sin reintentar. Tampoco sube a producción aunque se lo pidan.
- `--produccion` no está autorizado todavía.

Procedimiento de las pruebas reales: `PRUEBAS_REALES.md`.

## Qué hace

Cada minuto revisa la carpeta de Zoom. Un `.mp4` se toma cuando su tamaño no cambió durante 3 minutos. Después consulta
al servidor con la hora y la duración, y recién si está autorizado lo comprime y lo sube.

| Resultado | Qué significa | ¿Reintenta? |
|---|---|---|
| `publicado` | Ya se ve en Videos On Demand | No |
| `no_autorizada` | El horario no está activado | No |
| `clase_ambigua` | Hay más de una clase posible | No |
| `duracion_fuera_de_rango` | No respeta la duración de la clase | No |
| `varios_archivos` | La reunión dejó más de un video | No |
| `antigua` | Tiene más de 72 horas | No |
| `sin_clase` | Todavía no se encontró la clase | Sí, cada 30 minutos durante 24 horas |
| `sin_espacio` | Se llegó al límite de almacenamiento | Sí, cada 30 minutos durante 24 horas |

- **Clase:** el servidor busca la clase de esa profesora que empezó hasta 15 minutos antes o después de la grabación.
- **Duración:** por defecto entre 34 y 41 minutos para una clase de 40. Es un margen provisorio, pendiente de validar
  con grabaciones reales, y se cambia en `video_parametros`.
- **Si se apaga o se corta internet:** retoma después, sin comprimir de nuevo y sin volver a subir las partes
  completas. Las 24 horas se cuentan desde que se publica.
- **Dos ejecuciones a la vez:** la segunda termina sin hacer nada.
- **Registro:** `trabajo/agente.log` y `trabajo/cola.sqlite`.

La grabación original nunca se borra ni se modifica.
