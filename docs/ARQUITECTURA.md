# Arquitectura de Just Show Up

> Estado al **10/11/2026** (`main` = `3f765ee`, desplegado en theta). Complementa `docs/MER.md`. Detalle de cada regla
> y sus pruebas en `CLAUDE.md`; estado y pendientes en `CONTINUIDAD_PROYECTO.md`.

## 1. Tecnologías (todo en planes gratuitos, costo $0)

| Pieza | Tecnología | Para qué |
|---|---|---|
| Servidor y pantallas | **Python 3.12 + FastAPI + Jinja2** (HTML generado en el servidor, sin framework JS) | Rutas, permisos, formularios |
| Base de datos y cuentas | **Supabase Free**: PostgreSQL 17, Auth, Storage, Vault | Datos, RLS, funciones, sesiones, archivos privados, secretos |
| Hosting | **Vercel Hobby**: *theta* (sitio oficial, se despliega solo con cada merge a `main`) y *amber* (recibe las subidas de videos y corre la limpieza diaria) | |
| Correos | **Gmail SMTP** de una cuenta exclusiva del proyecto, configurado en Supabase Auth | Invitaciones y «Olvidé mi contraseña» |
| Pagos | **Mercado Pago Checkout Pro, solo modo de prueba** (cuentas ficticias) | Demostración de cobro |
| Videos | **Zoom Básico** (grabación en la computadora) + **programa local en Python + FFmpeg** | Grabar, comprimir, subir |
| PDF | `fpdf2` | Facturas |
| Pruebas | `unittest`, Postgres local (`pgserver`), Supabase local (Colima + CLI), Mailpit | Sin tocar producción |

## 2. Vista general

```mermaid
flowchart LR
    subgraph Usuarias
        A[Alumna] ; P[Profesora] ; D[Administradora] ; S[Superadministradora]
    end
    subgraph Vercel
        T["theta<br/>FastAPI + Jinja2"]
        M["amber<br/>API de videos + limpieza diaria"]
    end
    subgraph Supabase
        DB[("PostgreSQL<br/>RLS + funciones")]
        AU[Auth]
        ST[("Storage<br/>bucket privado")]
        V[Vault]
    end
    A & P & D & S -->|navegador| T
    T -->|sesión de cada persona| DB
    T --> AU
    AU -->|SMTP| G[Gmail del proyecto]
    T -->|preferencia de pago| MP[Mercado Pago de prueba]
    MP -->|aviso / vuelta| T
    T -->|URL firmada| ST
    Z[Zoom en la Mac de la profesora] --> AG[Programa local + FFmpeg]
    AG -->|token de la computadora| M
    M --> DB
    M --> ST
    T -.lee la credencial.-> V
```

- La app habla con Supabase **con la sesión de cada persona** (`_sb(usuario)`), así las políticas RLS se aplican a quien
  pide. La clave de servidor se usa solo para administrar cuentas (invitaciones, cerrar sesiones) y leer el Vault.
- Las cookies `sb_acceso` / `sb_refresco` son `httpOnly`, `SameSite=lax` y `secure`; el servidor valida y renueva el
  token en cada pedido.

## 3. Multiempresa

```mermaid
flowchart TB
    U[Una cuenta<br/>mail + contraseña] --> M1["Membresía en Just Show Up<br/>(ej.: alumna)"]
    U --> M2["Membresía en Pilates Centro<br/>(ej.: alumna)"]
    L["/login: lugar + mail + contraseña"] --> E{entrar_a_empresa}
    E -->|membresía activa en ese lugar| OK[Sesión con ese lugar<br/>sesion_empresa]
    E -->|cualquier otro caso| NO[401 con mensaje genérico]
```

- Cada fila de negocio lleva `id_empresa`; las políticas filtran por la **empresa de la sesión** (`empresa_de_sesion()`).
- Una persona puede estar en varias empresas con roles distintos y la misma contraseña; cada dispositivo elige su lugar.
- Nunca se muestran listas de empresas ni se distingue «lugar inexistente» de «credenciales incorrectas».
- La superadministradora no pertenece a ninguna empresa: entra por `/admin/login`.

## 4. Roles y permisos

Cada membresía tiene **exactamente un rol**. La base vuelve a comprobar todo lo que filtra la pantalla.

