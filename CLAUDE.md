# Just Show Up — clases presenciales + suscripción a clases grabadas

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
- Home page (`/`) muestra los horarios activos en vivo desde Supabase.
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

1. **Autenticación y perfiles** — Supabase Auth, roles alumna/profesora.
2. **Gestión de horarios y clases (Profesora)** — CRUD de horarios fijos +
   generación automática de clases de la semana.
3. **Grilla y reservas (Alumna)** — ver horarios/cupos, reservar y cancelar.
4. **Lista de espera** — anotarse cuando está lleno, liberación de cupo.
5. **Asistencia** — la profesora marca presente/ausente.
6. **Resumen mensual y pagos** — cálculo automático + registro de pagos.
7. **Suscripción y clases grabadas** — biblioteca de videos con gate de acceso.
8. **Notificaciones y extras** — mails (confirmación, recordatorio 24hs),
   integración con Google Calendar.

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

## Gotchas / lecciones aprendidas

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
