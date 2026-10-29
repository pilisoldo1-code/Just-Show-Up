# Guía de la demostración de Mercado Pago — defensa del 17/11/2026

Todo es **de prueba y con dinero ficticio**. Vendedora y compradora son cuentas de prueba de
Mercado Pago; nunca se usa una cuenta personal, una tarjeta real ni dinero real. Esta guía no
contiene contraseñas ni credenciales: las de las cuentas de prueba están en la nota privada de
Julieta y en `.env.cuentas-prueba` (fuera de git).

- Sitio: **https://just-show-up-theta.vercel.app**
- Alumna ficticia: **`alumna@prueba.invalid`** · Profesora ficticia: **`profesora@prueba.invalid`**
- Factura de demostración: **JSU-2026-09-0001 · septiembre 2026 · $800 (ficticios)** · una clase
  «Gimnasia (demostración)» del lunes 7/9 a las 10:00. El tipo de clase y el horario están
  **inactivos**: no aparecen en la grilla ni se pueden reservar.
- Desde el 1/11 `alumna@prueba.invalid` también tiene su factura normal de octubre: **para la
  demostración usar siempre la de septiembre**.

## 1. El circuito completo (el día de la defensa)

1. Entrar al sitio e iniciar sesión como `alumna@prueba.invalid`.
2. Ir a **Mis facturas**.
3. Buscar la factura **septiembre 2026 · JSU-2026-09-0001 · $800**, en estado *Pendiente de pago*.
4. Presionar **«Pagar con Mercado Pago»** (botón blanco con el logo). Se abre el checkout de
   Mercado Pago con el título «Factura JSU-2026-09-0001 – Just Show Up (PRUEBA, dinero ficticio)».
   El link vence a los 30 minutos: si se demora, volver a Mis facturas y presionar el botón otra vez.
5. En Mercado Pago, **iniciar sesión con la compradora de prueba** (mejor en una ventana de
   incógnito donde no haya ninguna cuenta personal abierta). Si pide un código por mail, ver §4.
6. Pagar con el **saldo ficticio** de la compradora (o con la tarjeta de prueba, ver §4).
7. Mercado Pago vuelve solo a Just Show Up y muestra «¡Pago aprobado!». La factura pasa a
   **Pagada**. (Si se cerró la ventana antes de volver, abrir Mis facturas: el pago se busca solo.)
8. Cerrar sesión e iniciar sesión como `profesora@prueba.invalid` → **Facturación** → mes
   **septiembre 2026** → abrir la factura. Se ve el pago: medio «Mercado Pago (prueba)», importe
   $800, **n.º de operación de Mercado Pago** y que se registró automáticamente después de
   verificarlo con Mercado Pago. Más abajo, los intentos de pago.
9. **Anular el pago**: en esa misma pantalla, «Anular pago», escribir un motivo (ej. «Demostración
   de la defensa, dinero ficticio») y confirmar. La factura vuelve a **Pendiente de pago** y el
   pago anulado queda en el historial.
10. Explicar: **anular el pago en Just Show Up no es una devolución en Mercado Pago**. Solo cambia
    el registro interno (por ejemplo, si la profesora se equivocó al registrar un cobro). El dinero
    ficticio queda en la vendedora de prueba; en un sistema real, la devolución se haría aparte
    desde Mercado Pago.

> **Importante:** anular solo **el pago**. Nunca usar «Anular y reemitir» sobre la factura de
> demostración (cambiaría su número).

## 2. Unos días antes (y el día anterior)

- Entrar al sitio al menos una vez por semana (Supabase gratuito se pausa tras ~7 días sin uso; si
  pasa, todo el sitio da error hasta reactivarlo desde el panel de Supabase con *Restore*).
- Iniciar sesión como `alumna@prueba.invalid` → Mis facturas: la de septiembre está **Pendiente de
  pago** y se ve el botón de Mercado Pago. Si figura *Pagada* (por un ensayo), anular el pago como
  en el paso 9. **No hace falta pagar para comprobarlo.**
- Iniciar sesión en Mercado Pago con la compradora de prueba (incógnito) y mirar que tenga saldo
  ficticio suficiente ($800 o más).
- Si el botón no aparece, ver §4.

## 3. Repetir la demostración (cuantas veces haga falta)

