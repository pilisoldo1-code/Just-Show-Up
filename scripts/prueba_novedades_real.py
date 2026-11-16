"""Prueba de SOLO LECTURA de Novedades con una cuenta real, contra el servidor LOCAL (rama notificaciones-novedades).

Sirve sobre todo para la alumna: al iniciar sesión en el navegador, la alumna va a «Reservar clases», que genera clases
y procesa listas de espera (escribe en la base). Este script inicia sesión SIN seguir esa redirección, lee solo
`/novedades` y `/novedades/contador` y cierra la sesión. Nunca pide `/`, `/dia/...` ni ninguna otra pantalla.

Lo único que cambia en la base es lo de cualquier inicio de sesión: la sesión de Supabase Auth, el lugar elegido
(`sesion_empresa`, `profiles.empresa_activa`) y su cierre al final. No toca clases, reservas, listas de espera,
asistencias, facturas ni pagos.

La contraseña se escribe en el teclado (no se muestra ni se guarda). Imprime solo los títulos de las novedades y el
contador (pueden incluir nombres de alumnas si la cuenta es administradora).

Uso (con el servidor levantado: .venv/bin/uvicorn app.main:app --port 8000 --env-file .env):
    .venv/bin/python scripts/prueba_novedades_real.py [http://localhost:8000]
"""
import getpass
import html
import re
import sys
from urllib.parse import urlparse

import httpx

LOCALES = {"localhost", "127.0.0.1", "::1"}
PERMITIDAS = {"/login", "/novedades", "/novedades/contador", "/logout"}


def base_local(url: str) -> str:
    """Solo el servidor de esta computadora: nunca theta, amber ni otro sitio."""
    p = urlparse(url)
    if p.scheme != "http" or p.hostname not in LOCALES:
        raise SystemExit(f"Solo se permite un servidor local (http://localhost:PUERTO), no {url!r}.")
    return f"http://{p.netloc}"


def titulos(pagina: str) -> list[tuple[str, str]]:
    """(etiqueta, título) de cada novedad de la pantalla."""
    bloques = re.findall(r'<li class="novedad novedad-\w+">(.*?)</li>\s*(?=<li class="novedad|</ul>)', pagina, re.S)
    salida = []
    for b in bloques:
        etiqueta = re.search(r'<span class="badge[^"]*">([^<]*)</span>', b)
        titulo = re.search(r'<h2 class="novedad-titulo">([^<]*)</h2>', b)
        salida.append((html.unescape(etiqueta.group(1)) if etiqueta else "",
                       html.unescape(titulo.group(1)) if titulo else ""))
    return salida


class Cliente:
    """httpx sin seguir redirecciones; las cookies de sesión se mandan a mano (con dominio «localhost» httpx no las
    manda) y cada pedido se controla contra la lista de rutas permitidas."""

    def __init__(self, base: str, transporte=None):
        self.base, self.cookies = base, {}
        self.http = httpx.Client(base_url=base, follow_redirects=False, timeout=30, transport=transporte)
        self.pedidas: list[str] = []

    def _pedir(self, metodo: str, ruta: str, **kw) -> httpx.Response:
        if ruta not in PERMITIDAS:
            raise RuntimeError(f"Ruta no permitida en esta prueba: {ruta}")
        self.pedidas.append(ruta)
        r = self.http.request(metodo, ruta, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
                              if self.cookies else None, **kw)
        for c in r.headers.get_list("set-cookie"):
            nombre, _, resto = c.partition("=")
            valor = resto.split(";", 1)[0]
            if valor and "max-age=0" not in c.lower():
                self.cookies[nombre.strip()] = valor
            else:
                self.cookies.pop(nombre.strip(), None)
        return r

    def entrar(self, lugar: str, mail: str, contrasena: str) -> bool:
        r = self._pedir("POST", "/login", data={"lugar": lugar, "mail": mail, "contrasena": contrasena})
        return r.status_code == 303 and "sb_acceso" in self.cookies  # la redirección NO se sigue

    def novedades(self) -> tuple[int, str]:
        r = self._pedir("GET", "/novedades")
        return r.status_code, r.text

    def contador(self) -> dict | None:
        r = self._pedir("GET", "/novedades/contador")
        return r.json() if r.status_code == 200 else None

    def salir(self) -> None:
        if self.cookies:
            self._pedir("POST", "/logout")


def main() -> int:
    base = base_local(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
    lugar = input("Nombre del lugar (por ejemplo, Just Show Up o Pilates Centro): ").strip()
    mail = input("Mail de la cuenta: ").strip()
    contrasena = getpass.getpass("Contraseña (no se muestra): ")
    cliente = Cliente(base)
    try:
        if not cliente.entrar(lugar, mail, contrasena):
            print("No se pudo iniciar sesión (lugar, mail o contraseña incorrectos, o el servidor no responde).")
            return 1
        estado, pagina = cliente.novedades()
        if estado != 200:
            print(f"/novedades respondió {estado}: la cuenta no tiene empresa en esta sesión o hubo un error.")
            return 1
        lista = titulos(pagina)
        print(f"\nContador de la campana: {cliente.contador()}")
        print(f"Novedades en pantalla: {len(lista)}")
        for etiqueta, titulo in lista:
            print(f"  [{etiqueta}] {titulo}")
        if "No pudimos revisar todas tus novedades" in pagina:
            print("ATENCIÓN: algún bloque falló (ver el registro del servidor).")
        return 0
    finally:
        cliente.salir()
        print(f"\nRutas pedidas: {', '.join(cliente.pedidas)}")


if __name__ == "__main__":
    sys.exit(main())
