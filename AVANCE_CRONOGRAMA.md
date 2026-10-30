# Cronograma hasta la defensa (17/11/2026) — registro diario

Cronograma aprobado por Julieta el 30/10. «M» = migración en Supabase (con su autorización explícita);
«Merge» = integrar un PR (lo autoriza ella). Ninguna autorización se da por aprobada.

| Fecha | Plan | Estado |
|---|---|---|
| Vie 30/10 | Multiempresa 1: diseño técnico; empresas, membresías, empresa activa, `id_empresa` en todas las tablas, datos actuales a la Empresa 1 | **Hecho** |
| Sáb 31/10 | Multiempresa 2: políticas por empresa, protección general, cierre de `anon`, contador por empresa | **Hecho (adelantado el 30/10)** |
| Dom 1/11 | Multiempresa 3: funciones por rol y por empresa, factura con profesora, Mercado Pago solo Empresa 1 | **Hecho (adelantado el 30/10)** |
| Lun 2/11 | Multiempresa 4 (servidor): empresa activa, selector, rutas por rol. En paralelo: Estadísticas y prototipo de Videos | **Hecho (adelantado el 30/10)** (sin las sesiones paralelas) |
| Mar 3/11 | Multiempresa 5 (pantallas): altas, profesora asignada, alumnas sin cédula para profesoras | **Hecho (adelantado el 30/10)** |
| Mié 4/11 | Multiempresa 6: panel de superadministradora, script de la Empresa 2, todas las pruebas | **Hecho (adelantado el 30/10)**; falta la revisión con datos reales en el PR |
| Jue 5/11 | Respaldo y PR de multiempresa → **① M** | |
| Vie 6/11 | Aplicar y verificar → **① Merge** y creación de la Empresa 2 | |
| Sáb 7/11 | Verificación en theta y Empresa 2 de demostración — **Hito 1** | |
| Dom 8/11 | Cobro del 25 % (base y servidor) | |
| Lun 9/11 | 25 % (pantallas, PDF, PR) + Estadísticas → **② M y merges** | |
| Mar 10/11 | Aplicar y verificar — **Hito 2** | |
| Mié 11/11 | Videos On Demand 1 | |
| Jue 12/11 | Videos On Demand 2 → **③ M y merge** | |
| Vie 13/11 | Magic Link y Avisos → **④ servicio de mails (Julieta), M y merge** | |
| Sáb 14/11 | Revisión de celulares → **⑤ Merge** | |
| Dom 15/11 | Pruebas generales y congelamiento → **⑥ Merge** — **Hito 3** | |
| Lun 16/11 | Preparación de la defensa y ensayo — **Hito 4** | |
| Mar 17/11 | Defensa | |

## Registro

### Vie 30/10
- Verificado en theta (solo lectura) el PR #26 de vencimiento.
- Decisiones de multiempresa cerradas con Julieta (ver `CONTINUIDAD_PROYECTO.md` y `MULTIEMPRESA_DISENO.md`).
- Diseño técnico: `MULTIEMPRESA_DISENO.md`.
- Migración 1 (`20261031000100_multiempresa_1_estructura.sql`) y vuelta atrás (`supabase/pendientes/vuelta_atras_multiempresa_1.sql`),
  rama `multiempresa`. **No aplicada en Supabase.**
- Prueba nueva `tests/base_local/test_multiempresa_1_base.py "<respaldo>"`: 39/39 sobre una **copia de los datos de producción**
  (respaldo `~/Documents/Just Show Up - respaldos/2026-10-31 antes de multiempresa/`): estructura igual a producción, todos
  los datos intactos, todo en la Empresa 1, membresías y profesora asignada, el código actual sigue funcionando (cuentas,
  horarios, clases, reservas), vuelta atrás exacta.
- Pruebas anteriores, ahora con la parte 1 aplicada: facturación 180/180, Mercado Pago 73/73, restricción 29/29,
  planificación 33/33, vencimiento 48/48, app 183.
- Hallazgo corregido: completar columnas con UPDATE disparaba `_horario_desde_tipo`; se cargan con valor por defecto.
- Sin bloqueos. Mañana: parte 2 (permisos por empresa, protección general, cierre de `anon`).

### Vie 30/10 (tarde) — adelanto de la parte 2
- Migración 2 escrita (`20261031000200_multiempresa_2_permisos.sql`): funciones de ayuda (`empresa_activa`, `es_admin`,
  `es_docente`, `es_superadmin`...), `es_profesora()` = docente de la empresa activa, políticas por empresa y rol en todas
  las tablas, protección general `_proteger_empresa`, claves compuestas (fila hija = empresa de la madre), únicos por
  empresa, contador de facturas por empresa (Empresa 1 continúa el global), cierre de `anon`.
- Compatibilidad con el código actual: planificación 33/33 y facturación 180/180 pasan con la migración 2.
- **Parte 2 terminada.** `tests/base_local/test_multiempresa_2_base.py "<respaldo>"`: **52/52** sobre la copia de producción
  con una segunda empresa (administradora, profesora, alumna y una alumna compartida): cada rol solo ve su empresa en las
  13 tablas; superadministradora sin datos operativos; profesora no administradora sin facturas ni reservas ajenas;
  alumna compartida separada al cambiar de empresa; empresa desactivada o sin membresía = nada; 23 ataques entre empresas
  (funciones y escrituras directas) sin efecto; protección general aunque una función tuviera un error; integridad de
  claves compuestas; acceso sin sesión cerrado; contador por empresa; vuelta atrás que se niega con dos empresas y deja la
  estructura idéntica a la parte 1.
- Compatibilidad con las partes 1 y 2 aplicadas: multiempresa 1 39/39, facturación 180/180, Mercado Pago 73/73,
  restricción 29/29, planificación 33/33, vencimiento 48/48, app 183.
