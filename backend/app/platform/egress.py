"""Guarda de egress para destinos que elige el usuario o el modelo (SPEC-024 / T2.5).

El bucle de *tool calling* deja que el modelo pida una URL y le devuelve el cuerpo
como resultado de la herramienta. Eso convierte cualquier fetch sin validar en un
SSRF **con lectura**: `http://169.254.169.254/latest/meta-data/` entrega las
credenciales de instancia, `http://127.0.0.1:8000/...` la propia API, y lo leído
vuelve al modelo y acaba en el artículo. Y la URL no la escribe una persona
autenticada: la elige el modelo, que lee documentos del RAG donde cabe una
instrucción incrustada.

Qué **no** protege esto, a propósito: los destinos de infraestructura que configura
quien opera —Ollama, Qdrant, los proveedores de LLM—. Son `localhost` o red privada
adrede, y su URL sale de la configuración, no de una entrada de usuario. Pasarlos
por aquí rompería el despliegue local sin cerrar ningún vector. La frontera es
«destino influido por usuario o modelo».

Tres decisiones que son el módulo entero:

* **Se comprueba la IP resuelta, no el hostname.** Un dominio público puede apuntar
  a `10.0.0.5`, y filtrar por texto no lo ve. Se resuelven **todas** las direcciones
  y basta una interna para rechazar.
* **Las redirecciones se siguen a mano.** `follow_redirects=True` valida la primera
  URL y luego obedece a `Location:` sin preguntar, que es la forma más cómoda de
  saltarse cualquier comprobación previa.
* **Un bloqueo se registra.** Un rechazo silencioso es indistinguible de un fallo de
  red, y quien lo diagnostique perderá la tarde.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

#: Lo único que tiene sentido para una herramienta de lectura web. `file:`,
#: `gopher:` y `ftp:` han sido vectores de SSRF clásicos; no se listan para
#: bloquearlos, se omiten porque solo pasa lo que está aquí.
ESQUEMAS_SEGUROS = ("https",)
ESQUEMA_CLARO = "http"


class EgressBlocked(RuntimeError):
    """El destino no está permitido. El mensaje dice por qué."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    #: Direcciones a las que resolvió el destino, cuando se llegó a resolver.
    addresses: Tuple[str, ...] = ()


def _lista(crudo: str) -> List[str]:
    return [d.strip().lower().rstrip(".") for d in (crudo or "").split(",") if d.strip()]


def _coincide_dominio(host: str, dominio: str) -> bool:
    """Coincidencia por **etiqueta**, no por sufijo de texto.

    `ejemplo.com` cubre `a.ejemplo.com` pero no `noejemplo.com`; con un
    `host.endswith(dominio)` a secas, registrar `noejemplo.com` bastaría para
    colarse en una allowlist que dice `ejemplo.com`.
    """
    host = host.lower().rstrip(".")
    return host == dominio or host.endswith("." + dominio)


