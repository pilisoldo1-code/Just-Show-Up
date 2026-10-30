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

## Empresa activa

La base guarda la empresa elegida en `profiles.empresa_activa`. `public.empresa_activa()` la devuelve
solo si la persona tiene membresía activa en esa empresa y la empresa está activa; si no, `null` y no se
ve nada. Todas las políticas filtran `id_empresa = public.empresa_activa()`. Elegir empresa es una
función (`elegir_empresa`) que valida la membresía; el servidor también la valida en cada pedido.

Consecuencia aceptada: el cambio de empresa se aplica a todas las sesiones de la persona.

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
| 4 | Datos personales protegidos: cédula, fecha de nacimiento y carné solo con `datos_personales()` (la persona o la administradora de su empresa); `profesoras_empresa()` para que las alumnas vean el nombre de las profesoras | No: se aplica justo después del merge (el código nuevo funciona antes y después) |

Cada una con respaldo previo, prueba en Postgres local que reconstruye producción, vuelta atrás probada
y comparación de estructura y datos antes y después.

## Pruebas

- Todas las pruebas existentes (base local y app) adaptadas a empresa.
- `tests/base_local/test_multiempresa_base.py`: dos empresas × superadministradora, administradora,
  profesora y alumna (incluida una alumna en las dos), intentando leer y modificar todo lo de la otra
  empresa por tablas, funciones y triggers.
- En theta, solo pruebas de lectura con las cuentas de prueba.