Pagar → mostrar *Pagada* y la vista de la profesora → **anular el pago** → la factura vuelve a
*Pendiente de pago* y se puede pagar otra vez. Cada vuelta suma un pago anulado y sus intentos al
historial de **esa** factura; no toca ninguna otra factura ni la numeración. Cada pago descuenta
$800 ficticios del saldo de la compradora (ver §4 para recargarlo).

## 4. Si algo falla

| Problema | Qué hacer |
|---|---|
| La compradora de prueba no puede iniciar sesión | En el panel de desarrolladores de Mercado Pago → *Tus integraciones* → la aplicación → *Cuentas de prueba* → menú ⋮ de la compradora → generar una contraseña nueva (gratis). Si pide un código de verificación de 6 dígitos, la documentación de Mercado Pago indica tomarlo de ese mismo panel de cuentas de prueba. |
| La compradora se quedó sin saldo ficticio | Mismo panel → menú ⋮ de la compradora → agregar dinero ficticio. O pagar con la **tarjeta de prueba**: Mastercard 5031 7557 3453 0604, venc. 11/30, CVV 123, titular **APRO** (aprobado), documento **Otro 123456789**. (Titular **OTHE** = pago rechazado, sirve para mostrar un rechazo.) |
| El checkout pide un mail en vez de iniciar sesión | La compradora no inició sesión: cerrar, iniciar sesión primero con la compradora de prueba y volver a presionar el botón. |
| No aparece el botón | Revisar: sesión de `alumna@prueba.invalid` (no otra), factura de septiembre *pendiente* (no pagada), Mercado Pago encendido (§5), sitio funcionando (Supabase no pausado). |
| Volví de Mercado Pago y dice «Estamos verificando…» | Esperar unos segundos y abrir Mis facturas de nuevo: el pago se consulta solo a Mercado Pago. |
| Necesito apagar Mercado Pago ya | §5. |

## 5. Apagar o encender Mercado Pago (sin tocar código)

En el panel de Supabase → proyecto **Just Show Up** → **SQL Editor** → nueva consulta:

```sql
select public.mp_cambiar_habilitado(false);  -- apagar: el botón desaparece y no se puede iniciar ningún pago
```

```sql
select public.mp_cambiar_habilitado(true);   -- volver a encender
```

Para ver el estado: `select habilitado from public.mp_configuracion;`. El cambio tarda hasta 30
segundos en verse en el sitio.

## 6. Qué controla Just Show Up (y qué no)

- El botón y el inicio del pago solo funcionan para alumnas con mail ficticio (`@demo.invalid`,
  `@prueba.invalid`), con su propia factura confirmada, publicada y pendiente; lo valida la
  pantalla, el servidor **y** la base de datos.
- El servidor solo usa la credencial si Mercado Pago confirma que es de una **vendedora de prueba**
  de Uruguay (si no, Mercado Pago queda apagado solo), y solo registra pagos cobrados por esa
  vendedora y pagados por una cuenta de prueba (`TESTUSER…`), por el importe exacto de la factura.
  Nunca confía en lo que dice la dirección de vuelta: siempre consulta el pago a Mercado Pago.
- **Depende de Mercado Pago (no se puede controlar desde Just Show Up):** quién inicia sesión en
  la pantalla de pago de Mercado Pago. Su documentación indica que vendedora y compradora deben ser
  cuentas de prueba y no mezclarse con cuentas reales; no se comprobó con una cuenta real (no se
  debe hacer). Por eso: en la demostración usar **solo** la compradora de prueba, en incógnito.
- Costos: ninguno. Mercado Pago de prueba, Supabase Free, Vercel Hobby y GitHub Free son gratuitos;
  la comisión que muestra Mercado Pago es simulada y la paga la vendedora de prueba con dinero ficticio.

## 7. Recrear la factura de demostración (solo si hiciera falta)

`scripts/factura_demo_mp.py` la crea y la borra (solo lo anotado en su manifiesto, que está en
`~/Documents/Just Show Up - respaldos/registro_demo_mp.json`, fuera de git). La numeración depende
del contador de facturas: leer las instrucciones al principio del script y **no reiniciar el
contador si ya existen facturas reales**. Pedir ayuda antes de usarlo.
