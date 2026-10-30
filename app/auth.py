import base64
import json
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import Response
from supabase_auth.errors import AuthApiError, AuthError
from supabase import Client, create_client

from app import validaciones

COOKIE_ACCESO = "sb_acceso"
COOKIE_REFRESCO = "sb_refresco"
DURACION_COOKIE = 60 * 60 * 24 * 30
LARGO_MINIMO_CONTRASENA = 8
ALFABETO_TEMPORAL = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"

MSG_CONEXION = "No pudimos conectarnos con el servidor. Revisá tu conexión y probá de nuevo."
MSG_GENERICO = "No pudimos completar la operación. Probá de nuevo en unos minutos."
MSG_SESION_VENCIDA = "Tu sesión venció. Iniciá sesión de nuevo."
# Mismo mensaje para lugar inexistente o desactivado, mail o contraseña incorrectos y persona que no es del lugar.
MSG_INGRESO = "No pudimos iniciar sesión con esos datos. Revisá el nombre del lugar, tu mail y tu contraseña."
MSG_INGRESO_ADMIN = "No pudimos iniciar sesión con esos datos. Revisá tu mail y tu contraseña."
MSG_DEMASIADOS_INTENTOS = "Hiciste demasiados intentos. Esperá unos minutos y probá de nuevo."
MSG_REINGRESAR = "Volvé a iniciar sesión escribiendo el nombre de tu lugar."
MSG_LINK_INVALIDO = "El link para restablecer la contraseña venció o ya se usó. Pedí uno nuevo."

# Solo a estos sitios puede apuntar el link del mail: armarlo con cualquier Host
# permitiría que alguien desvíe el link (y la sesión) a un dominio propio.
SITIOS_PERMITIDOS = {
    "localhost", "127.0.0.1",
    "just-show-up-amber.vercel.app", "just-show-up-theta.vercel.app",
}


class ErrorAuth(Exception):
    """Error con un mensaje apto para mostrarle a la usuaria."""


class Redirigir(Exception):
    def __init__(self, url: str, borrar_cookies: bool = False):
        self.url = url
        self.borrar_cookies = borrar_cookies


@dataclass
class Usuario:
    """Persona con sesión. Los roles son los de su empresa activa (multiempresa); la base vuelve a
    comprobar todo. `rol` es el de profiles (compatibilidad): sin datos de membresía, «profesora» se
    toma como administradora y profesora, y «alumna» como alumna (así se arman en las pruebas)."""
    id: str
    mail: str
    nombre: str
    apellido: str
    rol: str
    token: str = ""
    es_admin: bool = False
    es_docente: bool = False
    es_alumna: bool = False
    es_superadmin: bool = False
    empresa_id: Optional[int] = None
    empresa_nombre: str = ""
    empresa_codigo: str = ""
    con_membresias: bool = False

    def __post_init__(self):
        if not self.con_membresias and not self.es_superadmin:
            if self.rol == "profesora":
                self.es_admin = self.es_docente = True
            elif self.rol == "alumna":
                self.es_alumna = True

    @property
    def es_profesora(self) -> bool:
        """Docente (administradora o profesora) de la empresa activa: entra a la parte de profesora."""
        return self.es_docente

    @property
    def sin_empresa(self) -> bool:
        return self.con_membresias and not self.es_superadmin and self.empresa_id is None

    @property
    def inicio(self) -> str:
        """Pantalla de inicio según los roles en la empresa activa: administradora > profesora > alumna."""
        if self.es_superadmin:
            return "/admin/empresas"
        if self.es_admin:
            return "/administracion"
        if self.es_docente:
            return "/profesora/horarios"
        return "/"


# ---------- clientes de Supabase ----------

def cliente_nuevo() -> Client:
    # Uno por uso: el cliente guarda la sesion adentro, compartirlo mezclaria usuarias.
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def cliente_con_token(token: str) -> Client:
    cliente = cliente_nuevo()
    cliente.postgrest.auth(token)
    return cliente


