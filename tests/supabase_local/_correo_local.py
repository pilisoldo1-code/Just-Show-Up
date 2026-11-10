"""Ayuda para las pruebas contra el Supabase LOCAL: activar una cuenta invitada desde el panel con el correo que recibe
Mailpit (como en producción). Las cuentas creadas por invitación tienen que poder recibir correo: nunca .invalid."""
import html
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

SITIO = "http://localhost:8000"  # sitio permitido para los enlaces (SITIOS_PERMITIDOS y additional_redirect_urls)


def activar_por_correo(estado: dict, app, mail: str, clave: str, segundos: int = 15) -> None:
    mailpit = estado.get("MAILPIT_URL") or estado["INBUCKET_URL"]
    if not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", mailpit):
        raise SystemExit("Mailpit tiene que ser local.")
    fin, lista = time.time() + segundos, []
    while time.time() < fin and not lista:
        lista = httpx.get(f"{mailpit}/api/v1/search", params={"query": f'to:"{mail}"'}).json().get("messages") or []
        if not lista:
            time.sleep(0.3)
    if not lista:
        raise SystemExit(f"No llegó la invitación a {mail}.")
    cuerpo = httpx.get(f"{mailpit}/api/v1/message/{lista[0]['ID']}").json()["HTML"]
    enlace = html.unescape(re.search(r'href="([^"]+)"', cuerpo).group(1))
    token = parse_qs(urlsplit(enlace).fragment)["token_hash"][0]
    r = TestClient(app, base_url=SITIO).post("/activar-cuenta", data={"token_hash": token, "nueva": clave, "repetir": clave},
                                             follow_redirects=False)
    if r.status_code != 303:
        raise SystemExit(f"No se pudo activar {mail} (HTTP {r.status_code}).")
