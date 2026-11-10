# Guía de demostración — Just Show Up (defensa del 17/11/2026)

> Recorrido técnico por rol, sobre **theta** (https://just-show-up-theta.vercel.app). No es el speech: es la lista de qué
> mostrar y qué debería verse. **Las contraseñas no están acá**: están en `.env.cuentas-prueba` (solo en la computadora
> de Julieta). Todas las cuentas son de demostración (dominios `.invalid`); los datos son ficticios.

## Antes de empezar (el día anterior y una hora antes)

- Entrar a theta (Supabase Free se pausa tras ~7 días sin uso). `/api/health` tiene que responder.
- Octubre de Just Show Up: 7 facturas confirmadas (Sofía ve la suya, JSU-2026-10-0009, «Vencida» desde el 11/11) y la
  de **Marta en borrador para confirmarla en vivo** (ver `docs/PROCEDIMIENTOS_DEFENSA.md` §6). Noviembre no tiene
  asistencias pendientes al 10/11; las clases que terminen después sí (se marcan en la clase o en «Clases terminadas»).
- En Estadísticas, «Últimos 3 meses» muestra más datos que «Este mes».
- Ojo: abrir Asistencias, la grilla de una semana nueva o Planificación con una fecha **genera clases** (es lo normal).
- Mercado Pago: seguir `GUIA_DEFENSA_MERCADO_PAGO.md` (la factura de demostración aparece «Vencida», se paga igual).

## Cuentas

| Rol | Lugar | Cuenta | Quién es |
|---|---|---|---|
| Superadministradora | — (`/admin/login`) | `superadmin@prueba.invalid` | Administración general |
| Administradora | Just Show Up | `profesora@prueba.invalid` | Laura Profesora (también da las clases de JSU) |
| Administradora | Pilates Centro | `admin.pilates@demo.invalid` | Lucía Bentancor |
| Profesora | Pilates Centro | `profe.pilates@demo.invalid` | Carolina Méndez |
| Alumna | Just Show Up | `sofia@demo.invalid` | Sofía Pereira |
| Alumna en las dos empresas | Just Show Up y Pilates Centro | `alumna@prueba.invalid` | Marta Gómez (también la de Mercado Pago) |
| Alumnas de Pilates | Pilates Centro | `rocio.pilates@demo.invalid`, `ines.pilates@demo.invalid` | Rocío (factura pagada), Inés (ausencias al 25 %) |

## 1. Superadministradora
| Pantalla | Acción | Qué se ve |
|---|---|---|
| `/admin/login` | Entrar con mail y contraseña (sin lugar) | Va a **Empresas** |
| Empresas | Mirar las tarjetas | Just Show Up y Pilates Centro con cantidades de alumnas, profesoras y administradoras |
| Gestionar empresa (Pilates) | Abrir la ficha | Administradoras con sus acciones (⋮), cobro del 25 % vigente desde 01/10/2026 |
| (Opcional) Asignar administradora | Mostrar el formulario sin enviarlo | Explica que una cuenta nueva recibe una invitación |

## 2. Administradora (Just Show Up)
| Pantalla | Acción | Qué se ve |
|---|---|---|
| `/login` | «Just Show Up» + mail + contraseña | Panel con 3 indicadores y aviso de asistencias pendientes |
| Horarios / Tipos de clase | Recorrer | Horarios semanales, tipos con precio, duración y cupo |
| Alumnas → una ficha | Abrir | Datos, cédula, estado, «Enviar correo para cambiar la contraseña» |
| Alumnas → Nueva alumna | Mostrar (sin crear) | «Le enviamos un correo para que configure su propia contraseña» |
| Facturación | Elegir octubre | 7 confirmadas y la de Marta en borrador → abrirla → **Confirmar** (sale JSU-2026-10-0010); detalle, 25 % y PDF |
| Estadísticas | «Últimos 3 meses» | Asistencia general, gráficos, top de asistencias y faltas, por profesora |
| Videos | Bajar hasta el final | «Estado y errores de las grabaciones» |

## 3. Profesora (Pilates Centro, Carolina)
| Pantalla | Acción | Qué se ve |
|---|---|---|
| `/login` | «Pilates Centro» + mail + contraseña | **Mis horarios** (solo los suyos) |
| Asistencias | Abrir una clase de hoy | Presente/Ausente solo mientras dura la clase |
| Estadísticas | Abrir | Solo sus clases |
| Videos | Mostrar el formulario | Nombre, tipo, descripción, ID de Zoom, «Empezar a grabar» y los pasos |

## 4. Alumna (Just Show Up, Sofía)
| Pantalla | Acción | Qué se ve |
|---|---|---|
| Reservar clases | Semana actual | Días terminados atenuados con «Finalizado»; los demás con «Ver horarios ›» |
| Un día futuro | Reservar clase | Pasa a «Reservada», con hora límite para cancelar |
| Mis inscripciones / Mi historial | Recorrer | Próximas clases; historial por mes con Presente / Ausente / 25 % |
| Mis facturas | Abrir | JSU-2026-10-0009 con detalle (presentes al 100 %, ausencias al 25 %) y PDF |
| Mi perfil | Abrir | Datos de solo lectura y cambio de contraseña |

## 5. Multiempresa
- Entrar con **Marta** (`alumna@prueba.invalid`) en «Just Show Up» y después en «Pilates Centro»: misma contraseña,
  grilla, facturas y videos distintos en cada lugar.
- Escribir mal el lugar: el mensaje es genérico (no revela si el lugar existe).

## 6. Reservas y lista de espera
- En Pilates Centro hay una clase completa con lista de espera (escenario de demostración): una alumna ve «Clase
  completa» y «Anotarme en lista de espera»; la posición aparece en Mis inscripciones.
- Si una alumna con reserva cancela (hasta 1 h antes), la primera de la lista ve «Se liberó un lugar para vos» con
  «Confirmar lugar» / «No me interesa» (30 minutos).

## 7. Asistencias
- Profesora: marcar durante la clase. Administradora: Asistencias → «Clases terminadas» → corregir con motivo; el
  historial guarda quién, cuándo y por qué.

## 8. Facturación
- Administradora de Pilates (Lucía): octubre confirmado — PIL-2026-10-0006 (reemisión de la 0004 de Inés, con ausencias
  al 25 %), PIL-2026-10-0002 pagada (Rocío). Mostrar historial, PDF y «Vencida — deuda pendiente».
- Alumna (Inés o Rocío): Mis facturas, detalle con «Inasistencia · 25 %» y PDF.

## 9. Mercado Pago de prueba
- Seguir `GUIA_DEFENSA_MERCADO_PAGO.md`: Marta → Mis facturas → JSU-2026-09-0001 ($800) → «Pagar con Mercado Pago» →
  compradora de prueba → aprobado → la factura queda pagada; la administradora ve el pago con su número de operación.
  Para repetir: **anular el pago** (nunca «Anular y reemitir» esa factura).

## 10. Videos On Demand
- Si se hizo la prueba completa (ver `docs/PROCEDIMIENTOS_DEFENSA.md` §2) y el video sigue dentro de sus 24 h: mostrarlo
  como alumna (reproducción, tiempo restante). Si no, mostrar la pantalla «Empezar a grabar», el estado de las
  grabaciones y explicar el recorrido con el diagrama de `docs/ARQUITECTURA.md` §10.