| Acción | Alumna | Profesora | Administradora | Superadministradora |
|---|:-:|:-:|:-:|:-:|
| Reservar, cancelar, lista de espera | ✔ | | | |
| Mis inscripciones, Mi historial, Mis facturas | ✔ | | | |
| Pagar con Mercado Pago (solo la alumna de demostración) | ✔ | | | |
| Ver Videos On Demand de su empresa | ✔ | ✔ | ✔ | |
| Grabar («Empezar a grabar») | | ✔ | ✔ | |
| Horarios y clases | | solo los suyos | toda la empresa | |
| Marcar asistencia (durante la clase) | | sus clases | ✔ | |
| Corregir asistencias de clases terminadas (con motivo) | | | ✔ | |
| Planificación (licencias, cancelaciones puntuales) | | sus clases | ✔ | |
| Estadísticas | | sus clases | toda la empresa o por profesora | |
| Tipos de clase | | | ✔ | |
| Ver la lista de alumnas | | solo lectura | ✔ | |
| Alumnas y profesoras: invitar, editar, desactivar, cédula | | | ✔ | |
| Facturación, ajustes, confirmar, anular, pagos | | | ✔ | |
| Estado y errores de grabaciones | | | ✔ | |
| Empresas, administradoras, cobro del 25 % | | | | ✔ |

## 5. Reservas y lista de espera

```mermaid
sequenceDiagram
    actor Alumna
    participant App as theta
    participant DB as Base (funciones)
    Alumna->>App: Reservar clase
    App->>DB: reservar(clase) — bloquea la fila de la clase
    alt hay lugar
        DB-->>App: ok (reserva confirmada)
    else completa
        DB-->>App: completa → «Anotarme en lista de espera»
    end
    Note over DB: Cancelar hasta 1 h antes libera el lugar
    DB->>DB: _procesar_lista_espera: ofrece a la primera (30 min o hasta el inicio)
    Alumna->>App: Confirmar lugar / No me interesa
    App->>DB: confirmar_oferta / rechazar_oferta (pasa a la siguiente)
```

- No hay tareas programadas: las ofertas vencidas se procesan al abrir la grilla, el día o Mis inscripciones.
- Las clases se generan solas (idempotente) al abrir una semana; respetan licencias, cancelaciones y tipos.

## 6. Asistencias

```mermaid
flowchart LR
    C[Clase en curso] -->|profesora| MA[marcar_asistencia<br/>solo mientras dura]
    T[Clase terminada] -->|administradora + motivo| CO[corregir_asistencias<br/>todo o nada]
    MA --> H[(asistencia_historial)]
    CO --> H
    CO -.no si la factura del mes está confirmada.-> F[Facturación]
```

- Sin fila = «sin registrar» (distinto de ausente). Nada se marca ausente automáticamente.
- Inicio, Asistencias y la confirmación de facturas avisan las clases con asistencias pendientes.

## 7. Facturación y cobro del 25 %

```mermaid
stateDiagram-v2
    [*] --> Provisorio: durante el mes (se calcula, no se guarda)
    Provisorio --> Borrador: desde el último día del mes (al abrir Facturación)
    Borrador --> Borrador: ajustes / descuentos (con motivo)
    Borrador --> Confirmada: individual o masiva (todo o nada)
    Confirmada --> Anulada: anular y reemitir (con motivo)
    Anulada --> [*]
    Confirmada --> Pagada: pago manual o Mercado Pago de prueba
    Confirmada --> Vencida: después de vence_el (solo visual, se paga igual)
```

- **Qué se cobra**: reservas confirmadas de clases dictadas del mes. Las canceladas (por la alumna a tiempo o por la
  profesora) no se cobran.
- **25 %**: una ausencia explícita se cobra el 25 % del precio de la clase; presente o sin registrar, el 100 %. Rige
  desde `empresa.cobro_inasistencia_desde` (Just Show Up 01/09/2026, Pilates Centro 01/10/2026).
- **Vencimiento**: día 10 del mes siguiente, o 10 días desde la confirmación si se confirma después.
- La alumna ve sus facturas confirmadas desde el día 1 del mes siguiente (lo aplica RLS). PDF con los datos guardados.
- Todo cambio queda en `factura_historial`; numeración `JSU-AAAA-MM-NNNN` por empresa.

