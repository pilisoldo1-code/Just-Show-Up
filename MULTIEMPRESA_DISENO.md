# Multiempresa — diseño técnico (30/10/2026)

Requisito obligatorio del profesor para la defensa del 17/11/2026. Decisiones funcionales de Julieta
del 30/10 (resumen en `CONTINUIDAD_PROYECTO.md`). Este documento es la referencia técnica.

## Modelo

- **`empresa`**: `id`, `nombre`, `codigo` (2–6 letras mayúsculas, único, fijo desde la primera factura),
  `activa`, `ultimo_numero_factura` (contador propio), `mp_habilitado`, `creado_en`.
- **`membresia`**: una fila por persona y empresa, con `es_admin`, `es_profesora`, `es_alumna` (se pueden
  combinar) y `activa`. Única por `(id_usuario, id_empresa)`.
- **`profiles`**: `es_superadmin` (sin empresa) y `empresa_activa` (la empresa elegida; solo puede ser
  una con membresía activa en una empresa activa).
- **`id_empresa` en todas las tablas operativas**: `tipo_clase`, `horario`, `clase`, `reserva`,
  `listadeespera`, `asistencia`, `bloqueo`, `resumen_mensual`, `resumen_mensual_detalle`,
  `factura_historial`, `pago`, `pago_anulacion`, `mp_intento` (obligatoria) y `mp_evento` (opcional:
  es el registro de avisos de Mercado Pago, que llegan sin sesión).
- **Profesora asignada**: `horario.id_profesora`, `clase.id_profesora` y, en las líneas de factura,
  `resumen_mensual_detalle.id_profesora` + `profesora_nombre` (foto fija, como el resto de la línea).
- `mp_configuracion` sigue siendo una sola (modo prueba); `empresa.mp_habilitado` decide qué empresa
  puede usarla (solo la Empresa 1).

## Inicio de sesión (decisión del 30/10, noche)

- La pantalla pide **Nombre del lugar, Correo electrónico y Contraseña** y un botón «Ingresar». No hay
  lista de empresas en ningún lado (ni en el login ni en el menú) ni selector «Cambiar de empresa»: para
  usar otro lugar, se cierra sesión y se entra con el otro nombre (mismas credenciales).
- **Nombre del lugar**: se compara por una clave (`_clave_lugar`): minúsculas, sin tildes, sin espacios ni
  signos («Just Show Up» = «just  show-up» = «JÚST SHOW UP»). La clave es **única** entre empresas (índice
  único): no puede existir un lugar que se escriba igual. El código (`JSU`, `PIL`) queda interno (facturas).
- `entrar_a_empresa(lugar)` (con la sesión recién abierta) valida en un paso: lugar existente y activo +
  membresía activa con algún rol. Ante cualquier falla devuelve lo mismo y el servidor muestra un único
  mensaje («No pudimos iniciar sesión con esos datos…»), revoca la sesión recién abierta y no deja cookies.
  No se distingue lugar inexistente, desactivado, credenciales incorrectas ni falta de pertenencia.
- **Superadministradora**: acceso propio en `/admin/login` (mail y contraseña, sin lugar). Cualquier otra
  cuenta recibe el error genérico y su sesión se revoca. La superadministradora no puede entrar a un lugar.

## Empresa activa

La base guarda la empresa en uso en `profiles.empresa_activa`. `public.empresa_activa()` la devuelve
solo si la persona tiene membresía activa en esa empresa y la empresa está activa; si no, `null` y no se
ve nada. Todas las políticas filtran `id_empresa = public.empresa_activa()`.

**La empresa es de cada sesión (parte 3c, decisión del 30/10).** Cada token de Supabase trae `session_id`
(firmado, no se puede falsificar; es la fila de `auth.sessions` de ese dispositivo). `sesion_empresa`
guarda el lugar que eligió cada sesión al iniciar sesión, y `empresa_activa()` lee **esa** fila: una persona
puede estar en Just Show Up en la computadora y en Pilates Centro en el celular sin que una sesión cambie la
otra. Nadie lee ni escribe `sesion_empresa` directo; una sesión no puede pisar la de otra persona. Al cerrar
sesión se borra la fila, y al entrar se limpian las de sesiones que ya no existen. El servidor pregunta
`empresa_de_sesion()`; sin lugar, pide iniciar sesión de nuevo. Hasta la parte 4, una sesión sin fila (código
anterior) usa `profiles.empresa_activa` como respaldo; la parte 4 lo quita.

## Redirección por rol

| Roles en el lugar | Entra a | Además |
|---|---|---|
| Administradora (con o sin profesora o alumna) | `/administracion` (panel con resumen y accesos) | Todo su lugar; si también es alumna, «Mis clases →» |
| Profesora | `/profesora/horarios` (solo sus horarios, clases, planificación, asistencias y estadísticas) | Si también es alumna, «Mis clases →» |
| Alumna | `/` (clases, inscripciones, historial, facturas) | — |
| Superadministradora | `/admin/empresas` | — |

Desde las pantallas de alumna, quien también es docente ve «Panel de profesora →» o «Panel de
administración →». No se elige el rol a mano. Cada ruta exige su rol en el servidor y la base lo vuelve a
comprobar.

## Roles

