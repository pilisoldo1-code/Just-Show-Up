# Just Show Up — clases presenciales + suscripción a clases grabadas

> **Para Claude Code**: si durante el trabajo descubrís una regla, convención,
> restricción técnica o gotcha nuevo (algo que te costó averiguar y que le
> ahorraría tiempo a otra sesión o a otra persona del equipo), agregalo a este
> archivo en la sección que corresponda (`Gotchas`, `Convenciones`, `Reglas de
> negocio`, etc.) como parte del mismo commit, sin que haga falta que te lo
> pidan explícitamente. Mantenerlo desactualizado es peor que no tenerlo.

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
  - Menú según rol (`_menu.html`): a la izquierda los links (profesora =
    Horarios · Alumnas; alumna = Clases); **a la derecha el menú de cuenta**:
    círculo con las **iniciales** + nombre, que al hacer click despliega **Mi
    perfil · Mis inscripciones · Mi historial · Mis facturas · Cerrar sesión** (la profesora
    solo ve Mi perfil y Cerrar sesión). Es un `<details>` que se cierra al
    clickear afuera o con Escape.
  - **Mi perfil** (`/perfil`): información de contacto (solo lectura; si algo está
    mal se lo pide a la profesora) + formulario para cambiar la contraseña.
    **Mis inscripciones** (`/mis-inscripciones`): solo lo vigente — próximas
    clases (con link a `/dia/{fecha}` para cancelar) y lista de espera con
    posición. **Mi historial** (`/mi-historial`, pantalla aparte por decisión
    del usuario): clases canceladas y clases pasadas. **Mis facturas**
    (`/mis-facturas`): pantalla vacía explicativa hasta que exista el módulo de
    resumen mensual y pagos.
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
- **Cancelación de reserva (hecha)**: "Cancelar reserva" abre un diálogo propio
  (Volver / Confirmar cancelación). Se permite hasta 1 h antes del inicio
  (exactamente 1 h sí). Dentro de la última hora el diálogo muestra el mensaje
  de plazo vencido y el servidor también lo rechaza (reserva intacta, cupo sin
  liberar, queda cobrable para el resumen mensual futuro). Clase iniciada: sin
  botón y el servidor la rechaza. Probado con 29 casos (ver Gotchas: cupos).
- Lo que NO hace todavía la lista de espera: ofrecer el cupo liberado a la
  primera de la lista (ventana de 30 min), mails, ni salir de la lista.
- Panel de profesora en `/profesora/horarios`: listado completo (activos e
  inactivos), alta (`/profesora/horarios/nuevo`), edición
  (`/profesora/horarios/{id}/editar`) y activar/desactivar
  (`POST /profesora/horarios/{id}/toggle`). Probado en local end-to-end.
  **Sin protección de acceso todavía** — ver Gotchas.
- Deploy de referencia funcionando: https://just-show-up-amber.vercel.app
- Existe un segundo deploy "oficial" conectado por GitHub a otra cuenta de
  Vercel (la de la dueña del repo) — ver Gotchas.

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

- **Planificación** (pedido del profesor) — **hecho** (licencias, vacaciones y
  cancelaciones puntuales). Pendientes del mismo pedido: **Estadísticas** y
  **Tipo de clase** (falta que el usuario pase los nombres de los tipos).

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
4. **Lista de espera** — **parcial**: anotarse y ver la posición. Falta ofrecer
   el cupo liberado (30 min), notificar y salir de la lista.
5. **Asistencia** — **hecho** (profesora marca; la alumna lo ve en Mi historial).
6. **Resumen mensual y pagos** — cálculo automático + registro de pagos
   (Mercado Pago para pagos, ver Stack).
7. **Suscripción y clases grabadas** — biblioteca de videos con gate de
   acceso. Modalidad (suscripción mensual vs. compra individual con vigencia)
   **todavía no está decidida** — no implementar hasta que se defina.
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
- **Qué se cobra (para el futuro resumen mensual)**: las reservas `confirmada`
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
- **Cupos**: la fuente de verdad son las filas de `reserva` con
  `estado='confirmada'`; los "lugares disponibles" salen de la función
  `reservas_confirmadas` (las alumnas no pueden contar reservas ajenas por RLS).
  `clase.cantidad_inscriptas` es una copia que las funciones resincronizan. El
  cupo máximo sale de `horario.cupo_max` (la tabla `clase` no lo copia).
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