def cliente_admin() -> Client:
    clave = os.environ.get("SUPABASE_SERVICE_KEY")
    if not clave:
        raise ErrorAuth("Falta configurar la clave de administrador en el servidor.")
    return create_client(os.environ["SUPABASE_URL"], clave)


# ---------- mensajes de error ----------

def traducir_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPError):
        return MSG_CONEXION
    if isinstance(error, AuthApiError):
        codigo = (getattr(error, "code", None) or "").lower()
        texto = (getattr(error, "message", "") or "").lower()
        if codigo == "invalid_credentials" or "invalid login credentials" in texto:
            return "El mail o la contraseña no son correctos."
        if codigo in ("email_exists", "user_already_exists") or "already been registered" in texto:
            return "Ya existe una cuenta con ese mail."
        if codigo == "weak_password" or ("password" in texto and "least" in texto):
            return f"La contraseña es muy débil. Usá al menos {LARGO_MINIMO_CONTRASENA} caracteres."
        if codigo == "email_not_confirmed":
            return "Tu cuenta todavía no está confirmada."
        if codigo == "over_request_rate_limit" or getattr(error, "status", None) == 429:
            return MSG_DEMASIADOS_INTENTOS
        if codigo == "same_password" or "different from the old" in texto:
            return "La contraseña nueva tiene que ser distinta a la anterior."
        if codigo == "validation_failed" or "valid email" in texto:
            return "El mail no es válido."
    logging.error("Error de Supabase Auth sin traducir: %r", error)
    return MSG_GENERICO


def validar_contrasena(contrasena: str) -> Optional[str]:
    if len(contrasena) < LARGO_MINIMO_CONTRASENA:
        return f"La contraseña debe tener al menos {LARGO_MINIMO_CONTRASENA} caracteres."
    return None


def validar_mail(mail: str) -> Optional[str]:
    return validaciones.mail(mail)


# ---------- sesion ----------

def iniciar_sesion(mail: str, contrasena: str) -> tuple[str, str]:
    try:
        sesion = cliente_nuevo().auth.sign_in_with_password(
            {"email": mail, "password": contrasena}
        ).session
    except (AuthError, httpx.HTTPError) as e:
        raise ErrorAuth(traducir_error(e)) from e
    if sesion is None:
        raise ErrorAuth(MSG_GENERICO)
    return sesion.access_token, sesion.refresh_token


def entrar_a_empresa(acceso: str, lugar: str) -> Optional[int]:
    """Valida en la base que el lugar exista, esté activo y la persona tenga un rol ahí. None si algo falla."""
    r = cliente_con_token(acceso).rpc("entrar_a_empresa", {"p_lugar": lugar}).execute().data or {}
    return r.get("id") if r.get("resultado") == "ok" else None


def salir_de_empresa(acceso: Optional[str]) -> None:
    """Olvida el lugar de esta sesión (al cerrar sesión)."""
    if not acceso:
        return
    try:
        cliente_con_token(acceso).rpc("salir_de_empresa", {}).execute()
    except Exception:
        logging.warning("No se pudo olvidar el lugar de la sesión (se cierra igual).")


