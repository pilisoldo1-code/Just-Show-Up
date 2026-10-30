# Cronograma hasta la defensa (17/11/2026) — registro diario

Cronograma aprobado por Julieta el 30/10. «M» = migración en Supabase (con su autorización explícita);
«Merge» = integrar un PR (lo autoriza ella). Ninguna autorización se da por aprobada.

| Fecha | Plan | Estado |
|---|---|---|
| Vie 30/10 | Multiempresa 1: diseño técnico; empresas, membresías, empresa activa, `id_empresa` en todas las tablas, datos actuales a la Empresa 1 | **Hecho** |
| Sáb 31/10 | Multiempresa 2: políticas por empresa, protección general, cierre de `anon`, contador por empresa | |
| Dom 1/11 | Multiempresa 3: funciones por rol y por empresa, factura con profesora, Mercado Pago solo Empresa 1 | |
| Lun 2/11 | Multiempresa 4 (servidor): empresa activa, selector, rutas por rol. En paralelo: Estadísticas y prototipo de Videos | |
| Mar 3/11 | Multiempresa 5 (pantallas): altas, profesora asignada, alumnas sin cédula para profesoras | |
| Mié 4/11 | Multiempresa 6: panel de superadministradora, script de la Empresa 2, todas las pruebas | |
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
