# Pruebas reales de Videos On Demand

Procedimiento preparado, **todavía sin ejecutar**. Cada prueba necesita su autorización. Solo datos ficticios y un proyecto
de Supabase de prueba: nunca theta, el proyecto principal ni personas sin su consentimiento.

Requisito que no cambia: las clases de Videos On Demand duran **40 minutos**.

## Prueba 1: video generado con FFmpeg

Valida compresión y división, sin Zoom y sin conectarse a nada. Necesita FFmpeg.

1. Generar 40 minutos de imagen en movimiento y sonido:

   ```bash
   ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i "sine=frequency=440" -t 2400 -c:v libx264 -preset veryfast -c:a aac -pix_fmt yuv420p prueba-40min.mp4
   ```

2. Procesarlo con el programa, sin servidor:

   ```bash
   .venv/bin/python agente/agente_videos.py --probar-archivo prueba-40min.mp4 --salida ~/JSU-pruebas/salida
   ```

3. Anotar el tiempo, y revisar `parte-*.ts` y `lista.m3u8`.

**Aprobada si:** termina sin error, cada parte pesa hasta 45 MB, la duración es de unos 2400 s y el total ronda los 120 MB
o menos. El patrón generado no sirve para juzgar calidad: eso se ve con la grabación real.

## Prueba 2: grabación real de Zoom Basic

Valida el flujo completo: Zoom, programa local, FFmpeg, Storage, publicación, reproducción y vencimiento.

### Preparación

1. Proyecto de prueba creado, `.env.videos-prueba` completo y migraciones aplicadas:

   ```bash
   .venv/bin/python scripts/videos_prueba_migrar.py --aplicar --proyecto REF_DE_PRUEBA
   ```

2. Datos ficticios, con la fecha y la hora de la clase:

   ```bash
   .venv/bin/python scripts/videos_prueba_datos.py crear --fecha AAAA-MM-DD --hora HH:MM --proyecto REF_DE_PRUEBA
   ```

3. Agregar `VIDEOS_ENTORNO` a `.env.videos-prueba` y levantar el servidor local:

   ```bash
   .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env.videos-prueba
   ```

4. Zoom web, Settings > Recording: activar **Local recording** y **Automatic recording** en **On the local computer**.
   Crear la reunión **después** de ese cambio.
5. Zoom de escritorio, Settings > Recording: guardar en `~/JSU-pruebas/zoom`, una carpeta local sin sincronizar.
6. Dejar la Mac enchufada, sin reposo, y arrancar el programa en una terminal:

   ```bash
   .venv/bin/python agente/agente_videos.py
   ```

### La clase

1. Iniciar desde la app de escritorio dentro de los 15 minutos de la hora de la clase, con otra persona conectada que
   haya aceptado aparecer.
2. Grabar con cámara fija, movimiento real y voz o música.
3. Terminar con "End meeting for all" a los 38–39 minutos. No detener y volver a grabar.
4. Esperar la conversión de Zoom y `publicado` en `agente/trabajo/agente.log`.

### Qué comprobar

| Paso | Aprobado si |
|---|---|
| Detección | Espera a que el archivo esté completo |
| Autorización | Un horario sin activar no se comprime ni se sube |
| Asociación | Queda en la clase y la empresa correctas |
| Publicación | Aparece solo en Videos On Demand de esa empresa |
| Reproducción | Llega al final en computadora, Safari de iPhone y Chrome de Android |
| Pausar, adelantar, retroceder | Funciona y no cuenta como otro inicio |
| Otra empresa | No accede, ni con URL directa |
| Vencimiento | A las 24 h deja de reproducirse, también con una URL ya emitida |
| Limpieza | Los archivos desaparecen al listar el bucket y el registro queda |
| Fallas | Sin internet o con la Mac apagada retoma sin duplicar |

Para probar videos cortos antes de la clase real, solo en el proyecto de prueba: `videos_prueba_datos.py margen --menos-seg
2100 --proyecto REF_DE_PRUEBA`. Hay que deshacerlo con `margen --restaurar` antes de la grabación de 40 minutos.

## Si Zoom corta antes

Zoom Basic corta las reuniones grupales a los 40 minutos, y ese tiempo cuenta desde que empieza la reunión, no desde que
empieza la clase. Hay que anotar en cada prueba:

- Duración real del archivo y minuto exacto del corte.
- Si el archivo se convirtió bien después del corte.
- Si avisó antes de cortar.
- Si una reunión con una sola persona corta igual.

| Qué pasa | Solución gratuita |
|---|---|
| El archivo queda bien y dura 39–40 min | Nada que cambiar: entra en el margen de 34 a 41 min |
| Se pierde tiempo antes de empezar | Sin sala de espera, entrando justo al inicio y sin charla previa |
| El corte daña la grabación | Terminar la reunión 1 o 2 minutos antes del corte |
| Hacen falta 40 minutos netos y Zoom no los deja | Grabar la clase con OBS Studio, gratuito: no depende del corte de Zoom y el programa mira una carpeta. Requiere probarlo y ajustar cómo se detecta la hora |
| Una clase queda en dos reuniones | Hoy no se publica (`varios_archivos` o una sola parte fuera de rango). Se podría unir con FFmpeg, con cambios en el programa |

Si ninguna da 40 minutos, se documenta el resultado y se decide con el equipo: el requisito no se cambia desde el código.
