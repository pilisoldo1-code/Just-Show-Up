# Just Show Up — clases presenciales + suscripción a clases grabadas

> **Para Claude Code**: si durante el trabajo descubrís una regla, convención,
> restricción técnica o gotcha nuevo (algo que te costó averiguar y que le
> ahorraría tiempo a otra sesión o a otra persona del equipo), agregalo a este
> archivo en la sección que corresponda (`Gotchas`, `Convenciones`, `Reglas de
> negocio`, etc.) como parte del mismo commit, sin que haga falta que te lo
> pidan explícitamente. Mantenerlo desactualizado es peor que no tenerlo.

## Dónde quedamos (leer primero)

**`CONTINUIDAD_PROYECTO.md`** tiene el estado actual (GitHub, Supabase, respaldo, pruebas, cómo
seguir desde otra computadora), lo pendiente, lo que necesita autorización, las decisiones de
Mercado Pago y Videos On Demand y las ideas a evaluar. **Orden de trabajo (actualizado 28/10/2026)**:
1. ~~Integrar el PR #15 (facturación)~~ — **hecho**: integrado en `main` con merge común
   (`568d551`), theta desplegada y verificada (17/17, sin tocar datos reales). El PR #13 de
   Josefina quedó marcado "merged" automáticamente. amber sigue con la versión anterior.
