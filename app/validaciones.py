import re
from datetime import date
from typing import Optional

_LETRA = r"[^\W\d_]"
_NOMBRE = re.compile(rf"^{_LETRA}+(?:[ '’\-]{_LETRA}+)*$")
_MAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TELEFONO = re.compile(r"^09\d{7}$")
LARGO_MAXIMO_NOMBRE = 60


def nombre(valor: str, etiqueta: str) -> Optional[str]:
    if not valor:
        return f"{etiqueta} es obligatorio."
    if len(valor) > LARGO_MAXIMO_NOMBRE:
        return f"{etiqueta} es demasiado largo (máximo {LARGO_MAXIMO_NOMBRE} caracteres)."
    if not _NOMBRE.match(valor):
        return f"{etiqueta} solo puede tener letras: sin números ni símbolos."
    return None


def mail(valor: str) -> Optional[str]:
    if not valor:
        return "El mail es obligatorio."
    if not _MAIL.match(valor):
        return "El mail debe tener un @ y un formato válido (por ejemplo nombre@correo.com)."
    return None


def telefono(valor: str) -> tuple[str, Optional[str]]:
    """Devuelve (telefono_normalizado, error). Es opcional: vacío es válido."""
    limpio = re.sub(r"[ \-]", "", valor)
    if not limpio:
        return "", None
    if not _TELEFONO.match(limpio):
        return limpio, "El teléfono debe tener el formato 09xxxxxxx: 9 números, empezando con 09."
    return limpio, None


_PESOS_CEDULA = (2, 9, 8, 7, 6, 3, 4)


def cedula_valida(digitos: str) -> bool:
    """Cédula uruguaya: 7 u 8 dígitos, el último es el verificador (igual que _cedula_valida en la base)."""
    if not re.fullmatch(r"\d{7,8}", digitos):
        return False
    numero = digitos[:-1].zfill(7)
    suma = sum(int(d) * p for d, p in zip(numero, _PESOS_CEDULA))
    return (10 - suma % 10) % 10 == int(digitos[-1])


def cedula(valor: str) -> tuple[str, Optional[str]]:
    """Devuelve (cédula solo con dígitos, error). Es opcional: vacío es válido."""
    limpio = re.sub(r"[\s.\-]", "", valor or "")
    if not limpio:
        return "", None
    if not re.fullmatch(r"\d{7,8}", limpio):
        return limpio, "La cédula debe tener 7 u 8 números (por ejemplo 1.234.567-2)."
    if not cedula_valida(limpio):
        return limpio, "La cédula no es válida: revisá el número y el dígito verificador."
    return limpio, None


def formatear_cedula(digitos: Optional[str]) -> str:
    """'12345672' -> '1.234.567-2'. Vacío -> ''."""
    if not digitos:
        return ""
    cuerpo = f"{int(digitos[:-1]):,}".replace(",", ".")
    return f"{cuerpo}-{digitos[-1]}"


def _fecha(texto: str) -> Optional[date]:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def fecha_nacimiento(texto: str, hoy: date) -> Optional[str]:
    if not texto:
        return None
    f = _fecha(texto)
    if f is None:
        return "La fecha de nacimiento no es válida."
    if f >= hoy:
        return "La fecha de nacimiento debe ser anterior a hoy."
    return None


def vencimiento_carne(texto: str, hoy: date) -> Optional[str]:
    if not texto:
        return None
    f = _fecha(texto)
    if f is None:
        return "La fecha de vencimiento del carné de salud no es válida."
    if f <= hoy:
        return "La fecha de vencimiento del carné de salud debe ser posterior a hoy."
    return None


def validar_alumna(datos: dict, hoy: date) -> tuple[dict, list[str]]:
    """Devuelve (datos normalizados, lista de errores). Informa todos los errores juntos."""
    limpios = {k: (v or "").strip() for k, v in datos.items()}
    limpios["mail"] = limpios.get("mail", "").lower()
    limpios["telefono"], error_tel = telefono(limpios.get("telefono", ""))
    limpios["cedula"], error_ced = cedula(limpios.get("cedula", ""))
    errores = [
        e for e in (
            nombre(limpios.get("nombre", ""), "El nombre"),
            nombre(limpios.get("apellido", ""), "El apellido"),
            mail(limpios["mail"]),
            error_tel,
            error_ced,
            fecha_nacimiento(limpios.get("fecha_nacimiento", ""), hoy),
            vencimiento_carne(limpios.get("fecha_vencimiento_carne_salud", ""), hoy),
        ) if e
    ]
    return limpios, errores
