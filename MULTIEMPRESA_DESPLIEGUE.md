# Multiempresa — procedimiento de respaldo, despliegue y vuelta atrás

**Estado: para revisión de Julieta. Nada de esto está autorizado todavía.** Cada paso marcado con
**[autorización]** se hace solo con su OK explícito, en el momento. Todo es gratuito: no hay servicios,
extensiones ni planes nuevos (Supabase Free, Vercel Hobby, GitHub Free). Mercado Pago sigue en modo de
prueba y solo en la Empresa 1. Las pruebas que escriben datos se hacen solo en local.

## Qué se despliega

| Pieza | Archivo | Cuándo |
|---|---|---|
| Migración 1: empresas y membresías | `supabase/migrations/20261031000100_multiempresa_1_estructura.sql` | Antes del merge |
| Migración 2: permisos por empresa, cierre de `anon` | `…000200_multiempresa_2_permisos.sql` | Antes del merge |
| Migración 3: funciones por empresa y rol | `…000300_multiempresa_3_funciones.sql` | Antes del merge |
| Migración 3b: acceso por nombre del lugar | `…000350_multiempresa_3b_acceso.sql` | Antes del merge |
| Migración 3c: empresa por sesión (dispositivo) | `…000360_multiempresa_3c_sesiones.sql` | Antes del merge |
| Código (PR #27, rama `multiempresa`) | — | Merge a `main` → theta |
| Migración 4: datos personales, cédula por empresa, empresa solo por sesión | `…000400_multiempresa_4_datos_personales.sql` | Enseguida después del merge |
| Cuenta de superadministradora | `scripts/crear_superadmin.py` | Después de la migración 4 |
| Empresa 2 «Pilates Centro» (PIL) de demostración | `scripts/empresa_demo.py crear` | Al final, con autorización aparte |

### Compatibilidad entre versiones (probada en un Supabase local real, 30/10)

| Base | Código | Resultado |
|---|---|---|
| Migraciones 1–3c | **theta actual** (`main`) | 32/32: login, todas las pantallas, crear horarios, generar clases, reservar y cancelar |
| Migraciones 1–3c | código nuevo (los minutos entre el merge y la migración 4) | 33/33 |
| Migraciones 1–4 | código nuevo | 33/33 pantallas + 70/70 altas e inicio de sesión reales |
| Migraciones 1–4 | theta actual | **No compatible**: por eso la 4 va después del merge y se deshace antes de volver al código anterior |

**Efectos visibles esperados**
- amber deja de funcionar (se cierra el acceso sin sesión): aceptado.
- Al hacer el merge, cada persona tiene que volver a iniciar sesión **una vez**, ahora escribiendo
  «Just Show Up» como nombre del lugar. Las cuentas y contraseñas no cambian.
- `profesora@prueba.invalid` queda como administradora y profesora de Just Show Up.

**Cuándo NO desplegar**: el último día de un mes (abrir Facturación ese día arma los borradores del mes,
que es lo previsto pero complica comparar datos) ni cerca de la defensa. Conviene un momento sin clases en
curso; todo el procedimiento lleva unos 45 minutos.

## Paso 0 — Ensayo y respaldo — **HECHO el 31/10 a las 11:03** (autorizado; solo lectura)

Respaldo en `~/Documents/Just Show Up - respaldos/2026-10-31 11h antes de aplicar multiempresa/` (ver su `LEAME.md`):
cantidades iguales a producción, estructura idéntica a la del respaldo con el que se probó todo, todas las pruebas
de multiempresa pasan sobre esta copia y la restauración se ensayó en un Supabase local real (todas las filas
iguales, contador de facturas igual, theta funciona sobre los datos restaurados). Si pasan días antes del paso 1,
repetir el respaldo el mismo día de aplicar las migraciones.

Procedimiento:

1. **Ensayo en Supabase local** (no toca producción): con Colima y `supabase start` en la copia aislada
   `~/.local/share/jsu-supabase-local`, correr `tests/supabase_local/recorrido_pantallas.py` con el código de
   `main` (migraciones 1–3c) y con el nuevo, y `tests/supabase_local/prueba_altas_reales.py` (todas). Todo OK.
2. **Respaldo de producción (solo lectura)**, en una carpeta nueva
   `~/Documents/Just Show Up - respaldos/AAAA-MM-DD antes de aplicar multiempresa/`:
   - `python "<respaldo del 31/10>/descargar_datos.py" "<carpeta nueva>"`: todas las tablas, las cuentas (sin
     contraseñas: Supabase no las expone) y los archivos del bucket `tipos-clase`;
   - con consultas de solo lectura: huellas de estructura (`huellas_estructura.sql`), definiciones de todas
     las funciones, políticas y permisos de `public`, y la lista de migraciones aplicadas;
   - `git bundle` del repositorio.
3. Sobre ese respaldo nuevo, correr todas las pruebas de `tests/base_local/` (copia de producción local): todo OK.

### Etapa A — HECHA el 31/10 a las 11:15 (autorizada; solo lectura)
Respaldo `2026-10-31 11h15 etapa A antes de migraciones`: igual fila por fila al de las 11:03 y estructura igual a
producción; sin sesiones activas en las 2 horas previas. Huellas esperadas después de cada migración calculadas
(`huellas_esperadas.json`, con `tests/base_local/huellas_esperadas.py`). Ensayo general en Supabase local real con
estos datos: huellas iguales a las esperadas tras cada migración, `scripts/verificar_despliegue.py` todo OK, theta
32/32, y vuelta atrás completa 3c → 1 con estructura igual a producción y datos iguales al respaldo.

## Paso 1 — Migraciones 1, 2, 3, 3b y 3c [autorización]

**HECHO el 31/10 entre las 13:47 y las 13:55** (etapa B autorizada): huellas iguales a las esperadas después de
cada migración, verificación de datos todo OK y theta funcionando. Ver `AVANCE_CRONOGRAMA.md`.

Nombres con los que se aplican (las vueltas atrás los borran del registro por nombre): `multiempresa_1_estructura`,
`multiempresa_2_permisos`, `multiempresa_3_funciones`, `multiempresa_3b_acceso`, `multiempresa_3c_sesiones`.
Después de cada una: huellas de estructura (lectura) iguales a `huellas_esperadas.json`; si no, se frena y se
deshace. Al final: `python scripts/verificar_despliegue.py "<respaldo etapa A>"` (solo lectura) y las pantallas de theta.

1. Aplicarlas en ese orden (una por vez con el conector de Supabase), comprobando que cada una termina bien.
2. Verificación de **solo lectura**:
   - estructura igual a la de la base local con las mismas migraciones (huellas);
   - datos: cada fila igual al respaldo (más las columnas nuevas); una empresa «Just Show Up»/JSU con Mercado
     Pago habilitado; una membresía por persona según su rol; contador de facturas = el global;
   - la API sigue resolviendo las consultas anidadas (sin errores PGRST201).
3. theta (código viejo) sigue funcionando: entrar con las cuentas de prueba y abrir **solo pantallas que no
   escriben**: Horarios, Alumnas, Estadísticas, Mi historial, Mis facturas, Mi perfil. No abrir la grilla, el
   día ni Mis inscripciones (generan clases). No confirmar, anular ni reemitir facturas.
4. Si algo falla: vuelta atrás de 3c → 3b → 3 → 2 → 1 y theta queda como antes (el código no cambió).

## Paso 2 — Merge del PR #27 [autorización]

1. Merge común de `multiempresa` a `main` → theta despliega solo (Vercel, plan gratuito).
2. Comprobaciones de solo lectura en theta: «Just Show Up» + cuenta de prueba de alumna y de profesora;
   mensaje genérico con un lugar inventado; la profesora entra al «Panel de administración».

## Paso 3 — Migración 4 [autorización]

Aplicarla enseguida. Verificar (solo lectura): las cédulas actuales quedaron en la Empresa 1; una sesión de
alumna ya no puede leer cédulas directo; Mi perfil y las facturas muestran la cédula a quien corresponde; una
sesión sin lugar elegido no ve nada.

## Paso 4 — Superadministradora y Empresa 2 [autorización aparte]

1. `python scripts/crear_superadmin.py` (Julieta escribe la contraseña en su teclado).
2. Cuando lo autorice: `python scripts/empresa_demo.py crear` (Pilates Centro con sus cuentas `@demo.invalid`,
   clases, reservas y asistencias; Mercado Pago apagado). Se borra con `empresa_demo.py borrar`.

## Vuelta atrás (en orden inverso, cada script es todo o nada)

| Deshacer | Cómo | Ojo |
|---|---|---|
| Empresa 2 | `python scripts/empresa_demo.py borrar` | Probado: deja todo idéntico |
| Migración 4 | `supabase/pendientes/vuelta_atras_multiempresa_4.sql` | Las cédulas de la Empresa 1 vuelven al perfil; las cargadas en otras empresas se pierden |
| Código | Revert del merge en GitHub | **Antes**, deshacer la migración 4 |
| Migración 3c | `…/vuelta_atras_multiempresa_3c.sql` | |
| Migración 3b | `…/vuelta_atras_multiempresa_3b.sql` | |
| Migración 3 | `…/vuelta_atras_multiempresa_3.sql` | Se niega si hay más de una empresa (borrar antes la Empresa 2) |
| Migración 2 | `…/vuelta_atras_multiempresa_2.sql` | |
| Migración 1 | `…/vuelta_atras_multiempresa_1.sql` | |

Todas probadas sobre la copia de producción: la estructura vuelve a quedar idéntica a la de antes de cada
migración. Si además hubiera que recuperar datos: `scripts/restaurar_datos.py "<respaldo>" --confirmo-produccion`
recrea las cuentas que falten (contraseña nueva: «Olvidé mi contraseña») y genera un SQL que Julieta ejecuta en el
editor SQL del panel de Supabase (la API no permite conservar los ids). Puntos sin vuelta atrás exacta: las sesiones abiertas se cierran una vez; las facturas confirmadas
con numeración por empresa conservan su número.

## Vistas previas de Vercel

Las vistas previas (cada rama y PR) usan las mismas variables que theta, o sea, la base de **producción**. Desde
el commit de esta etapa:
- `vercel.json` apaga los despliegues automáticos de todas las ramas menos `main` (gratis, sin tocar la cuenta
  de Vercel);
- y si igual se desplegara una vista previa, la app no atiende pedidos ni se conecta a la base
  (`VERCEL_ENV = preview` → 503).

Las vistas previas **ya creadas** de commits anteriores de la rama siguen existiendo en la cuenta de Vercel
de Pilar. Recomendación: que Pilar las elimine (Deployments → filtrar por la rama `multiempresa` → Delete), o
al menos confirme que tienen activada la protección de vistas previas (Vercel Authentication, gratis). Mientras
tanto, nadie debe usarlas: con la base actual fallan al iniciar sesión y no escriben datos de la app.