def usuario_de_acceso(acceso: str) -> Optional[Usuario]:
    cliente = cliente_nuevo()
    try:
        respuesta = cliente.auth.get_user(acceso)
    except AuthError:
        return None
    if respuesta is None or respuesta.user is None:
        return None
    cliente.postgrest.auth(acceso)
    filas = (
        cliente.table("profiles").select("nombre, apellido, mail, rol, es_superadmin, empresa_activa")
        .eq("id", respuesta.user.id).execute().data
    )
    if not filas:
        return None
    p = filas[0]
    usuario = Usuario(
        id=respuesta.user.id, mail=p["mail"], nombre=p["nombre"], apellido=p["apellido"], rol=p["rol"],
        token=acceso, es_superadmin=bool(p.get("es_superadmin")), con_membresias=True,
    )
    if usuario.es_superadmin:
        return usuario
    # La empresa es la que eligió ESTA sesión al iniciar sesión (la base la guarda por session_id del token
    # y valida membresía y empresa activas): otra sesión de la misma cuenta en otro dispositivo no la cambia.
    empresa_id = cliente.rpc("empresa_de_sesion", {}).execute().data
    if not empresa_id:
        return usuario  # sin lugar en esta sesión: tiene que volver a iniciar sesión con el nombre del lugar
    membresia = (cliente.table("membresia").select("es_admin, es_profesora, es_alumna")
                 .eq("id_usuario", usuario.id).eq("id_empresa", empresa_id).eq("activa", True).execute().data)
    empresa = (cliente.table("empresa").select("id, nombre, codigo, activa").eq("id", empresa_id).execute().data
               if membresia else [])
    if not empresa or not empresa[0]["activa"]:
        return usuario
    m, e = membresia[0], empresa[0]
    usuario.empresa_id, usuario.empresa_nombre, usuario.empresa_codigo = e["id"], e["nombre"], e["codigo"]
    usuario.es_admin = bool(m["es_admin"])
    usuario.es_docente = bool(m["es_admin"] or m["es_profesora"])
    usuario.es_alumna = bool(m["es_alumna"])
    return usuario


def _validar(request: Request) -> Optional[Usuario]:
    acceso = request.cookies.get(COOKIE_ACCESO)
    refresco = request.cookies.get(COOKIE_REFRESCO)
    if not acceso and not refresco:
        return None
    try:
        if acceso:
            usuario = usuario_de_acceso(acceso)
            if usuario:
                return usuario
        if refresco:
            sesion = cliente_nuevo().auth.refresh_session(refresco).session
            if sesion:
                usuario = usuario_de_acceso(sesion.access_token)
                if usuario:
                    request.state.tokens_nuevos = (sesion.access_token, sesion.refresh_token)
                    return usuario
    except AuthError:
        return None
    except httpx.HTTPError as e:
        raise ErrorAuth(MSG_CONEXION) from e
    return None


def _usuario_cacheado(request: Request) -> Optional[Usuario]:
    if not hasattr(request.state, "usuario_cache"):
        request.state.usuario_cache = _validar(request)
    return request.state.usuario_cache


def usuario_opcional(request: Request) -> Optional[Usuario]:
    """Usuaria de la sesion si es valida; nunca falla (sirve para /login)."""
    try:
        return _usuario_cacheado(request)
    except ErrorAuth:
        return None


def url_login(aviso: str = "") -> str:
    return "/login" + (f"?{urlencode({'ok': 0, 'aviso': aviso})}" if aviso else "")


def _exigir_sesion(request: Request) -> Usuario:
    try:
        usuario = _usuario_cacheado(request)
    except ErrorAuth as e:
        raise Redirigir(url_login(str(e)))
    if usuario is not None:
        return usuario
    tenia_sesion = bool(request.cookies.get(COOKIE_ACCESO) or request.cookies.get(COOKIE_REFRESCO))
    raise Redirigir(
        url_login(MSG_SESION_VENCIDA if tenia_sesion else ""),
        borrar_cookies=tenia_sesion,
    )


# ---------- dependencias de FastAPI ----------

def destino(usuario: Usuario) -> str:
    """Pantalla de inicio según el rol en la empresa activa (sin empresa: volver a iniciar sesión)."""
    return url_login(MSG_REINGRESAR) if usuario.sin_empresa else usuario.inicio


def _con_empresa(request: Request) -> Usuario:
    usuario = _exigir_sesion(request)
    if usuario.es_superadmin:
        raise Redirigir(usuario.inicio)
    if usuario.sin_empresa:
        cerrar_sesion(request.cookies.get(COOKIE_ACCESO))
        raise Redirigir(url_login(MSG_REINGRESAR), borrar_cookies=True)
    return usuario


def requiere_login(request: Request) -> Usuario:
    return _exigir_sesion(request)


def requiere_alumna(request: Request) -> Usuario:
    """Alumna de la empresa activa (también una profesora o administradora que además es alumna ahí)."""
    usuario = _con_empresa(request)
    if not usuario.es_alumna:
        raise Redirigir(usuario.inicio)
    return usuario


