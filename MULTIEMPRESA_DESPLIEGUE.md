# Multiempresa — procedimiento de respaldo, despliegue y vuelta atrás

**Estado: para revisión de Julieta. Nada de esto está autorizado todavía.** Cada paso marcado con
**[autorización]** se hace solo con su OK explícito, en el momento. Todo es gratuito: no hay servicios,
extensiones ni planes nuevos (Supabase Free, Vercel Hobby, GitHub Free). Mercado Pago sigue en modo de
prueba y solo en la Empresa 1.

## Qué se despliega

| Pieza | Archivo | Cuándo |
|---|---|---|
| Migración 1: empresas y membresías | `supabase/migrations/20261031000100_multiempresa_1_estructura.sql` | Antes del merge |
| Migración 2: permisos por empresa, cierre de `anon` | `…000200_multiempresa_2_permisos.sql` | Antes del merge |
| Migración 3: funciones por empresa y rol | `…000300_multiempresa_3_funciones.sql` | Antes del merge |
| Migración 3b: acceso por nombre del lugar | `…000350_multiempresa_3b_acceso.sql` | Antes del merge |
| Código (PR de la rama `multiempresa`) | — | Merge a `main` → theta |
| Migración 4: datos personales protegidos | `…000400_multiempresa_4_datos_personales.sql` | Enseguida después del merge |
| Cuenta de superadministradora | `scripts/crear_superadmin.py` | Después de la migración 4 |
| Empresa 2 de demostración | `scripts/empresa_demo.py crear` (con nombres definitivos) | Al final |

Las migraciones 1, 2, 3 y 3b son compatibles con el código que hoy está en theta (probado con la copia de
producción). La migración 4 necesita el código nuevo (el nuevo funciona antes y después de aplicarla).

**Efectos visibles esperados**
- amber deja de funcionar (se cierra el acceso sin sesión): aceptado.
- Al hacer el merge, todas las personas tienen que volver a iniciar sesión **una vez**, ahora con
  «Nombre del lugar» = **Just Show Up**. Las cuentas y contraseñas no cambian.
- La cuenta `profesora@prueba.invalid` queda como administradora (y profesora) de Just Show Up.

## Paso 0 — Respaldo (solo lectura) [autorización]

1. `python "~/Documents/Just Show Up - respaldos/2026-10-31 antes de multiempresa/descargar_datos.py"` en una
   carpeta nueva `… respaldos/AAAA-MM-DD antes de aplicar multiempresa/` (descarga todas las tablas con la
   clave de administrador, solo lectura).
2. Guardar las huellas de estructura (`huellas_estructura.sql`) y las definiciones actuales de las funciones.
3. Correr de nuevo, sobre ese respaldo, todas las pruebas locales (`tests/base_local/…`, incluidas
   multiempresa 1, 2, 3, 3b, 4 y Empresa 2): tienen que dar todo OK antes de seguir.

## Paso 1 — Migraciones 1, 2, 3 y 3b [autorización]

1. Aplicarlas en ese orden con `apply_migration` (una por vez, comprobando cada una).
2. Verificación de solo lectura: estructura igual a la de la base local; mismos datos que el respaldo
   (fila por fila, con las columnas nuevas); `empresa` = una fila «Just Show Up»/JSU con Mercado Pago
   habilitado; una membresía por persona; contador de facturas de la Empresa 1 igual al global.
3. theta (código viejo) sigue funcionando: comprobaciones de solo lectura con las cuentas de prueba
   (login, grilla, Mis facturas, Facturación). **No** confirmar, anular ni reemitir facturas.

## Paso 2 — Merge del PR [autorización]

1. Merge común de la rama `multiempresa` a `main` → theta despliega solo.
2. Comprobaciones de solo lectura en theta: login con «Just Show Up» de la alumna y de la profesora de
   prueba; mensaje genérico con un lugar inventado; la profesora entra a «Panel de administración».

## Paso 3 — Migración 4 [autorización]

Aplicarla enseguida. Verificar: una sesión de alumna ya no puede leer la cédula directo; Mi perfil y las
facturas siguen mostrando la cédula a quien corresponde.

## Paso 4 — Superadministradora y Empresa 2 [autorización]

1. `python scripts/crear_superadmin.py` (la contraseña se escribe en el teclado de Julieta y se guarda en
   `.env.cuentas-prueba`).
2. Con los nombres definitivos confirmados: `python scripts/empresa_demo.py crear` (crea la Empresa 2, sus
   cuentas `@demo.invalid`, clases, reservas y asistencias; Mercado Pago apagado).
3. Verificar en theta: superadministradora en `/admin/login`; administradora de la Empresa 2; alumna en las
   dos empresas entrando con cada nombre de lugar.

## Vuelta atrás (en orden inverso, cada una todo o nada)

| Deshacer | Cómo |
|---|---|
| Empresa 2 | `python scripts/empresa_demo.py borrar` (deja todo como antes, probado) |
| Migración 4 | `supabase/pendientes/vuelta_atras_multiempresa_4.sql` |
| Código | Revert del merge en GitHub (theta vuelve al código anterior) |
| Migración 3b | `supabase/pendientes/vuelta_atras_multiempresa_3b.sql` |
| Migración 3 | `supabase/pendientes/vuelta_atras_multiempresa_3.sql` (se niega si hay más de una empresa) |
| Migración 2 | `supabase/pendientes/vuelta_atras_multiempresa_2.sql` |
| Migración 1 | `supabase/pendientes/vuelta_atras_multiempresa_1.sql` |

Todas probadas sobre la copia de producción: la estructura vuelve a quedar idéntica a la de antes.
Antes de deshacer el código hay que deshacer la migración 4 (el código viejo lee la cédula directo).

## Texto del PR (borrador)

**Multiempresa: lugares, roles, inicio de sesión por nombre del lugar y superadministradora**

- Empresas y membresías; todos los datos actuales pasan a la Empresa 1 «Just Show Up» (JSU).
- Inicio de sesión con nombre del lugar, correo y contraseña; mensaje único ante cualquier falla; sin
  listas de empresas. Acceso propio de la superadministradora en `/admin/login`.
- Redirección por rol: administradora → panel de administración; profesora → sus horarios; alumna →
  clases. Roles combinados con acceso cruzado («Mis clases →», «Panel de profesora →»).
- Permisos por empresa y rol en la base (políticas, protección general, funciones) y en el servidor.
  Profesoras sin facturación ni datos personales. Cierre del acceso sin sesión.
- Facturas por empresa con numeración propia y profesora en cada línea (pantalla y PDF).
- Panel de la superadministradora: crear (con administradora inicial), editar, desactivar y reactivar
  empresas; ve cantidades y administradoras, ningún dato operativo.
- Mercado Pago sigue solo en modo de prueba, solo Empresa 1 y solo `alumna@prueba.invalid`.
- Migraciones 1, 2, 3, 3b (antes del merge) y 4 (después), con sus vueltas atrás. Procedimiento en
  `MULTIEMPRESA_DESPLIEGUE.md`.
- Pruebas: ver `AVANCE_CRONOGRAMA.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