def _es_interna(ip: ipaddress._BaseAddress) -> Optional[str]:
    """Motivo por el que la IP es interna, o `None` si es pública.

    Se desmapea IPv4-en-IPv6 **antes** de clasificar: `::ffff:127.0.0.1` no es
    `is_loopback` como IPv6, y ese es el hueco de siempre.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif getattr(ip, "sixtofour", None) is not None:
            ip = ip.sixtofour

    if ip.is_unspecified:
        return "dirección no especificada (0.0.0.0 / ::)"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        # Aquí vive 169.254.169.254, el endpoint de metadatos de AWS/GCP/Azure.
        return "enlace-local (incluye el endpoint de metadatos de la nube)"
    if ip.is_private:
        return "red privada"
    if ip.is_reserved:
        return "rango reservado"
    if ip.is_multicast:
        return "multicast"
    return None


def _resolver(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    direcciones = []
    for info in infos:
        direccion = info[4][0]
        if direccion not in direcciones:
            direcciones.append(direccion)
    return direcciones


def is_egress_allowed(url: str) -> Decision:
    """¿Se puede salir a esta URL? Devuelve la decisión con su motivo; no lanza.

    Orden de lo barato a lo caro: esquema → denylist → allowlist → DNS → IPs. La
    resolución es lo único que cuesta, y no se hace si algo anterior ya decidió.
    """
    from app.core.config import settings

    try:
        partes = urlparse(url)
    except ValueError as error:
        return Decision(False, f"URL no analizable: {error}")

    esquema = (partes.scheme or "").lower()
    permitidos = list(ESQUEMAS_SEGUROS)
    if getattr(settings, "EGRESS_ALLOW_HTTP", False):
        permitidos.append(ESQUEMA_CLARO)
    if esquema not in permitidos:
        return Decision(False, f"esquema '{esquema or '(ninguno)'}' no permitido; se admite {', '.join(permitidos)}")

    host = (partes.hostname or "").lower().rstrip(".")
    if not host:
        return Decision(False, "la URL no tiene host")

    for dominio in _lista(getattr(settings, "EGRESS_DENIED_DOMAINS", "")):
        if _coincide_dominio(host, dominio):
            return Decision(False, f"'{host}' está en la denylist de egress ({dominio})")

    permitidos_dominios = _lista(getattr(settings, "EGRESS_ALLOWED_DOMAINS", ""))
    if permitidos_dominios and not any(_coincide_dominio(host, d) for d in permitidos_dominios):
        return Decision(False, f"'{host}' no está en la allowlist de egress")

    puerto = partes.port or (443 if esquema == "https" else 80)
    try:
        direcciones = _resolver(host, puerto)
    except socket.gaierror as error:
        return Decision(False, f"no se pudo resolver '{host}': {error}")
    if not direcciones:
        return Decision(False, f"'{host}' no resolvió a ninguna dirección")

    for cruda in direcciones:
        try:
            ip = ipaddress.ip_address(cruda)
        except ValueError:
            return Decision(False, f"dirección no interpretable: {cruda!r}", tuple(direcciones))
        motivo = _es_interna(ip)
        if motivo:
            return Decision(
                False, f"'{host}' resuelve a {cruda}, que es {motivo}", tuple(direcciones)
            )

    return Decision(True, "destino público permitido", tuple(direcciones))


def assert_safe_url(url: str, *, quien: str = "egress") -> Decision:
    """Como `is_egress_allowed`, pero levanta. Es la que usan las herramientas.

    Con la versión que devuelve un booleano se puede olvidar mirar el resultado;
    con esta, olvidarlo no compila el fallo silencioso.
    """
    decision = is_egress_allowed(url)
    if not decision.allowed:
        logger.warning("Egress bloqueado en %s: %s (%s)", quien, url, decision.reason)
        raise EgressBlocked(decision.reason)
    return decision


async def safe_get(
    url: str,
    *,
    quien: str = "egress",
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 15.0,
    max_redirects: Optional[int] = None,
) -> httpx.Response:
    """GET con la guarda aplicada **en cada salto**.

    Las redirecciones se siguen a mano porque `follow_redirects=True` valida la
    primera URL y luego obedece a `Location:` sin preguntar: es la vía más cómoda
    para saltarse cualquier comprobación previa, y no deja rastro de que se ha
    saltado. Y sin `verify=False`: un proxy con inspección TLS se arregla
    instalando su CA, no apagando la verificación para todo el mundo.
    """
    from app.core.config import settings

    saltos = max_redirects if max_redirects is not None else getattr(settings, "EGRESS_MAX_REDIRECTS", 3)
    actual = url

    # Se valida **antes** de construir el cliente, no dentro del bucle: AC1 dice
    # «antes de abrir la conexión», y un cliente ya creado contra un destino
    # bloqueado es una conexión a punto de salir.
    assert_safe_url(actual, quien=quien)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(saltos + 1):
            respuesta = await client.get(actual, params=params, headers=headers)
            if respuesta.status_code not in (301, 302, 303, 307, 308):
                return respuesta
            destino = respuesta.headers.get("location")
            if not destino:
                return respuesta
            actual = str(httpx.URL(actual).join(destino))
            # Cada salto se revalida: `follow_redirects=True` obedecería a
            # `Location:` sin preguntar, que es la vía cómoda de saltarse la
            # comprobación de la URL inicial.
            assert_safe_url(actual, quien=quien)
            # Los parámetros son de la petición original: reenviarlos al destino de
            # una redirección los filtraría a un tercero.
            params = None

    raise EgressBlocked(f"demasiadas redirecciones ({saltos}) desde {url}")