def requiere_profesora(request: Request) -> Usuario:
    """Administradora o profesora de la empresa activa."""
    usuario = _con_empresa(request)
    if not usuario.es_profesora:
        logging.warning("Acceso denegado a %s para %s", request.url.path, usuario.id)
        raise Redirigir("/?" + urlencode({"ok": 0, "aviso": "No tenés permiso para entrar a esa sección."}))
    return usuario


def requiere_admin(request: Request) -> Usuario:
    """Solo la administradora de la empresa activa (facturación, pagos, altas, tipos de clase)."""
    usuario = requiere_profesora(request)
    if not usuario.es_admin:
        logging.warning("Acceso de administradora denegado a %s para %s", request.url.path, usuario.id)
        raise Redirigir("/profesora/horarios?" + urlencode({"ok": 0, "aviso": "Esa sección es solo para la administradora."}))
    return usuario


def requiere_superadmin(request: Request) -> Usuario:
    usuario = _exigir_sesion(request)
    if not usuario.es_superadmin:
        logging.warning("Acceso de superadministradora denegado a %s para %s", request.url.path, usuario.id)
        raise Redirigir(destino(usuario))
    return usuario


# ---------- cookies ----------

def _es_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def poner_cookies(response: Response, request: Request, acceso: str, refresco: str) -> None:
    for nombre, valor in ((COOKIE_ACCESO, acceso), (COOKIE_REFRESCO, refresco)):
        response.set_cookie(
            nombre, valor, max_age=DURACION_COOKIE, httponly=True,
            samesite="lax", secure=_es_https(request), path="/",
        )


def borrar_cookies(response: Response) -> None:
    for nombre in (COOKIE_ACCESO, COOKIE_REFRESCO):
        response.delete_cookie(nombre, path="/")


def cerrar_sesion(acceso: Optional[str]) -> None:
    if not acceso:
        return
    try:
        cliente_admin().auth.admin.sign_out(acceso, "local")
    except Exception:
        logging.warning("No se pudo revocar la sesión en Supabase (se borran igual las cookies).")


# ---------- administracion de cuentas (solo servidor, con la service key) ----------

def generar_contrasena_temporal(largo: int = 10) -> str:
    return "".join(secrets.choice(ALFABETO_TEMPORAL) for _ in range(largo))


def crear_cuenta(
    mail: str, nombre: str, apellido: str, rol: str = "alumna", extras: Optional[dict] = None,
    contrasena: Optional[str] = None, id_empresa: Optional[int] = None, rol_empresa: Optional[str] = None,
    sin_empresa: bool = False,
) -> tuple[str, str]:
    """Crea la cuenta (ya confirmada). Con id_empresa y rol_empresa la base crea la membresía en esa
    empresa (alumna, profesora, admin o admin_profesora); sin_empresa=True es para la superadministradora."""
    contrasena = contrasena or generar_contrasena_temporal()
    admin = cliente_admin()
    meta = {"nombre": nombre, "apellido": apellido}
    if id_empresa is not None:
        meta.update({"id_empresa": id_empresa, "rol_empresa": rol_empresa or "alumna"})
    elif sin_empresa:
        meta["sin_empresa"] = True
    try:
        creada = admin.auth.admin.create_user(
            {
                "email": mail,
                "password": contrasena,
                "email_confirm": True,
                "user_metadata": meta,
            }
        )
    except (AuthError, httpx.HTTPError) as e:
        raise ErrorAuth(traducir_error(e)) from e

    cambios = {k: v for k, v in (extras or {}).items() if v}
    if rol != "alumna" and id_empresa is None:
        # solo el alta vieja (sin empresa) usa profiles.rol; con empresa, el rol es el de la membresía
        cambios["rol"] = rol
    if cambios:
        try:
            admin.table("profiles").update(cambios).eq("id", creada.user.id).execute()
        except Exception as e:
            # sin los datos del perfil la cuenta queda a medias: se borra y se informa
            logging.exception("No se pudieron guardar los datos del perfil; se borra la cuenta recién creada")
            try:
                admin.auth.admin.delete_user(creada.user.id)
            except Exception:
                logging.exception("No se pudo borrar la cuenta a medias")
            if "profiles_cedula_unica" in str(e) or "23505" in str(e):
                raise ErrorAuth("Ya existe una alumna con esa cédula.") from e
            raise ErrorAuth(MSG_GENERICO) from e
    return creada.user.id, contrasena