## 8. Mercado Pago (solo modo de prueba)

```mermaid
sequenceDiagram
    actor Alumna as Alumna de demostración
    participant App as theta
    participant DB as Base
    participant MP as Mercado Pago (prueba)
    Alumna->>App: Pagar con Mercado Pago
    App->>DB: crear_intento_mp (importe = total de la factura)
    App->>MP: preferencia (UYU, 1 cuota, vence en 30 min)
    MP-->>Alumna: Checkout Pro (cuenta compradora de prueba)
    MP->>App: vuelta + aviso (webhook)
    App->>MP: consulta el pago
    App->>DB: registrar_pago_mercado_pago (solo aprobado, importe y moneda exactos, sin duplicados)
```

- Credencial de prueba cifrada en el Vault; interruptor en la base. Solo `alumna@prueba.invalid` puede iniciar un pago.
- Guía de la demostración: `GUIA_DEFENSA_MERCADO_PAGO.md`.

## 9. Invitaciones y autenticación

```mermaid
sequenceDiagram
    actor Admin as Administradora
    participant App as theta
    participant SA as Supabase Auth
    actor Nueva as Persona invitada
    Admin->>App: Crear alumna / profesora
    App->>SA: invite_user_by_email (sin contraseña)
    SA-->>Nueva: «Te invitaron a Just Show Up» (Gmail del proyecto)
    Nueva->>App: /activar-cuenta (abrir no gasta el enlace)
    Nueva->>App: Guardar contraseña
    App->>SA: verify_otp(invite) + contraseña elegida
    App-->>Nueva: /login (lugar + mail + contraseña)
```

- Ninguna pantalla genera ni muestra contraseñas. Una cuenta existente (otra empresa) solo suma la membresía.
- Cuentas pendientes: no pueden entrar; se reenvía o se anula la invitación. Enlaces de 1 h y un solo uso.
- «Olvidé mi contraseña» responde igual exista o no la cuenta; «Enviar correo para cambiar la contraseña» usa ese flujo.

## 10. Videos On Demand

```mermaid
sequenceDiagram
    actor Prof as Profesora
    participant App as theta
    participant Z as Zoom (Mac)
    participant AG as Programa local
    participant AM as amber (API)
    participant ST as Storage privado
    Prof->>App: nombre, tipo, descripción → Empezar a grabar
    App->>App: grabacion_independiente_crear (plazo 3 h)
    App->>Z: abre su sala personal (zoommtg://…join)
    Prof->>Z: Unirse → Grabar → Finalizar la reunión
    Z->>AG: .mp4 en Documentos/Zoom
    AG->>AM: ¿corresponde? (hora y duración)
    AM-->>AG: autorizada (una sola candidata)
    AG->>AG: FFmpeg 360p + miniatura
    AG->>ST: sube las partes (URLs firmadas)
    AG->>AM: publicar → visible 24 h para su empresa
    Note over AM,ST: limpieza diaria borra los archivos vencidos y conserva el registro
```

- El programa no lleva claves de Supabase: URL, entorno y un token por computadora (en la base solo su huella).
- **Regla de los 15 minutos** (`_video_evaluar`, 10/11): una grabación se asocia a la solicitud de «Empezar a grabar»;
  una clase presencial solo compite si tiene grabación activada o ya tiene video. Sin solicitud ni clase candidata, no se
  sube nada.
- Reproducción con URLs firmadas de hasta 90 min; topes de almacenamiento y transferencia en `video_parametros`.

## 11. Seguridad

- **RLS en todas las tablas**; ninguna política ni permiso de escritura para `anon` (verificado el 10/11).
- **Escrituras solo por funciones** que validan rol, empresa, fechas y versión (`security definer`).
- **Rutas protegidas en el servidor** (`requiere_login/alumna/profesora/admin/superadmin`); sin sesión → `/login`.
- **Secretos** solo en `.env`, Vercel, Vault y el panel de Supabase; nunca en el repo.
- **Auditoría**: `factura_historial`, `asistencia_historial`, `baja_docente`, `pago_anulacion`, `mp_evento`.
- Pendientes conocidos: claves legacy de Supabase todavía activas en theta y amber; procedimiento en
  `docs/PROCEDIMIENTOS_DEFENSA.md`.
