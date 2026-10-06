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
  espera** (cupo 0), "Reservada" + **Cancelar clase** (hasta 1 h antes) o
  "Finalizada". Una clase ya reservada no vuelve a ofrecer "Reservar". Las
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
- **Autenticación (módulo en curso, 2026-10-06)** — decisiones del usuario:
  hay **una sola profesora**; **no hay registro público**: la profesora crea las
  cuentas de sus alumnas presenciales (los usuarios del contenido online se
  resuelven en su módulo). Hecho en base: tabla `profiles` (RLS activado **sin
  policies** a propósito: solo el servidor accede, con la service key) + trigger
  `on_auth_user_created` (el rol siempre nace `alumna`, nunca se toma del
  cliente) + FK `reserva.id_alumna` y `listadeespera.id_alumna` → `profiles(id)`.
  Pendiente: login/logout, sesión por cookie, proteger rutas, panel de alumnas
  de la profesora, script para crear la cuenta de la profesora, quitar el
  selector provisorio. **Hasta que se despliegue ese código, reservar en los
  deploys falla** (las alumnas provisorias ya no existen para la FK).
- **PROVISORIO — identidad de alumna** (se elimina con el módulo de Auth): no hay login. La grilla tiene un selector
  de "Alumna (prueba)" (3 alumnas fijas en `app/clases.py`, guardada en la
  cookie `alumna_id`). `reserva.id_alumna` y `listadeespera.id_alumna` son
  `uuid` **sin foreign key** porque todavía no existe `profiles`. Al hacer el
  módulo de Auth: crear `profiles`, borrar los datos de prueba, agregar la FK
  y reemplazar el selector por la sesión real.
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

1. **Autenticación y perfiles** — pendiente. Supabase Auth, roles alumna/profesora.
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
5. **Asistencia** — la profesora marca presente/ausente.
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
  conservan. Si dos deploys distintos fallan juntos, sospechar de Supabase
  antes que de Vercel. Para evitarlo, entrar al sitio al menos una vez por
  semana (o programar un ping).

- **El deploy oficial (`just-show-up-theta.vercel.app`) NO auto-deploya al
  pushear a `main`**, pese a figurar "conectado" a GitHub en el dashboard de
  esa cuenta. Cada push hay que ir manualmente a Deployments → Redeploy para
  que tome el código nuevo — confirmado varias veces (quedó una versión vieja
  varios commits atrás hasta hacer Redeploy a mano). Pendiente de investigar
  la causa de fondo (webhook roto, GitHub App con permisos parciales, etc.)
  — no se pudo diagnosticar del todo porque esa cuenta de Vercel no es
  accesible desde este CLI. Mientras tanto: **avisar siempre que se pusheó
  algo y haga falta Redeploy manual para verlo reflejado**.
- **Las policies RLS de `clase`, `reserva` y `listadeespera` están abiertas a
  `anon`** (select/insert/update), igual que `horario`: es provisorio hasta que
  exista Auth. Antes de dar por cerrado el proyecto hay que reemplazarlas por
  policies por usuario (`auth.uid()`) y por rol.
- **Zona horaria**: las horas de clase son locales (`America/Montevideo`) pero
  Vercel corre en UTC. Toda comparación de "ahora" va por `clases.ahora()`
  (usa `zoneinfo`; `tzdata` está en `requirements.txt` porque el runtime no
  garantiza la base de zonas horarias). No usar `date.today()` ni `datetime.now()`.
- **Cupos**: `clase.cantidad_inscriptas` se recalcula contando reservas
  confirmadas (`_sincronizar_inscriptas`); el cupo máximo sale de
  `horario.cupo_max` (la tabla `clase` no lo copia). Para evitar sobreventa sin
  funciones SQL, se inserta la reserva y se recuenta: si se pasó del cupo se
  cancela esa reserva. Es suficiente para la demo, no es transaccional.
- **Las rutas `/profesora/...` no tienen ningún control de acceso todavía**
  (no existe login). Cualquiera que entre a la URL puede usarlas, a propósito
  — se decidió no armar un login provisorio que después se tira. Se cierra
  cuando se implemente el módulo de Autenticación.

- **`vercel.json`**: no usar `rewrites` tipo catch-all con
  `destination: "/api/index.py"`. Un cambio reciente de Vercel hace que el
  rewrite reescriba el path real que recibe FastAPI, y todo devuelve 404. Con
  un solo `api/index.py` alcanza con `functions.includeFiles`, sin rewrites.
- **`supabase-py` 2.7.4** valida que `SUPABASE_KEY` tenga formato JWT exacto
  (regex con puntos). No acepta las publishable keys nuevas (`sb_publishable_...`).
  Usar siempre la anon key legacy (empieza con `eyJ...`).
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
- Comentarios en el código: mínimos, solo si aclaran un "por qué" no obvio.
- Commits en español, mensaje corto (y cuerpo si hace falta explicar el motivo).
- Trabajo en rama por módulo + PR a `main` (ver "Trabajo en equipo" arriba).
