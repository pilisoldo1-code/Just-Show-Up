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

### Vie 30/10 (noche, segunda parte) — ajustes pedidos por Julieta antes de producción
- **Inicio de sesión** con «Nombre del lugar», «Correo electrónico» y «Contraseña» + «Ingresar». Sin selector ni lista
  de empresas (se quitaron `/empresa/elegir` y «Cambiar de empresa»; la sesión ya no conoce las otras empresas de la
  cuenta). Migración **3b** (`20261031000350_multiempresa_3b_acceso.sql`, se aplica con las 1–3 antes del merge): clave
  normalizada y **única** del nombre del lugar (sin mayúsculas, tildes, espacios ni signos) y `entrar_a_empresa()`, que
  valida lugar existente y activo + membresía activa con rol. Cualquier falla → el mismo mensaje y se revoca la sesión.
  La empresa de cada sesión va en una cookie que la base revalida en cada pedido.
- **Superadministradora**: acceso propio `/admin/login` (sin lugar); el alta de empresa exige la administradora
  inicial; nombres de lugar repetidos rechazados; no hay forma de borrar empresas. Script `scripts/crear_superadmin.py`.
- **Redirección por rol**: administradora → `/administracion` (panel nuevo: resumen y accesos); profesora →
  sus horarios; alumna → clases. Roles combinados: «Mis clases →» desde el panel y «Panel de profesora/administración →»
  desde lo de alumna, sin cerrar sesión. La profesora no administradora ve solo sus horarios, clases, días y
  planificación; la administradora elige la profesora de cada horario nuevo y la ve en la lista.
- **Error de seguridad encontrado y corregido**: al crear una profesora desde la app, el trigger de compatibilidad
  de la parte 1 la convertía en administradora (copiaba `profiles.rol`). Corregido en la app y en la base.
- Procedimiento de respaldo, despliegue y vuelta atrás + texto del PR: `MULTIEMPRESA_DESPLIEGUE.md`.
- **Pruebas**: app 237; base local multiempresa 1 39/39, 2 52/52, 3 57/57, **3b 48/48**, 4 37/37, Empresa 2 19/19,
  facturación 180/180, Mercado Pago 73/73, restricción 29/29, planificación 33/33, vencimiento 48/48. Revisión visual
  del login, el panel de administración y los menús (celular y escritorio) con datos simulados.


### Vie 30/10 (noche, tercera parte) — decisiones de Julieta: cédula por empresa y sesiones por dispositivo
- **Empresa por sesión** (migración **3c**, antes del merge): la base guarda el lugar de cada sesión por el
  `session_id` firmado del token (`sesion_empresa`). Una persona puede estar en Just Show Up en la computadora y en
  Pilates Centro en el celular sin que una sesión cambie la otra. Sin cookie de empresa; cerrar sesión olvida el lugar
  solo de ese dispositivo.
- **Cédula por empresa** (en la migración 4): `membresia.cedula`, única solo dentro de la empresa; las cédulas
  actuales pasan a la Empresa 1. El alta y «Cargar cédula» ya no comparan con otras empresas (ningún aviso revela que
  alguien está en otro lugar). La factura copia la cédula de la empresa que factura.
- **Auditoría de altas de cuentas** (`tests/base_local/test_altas_cuentas_base.py`, 48 casos): rutas reales de la
  app, `crear_cuenta`, scripts (superadministradora, profesora, Empresa 2) y triggers reales; intentos de ganar
  permisos (cambiar rol, superadmin, membresías, metadatos propios). Se encontró otro camino teórico (si alguien
  borrara su marca en los metadatos y algo cambiara `profiles.rol`) y se cerró: la parte 4 elimina la
  compatibilidad con `profiles.rol`; el script de la profesora crea la membresía directo.
- **Respaldo del repositorio**: GitHub sigue inaccesible desde esta red (todas sus direcciones, también SSH). Se
  guardó un `git bundle` verificado en `~/Documents/Just Show Up - respaldos/repositorio/` (solo código, sin `.env`).
- **Pruebas**: app 237; base local: multiempresa 1 39/39, 2 52/52, 3 57/57, 3b 41/41, **3c 27/27**, **4 51/51**,
  Empresa 2 19/19, **altas 48/48**, facturación 180/180, Mercado Pago 73/73, restricción 29/29, planificación 33/33,
  vencimiento 48/48.

### Vie 30/10 (noche, cuarta parte) — pruebas con Supabase local real
- Instalados Colima, Lima y el cliente de Docker (gratis, código abierto, sin permisos de administradora) y un
  Supabase local en una copia aislada. Las 22 migraciones del repo se aplican sin errores en un Supabase real.
- **Errores encontrados por las pruebas reales y corregidos**: (1) **grave**: las claves foráneas compuestas de la
  migración 2 duplicaban relaciones y las consultas anidadas de la API fallaban (PGRST201): theta se habría roto al
  aplicar la migración 2 → reemplazadas por el trigger `_validar_empresa_madre` (misma validación, la empresa de una
  fila ya no se puede cambiar); (2) al sumar como alumna a una profesora de la misma empresa se le quitaba el rol de
  profesora → los roles ahora se suman; (3) error en el trigger nuevo (CASE en PL/pgSQL) → IF por tabla.