| Rol | Puede | No puede |
|---|---|---|
| Superadministradora | Crear, editar, desactivar y reactivar empresas; ver nombre, código, estado, cantidad de profesoras y alumnas y administradoras | Ver datos operativos o personales de una empresa |
| Administradora | Todo dentro de su empresa activa | Nada de otra empresa |
| Profesora (no admin) | Sus horarios, clases, licencias, cancelaciones, asistencias y estadísticas; nombre y contacto de alumnas de su empresa | Cédula y otros datos personales; facturas y pagos; altas de personas; clases de otra profesora |
| Alumna | Lo de hoy, dentro de la empresa activa | Ver o modificar otra empresa |

Funciones de ayuda en la base: `empresa_activa()`, `es_superadmin()`, `es_admin()`,
`es_docente()` (admin o profesora), `puede_gestionar_horario(id)` / `puede_gestionar_clase(id)`
(admin, o la profesora asignada). `es_profesora()` conserva el nombre por compatibilidad y pasa a
significar `es_docente()`; las funciones que solo puede usar la administradora usan `es_admin()`.

## Separación de datos (tres capas)

1. **Políticas de lectura** por empresa activa y rol en todas las tablas.
2. **Protección general** (`_proteger_empresa`, trigger en cada tabla operativa): cualquier alta,
   modificación o baja hecha con sesión de usuaria sobre una fila de otra empresa se rechaza, aunque
   venga de una función `security definer` con un error. La empresa de las filas nuevas se completa
   sola (`_completar_empresa`: desde la fila madre o la empresa activa) y tiene que coincidir con la
   de la fila madre.
3. **Servidor**: cada pedido carga la membresía de la empresa activa y las rutas exigen el rol.

Además se cierra el acceso sin sesión (`anon`) de `horario`, `clase`, `reserva` y `listadeespera`
(amber deja de funcionar: aceptado).

## Facturación por empresa

Una factura por alumna, empresa y mes (el índice único pasa a incluir `id_empresa`). Número:
`<código>-AAAA-MM-NNNN` con el contador de la empresa (bloqueo de fila). La Empresa 1 (`JSU`) continúa
desde el contador global actual: la factura `JSU-2026-09-0001` no cambia y la próxima de octubre es
`JSU-2026-10-0001`. Vencimiento, correcciones y PDF siguen igual, por empresa.

## Migraciones (cada una compatible con el código que esté en theta)

| # | Contenido | Compatible con el código actual |
|---|---|---|
| 1 | Tablas `empresa` y `membresia`, columnas nuevas, Empresa 1 con todos los datos, membresías desde `profiles.rol`, profesora asignada, triggers que completan la empresa | Sí: no cambia permisos |
| 2 | Funciones de ayuda, políticas por empresa, protección general, índices únicos por empresa, cierre de `anon` | Sí: todas las cuentas tienen la Empresa 1 activa |
| 3 | Funciones por rol y por empresa, numeración por empresa, profesora en las líneas, Mercado Pago solo en la Empresa 1, funciones de superadministradora y de membresías | Sí: mismas firmas (se agregan parámetros con valor por defecto) |
| 3b | Acceso por nombre del lugar: clave normalizada y única del nombre, `entrar_a_empresa()`, crear/editar empresa rechazan nombres repetidos | Sí: solo agrega funciones e índice |
| 3c | Empresa por sesión: `sesion_empresa`, `empresa_de_sesion()`, `salir_de_empresa()`; `entrar_a_empresa` ya no toca el perfil | Sí: las sesiones del código anterior usan el perfil como respaldo |
| 4 | Datos personales protegidos (fecha de nacimiento y carné solo con `datos_personales()`); **cédula por empresa** (`membresia.cedula`, única solo dentro de la empresa; las actuales pasan a la Empresa 1); `profesoras_empresa()`; la empresa sale **solo** de la sesión; se elimina la compatibilidad con `profiles.rol` | No: se aplica justo después del merge (el código nuevo funciona antes y después) |

Cada una con respaldo previo, prueba en Postgres local que reconstruye producción, vuelta atrás probada
y comparación de estructura y datos antes y después.

## Pruebas

- Todas las pruebas existentes (base local y app) adaptadas a empresa.
- `tests/base_local/test_multiempresa_base.py`: dos empresas × superadministradora, administradora,
  profesora y alumna (incluida una alumna en las dos), intentando leer y modificar todo lo de la otra
  empresa por tablas, funciones y triggers.
- En theta, solo pruebas de lectura con las cuentas de prueba.

## Cédula por empresa (decisión del 30/10)

Una sola cuenta por persona, pero la cédula se registra en cada empresa (`membresia.cedula`). Cada
administradora carga y ve solo la de su empresa; «cédula repetida» se controla solo dentro de la empresa, así
ningún aviso revela que la persona está en otro lugar. La factura copia la cédula registrada en la empresa
que factura. Nadie lee `membresia.cedula` directo (permiso por columna); se lee con `datos_personales()`.

## Altas de cuentas: de dónde salen los permisos

Los roles salen **solo** de `membresia`, que solo escriben: el alta de la cuenta (metadatos que pone el
servidor; el registro público está apagado), `asignar_membresia` (administradora, en su empresa) y
`asignar_administradora` (superadministradora). `es_superadmin` solo se cambia con la clave de administrador
(script). Ninguna sesión puede escribir `membresia`, `profiles.rol`, `profiles.es_superadmin` ni
`sesion_empresa`, y desde la parte 4 cambiar `profiles.rol` no tiene ningún efecto. Auditoría:
`tests/base_local/test_altas_cuentas_base.py`.