def reiniciar_contrasena(usuario_id: str) -> str:
    nueva = generar_contrasena_temporal()
    try:
        cliente_admin().auth.admin.update_user_by_id(usuario_id, {"password": nueva})
    except (AuthError, httpx.HTTPError) as e:
        raise ErrorAuth(traducir_error(e)) from e
    return nueva


def cambiar_contrasena(usuario: Usuario, actual: str, nueva: str) -> None:
    problema = validar_contrasena(nueva)
    if problema:
        raise ErrorAuth(problema)
    try:
        cliente_nuevo().auth.sign_in_with_password({"email": usuario.mail, "password": actual})
    except AuthApiError as e:
        raise ErrorAuth("La contraseña actual no es correcta.") from e
    except httpx.HTTPError as e:
        raise ErrorAuth(MSG_CONEXION) from e
    try:
        cliente_admin().auth.admin.update_user_by_id(usuario.id, {"password": nueva})
    except (AuthError, httpx.HTTPError) as e:
        raise ErrorAuth(traducir_error(e)) from e


# ---------- olvidé mi contraseña (link por mail de Supabase) ----------

def url_restablecer(request: Request) -> Optional[str]:
    if request.url.hostname not in SITIOS_PERMITIDOS:
        return None
    esquema = "https" if _es_https(request) else "http"
    return f"{esquema}://{request.url.netloc}/restablecer-contrasena"


def pedir_recuperacion(mail: str, redirect_to: Optional[str]) -> None:
    """No informa si el mail existe: la respuesta es la misma en los dos casos."""
    try:
        cliente_nuevo().auth.reset_password_for_email(
            mail, {"redirect_to": redirect_to} if redirect_to else {}
        )
    except AuthApiError as e:
        if getattr(e, "status", None) == 429 or (getattr(e, "code", None) or "").startswith("over_"):
            raise ErrorAuth("Ya pediste varios links. Esperá unos minutos y probá de nuevo.") from e
        logging.warning("Supabase no envió el mail de recuperación: %r", e)
    except httpx.HTTPError as e:
        raise ErrorAuth(MSG_CONEXION) from e


def _es_sesion_de_recuperacion(acceso: str) -> bool:
    # Una sesión iniciada con contraseña tiene amr "password"; la del link del mail, "otp".
    try:
        carga = acceso.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))
    except (IndexError, ValueError):
        return False
    return any(m.get("method") in ("otp", "recovery") for m in claims.get("amr") or [])


def restablecer_contrasena(acceso: str, refresco: str, nueva: str) -> None:
    problema = validar_contrasena(nueva)
    if problema:
        raise ErrorAuth(problema)
    if not acceso or not refresco or not _es_sesion_de_recuperacion(acceso):
        raise ErrorAuth(MSG_LINK_INVALIDO)
    cliente = cliente_nuevo()
    try:
        cliente.auth.set_session(acceso, refresco)
    except (AuthError, ValueError) as e:
        raise ErrorAuth(MSG_LINK_INVALIDO) from e
    except httpx.HTTPError as e:
        raise ErrorAuth(MSG_CONEXION) from e
    try:
        cliente.auth.update_user({"password": nueva})
    except (AuthError, httpx.HTTPError) as e:
        # La sesión del link queda viva para que pueda corregir y reintentar.
        raise ErrorAuth(traducir_error(e)) from e
    try:
        cliente.auth.sign_out()
    except Exception:
        logging.warning("No se pudo cerrar la sesión del link de recuperación.")
