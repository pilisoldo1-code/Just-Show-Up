# Programa local de Videos On Demand

Corre en la computadora donde la profesora da la clase con Zoom, porque Zoom gratuito guarda la grabación solo ahí.

## Qué tiene que hacer la profesora

1. Iniciar la reunión desde la **app de escritorio** de Zoom. Desde el celular no se graba.
2. Terminar la clase antes de los 40 minutos, que es cuando Zoom corta. Conviene planificarla en unos 38.
3. Dejar la computadora prendida y con internet hasta que termine de subir.

## Instalación (una sola vez)

1. Instalar Python 3.10 o más nuevo y FFmpeg.
2. En Zoom web, Settings > Recording: activar **Automatic recording** en **On the local computer**.
3. Crear el token de esa computadora, desde una compu con el `.env` del proyecto:

   ```bash
   python scripts/video_agente.py crear profesora@ejemplo.com "Notebook de la profesora"
   ```

   Se muestra una sola vez y en la base queda solo su huella. No mandarlo por chat.
4. Copiar `config.ejemplo.json` como `config.json` y pegar el token.
5. Hacer que arranque solo:
   - **Mac**: un `launchd` que ejecute `python agente_videos.py` al iniciar sesión.
   - **Windows**: una tarea del Programador de tareas que ejecute `python agente_videos.py --una-vez` cada 5 minutos.

## Qué hace

Cada minuto revisa la carpeta de Zoom. Un `.mp4` se toma cuando su tamaño no cambió durante 3 minutos y FFmpeg lo
puede leer. Las grabaciones de menos de 20 minutos se ignoran.

- **Clase:** el servidor busca la clase de esa profesora que empezó hasta 30 minutos antes o después de la grabación.
  Si no hay una sola coincidencia, no publica nada.
- **Si se apaga o se corta internet:** retoma después, sin comprimir de nuevo y sin volver a subir las partes
  completas. Las 24 horas se cuentan desde que se publica.
- **Registro:** `trabajo/agente.log` y `trabajo/cola.sqlite`.

La grabación original nunca se borra ni se modifica.