- Siguiente (domingo 1/11, se puede adelantar): parte 3, funciones por rol y por empresa.
- Ojo para la parte 3: `generar_clases` recorre los horarios de TODAS las empresas; con dos empresas, la protección general
  rechazaría las clases de la otra. Hay que limitarla a la empresa activa antes de crear la Empresa 2.

### Vie 30/10 (noche) — adelanto de la parte 3
- Migración 3 (`20261031000300_multiempresa_3_funciones.sql`), generada por `scripts/multiempresa_3/generar.py` desde
  `scripts/multiempresa_3/manual.sql` + 12 funciones que pasan a ser solo de la administradora (facturación, pagos,
  cédula, tipos de clase). Vuelta atrás generada con las definiciones originales exactas (32 restauradas, 9 borradas).
- Contenido: generar clases, licencias, cobro, borradores, confirmar el mes, cupos y listas de espera solo de la empresa
  activa; asistencia y cancelaciones solo de la profesora de la clase o la administradora; licencia general de una
  profesora = solo sus clases (`bloqueo.id_profesora`); profesora en cada línea de factura (trigger); Mercado Pago
  además exige `empresa.mp_habilitado` (solo Empresa 1) y un pago aprobado de una empresa sin MP queda «a revisar»;
  funciones nuevas `elegir_empresa`, `asignar_membresia`, `cambiar_estado_membresia`, `crear_empresa`, `editar_empresa`,
  `cambiar_estado_empresa`, `resumen_empresas`, `asignar_administradora`.
- `tests/base_local/test_multiempresa_3_base.py "<respaldo>"`: **57/57** (copia de producción + Empresa 2 creada con las
  funciones de la superadministradora; operación de punta a punta de las dos empresas; factura PIL-2026-10-0001 y
  JSU-2026-10-0001 independientes; alumna compartida con una factura por empresa; Mercado Pago solo Empresa 1; permisos
  de cada rol; vuelta atrás exacta).
- Errores encontrados y corregidos por las pruebas: (1) las cuentas de administradora/profesora nacían también como
  alumna → los metadatos ahora llevan `rol_empresa`; (2) una profesora podía quitar una licencia general de la
  administradora (comparación con nulo) → corregido.
- Todas las suites: multiempresa 1 39/39, 2 52/52, 3 57/57, facturación 180/180, Mercado Pago 73/73, restricción 29/29,
  planificación 33/33, vencimiento 48/48, app 183. Las pruebas históricas aplican solo las migraciones anteriores a la
  que prueban.
- Siguiente: parte 4, servidor (empresa activa, selector, rutas por rol).

### Vie 30/10 (noche) — adelanto de las partes 4, 5 y 6
- **Servidor** (`app/auth.py`): la sesión trae la empresa activa y los roles de esa empresa (administradora, profesora,
  alumna) o la superadministradora. Con una sola empresa se entra directo; con varias, `/empresa/elegir` y «Cambiar de
  empresa» en el menú de cuenta. Facturación, pagos, tipos de clase, altas, cédula y reinicio de contraseña: solo la
  administradora (25 rutas). La base vuelve a comprobar todo.
- **Pantallas**: menú por rol (la profesora no administradora no ve Facturación ni Tipos de clase; la superadministradora
  solo ve Empresas); el menú de cuenta muestra la empresa. Alumnas: lista de las personas de la empresa con su rol; la
  profesora no administradora ve nombre y contacto, sin cédula, carné ni acciones. «Nueva persona» con rol (alumna,
  profesora, administradora, administradora y profesora); si el mail ya tiene cuenta se suma a la empresa con la misma
  contraseña. Reiniciar contraseña solo a personas que están únicamente en esta empresa. La profesora aparece en el día de
  la alumna, en Asistencias (administradora), en las líneas de la factura y en el PDF, que lleva el nombre de la empresa.
- **Panel de la superadministradora** (`/admin/empresas`): crear, editar (el código queda fijo después de la primera
  factura), desactivar/reactivar y asignar administradora (cuenta nueva con contraseña temporal o cuenta existente).
  Solo ve nombre, código, estado, cantidad de profesoras y alumnas y quién administra.
- **Migración 4** (`20261031000400_multiempresa_4_datos_personales.sql`, se aplica después del merge): la cédula, la
  fecha de nacimiento y el carné ya no se leen directo de `profiles` con una sesión: solo con `datos_personales()` (la
  persona misma o la administradora de su empresa). `profesoras_empresa()` da a las alumnas solo el nombre de las
  profesoras. El código funciona antes y después de aplicarla. Vuelta atrás incluida.
- **Script de la Empresa 2** (`scripts/empresa_demo.py crear|borrar`): Pilates Centro (PIL) con administradora y
  profesora, otra profesora, 5 alumnas `@demo.invalid` y alumna@prueba.invalid compartida; tipos, horarios, clases,
  reservas y asistencias; sin facturas; Mercado Pago apagado. **No se corrió en Supabase** (necesita autorización y los
  nombres definitivos).
- **Pruebas**: app 216 (28 nuevas de multiempresa); base local: multiempresa 1 39/39, 2 52/52, 3 57/57, **4 37/37**,
  **script de la Empresa 2 19/19** (borrar deja todo idéntico), facturación 180/180, Mercado Pago 73/73, restricción
  29/29, planificación 33/33, vencimiento 48/48. Revisión visual en celular con datos simulados (selector, empresas, lista
  de la profesora, menú de cuenta) y del PDF con profesora.
- Siguiente (jueves 5/11 o antes): respaldo de producción y PR de multiempresa → **autorización ①** para aplicar las
  migraciones 1–3, merge y migración 4.