- Pruebas reales (`tests/supabase_local/`): theta actual con migraciones 1–3c **32/32**; código nuevo con 1–3c
  **33/33** y con todas **33/33**; altas, inicio de sesión, roles, dos dispositivos, cédula e intentos de elevar
  permisos con el servicio de cuentas real **70/70**. Base local: todas las suites OK; app 241.
- Vistas previas de Vercel aisladas: `vercel.json` (solo `main` despliega) + la app no atiende en `preview`.
- `MULTIEMPRESA_DESPLIEGUE.md` revisado (ensayo, respaldo, verificaciones de solo lectura, compatibilidad,
  cuándo no desplegar, vuelta atrás con advertencias, vistas previas viejas).

### Sáb 31/10 — respaldo de producción (autorizado, solo lectura)
- El commit `257d146` está en GitHub y es la cabeza del PR #27 (texto actualizado). Ya no hay verificaciones de
  Vercel en el PR porque `vercel.json` apaga las vistas previas de la rama (el push nuevo no generó ninguna).
- Vistas previas viejas de la rama `multiempresa`: 6 (commits `e802070` a `00bca68`), protegidas por Vercel
  Authentication (sin sesión redirigen a vercel.com). Pilar puede borrarlas desde el panel (gratis).
- Respaldo tomado a las 11:03 con lecturas: 16 tablas + 16 cuentas (sin contraseñas), cantidades iguales a
  producción, estructura idéntica a la del 31/10 (11 categorías), 16 migraciones aplicadas (ninguna de
  multiempresa), copia del repositorio. Las 8 suites de multiempresa pasan sobre esta copia.
- **Restauración ensayada** en un Supabase local real con `scripts/restaurar_datos.py` (nuevo): la API no deja
  conservar ids (columnas de identidad), así que los datos se restauran con un SQL generado; desde una base vacía
  todas las filas quedan iguales, el contador de facturas también, repetirla no duplica y theta funciona (32/32).

### Sáb 31/10 — ETAPA B aplicada en producción (autorizada) ✅
- 13:47 sin actividad en theta (0 sesiones, 0 tokens renovados, 0 reservas en la última hora); respaldo de las 11:15.
- Aplicadas con el conector de Supabase, una por vez: `multiempresa_1_estructura`, `multiempresa_2_permisos`,
  `multiempresa_3_funciones`, `multiempresa_3b_acceso`, `multiempresa_3c_sesiones` (21 migraciones en total).
- Después de **cada** una: huellas de estructura **idénticas a las esperadas** (11 categorías) y
  `scripts/verificar_despliegue.py` **TODO OK** (16 tablas intactas fila por fila, todo en la Empresa 1, 16
  membresías según el rol, profesora en clases y facturas, consultas anidadas de la API sin errores).
- theta (código actual `c1608d5`) funciona: login de profesora y alumna, 8 pantallas de solo lectura en 200, la
  alumna ve su factura de demostración. Al final: 146 reservas, 71 asistencias, 1 factura (JSU-2026-09-0001,
  confirmada/pendiente, $800), 0 pagos, contador sin usar; Mercado Pago igual (prueba, solo
  `alumna@prueba.invalid`, solo JSU); acceso sin sesión cerrado (amber deja de funcionar: aceptado).
- Siguiente: etapa C (merge del PR #27) con autorización.

### Sáb 31/10 — ETAPAS C y D en producción (autorizadas) ✅ — multiempresa desplegada
- **C (13:58)**: merge del PR #27 (`1a7603d`, merge común) → Vercel desplegó theta en menos de un minuto. theta con el
  código nuevo: 23/23 (login con «Just Show Up», panel de administración, rutas por rol, mensaje genérico ante lugar
  inexistente o contraseña incorrecta, acceso de superadministradora protegido). Datos todo OK.
- **D (14:07)**: migración `multiempresa_4_datos_personales` (22 migraciones). Huellas idénticas a las esperadas,
  datos 40/40 OK. Seguridad con tokens reales en producción 18/18: una sesión sin lugar no ve nada, dos dispositivos
  de la misma alumna independientes, cédulas ilegibles directo, intentos de elevar permisos rechazados, sin sesión
  no se lee nada. theta 13/13. Mercado Pago: la credencial del Vault es de una cuenta de PRUEBA (`test_user`, MLU) y
  el código exige vendedora de prueba, compradora TESTUSER y solo `alumna@prueba.invalid`.
- Datos finales: 146 reservas, 71 asistencias, 1 factura (JSU-2026-09-0001), 0 pagos, contador sin usar.
- Falta (con autorización aparte): superadministradora, Pilates Centro, revisión de las vistas previas viejas (Pilar).
- Rama `panel-admin-simple` (sin desplegar): inicio del panel con 3 indicadores.