2. ~~Planificación: cancelar clases puntuales con checkboxes + mejoras del menú~~ — **hecho**
   (PR #16–#18; migración `20261028003643`). Pruebas finales "opción A" (solo lectura, 28/10):
   función idéntica al repo, permisos de alumna/anon, licencias intactas, 33/33 locales. Header
   responsive y menú de la alumna (PR #20) integrados y desplegados en theta (`7183efc`).
3. **Mercado Pago Checkout Pro SOLO EN MODO DE PRUEBA** — **etapa 2 hecha** (rama
   `mercado-pago-prueba`, commit local, sin push): código + migración (sin aplicar) + pruebas.
   Siguen: etapa 3 (aplicar la migración con respaldo y guardar el token en Vault), PR, y
   encender el interruptor en theta desde el 1/11 (ver Estado actual y
   `CONTINUIDAD_PROYECTO.md` §7). **Decisión: theta es la plataforma de la defensa; la
   credencial va en Supabase Vault (no en Vercel) para no depender de Pilar.**
4. Videos On Demand: Zoom gratuito (~40 min, carga manual) + Mux gratuito si pasa la prueba de
   reproducción firmada.

Ideas pendientes **para evaluar (no aprobadas)**: cobro del 25 % por inasistencia, notificaciones
de cancelación, magic links, gráficas en Estadísticas, revisión integral para celulares
(`CONTINUIDAD_PROYECTO.md` §11).

**Todo push o merge a `main` despliega solo en theta: consultar antes.**

## Qué es esto

Plataforma web que reemplaza un proceso manual por WhatsApp: una profesora
maneja horarios y cupos de clases presenciales a mano y quiere digitalizarlo,
además de sumar un servicio de suscripción mensual a una biblioteca de clases
grabadas. Especificación completa (roles, reglas de negocio, entidades,
workflows) recibida como PDF del usuario ("Obligatorio Incorporación
Estratégica"); resumen relevante más abajo.

## Stack

- **DB**: Supabase (Postgres + Auth). Proyecto "Just Show Up",
  `project_id nspgafucytcjqkqnmkry`, región `sa-east-1`. Se administra con el
  conector MCP de Supabase conectado a esta sesión (no hace falta el CLI de
  supabase para esto, aunque también está instalado localmente).
- **Backend + frontend**: Python (FastAPI + Jinja2), server-rendered. Decisión
  explícita del usuario: sin framework JS aparte ("una web con Python alcanza").
- **Hosting**: Vercel, deploy automático desde GitHub al pushear a `main`.
- **Repo**: [pilisoldo1-code/Just-Show-Up](https://github.com/pilisoldo1-code/Just-Show-Up)
  (público).
- **Integraciones planeadas (todavía no configuradas en el código)**: Resend
  (emails), n8n (automatizaciones), Mercado Pago (pagos), Google Calendar API,
  PostHog (analytics). Se van a ir sumando módulo por módulo, no de una.

## Estructura

```
app/main.py           rutas FastAPI
app/db.py              cliente Supabase (lee SUPABASE_URL / SUPABASE_KEY de env)
app/templates/          vistas Jinja2
app/static/             CSS
api/index.py            entrypoint serverless para Vercel (importa app.main:app)
supabase/migrations/    migraciones SQL (aplicadas también directo al proyecto
                         real vía el conector MCP de Supabase — no solo con el CLI)
scripts/seed.py          script standalone de prueba de conexión a Supabase
```

## Estado actual

- Tabla `horario` creada y poblada (10 registros: lunes a viernes, bloques
  08:00 y 09:00, 60 min, cupo 7, $800).
- Home page (`/`): grilla de alumna con **una tarjeta por día** de la semana
  visible (hoy + 2 semanas), con las horas del día y insignias si ya estás
  reservada o en espera. Los días ya pasados salen "Finalizado" y no se abren.
  Layout (decisión del usuario): tarjetas grandes, 3 en la primera fila y el
  resto centrado debajo (Lun-Mar-Mié / Jue-Vie); en pantallas ≤720px pasa a 2
  por fila y ≤440px a 1. Nunca debe haber scroll horizontal (flex-basis en %).
- Pantalla del día (`/dia/{fecha}`): una tarjeta por clase (08:00 / 09:00) que
  hace de **detalle**: día y fecha, hora, duración, precio, cupo máximo y
  "x lugares disponibles". Según el estado: **Reservar clase** (reserva directa,
  **sin diálogo de confirmación**), "Clase completa" + **Anotarme en lista de
  espera** (cupo 0), "Reservada" + **Cancelar reserva** o "Finalizada". Una clase ya reservada no vuelve a ofrecer "Reservar". Las
  acciones vuelven a esta pantalla con un aviso. Fechas inválidas o fuera de la
  ventana de 3 semanas redirigen a `/`. Decisiones del usuario (2026-10-06):
  mostrar precio y cupo máximo en el detalle, y mantener cancelar/lista de
  espera aunque ya existan. Vocabulario: "reservar/reserva", no "inscribirse".
  Las `clase` se generan solas (idempotente) al abrir cada semana o día, a
  partir de los horarios activos.
- Tablas `clase`, `reserva` y `listadeespera` creadas (migración
  `*_create_clase_reserva_listadeespera.sql`), con los atributos del documento.
  Índices únicos parciales impiden reserva activa duplicada y estar dos veces
  en la misma lista de espera.
- **Autenticación — etapa 1 hecha (2026-10-07)**. Modelo acordado con el usuario:
  **un despliegue = una profesora (administradora total, rol `profesora`)**; **no
  hay registro público**: la profesora crea las cuentas de sus alumnas
  presenciales desde `/profesora/alumnas` y ellas entran con mail + contraseña
  temporal (que pueden cambiar en `/cuenta/contrasena`). Los usuarios del
  contenido online (sin clases presenciales) se resuelven en su módulo.
  - Login en `/login`, logout `POST /logout`. Sesión = 2 cookies `httpOnly`
    (`sb_acceso`, `sb_refresco`, SameSite=lax, `secure` en https); el servidor
    valida el token en cada pedido y lo **renueva solo** con el refresh token.
    Páginas privadas con `Cache-Control: no-store`.
  - Rutas (la profesora además tiene `/profesora/clases/*`): públicas `/login`, `/olvide-contrasena`, `/restablecer-contrasena`,
    `/api/health`, `/static`; alumna `/`, `/dia/*`,
    `/clases/*`, `/mis-inscripciones`, `/mi-historial`, `/mis-facturas`; solo profesora
    `/profesora/*`; cualquiera logueada `/perfil`, `/perfil/contrasena`,
    `/api/horario`. Sin sesión → `/login`; alumna en
    `/profesora/*` → vuelve a `/` con aviso; profesora en `/` → su panel.
    Dependencias en `app/auth.py`: `requiere_login`, `requiere_alumna`,
    `requiere_profesora`. La protección es del lado del servidor.
  - Menú según rol (`_menu.html`, header responsive desde 28/10, rama `header-responsive`):
    el header va **fuera de `<main>`** (mismo ancho, hasta 1200 px, en todas las pantallas):
    «Just Show Up» (lleva al inicio del rol) · secciones · **menú de cuenta** (círculo con las
    **iniciales** + nombre, que despliega solo **Mi perfil · Cerrar sesión** para los dos roles;
    es un `<details>` que se cierra al clickear afuera o con Escape). Secciones: profesora =
    Horarios · Tipos de clase · Asistencias · Planificación · Alumnas · Facturación ·
    Estadísticas; **alumna = Clases · Mis inscripciones · Mi historial · Mis facturas** (ya no
    están en el menú de cuenta). La activa va en verde menta (Clases también en `/dia/...`).
    **Hasta 900 px** las secciones se esconden detrás de un botón ☰ (lista vertical debajo del
    header; se cierra con ✕, tocando afuera, con Escape o al elegir una sección; abrir ☰ cierra el
    perfil y viceversa); entre 901 y 1100 px se oculta el nombre del perfil. Pruebas:
    `tests/test_menu.py`.
  - **Mi perfil** (`/perfil`): información de contacto (solo lectura; si algo está
    mal se lo pide a la profesora) + formulario para cambiar la contraseña.
    **Mis inscripciones** (`/mis-inscripciones`): solo lo vigente — próximas
    clases (con link a `/dia/{fecha}` para cancelar) y lista de espera con
    posición. **Mi historial** (`/mi-historial`, pantalla aparte por decisión
    del usuario): clases canceladas y clases pasadas. **Mis facturas**
    (`/mis-facturas`): resúmenes mensuales con detalle de clases (ver módulo 6).
  - **Foto de perfil: pendiente de decisión.** Hoy el avatar son las iniciales
    porque no existe dónde guardar fotos. Para fotos reales haría falta una
    columna `foto_url` en `profiles` + un bucket de Supabase Storage con sus
    policies: **confirmar con el usuario antes de crearlos**.
  - Base: `profiles` (= `Usuario`) con RLS: cada usuaria **lee solo su fila**, la
    profesora lee todas (`es_profesora()`); el permiso de UPDATE es **por
    columna** (solo nombre, apellido, teléfono, fecha de nacimiento y carné):
    **`rol`, `mail` e `id` no son editables por el cliente**. El trigger crea el
    perfil siempre como `alumna`. FK `reserva.id_alumna` y
    `listadeespera.id_alumna` → `profiles(id)`.
  - Cuentas creadas solo desde el servidor con la `SUPABASE_SERVICE_KEY`
    (`auth.admin.create_user`, ya confirmadas, sin mail de confirmación). Se
    muestra la contraseña temporal **una sola vez** (todavía no se envía por
    mail: falta cuenta de Resend + dominio propio verificado).
  - **Cuentas de prueba para la demo (creadas 2026-10-07)**:
    `profesora@prueba.invalid` (rol profesora) y `alumna@prueba.invalid`. Las
    contraseñas **no se documentan acá**: están en el archivo local
    `.env.cuentas-prueba` (ignorado por git, regenerado 2026-10-13). No se
    pueden recuperar (Supabase guarda solo el hash): si se pierden, se
    reinician con `auth.admin.update_user_by_id` y la service key.
    La de profesora es una administradora real en un sitio público: **borrarla
    antes de usar el sistema de verdad** y crear la profesora real con el script.
    Limpieza: borrar sus `reserva`/`listadeespera` y después
    `delete from auth.users where email like '%@prueba.invalid'` (`profiles`
    cae en cascada; incluye las 3 usuarias fijas del modo prueba).
  - **Datos de demo (cargados 2026-10-13)**: `python scripts/datos_demo.py crear`
    crea 8 alumnas `@demo.invalid` con reservas pasadas (con asistencia),
    futuras, una clase completa y lista de espera; `... borrar` elimina **solo**
    eso (verificado: la base queda idéntica a antes). `sofia@demo.invalid` puede
    iniciar sesión (contraseña en `.env.cuentas-prueba`). Las demás no tienen
    contraseña conocida. Borrar antes de usar el sistema de verdad.
  - **Crear a la profesora al entregar el sistema**: `python scripts/crear_profesora.py`
    (pide mail, nombre, apellido y contraseña por teclado; no pasan por el chat).
  - **Olvidé mi contraseña (hecho 2026-10-13, decisión del usuario: link por
    mail de Supabase)**: link en `/login` → `/olvide-contrasena` (pide el mail y
    siempre responde lo mismo, exista o no la cuenta) → mail de Supabase con un
    link → `/restablecer-contrasena` (contraseña nueva + repetir) → `/login`. El
    `redirect_to` solo se arma para los hosts de `auth.SITIOS_PERMITIDOS` (si se
    suma un dominio, agregarlo ahí **y** en Supabase). El servidor solo acepta
    sesiones del link (`amr` = `otp`), no una sesión de login normal. Probado con
    23 casos + navegador.
  - **Validaciones del alta de alumna** (`app/validaciones.py`, una sola fuente de
    verdad; el servidor valida y muestra TODOS los errores juntos; el navegador
    refuerza con `pattern`/`min`/`max`): nombre y apellido = solo letras (acentos,
    ñ, espacios simples, apóstrofe y guion; sin números ni símbolos; máx. 60);
    mail = debe tener `@` y un dominio con punto; teléfono (opcional) = `09` +
    7 dígitos (se aceptan espacios/guiones al escribirlo y se guarda
    normalizado, ej. `099123456`); fecha de nacimiento (opcional) = **anterior a
    hoy**; vencimiento del carné (opcional) = **posterior a hoy** (hoy no vale).
    "Hoy" es la fecha de Montevideo (`clases.ahora()`).
  - Probado con 101 casos de auth + 78 de validaciones (rutas, roles, errores, sesión, seguridad en la base).
- **Autenticación — etapa 2, fase 1 hecha (2026-10-13)**: la app habla con
  Supabase **con la sesión de cada usuaria** (`_sb(usuario)` en `main.py`), no con
  un cliente anónimo. Migración `*_auth_etapa2_politicas_y_funciones.sql`:
  - Políticas para `authenticated`: horarios y clases los leen todas, solo la
    profesora los crea/edita/borra; `reserva` y `listadeespera`: cada alumna lee
    solo las suyas, la profesora todas. Las alumnas **no escriben directo**.
  - Funciones SQL (`security definer`, solo con sesión): `reservar`,
    `cancelar_reserva`, `anotar_en_espera` (bloquean la fila de la clase: sin
    sobreventa, y el plazo de 1 h lo controla la base), `reservas_confirmadas`
    (cuenta cupos sin revelar quiénes reservaron) y `generar_clases` (semanas
    cercanas, idempotente). `clases.py` traduce sus códigos a los mensajes.
  - Se quitó el **modo prueba** (`ALUMNA_PRUEBA` y el selector de alumnas).
  - Probado con 53 casos (cupos con reservas simultáneas, lista de espera,
    plazos, aislamiento entre alumnas, permisos de profesora, rutas).
  - **Fase 2 PENDIENTE**: `supabase/pendientes/auth_etapa2_cerrar_anon.sql`
    quita las políticas abiertas a `anon`. **Aplicarla solo cuando amber y theta
    tengan este código**: con el código viejo los sitios dejan de funcionar.
- **Asistencia — pantalla de la profesora (hecha 2026-10-13)**. Menú de la
  profesora: Horarios · **Clases** · Alumnas. `/profesora/clases` = clases de
  hoy, con botones para elegir día (**Hoy, Mañana, … hasta hoy + 7**, ej. del
  martes 13 al martes 20; decisión del usuario: ver inscriptas como máximo una
  semana adelante, `DIAS_ADELANTE_PROFESORA`). **Solo aparecen los días que
  tienen clases** (decisión del usuario: si mañana es sábado sin clase, se pasa
  directo al lunes) y la pantalla abre en el primero de ellos. `?fecha=` fuera de ese rango o sin clases
  vuelve a hoy y el detalle de una clase a más de 7 días tampoco se abre. En los
  días futuros se ve quién reservó pero los boxes siguen bloqueados; `/profesora/clases/{id}` = listado de
  alumnas con reserva confirmada y dos boxes, **Presente** y **Ausente**
  (radios con aspecto de checkbox: marcar uno desmarca el otro, se guarda al
  hacer click). Decisiones del usuario: solo la profesora marca; antes de la
  clase no hay nada marcado; **solo se puede marcar el día de la clase y
  mientras dura** (de la hora de inicio a inicio + duración, hora de Montevideo);
  antes o después los boxes están bloqueados y la base también lo rechaza.
  Tabla `asistencia` (id, id_reserva **único**, estado presente/ausente): una
  sola fila por reserva = no puede estar presente y ausente a la vez; sin fila =
  sin marcar. Se escribe solo con la función `marcar_asistencia` (verifica
  profesora, reserva confirmada y horario). Cada alumna lee solo la suya.
  Probado con 31 casos + navegador (escritorio y celular).
- **Asistencia — pantalla de la alumna (hecha 2026-10-13)**: en **Mi historial**
  cada clase pasada muestra **Presente** (verde), **Ausente** (rojo), **Sin
  registrar** (gris, la profesora no marcó) o **Cancelada**, y arriba un resumen
  "N clases asistidas · M ausencias". Probado con 10 casos + navegador.
- **Planificación (hecha 2026-10-16)** — `/profesora/planificacion`, menú de la
  profesora: Horarios · Clases · **Planificación** · Alumnas. La profesora crea
  un **bloqueo** (fecha de inicio, de finalización, motivo y opcionalmente
  "Solo este horario") para licencias, vacaciones o un médico.
  - **Dos pasos**: "Revisar clases afectadas" muestra (sin escribir nada en la
    base) qué clases se cancelan, cuántas reservas y **qué alumnas**, la lista de
    espera, las clases que ya empezaron (no se tocan) y las que aún no se
    generaron. Recién "Confirmar y cancelar N clases" ejecuta `crear_bloqueo`,
    que bloquea las filas de `clase` como `reservar`: lo que se cancela es lo que
    hay al confirmar (si cambió desde la vista previa, el aviso lo dice).
  - Al confirmar: clases → `cancelada` (no se pueden reservar ni anotarse en
    espera), reservas confirmadas → `cancelada` con **`reserva.id_bloqueo`** = el
    bloqueo, lista de espera → `expirado`. Las clases que se generen dentro del
    rango nacen canceladas (`generar_clases` consulta `bloqueo`).
  - La alumna ve "Cancelada por la profesora" en la grilla (hora tachada), en el
    día, en Mis inscripciones (sección propia) y en Mi historial. El motivo no se
    le muestra (puede ser privado).
  - **Eliminar** (con diálogo de confirmación): solo bloqueos *Próximo* o *En
    curso*; los *Finalizado* quedan como historial (la base también lo
    rechaza). `quitar_bloqueo` vuelve a habilitar las clases **futuras** que no
    cubra otro bloqueo (incluidas las que se generaron canceladas) y **recupera
    solo las reservas que canceló ese bloqueo**, sin duplicar y sin pasar el cupo
    actual (en orden de reserva; las que no entran quedan canceladas y marcadas).
    Las reservas de clases que siguen cubiertas por otro bloqueo pasan a ese
    bloqueo. Las cancelaciones de las alumnas nunca se recuperan. La lista de
    espera no se recupera.
  - Seguridad: `bloqueo` solo la lee la profesora; nadie escribe directo; las
    funciones verifican `es_profesora()`.
  - Probado con 75 casos + 16 de concurrencia (8 alumnas reservando mientras se
    crea el bloqueo) + navegador escritorio/celular; la base quedó idéntica
    después de cada prueba. Pruebas con horarios de sábado (no hay clases reales).
- **Planificación: cancelar clases puntuales (hecha 2026-10-27; PR #16, ajustes visuales en
  #17 y #18, todos integrados en `main` = `2d3f63c` y desplegados en theta; migración aplicada
  en Supabase el 28/10 como `20261028003643 planificacion_cancelacion_puntual`, función
  verificada idéntica al archivo del repo)** — segunda pestaña en `/profesora/planificacion`
  ("Licencia / ausencia" ↔ "Cancelar clases puntuales"; la primera queda igual).
  La profesora elige una fecha (se generan las clases de esa semana si hiciera
  falta, igual que en `/profesora/clases`), ve las clases de ese día con
  checkboxes (hora y tipo), marca una o varias, escribe el motivo y confirma en
  un diálogo que muestra fecha, horarios y cantidad antes de cancelar.
  - **Decisión de modelo**: **un `bloqueo` `puntual` por clase elegida** (no uno
    con una lista de clases), reutilizando 100% el mecanismo de licencias: cada
    clase de un día ya tiene un `id_horario` distinto, así que "cancelar la de
    8:00 y la de 9:00 pero no la de 17:00" son dos bloqueos con
    `fecha_desde = fecha_hasta` = ese día. Por eso "Bloqueos registrados" y
    "Eliminar" (con su recuperación de reservas respetando cupo) funcionan
    igual, clase por clase, sin tocar nada de licencias.
  - Función nueva `crear_bloqueos_puntuales(p_ids_clase, p_motivo)` (migración
    `20261027220000_planificacion_cancelacion_puntual.sql`), `security definer`,
    atómica: bloquea las filas de `clase` involucradas, valida que **todas**
    existan, estén `programada`, no hayan empezado y sean de un horario activo
    (si una sola no cumple, `clases_invalidas` y no cancela ninguna — todo o
    nada), y recién ahí crea un
    bloqueo por clase y cancela clase + reservas (con `id_bloqueo`) + lista de
    espera, igual que `crear_bloqueo`. Las clases ya canceladas se muestran en
    la pantalla identificadas y sin checkbox (también lo rechaza la base).
  - `app/clases.py`: `clases_para_cancelar` (lista del día con
    `seleccionable`/`cancelada`/`empezada`) y `cancelar_clases_puntuales`
    (traduce los códigos). Rutas en `app/main.py`:
    `/profesora/planificacion?modo=puntual&fecha=...` (GET) y
    `/profesora/planificacion/puntual/cancelar` (POST).
  - Probado: **33 casos en un Postgres local** con todas las migraciones del
    repo (`python tests/base_local/test_planificacion_puntual_base.py`): una
    clase, varias del mismo día, las no elegidas siguen activas, todo o nada
    (id inexistente / ya cancelada / ya empezada / horario desactivado en el
    mismo pedido no cancela nada), validaciones, permisos (alumna y anon
    rechazados), concurrencia (dos pedidos simultáneos por la misma clase: solo
    uno cancela), `quitar_bloqueo` revierte una cancelación puntual sin tocar
    las otras del mismo día. Más **24 casos de rutas y lógica pura** mockeados
    (`tests/test_planificacion.py`, incluye el menú) y navegador escritorio/
    celular con datos simulados (sin Supabase disponible en esa sesión). No se
    tocó la base real.
  - **PENDIENTE: pruebas finales contra Supabase real** (todavía no se crearon datos de
    prueba). Antes hay que resolver una **contradicción del plan de pruebas**: las pruebas
    reales anteriores usan horarios de prueba **inactivos** (para que no los vean las alumnas),
    pero `crear_bloqueos_puntuales` exige horario activo (`h.activo`) y `generar_clases` solo
    genera clases de horarios **y** tipos activos; con un horario inactivo la cancelación
    siempre devuelve `clases_invalidas`. Opciones en `CONTINUIDAD_PROYECTO.md` §9.
  - Ojo en pruebas: abrir la pestaña puntual con una `fecha` **llama a `generar_clases`** para
    esa semana (escribe en la base, igual que `/profesora/clases`).
  - Cupos (PR #17): los textos muestran "7 cupos" / "1 cupo" según `cupo_max`.
- **Menú de la profesora (hecho 2026-10-27, mismo trabajo)**: orden fijo
  Horarios · Tipos de clase · Asistencias · Planificación · Alumnas ·
  Facturación · Estadísticas (antes "Clases" se llamaba así y "Tipos de clase"
  solo se llegaba desde un botón dentro de Horarios; ese botón se quitó el 28/10 en
  `header-responsive` por estar duplicado con el menú).
  La pestaña de la sección donde está la profesora se resalta en verde menta
  (`--accent`) con `request.url.path.startswith(...)` en `_menu.html` — no hace
  falta tocar las rutas. Se mantiene al entrar a pantallas internas (el detalle
  de una clase resalta "Asistencias", etc.).
- **Estadísticas (hecha 2026-10-19, rama `modulo-estadisticas`)** —
  `/profesora/estadisticas`, menú de la profesora: … · Alumnas · **Estadísticas**.
  Filtro de período: **Este mes** (desde el 1°), **Últimos 3 meses** (el mes
  actual y los dos anteriores, desde el 1°) y **Todo**; siempre hasta hoy (hora
  de Montevideo). Muestra % de asistencia general, día de la semana, horario
  (día + hora) y mes con más asistencias (si hay empate se listan todos), top 3
  con más asistencias y top 3 con más faltas. "Asistencia" = fila de
  `asistencia` en `presente`; "falta" = `ausente`; las clases sin registrar no
  entran en el porcentaje y no se cuentan reservas canceladas. Solo lectura con
  la sesión de la profesora (RLS), sin tablas nuevas; lógica en
  `app/estadisticas.py` (`calcular` es pura, sin base). Pruebas sin tocar la
  base: `python -m unittest tests.test_estadisticas -v` (20 casos, incluido que
  una alumna no pueda entrar) + navegador escritorio/celular con datos simulados.
  **Falta verificarla contra la base real** (no había `.env` en esa sesión).
- **Tipos de clase (rama `modulo-tipos-clase`, 2026-10-21; migración
  `20261021002249_tipos_de_clase` APLICADA en Supabase el 21/10; código en PR)**. Tabla `tipo_clase` (nombre único, descripción,
  `imagen_url`, `duracion`, `precio_presencial`, `cupo_max`, `activo`; nunca se borra,
  se desactiva). Tipo inicial **Gimnasia** (60 min, $800, cupo 7) para los 10 horarios.
  - `horario.id_tipo_clase` obligatorio. Las columnas viejas `duracion`/`cupo_max`/`precio`
    de `horario` **siguen existiendo** (el código desplegado las lee): un trigger
    (`_horario_desde_tipo`) las mantiene iguales a las del tipo. Se borran en una fase 2,
    cuando amber y theta tengan este código.
  - `clase` guarda su foto: `precio`, `duracion` (ya existían), **`cupo_max` e
    `id_tipo_clase`** (nuevas). Todas las funciones de reservas / lista de espera /
    bloqueos leen `clase.cupo_max` (antes `horario.cupo_max`). El resumen mensual usa
    `clase.precio`: no se ve afectado.
  - Editar un tipo (`aplicar_cambio_tipo`, con vista previa `previsualizar_cambio_tipo`):
    clases futuras **sin reservas ni ofertas** toman precio, duración y cupo nuevos; **con
    reservas u ofertas** conservan precio y duración y toman el cupo nuevo; las pasadas no
    cambian. Bajar el cupo **nunca cancela reservas ni ofertas vigentes** (solo deja de
    aceptar nuevas); subirlo hace que la lista de espera ofrezca en orden.
  - Cambiar el tipo de un horario (`cambiar_tipo_horario`, con vista previa): las clases
    futuras sin reservas pasan al tipo nuevo; las que tienen reservas quedan como estaban.
  - No se puede desactivar un tipo con horarios activos ni activar un horario de un tipo
    inactivo. Imágenes: bucket público `tipos-clase` (JPG/PNG/WebP, 2 MB; solo la profesora
    sube; el servidor valida el contenido real del archivo). Pensado para On Demand: un
    futuro `video.id_tipo_clase` reusa nombre, descripción e imagen; los precios de On
    Demand van aparte.
  - Pantallas: Horarios muestra Día · Hora · Tipo · Estado y tiene el botón "Tipos de
    clase" (`/profesora/tipos-clase`: crear, editar con vista previa, activar/desactivar,
    imagen). Alumna: "08:00 · Gimnasia" en la grilla y tipo + imagen en el día.
  - Probado en un **Postgres local** (pgserver) que reconstruye el esquema con todas las
    migraciones + copia de los datos: 89 casos de la migración y funciones (incluye
    concurrencia y permisos) y compatibilidad con la migración del resumen (PR #13).
    Después de aplicarla: 42 casos de la app (incluye subir imágenes a Storage), todas las
    pruebas de los demás módulos con el código nuevo y con el de theta y amber (que siguen
    funcionando con la base nueva), navegador escritorio/celular. Antes de aplicarla se
    hizo un respaldo de todos los datos y un script de vuelta atrás probado (deja las 6
    funciones modificadas idénticas byte a byte a las de producción).
- **Facturación mensual y pagos (módulo 6, rama `modulo-6-resumen-correcciones`;
  migración `20261021150000_resumen_mensual_pagos.sql` APLICADA en Supabase el 27/10 como
  `20261027205754 resumen_mensual_pagos`; **integrado en `main` el 27/10 (PR #15, merge común,
  `568d551`) y desplegado en theta**)**. Parte del PR #13 de Josefina (su rama `modulo-6-resumen` queda intacta),
  rediseñado con Julieta. **Simulación educativa**: sin requisitos fiscales, numeración
  oficial ni pasarela de pagos (sin Mercado Pago); los pagos los marca la profesora.
  - Tablas: `resumen_mensual` = la factura (`estado_factura` borrador/confirmada/anulada y
    `estado_pago` pendiente/pagado/sin_cargo, importes, `version`, `numero` JSU-AAAA-MM-NNNN
    al confirmar, `reemplaza_a`); `resumen_mensual_detalle` = una línea por clase cobrada
    (foto: fecha, hora, tipo, `precio_original`, `ajuste_tipo`, `importe_final`);
    `factura_historial` y `pago_anulacion` (motivos internos, **solo los lee la profesora**);
    `pago` (vigente/anulado). Nadie escribe directo: solo funciones que validan rol, fechas,
    versión e importes. Una sola factura no anulada por alumna y mes; un solo pago vigente.
  - **Fechas (Montevideo)**: durante el mes la profesora ve un **provisorio** (lo calcula la
    app, no se guarda). Desde el **último día del mes** se arman **borradores**
    (`sincronizar_borradores`, se llama sola al abrir el mes; agrega/quita clases sin perder
    los ajustes; solo clases ya empezadas). **Confirmar** exige que hayan terminado todas las
    clases programadas del mes (`_periodo_finalizado`) y que el borrador esté al día con las
    reservas (`_factura_al_dia`). La alumna ve sus facturas confirmadas **desde el día 1 del
    mes siguiente, 00:00** (lo aplica RLS, no la pantalla); nunca ve borradores.
  - **Ajustes** (solo en borrador, con motivo): bonificar una clase o cobrarle menos (entre $0
    y el precio original), y descuento general en monto o porcentaje. El total se recalcula
    en la base. La alumna ve "Descuento"/"Bonificada", no el motivo.
  - **Confirmación individual y masiva**: la masiva muestra antes cantidad y total y es todo o
    nada (si algo cambió desde la vista previa, no confirma ninguna: compara una "firma" de
    ids+versiones). Toda modificación lleva la `version` que la profesora tenía en pantalla.
  - **Corregir una factura confirmada**: "Anular y reemitir" (con motivo; si tiene pago
    vigente, primero anular el pago). La anulada queda en el historial y se crea un borrador
    con las mismas clases, ajustes y motivos, que se confirma con otro número. La alumna ve
    la anulada solo si ya estaba publicada, marcada "reemplazada por…".
  - **Pagos simulados**: solo facturas confirmadas, total exacto, fecha no futura, uno vigente
    (índice único + bloqueo), anulación con motivo/responsable/fecha en `pago_anulacion`.
    Factura en $0 = "sin cargo".
  - **PDF** (`app/factura_pdf.py`, `fpdf2`): "Factura mensual – Just Show Up", generado al
    descargar con los datos guardados (no cambia si cambian precios). Alumna:
    `/mis-facturas/{id}/pdf`; profesora: `/profesora/resumenes/{id}/pdf` (borrador marcado).
  - **Identificación (decisión del usuario, 27/10)**: cada factura muestra nombre completo,
    cédula y período. En borrador salen del perfil actual; **al confirmar se copian**
    (`alumna_nombre`, `alumna_apellido`, `alumna_cedula`) y ya no cambian aunque cambie el
    perfil. Sin cédula se puede confirmar igual: dice "Cédula: no registrada" y la vista previa
    masiva avisa cuántas facturas no tienen cédula.
  - Pantallas: menú de la profesora "Facturación" (`/profesora/resumenes`, detalle
    `/profesora/resumenes/{id}`, vista previa `/profesora/resumenes/confirmar-mes`); alumna:
    Mis facturas.
  - Orden de bloqueo: factura → líneas → pago; sincronizar, confirmar y anular facturas toman
    además un candado por mes. Todo "ahora" de estas funciones pasa por `public._ahora()`.
  - Probado: **180 casos en un Postgres local** con reloj simulado y datos sintéticos
    (`python tests/base_local/test_facturacion_base.py`, necesita `requirements-dev.txt`):
    fechas de cierre y publicación, cambio de año, concurrencia, permisos, cédula, foto fija,
    PDF y el código real de `app/facturas.py`. 49 tests unitarios (`tests.test_facturas`) y
    navegador escritorio/celular contra la base local. Migraciones aplicadas el 27/10 tras un
    respaldo verificado (`~/Documents/Just Show Up - respaldos/2026-10-27 antes de facturación
    (módulo 6)/`, con `vuelta_atras_modulo6.sql`); después de aplicarlas la estructura quedó
    idéntica a la de los archivos del repo, los datos sin cambios y los permisos probados con
    las cuentas de prueba. **Probado contra Supabase real el 27/10** con el servidor local
    (`tests/supabase_real/prueba_modulo6_real.py`, 73 casos: facturación completa de profesora y
    alumna, PDF, permisos y regresión de login, alumnas, reservas, lista de espera, tipos,
    planificación y asistencia) usando solo cuentas `@prueba.invalid` y septiembre de 2026 (sin
    datos reales); limpieza y comparación con el respaldo: idéntico. **Theta verificada después
    del merge** (17 comprobaciones de solo lectura con cuentas de prueba, huella de 12 tablas
    idéntica antes y después). **Desde el último día de cada mes** (31/10 para octubre), abrir
    Facturación en ese mes crea los borradores de las alumnas reales: es lo previsto.
- **Mercado Pago Checkout Pro — SOLO MODO DE PRUEBA, dinero ficticio (rama `mercado-pago-prueba`,
  29/10; migración `20261029120000_mercado_pago_prueba.sql` ESCRITA, NO APLICADA; sin push)**.
  Proyecto universitario: nunca credenciales productivas, tarjetas reales ni costos.
  - **Cuentas (Julieta, 28/10)**: aplicación Checkout Pro creada desde su cuenta personal; la
    vendedora de prueba (creada sola con la app; sus credenciales son las "de prueba") y una
    compradora de prueba de Uruguay con saldo ficticio. Verificado con `GET /users/me`: etiquetas
    `test_user` + `user_product_seller`, sitio `MLU`. Prueba técnica real: pago aprobado (saldo
    ficticio) y rechazado (tarjeta de prueba, titular `OTHE`), sin movimientos en la cuenta personal.
  - **Credenciales**: `MP_ACCESS_TOKEN` y `MP_USER_ID` en el `.env` local (solo para pruebas
    técnicas); en producción el token va **cifrado en Supabase Vault** (secreto `mp_access_token`;
    opcional `mp_webhook_secret`) y el **interruptor** en `mp_configuracion.habilitado` (arranca
    en `false`). Solo los lee `mp_configuracion_servidor()`, que ejecuta únicamente `service_role`:
    así theta lo usa con su `SUPABASE_SERVICE_KEY` sin variables nuevas en Vercel (theta es de
    Pilar). Si falta algo o la cuenta no es de prueba, Mercado Pago queda apagado.
  - **Flujo**: Mis facturas → «Pagar con Mercado Pago» (botón blanco con el logo oficial, solo
    alumnas `@demo.invalid`/`@prueba.invalid`, factura confirmada, publicada y pendiente) →
    `crear_intento_mp` (sesión de la alumna; importe = total de la factura) → preferencia (UYU,
    `binary_mode`, sin `ticket`, 1 cuota, vence en 30 min, `back_urls` y `notification_url` al
    mismo sitio si está en `auth.SITIOS_PERMITIDOS`) → `init_point` → vuelta
    `/mis-facturas/mercado-pago/vuelta` y aviso `POST /webhooks/mercado-pago`. Ambos **consultan
    el pago a Mercado Pago** y llaman a `registrar_pago_mercado_pago` (solo `service_role`).
    Además, al abrir Mis facturas se concilian los intentos de las últimas 48 h sin resultado
    (alumna que cerró Mercado Pago sin volver o aviso que no llegó).
  - **Registro**: solo pago aprobado, cobrado por nuestra vendedora de prueba, pagado por una
    cuenta `TESTUSER…`, UYU, importe = intento = total actual de una factura confirmada y
    pendiente → `pago` (`medio_pago = 'mercado_pago'`, `id_transaccion` = n.º de operación,
    índice único) y factura `pagado`. Cualquier otra cosa → intento `rechazado`/`a_revisar` sin
    tocar la factura. Idempotente: el mismo n.º de operación nunca se registra dos veces (aunque
    se anule el pago). La profesora ve el pago (con n.º de operación) y los intentos en el
    detalle de la factura y puede anularlo como uno manual (para ensayar otra vez).
  - Pruebas: `python tests/base_local/test_mercado_pago_base.py` (73, Postgres local con Vault
    simulado) y `tests/test_mercadopago.py` (49, Mercado Pago y Supabase simulados).
  - **Vuelta atrás**: `supabase/pendientes/vuelta_atras_mercado_pago.sql` (borra solo lo de Mercado
    Pago y los secretos `mp_*` del Vault; se niega si ya hay pagos `mercado_pago`; todo o nada).
    Probada con `python tests/base_local/vuelta_atras_mercado_pago.py "<carpeta del respaldo>"`
    (24 casos, 3 corridas): sobre una copia local del respaldo del 29/10, aplicar → usar → deshacer
    3 veces deja estructura, 13 tablas y secuencia de facturas idénticas al respaldo.
  - Pendiente: etapa 3 pasos 3–5 (aplicar la migración + guardar el token en Vault), PR, y desde
    el 1/11 encender el interruptor y probar en theta la vuelta automática y los avisos (no se
    pueden probar en local: Mercado Pago no acepta `localhost` en `back_urls`).
- **Cédula de identidad (rama `modulo-6-resumen-correcciones`, migración
  `20261021140000_profiles_cedula.sql` APLICADA el 27/10 como `20261027205550 profiles_cedula`)**: `profiles.cedula` opcional (las usuarias
  existentes quedan sin cédula; no se completa nada), solo dígitos, validada con el dígito
  verificador uruguayo (`_cedula_valida` en la base y `validaciones.cedula` en Python, mismo
  algoritmo) y única. Cada alumna ve la suya en Mi perfil (solo lectura) y **no puede
  cambiarla** (no está en su permiso de UPDATE por columna). La profesora la carga en el alta o
  después con "Cargar/Corregir cédula" en Alumnas (función `actualizar_cedula`, verifica rol;
  vacía = quitarla).
- **Cancelación de reserva (hecha)**: "Cancelar reserva" abre un diálogo propio
  (Volver / Confirmar cancelación). Se permite hasta 1 h antes del inicio
  (exactamente 1 h sí). Dentro de la última hora el diálogo muestra el mensaje
  de plazo vencido y el servidor también lo rechaza (reserva intacta, cupo sin
  liberar, queda cobrable para el resumen mensual futuro). Clase iniciada: sin
  botón y el servidor la rechaza. Probado con 29 casos (ver Gotchas: cupos).
- **Lista de espera completa (módulo 4, hecho 2026-10-20)**. Migración
  `*_lista_espera_ofertas.sql`. Decisiones del usuario: rechazar con botón, la
  profesora ve la lista (solo lectura), estado nuevo `retirado`.
  - Cuando se libera un lugar (cancela una alumna, la profesora sube el cupo,
    etc.) se le **ofrece a la primera** de la lista: estado `ofrecido` y
    `oferta_vence_en` = ahora + 30 min **o el inicio de la clase, lo que pase
    primero**. Mientras la oferta está vigente el lugar queda guardado: las demás
    ven la clase completa y `reservar` devuelve `completa`.
  - La alumna ve el aviso **dentro de la app** (día: "Se liberó un lugar para vos:
    confirmá antes de las HH:MM" con **Confirmar lugar** / **No me interesa**;
    grilla: "¡Lugar disponible!"; Mis inscripciones: sección propia). Sin mails.
  - Confirmar → reserva `confirmada`, espera `confirmado`. No me interesa →
    `retirado` y pasa enseguida a la siguiente. Vence sin respuesta → `expirado`
    y pasa a la siguiente. **Salir de la lista** (con confirmación) → `retirado`;
    si vuelve a anotarse queda al final.
  - **No hay tareas programadas**: `_procesar_lista_espera(clase)` (interna) vence
    ofertas y ofrece lugares libres; la llaman `reservar`, `cancelar_reserva`,
    `anotar_en_espera`, `confirmar_oferta`, `rechazar_oferta`,
    `salir_lista_espera` y `actualizar_listas_espera(ids)` (que la app llama al
    abrir la grilla, el día, Mis inscripciones y el detalle de la profesora).
    Todas bloquean la fila de la clase. No toca clases canceladas ni empezadas.
  - `posicion` guardada = máximo histórico + 1 (nunca se repite, define el orden);
    la posición que se muestra es la actual (`mi_lista_espera`). Lugares guardados:
    `ofertas_vigentes(ids)` (sin revelar para quién).
  - Planificación: un bloqueo cierra las ofertas (`expirado`) y al quitarlo no se
    reabren.
  - Probado con 49 casos (incluye concurrencia: dos cancelaciones a la vez, carreras
    entre la alumna con oferta y otras, anotaciones simultáneas) + navegador.
- Panel de profesora en `/profesora/horarios`: listado completo (activos e
  inactivos), alta (`/profesora/horarios/nuevo`), edición
  (`/profesora/horarios/{id}/editar`) y activar/desactivar
  (`POST /profesora/horarios/{id}/toggle`). Probado en local end-to-end.
  **Sin protección de acceso todavía** — ver Gotchas.
- Deploy de referencia funcionando: https://just-show-up-amber.vercel.app
- Existe un segundo deploy "oficial" conectado por GitHub a otra cuenta de
  Vercel (la de la dueña del repo) — ver Gotchas.

## Plan del equipo hasta el martes 20/10/2026 (HISTÓRICO: ya pasó; el estado vigente está en `CONTINUIDAD_PROYECTO.md`)

> **Para Claude Code**: leé esta sección antes de empezar. Cada integrante trabaja en
> **un módulo distinto, en su propia rama**. No toques módulos de otra persona.

### Estado al viernes 16/10 (lo que hay que saber antes de arrancar)
- `main` = commit `e5760f9` (incluye Planificación). theta y **amber** ya tienen ese código.
  amber se desplegó desde una copia limpia de `main` (sin `.env`); vuelta atrás:
  `vercel rollback dpl_H4KXxxhVEtVBqKbmrHDPrdM1yQp8`.
- **Rotación de claves EN CURSO**: ya existen las claves nuevas (`sb_publishable_...` y
  la secret `servidor_app`, `sb_secret_...`). Julieta ya las tiene en su `.env` y la app
  pasa todas las pruebas con ellas. amber y theta **todavía usan las claves viejas**,
  que siguen activas. **Nadie desactive las claves viejas** hasta que Julieta lo coordine.
- **Fase 2 de seguridad (RLS) NO aplicada todavía**: sin sesión se puede leer y escribir
  `horario`, `clase`, `reserva` y `listadeespera`. No crear tablas nuevas con acceso `anon`.
- **Licencia "Vacaciones" 20–26/10 (era una prueba)**: la eliminó por error una prueba
  automática el 16/10 (tomaba "el primer bloqueo de la tabla"). Volvieron las 10 clases y
  las 22 reservas de demo. **Decisión pendiente de Julieta**: recrearla o dejarla así.
  No la recreen ni toquen bloqueos reales hasta que Julieta decida.
- Datos de demo cargados (8 alumnas `@demo.invalid`) y cuentas de prueba `@prueba.invalid`.

### Metas por integrante (para el martes 20/10)

> La **seguridad (rotación de claves y fase 2 RLS) queda en pausa** y se retoma después
> del martes. Mientras tanto: **nadie desactiva las claves viejas, nadie aplica
> `supabase/pendientes/auth_etapa2_cerrar_anon.sql` y nadie cambia variables de Vercel.**
> Las claves viejas siguen funcionando, así que el `.env` de cada una sigue sirviendo.

**Pilar — Estadísticas** (pedido del profesor) · rama `modulo-estadisticas`
- Pantalla de la profesora `/profesora/estadisticas` (opción "Estadísticas" en su menú) con
  filtro de período (este mes / últimos 3 meses / todo): día de la semana con más
  asistencias, horario con más asistencias, top 3 alumnas con más asistencias, top 3 con
  más faltas, mes con más asistencias y % de asistencia general.
- "Asistencia" = fila de `asistencia` con estado `presente`; "falta" = `ausente`.
- **No necesita tablas nuevas**: solo lectura con la sesión de la profesora (RLS ya lo
  permite). Lógica en `app/estadisticas.py`. Con los datos de demo ya hay números.
- **Listo cuando**: pantalla funcionando, probada en celular, pruebas (incluido que una
  alumna no pueda entrar), PR abierto.

**Integrante 3 — Resumen mensual y pagos (módulo 6, sin Mercado Pago)** · rama `modulo-6-resumen`
- **Necesita autorización de Julieta para crear tablas**: `resumenmensual` y `pago` con los
  atributos del modelo de datos (tabla más abajo), con RLS desde el inicio (profesora
  todo; alumna solo lo suyo; **sin acceso anon**). Avisar en el grupo antes de la migración.
- Regla de cobro (ver "Reglas de negocio"): reservas `confirmada` de clases `programada`
  ya dictadas en el mes. No se cobran las canceladas (por la alumna ni por la profesora).
- Profesora: pantalla "Resúmenes" (elegir mes → por alumna: cantidad de clases, total,
  estado pendiente/pagado), botón "Generar resumen del mes" y "Registrar pago"
  (efectivo/transferencia) que lo pasa a pagado.
- Alumna: **Mis facturas** (hoy vacía) muestra sus resúmenes y el detalle de clases.
- Lógica en `app/resumen.py`.
- **Listo cuando**: migración aplicada con RLS verificado, pantallas de profesora y alumna,
  pruebas con datos de prueba que se limpian, PR abierto.

**Julieta — Lista de espera completa (módulo 4)** · rama `modulo-4-lista-espera`
- Hoy solo se puede anotarse y ver la posición. Falta (reglas de negocio): **cuando se
  libera un lugar, se le ofrece a la primera de la lista, que tiene 30 minutos para
  confirmar; si no confirma, pasa a la siguiente**. Y poder **salir de la lista**.
- **No necesita tablas nuevas**: `listadeespera` ya tiene los estados `ofrecido`,
  `confirmado`, `expirado` y la columna `oferta_vence_en` (hoy sin uso). Sí hay que
  **modificar funciones de la base** (`cancelar_reserva`, `reservar`, `anotar_en_espera`)
  y agregar alguna nueva (confirmar oferta, salir de la lista, vencer ofertas): avisar en el
  grupo antes de aplicarlas.
- Sin mails todavía: el aviso es **dentro de la app** (en el día, en la grilla y en Mis
  inscripciones: "Se liberó un lugar: confirmá antes de las HH:MM"). Mientras la oferta
  está vigente, ese lugar no lo puede tomar otra alumna. Las ofertas vencidas se procesan
  al consultar (no hay tareas programadas).
- Cuidado con Planificación: si la profesora cancela la clase, las ofertas se cierran.
- Decisión pendiente de Julieta (de antes): recrear o no la licencia "Vacaciones".
- **Listo cuando**: oferta, confirmación, vencimiento y salida funcionando, con pruebas de
  concurrencia (dos alumnas intentando el mismo lugar) y PR abierto.

**Para después del martes**: cerrar la seguridad (rotación de claves + fase 2 RLS +
desactivar claves viejas; ver estado arriba y Gotchas), Tipo de clase (faltan los nombres
de los tipos), mails con Resend, suscripción y videos (modalidad sin decidir).

### Reglas para no pisarse
- Antes de arrancar: `git pull` en `main`, `pip install -r requirements.txt` y
  `pip uninstall gotrue` (la librería de Supabase se actualizó a 2.32).
- Cada una en su rama y PR a `main`; merge recién cuando la dueña del módulo lo pruebe.
- Archivos compartidos (`app/main.py`, `app/templates/_menu.html`, `app/static/style.css`,
  `CLAUDE.md`): agregar código en **una sección propia al final** (rutas al final de
  `main.py`, CSS al final con un comentario `/* ---------- módulo ---------- */`). La lógica
  nueva va en **un archivo propio** (`app/estadisticas.py`, `app/resumen.py`), no en
  `app/clases.py`. En `CLAUDE.md`, cada una edita solo la sección de su módulo.
- **Base de datos compartida**: avisar en el grupo antes de cualquier migración; nunca
  borrar ni modificar datos reales ni de demo; las pruebas crean y borran solo lo suyo.
- Las cuentas de prueba están en `.env.cuentas-prueba` de Julieta (no está en git):
  pedírselas por privado. Nunca pegar claves ni contraseñas en el chat ni en el repo.
- Si el sitio da error 500 en todos lados: Supabase pausado (ver Gotchas).

## Trabajo en equipo

Somos **3 personas** implementando sobre el mismo repo. Para no pisarnos:

- **Ramas por módulo, no push directo a `main`.** Cada módulo del roadmap de
  abajo es una unidad razonable de trabajo: `git checkout -b modulo-2-horarios`,
  trabajar ahí, abrir PR a `main` cuando esté probado. `main` tiene que quedar
  siempre en estado deployable (es lo que Vercel toma para el deploy oficial).
- **Coordinar antes de tocar la base de datos.** Las migraciones de
  `supabase/migrations/` se aplican directo al proyecto real de Supabase vía
  el conector MCP — no es un archivo que se mergea después, el efecto es
  inmediato sobre datos compartidos. Si dos personas crean/alteran tablas al
  mismo tiempo se pueden pisar. Avisar en el grupo antes de correr una
  migración, y idealmente que la aplique una sola persona por vez.
- **Antes de empezar a trabajar**: `git pull` sobre `main` para partir de lo
  último. Si dos personas tocan el mismo módulo, mejor hablarlo antes.
- **Los módulos del roadmap tienen dependencias** (ver más abajo) — el 3 y el
  4 necesitan que el 1 y el 2 estén al menos empezados, por ejemplo. Elegir
  módulos en paralelo que no dependan entre sí evita bloqueos.
- El repo (`pilisoldo1-code/Just-Show-Up`) y el deploy oficial en Vercel son
  de una sola cuenta/persona del equipo; las otras dos personas necesitan ser
  agregadas como colaboradoras en GitHub (y opcionalmente en el proyecto de
  Vercel) para poder pushear y ver deploys directamente.

## Roadmap por módulos

Se definieron como módulos verticales: cada uno entrega algo visible/probable
en la web, no solo cambios de esquema.

- **Planificación** (pedido del profesor) — **hecho**: licencias/vacaciones por
  período y, desde el 27/10, **cancelar clases puntuales** con checkboxes (ver
  Estado actual; PR #16–#18 en `main`, migración aplicada; **faltan las pruebas
  finales contra la base real**). Estadísticas **hecha**. Tipos de clase **hecho**.
  Menú de la profesora reordenado y con pestaña activa resaltada **hecho** (ver
  Estado actual); en celular el menú pasa a varias líneas (`flex-wrap`), una
  revisión integral para celulares queda como idea a evaluar.

1. **Autenticación y perfiles** — etapa 1 y olvidé mi contraseña hechos; etapa 2
   fase 1 hecha, falta la fase 2 (cerrar `anon`) y rotar claves (ver Gotchas).
2. **Gestión de horarios y clases (Profesora)** — **en progreso**, arrancado
   antes que el 1 (decisión del usuario). Hecho: CRUD de horarios fijos
   (alta/edición/activar-desactivar), tabla `clase` con generación automática
   por semana, y grilla de alumna en tarjetas. Pendiente: botón de la profesora
   para generar la semana siguiente a pedido (hoy se genera sola al verla).
3. **Grilla y reservas (Alumna)** — **hecho con alumna provisoria**: inscribirse,
   cancelar (hasta 1 h antes), cupos en vivo. Falta el mail de confirmación y
   "mis próximas reservas".
4. **Lista de espera** — **hecho** (oferta de 30 min dentro de la app, confirmar,
   rechazar, salir; vista de la profesora). Falta solo el aviso por mail (Resend).
5. **Asistencia** — **hecho** (profesora marca; la alumna lo ve en Mi historial).
6. **Facturación mensual y pagos** — **hecho** (simulado; Mercado Pago de prueba en curso, ver
   Estado actual): PR #15 integrado en `main` el 27/10
   (merge común, `568d551`), desplegado en theta y verificado; migraciones aplicadas en Supabase. Pago con **Mercado Pago Checkout Pro solo en modo de prueba**: paso 3 del
   orden de trabajo (propuesta en `CONTINUIDAD_PROYECTO.md` §7).
7. **Clases grabadas → "Videos On Demand"** (decidido 27/10): la profesora sube la grabación (Zoom
   gratuito, carga manual) y todas las alumnas la ven 24 h desde la publicación; alojamiento en Mux
   gratuito sujeto a validación. Paso 4 del orden de trabajo (`CONTINUIDAD_PROYECTO.md` §8). La
   suscripción paga original queda fuera por ahora.
8. **Notificaciones y extras** — mails vía Resend (confirmación, recordatorio
   24hs), integración con Google Calendar, automatizaciones con n8n,
   analytics con PostHog.

Actualizar esta lista (tachar/marcar) a medida que se completa cada módulo.

## Modelo de datos (entidades del documento de especificación)

| Entidad | Atributos clave |
|---|---|
| Usuario | id, nombre, apellido, mail, telefono, fecha_nacimiento, fecha_vencimiento_carne_salud, rol (alumna/profesora) |
| Horario | id, dia_semana, hora_inicio, duracion, cupo_max, precio, activo |
| Clase | id, id_horario, fecha, hora_inicio, cantidad_inscriptas, estado (programada/cancelada/finalizada), duracion, precio |
| Reserva | id, id_alumna, id_clase, fecha_reserva, estado (confirmada/cancelada), fecha_cancelacion |
| ListaDeEspera | id, id_alumna, id_clase, posicion, fecha_ingreso, estado (en_espera/ofrecido/confirmado/expirado), oferta_vence_en |
| Asistencia | id, id_reserva, estado (presente/ausente) |
| ResumenMensual | id, id_alumna, mes, año, cantidad_clases, total, estado (pendiente/pagado) |
| Suscripcion | id, id_usuario, id_suscripcion_mercado_pago, fecha_inicio, fecha_vencimiento, estado (activa/vencida/pendiente_pago) |
| Pago | id, id_usuario, tipo_pago (resumen_mensual/suscripcion), id_resumen_mensual, id_suscripcion, monto, fecha, medio_pago (efectivo/transferencia/mercado_pago), id_transaccion |
| Video | id, titulo, descripcion, categoria, url_video, duracion, estado (publicado/desactivado), fecha_publicacion, id_profesora |

`Usuario` se implementa como tabla `profiles` vinculada 1:1 a `auth.users` de
Supabase Auth (no se reinventa autenticación).

## Reglas de negocio clave

- Cupo máximo por clase: al llenarse, se habilita lista de espera.
- Una alumna no puede reservar dos veces la misma clase.
- Cancelación permitida hasta 1 hora antes del inicio de la clase.
- Si no cancela a tiempo (o no asiste), la clase se cobra igual.
- Al liberarse un cupo, se notifica a la primera de la lista de espera, que
  tiene 30 minutos para confirmar antes de que se ofrezca a la siguiente.
- Solo la profesora marca asistencia (presente/ausente).
- **Qué se cobra (resumen mensual)**: las reservas `confirmada`
  de clases `programada` que ya pasaron (presente, ausente o sin registrar). No
  se cobran las reservas `cancelada`: las canceló la alumna a tiempo
  (`id_bloqueo` NULL) o la profesora (`id_bloqueo` con valor).
- El resumen mensual se genera al cierre de mes; estado pendiente → pagado
  cuando la profesora registra el cobro.
- La suscripción a clases grabadas es independiente de asistir a clases
  presenciales, y da acceso solo mientras está activa.

## Forma de trabajo por módulo (acordado con el usuario)

Para cada módulo, en este orden:

1. Inspeccionar el código y la base existente antes de escribir nada.
2. Reutilizar lo que ya funciona, no reconstruir componentes existentes.
3. No eliminar datos ni tablas existentes de Supabase.
4. No inventar estructuras de Supabase — si falta una tabla, columna, env var
   o integración, avisar y confirmar antes de crearla.
5. Implementar únicamente lo que pide el módulo solicitado (no adelantar
   funcionalidad de otros módulos del roadmap).
6. Al terminar, verificar que funcione (probar en local, no asumir) antes de
   seguir.
7. No avanzar automáticamente al siguiente módulo sin que el usuario lo pida.
8. Priorizar la solución más simple y demostrable — es un proyecto
   universitario, no producción a escala.

## Gotchas / lecciones aprendidas

- **PENDIENTE DE SEGURIDAD — rotar las claves de Supabase antes de cargar
  alumnas reales.** La `service_role` legacy fue pegada en el chat el 2026-10-06:
  tratarla como expuesta. El proyecto ya usa el sistema nuevo de JWT Signing
  Keys (ECC), así que **no existe "generar nuevo secreto"**: la forma de anularla
  es pasar a las claves nuevas y desactivar las legacy. Pasos: (1) API Keys >
  crear *secret key* (`sb_secret_...`) y usar la *publishable* (`sb_publishable_...`)
  como `SUPABASE_KEY`; (2) cargarlas en `.env` (con `sed ... $(pbpaste) ...`) y en
  **ambos** Vercel + Redeploy; (3) verificar los dos sitios; (4) recién ahí API
  Keys > Legacy API keys > *Disable*. No revocar la "Previous key" (HS256) antes:
  rompe las claves legacy que la app usa hoy. Nunca pedir ni aceptar claves por el chat.
- **El deploy oficial (theta) necesita su propia `SUPABASE_SERVICE_KEY`**, cargada
  en el dashboard de esa cuenta de Vercel (Settings > Environment Variables).
  Sin ella el login y las reservas funcionan, pero fallan: alta y reinicio de
  contraseña de alumnas, cambio de contraseña y revocar la sesión al salir
  (muestran "Falta configurar la clave de administrador"). Pasarle la clave a
  esa persona por un medio privado (no por chat grupal) y rotarla al final.
- **`SUPABASE_SERVICE_KEY` (service_role) es un secreto de administrador**: solo
  en `.env` y en variables de Vercel, jamás en el repo, en el chat ni en HTML.
  Ignora RLS. Se usa únicamente en el servidor (crear usuarias con
  `auth.admin`, leer/escribir `profiles`). Hay que cargarla en cada proyecto de
  Vercel por separado. Para login usar un cliente **descartable por pedido**
  (`create_client`), nunca el singleton: `sign_in_with_password` guarda la
  sesión dentro del cliente y mezclaría usuarias entre pedidos.

- **Supabase free pausa el proyecto tras ~1 semana sin actividad** y todo el
  sitio pasa a dar `Internal Server Error` (500) en *todos* los deploys a la
  vez, aunque el código esté bien. Pasó el 2026-10-06. Diagnóstico: `get_project`
  devuelve `status: INACTIVE`. Solución: `restore_project` (o botón *Restore*
  en el dashboard de Supabase); tarda ~3 min en volver y los datos se
  conservan (volvió a pasar el 2026-10-13; en el login se ve "No pudimos
  completar la operación"). Ojo: durante la restauración el estado pasa por
  `COMING_UP` y `RESTORING` y la base **parece vacía** (0 tablas, 0 usuarias) y
  después da 500 intermitentes: no es pérdida de datos, esperar a que quede
  estable antes de tocar nada. Si dos deploys distintos fallan juntos, sospechar de Supabase
  antes que de Vercel. Para evitarlo, entrar al sitio al menos una vez por
  semana (o programar un ping).

- **ACTUALIZACIÓN 2026-10-16: el deploy oficial (theta) sí se actualiza solo al
  mergear a `main`** (verificado: el deploy de producción coincide con el último
  commit de `main`). Lo de abajo queda como historia por si vuelve a fallar.
- **El deploy oficial (`just-show-up-theta.vercel.app`) NO auto-deploya al
  pushear a `main`**, pese a figurar "conectado" a GitHub en el dashboard de
  esa cuenta. Cada push hay que ir manualmente a Deployments → Redeploy para
  que tome el código nuevo — confirmado varias veces (quedó una versión vieja
  varios commits atrás hasta hacer Redeploy a mano). Pendiente de investigar
  la causa de fondo (webhook roto, GitHub App con permisos parciales, etc.)
  — no se pudo diagnosticar del todo porque esa cuenta de Vercel no es
  accesible desde este CLI. Mientras tanto: **avisar siempre que se pusheó
  algo y haga falta Redeploy manual para verlo reflejado**.
- **Usuarias de prueba en `auth.users`** (mails `*@prueba.invalid`, ids
  `a0000000-0000-4000-8000-00000000000{1,2,3}`, sin contraseña): eran del selector
  del modo prueba (ya quitado). Sirven para probar varias alumnas a la vez:
  `auth.admin.generate_link({"type": "magiclink", ...})` da una sesión sin
  contraseña (abrir el link y leer `access_token` del `#`). Borrarlas al entregar (primero sus
  `reserva`/`listadeespera`, después `delete from auth.users where email like
  '%@prueba.invalid'`; `profiles` cae en cascada).
- **Zona horaria**: las horas de clase son locales (`America/Montevideo`) pero
  Vercel corre en UTC. Toda comparación de "ahora" va por `clases.ahora()`
  (usa `zoneinfo`; `tzdata` está en `requirements.txt` porque el runtime no
  garantiza la base de zonas horarias). No usar `date.today()` ni `datetime.now()`.
- **El linter de Supabase avisa "Signed-In Users Can Execute SECURITY DEFINER
  Function"** para `reservar`, `cancelar_reserva`, `anotar_en_espera`,
  `reservas_confirmadas`, `generar_clases`, `marcar_asistencia` y `es_profesora`:
  es intencional (son la única vía de escritura y validan adentro quién llama).
- **`reserva.id_bloqueo` no tiene clave foránea a propósito**: si la tuviera, al
  eliminar un bloqueo se perdería la marca de las reservas que no se recuperan
  (clases pasadas) y no se podría distinguir quién canceló. NULL = canceló la
  alumna (o está activa); con valor = la canceló la profesora con ese bloqueo.
- **CSS: `.btn-secundario` está definido antes que `.btn`** en `style.css`, así
  que en elementos con las dos clases gana el fondo verde de `.btn` (pasa en
  "Semana anterior/siguiente" y en el "Volver" de los diálogos). Pendiente de
  corregir; en Planificación se corrigió solo para esa pantalla
  (`.acciones-confirmar`) para no cambiar el diseño del resto sin pedirlo.
- **No aplicar `20261021000000_resumen_mensual_pagos.sql` (rama de Josefina, PR #13)**:
  la reemplaza `20261021150000_resumen_mensual_pagos.sql` (facturación completa con sus
  protecciones). Aplicar las dos crearía conflicto.
- **Las pruebas locales viven en `tests/base_local/` (no en el scratchpad)**: el 27/10 macOS
  borró los scripts que estaban en `/private/tmp` (limpieza automática). No guardar en `/tmp`
  nada que haya que conservar. Esas pruebas usan solo datos sintéticos (nunca copiar datos
  reales al repo) y `unittest discover` no las corre (no tienen `__init__.py`: necesitan Postgres).
- **Pruebas contra Supabase real (`tests/supabase_real/`)**: no visitar la grilla (`/`), el día
  (`/dia/...`) ni Mis inscripciones con datos reales: generan clases de semanas nuevas y procesan
  listas de espera reales (cambia la base). Para facturar en pruebas usar un mes cerrado sin
  datos reales. Las alumnas de prueba sin contraseña entran con `auth.admin.generate_link`
  (magic link) + `verify_otp(token_hash)`; en httpx poner las cookies `sb_acceso/sb_refresco`
  SIN dominio (con `domain="localhost"` no se mandan). Las pruebas consumen números de
  `factura_numero_seq`: al terminar, si no hay facturas, reiniciarla con
  `select setval('public.factura_numero_seq', 1, false)`.
- **Mercado Pago: con las cuentas de prueba actuales los pagos vienen con `live_mode = true`**
  (las pruebas corren en el entorno productivo entre cuentas ficticias). No sirve para distinguir
  pagos de prueba: se verifica que la vendedora tenga la etiqueta `test_user` y que el pagador sea
  `TESTUSER…`. En pagos con tarjeta Mercado Pago **no devuelve el mail del pagador**: mirar
  `payer.id` → `GET /users/{id}`. La comisión que muestra (ej. $0,06 sobre $1) es simulada y la
  paga la vendedora de prueba. En el checkout, el documento del titular de prueba va como
  **Otro / 123456789** (la CI 12345678 no pasa el dígito verificador).
- **Logo de Mercado Pago**: el oficial está en `app/static/img/mercado-pago-logo.svg` (zip
  "Logos Mercado Pago 2025", uso digital RGB, color horizontal), **sin editar** (la guía lo
  prohíbe; una prueba compara su huella). Reglas de la marca: no ponerlo sobre celeste/azul de
  la marca ni otros colores (sí sobre blanco), no deformarlo ni recolorearlo, respetar su margen.
- **Pruebas locales con Vault**: `localdb.SHIM` simula el esquema `vault` (sin cifrado, sin acceso
  para anon/authenticated). `test_facturacion_base.py` aplica la migración de Mercado Pago después
  de las de facturación (depende de la tabla `pago`).
- **`public._ahora()`** es el único "ahora" de la facturación: las pruebas locales lo
  reemplazan para simular fechas (último día del mes, día 1, cambio de año). No usar `now()`
  directo en funciones nuevas de facturación (sí en `default` de columnas de auditoría).
- **PDF con fuentes estándar (Helvetica)**: solo Latin-1. `factura_pdf._txt` cambia "−", "—",
  "→" y similares; un carácter nuevo fuera de Latin-1 saldría como "?". Para probar el texto
  de un PDF hay que descomprimir los streams (y los paréntesis aparecen escapados: `\(`).
- **La app de facturación no usa "embeds" de PostgREST** (`tabla(campos)` dentro del select):
  solo filtros simples. Así se pudo probar el código real contra un Postgres local con un
  cliente falso (sin PostgREST). Si se agregan embeds, esa prueba deja de cubrirlos.
- **Mientras no se aplique la fase 2 de seguridad, `anon` puede modificar `clase` y
  `reserva`** (verificado 2026-10-21 en la base local: cambiar el precio de una clase pasada,
  cancelar una reserva cobrable o crear una reserva confirmada en una clase pasada). Eso
  altera lo que se cobra en los resúmenes **pendientes** (los pagados quedan fijos por el
  detalle). Las alumnas con sesión no pueden. Se cierra con `auth_etapa2_cerrar_anon.sql`.
- **Las pruebas automáticas NUNCA deben elegir "el primer registro" de una tabla
  compartida** (16/10: una prueba tomó el primer bloqueo creyendo que era el suyo y eliminó
  una licencia real). Identificar lo propio por un marcador (`motivo` que empieza con
  `PRUEBA`, horarios de prueba con precio 1, mails `@prueba.invalid`) y abortar si se va a
  tocar algo que no lo tiene. Comparar una "foto" (hash) de las tablas antes y después.
- **Desde Tipos de clase, el cupo/precio/duración de un horario los define su tipo**: un
  trigger pisa lo que se escriba en `horario.duracion/cupo_max/precio`. Las pruebas que
  necesiten un cupo particular tienen que crear un tipo propio (`PRUEBA-...`) y, para
  "cambiar el cupo" de una clase ya generada, actualizar `clase.cupo_max` (cada clase guarda
  el suyo). Las pruebas deben recordar los ids de lo que crean: buscar "los horarios del
  tipo de prueba" falla si la prueba misma les cambió el tipo (pasó el 21/10).
- **Imágenes de Storage: la URL pública puede seguir respondiendo un rato después de borrar
  el archivo** (caché del CDN). Para verificar que se borró, listar el bucket, no pedir la URL.
- **Los archivos de migración del repo tienen comentarios dentro de las funciones, pero en
  Supabase se aplicaron sin ellos** (planificación y lista de espera): `pg_get_functiondef`
  difiere solo en esas líneas. Para comparar o restaurar, sacar las líneas `--`.
- **Cupos**: la fuente de verdad son las filas de `reserva` con
  `estado='confirmada'`; los "lugares disponibles" salen de la función
  `reservas_confirmadas` (las alumnas no pueden contar reservas ajenas por RLS).
  `clase.cantidad_inscriptas` es una copia que las funciones resincronizan. El
  cupo máximo sale de `clase.cupo_max` (desde Tipos de clase cada clase guarda el suyo; antes
  salía de `horario.cupo_max`).
  `reservar`/`cancelar_reserva`/`anotar_en_espera` hacen `select ... for update`
  sobre la clase, así que los pedidos simultáneos se ordenan: sin sobreventa
  (probado: 3 reservas a la vez con cupo 2 → 2 ok, 1 completa).
- **Cambiar la contraseña cierra todas las sesiones en Supabase**, incluida la
  de quien la cambia. Por eso `contrasena_cambiar` abre una sesión nueva
  automáticamente (fijando `request.state.tokens_nuevos`, que el middleware
  convierte en cookies). Sin eso la usuaria quedaba afuera apenas la cambiaba.
- **Recuperación de contraseña — configuración en el dashboard de Supabase**:
  (1) Authentication > URL Configuration > *Redirect URLs* tiene que incluir
  `https://just-show-up-amber.vercel.app/**` y `https://just-show-up-theta.vercel.app/**`
  (cargadas y verificadas el 2026-10-13); si se suma otro dominio y no se agrega, Supabase ignora el `redirect_to` y manda al *Site URL* (hoy
  `http://localhost:3000`, que no existe). localhost en cualquier puerto sí está
  permitido. (2) El mailer gratis de Supabase solo entrega a mails de miembros
  del equipo de la organización y ~2 por hora; para alumnas reales hace falta
  SMTP propio (Resend). Con mails inexistentes (`*.invalid`) Supabase falla y la
  app lo registra en el log sin decírselo a la usuaria.
- **El link de recuperación trae la sesión después del `#`** (flujo implícito),
  que nunca llega al servidor: un script en `restablecer_contrasena.html` la
  pasa a campos ocultos del formulario y limpia la URL. Para probar sin mandar
  mails: `auth.admin.generate_link({"type": "recovery", ...})` devuelve el link
  (abrirlo lo gasta: sirve una sola vez).
- **Python local ≥ 3.10**: el código usa `str | None` en anotaciones, así que el
  Python 3.9 que trae macOS no lo puede importar (Vercel usa 3.12). Con `uv`:
  `uv venv --python 3.12 .venv && uv pip install -r requirements.txt httpx`
  (`httpx` lo pide `TestClient` para las pruebas).
- **`pgserver` (el Postgres local de `tests/base_local/`) solo tiene wheels hasta
  Python 3.12** (verificado 2026-10-27: `pgserver==0.1.4` en PyPI no publica nada
  para cp313/cp314). Si la compu trae Python 3.13 o 3.14 por defecto (`python3
  --version`), `pip install -r requirements-dev.txt` falla con "No matching
  distribution found for pgserver" aunque el resto de las dependencias instale
  bien — no es un problema de red. Sin `uv`: `brew install python@3.12` y
  `$(brew --prefix python@3.12)/bin/python3.12 -m venv .venv312 && .venv312/bin/pip
  install -r requirements-dev.txt` (dejar el `.venv` normal como esté; `.venv312`
  está en `.gitignore`). Para solo correr la app o las pruebas sin Postgres local
  (`unittest discover tests`), cualquier Python ≥ 3.10 alcanza.
- **Servidor local**: `uvicorn app.main:app --port 8000 --env-file .env` (la app
  no carga `.env` sola; sin `--env-file` da `KeyError: 'SUPABASE_URL'`). El
  panel de preview del desktop no puede ejecutar el venv dentro de `~/Desktop`
  (permiso de macOS): levantarlo con Bash en segundo plano.
- **No insertar filas a mano en `auth.users` con SQL sin completar los campos
  de token** (`confirmation_token`, `recovery_token`, `email_change`, etc. deben
  ser `''`, no NULL): GoTrue falla con 500 "Database error finding users". Crear
  siempre con `auth.admin.create_user`. Las 3 usuarias de prueba se corrigieron.
- **Un mail repetido no se puede detectar con el registro público de Supabase**
  si la confirmación por mail está activada (devuelve éxito para no revelar
  cuentas). Acá no aplica: las cuentas las crea el servidor con el admin API, que
  sí informa `email_exists`.
- **Las policies RLS siguen abiertas a `anon`** en `horario`, `clase`, `reserva` y
  `listadeespera` hasta aplicar la fase 2 de la etapa 2 (ver Estado actual): la
  app ya no las usa, pero alguien con la clave pública podría escribir directo.
  `profiles` sí está cerrada. El registro abierto de Supabase ("Allow new users
  to sign up") está **apagado** (verificado 2026-10-13, `disable_signup:true`).

- **`vercel.json`**: no usar `rewrites` tipo catch-all con
  `destination: "/api/index.py"`. Un cambio reciente de Vercel hace que el
  rewrite reescriba el path real que recibe FastAPI, y todo devuelve 404. Con
  un solo `api/index.py` alcanza con `functions.includeFiles`, sin rewrites.
- **`supabase-py` actualizado a 2.32.0** (2026-10-13): acepta las claves nuevas
  `sb_publishable_`/`sb_secret_` (la 2.7.4 solo aceptaba JWT `eyJ...`). Los errores
  de auth se importan de `supabase_auth.errors` (el paquete `gotrue` ya no se usa).
  Al actualizar el venv local, desinstalar `gotrue` para no importar el viejo.
- Pegar esa key manualmente en un campo de Vercel desde el celular es muy
  propenso a truncarse — mejor usar el botón de copiar del bloque de código,
  o hacer ese paso puntual desde una computadora.
- Las env vars en Vercel solo toman efecto en el **próximo deploy** — hay que
  hacer *Redeploy* después de guardarlas, no alcanza con guardarlas solas.
- Hay **dos deployments de Vercel distintos** para este mismo repo:
  - el de prueba (`just-show-up-amber.vercel.app`), cuenta `julietaalvez-lgtm`,
    sin auto-deploy por GitHub (el GitHub App de Vercel no tiene permiso sobre
    el repo en esa cuenta);
  - el "oficial" (`just-show-up-theta.vercel.app`), conectado por GitHub,
    en la cuenta de la dueña del repo.
  Las env vars hay que cargarlas en **cada proyecto de Vercel por separado**.

## Convenciones

- Nombres de tablas/columnas en snake_case en español, tal como están en el
  documento de especificación (`dia_semana`, `hora_inicio`, etc.) — no
  traducir al inglés.
- **Días de la semana**: en la base van sin tilde (`miercoles`, `sabado`; el
  check de `horario.dia_semana` solo acepta esos valores). Para mostrarlos usar
  siempre `clases.nombre_dia(...)` (o el filtro Jinja `| nombre_dia`), que da
  "Miércoles"/"Sábado"; nunca `.capitalize()` sobre el valor guardado.
- Comentarios en el código: mínimos, solo si aclaran un "por qué" no obvio.
- Commits en español, mensaje corto (y cuerpo si hace falta explicar el motivo).
- Trabajo en rama por módulo + PR a `main` (ver "Trabajo en equipo" arriba).
