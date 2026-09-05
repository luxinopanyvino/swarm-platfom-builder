"""Guarda de egress de las herramientas de agente (SPEC-024 / T2.5 / #159).

El bucle de *tool calling* deja que **el modelo** pida una URL y le devuelve el
cuerpo como resultado. Sin validar el destino, eso no es un SSRF ciego: es
exfiltración — `169.254.169.254` entrega las credenciales de instancia y lo leído
vuelve al contexto y acaba en el artículo. Y la URL no la escribe una persona
autenticada: la elige el modelo, que lee documentos del RAG donde cabe una
instrucción incrustada.

Lo que estos tests protegen, más allá de la lista de rangos:

* que se comprueba la **IP resuelta** y no el aspecto del hostname;
* que una **redirección** a un destino interno se corta como la URL inicial, que
  es la vía cómoda de saltarse cualquier comprobación previa;
* y que nadie vuelve a colar un `verify=False` ni un cliente HTTP propio en las
  herramientas — sin eso, la protección dura hasta el siguiente que tenga prisa.
"""
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "ci-secret-not-for-prod")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_egress.db")

from app.platform import egress  # noqa: E402
from app.platform.egress import EgressBlocked, is_egress_allowed  # noqa: E402

APP_DIR = ROOT_DIR / "app"
TOOLS = APP_DIR / "platform" / "capabilities" / "tools.py"


@pytest.fixture
def resuelve(monkeypatch):
    """Fija a qué resuelve un hostname, para probar sin depender del DNS real."""
    def _fijar(direcciones):
        def _falso(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (d, port))
                    for d in direcciones]
        monkeypatch.setattr(egress.socket, "getaddrinfo", _falso)
    return _fijar


# ── AC1: destinos internos ──────────────────────────────────────────────────

@pytest.mark.parametrize("url,fragmento", [
    ("https://169.254.169.254/latest/meta-data/", "enlace-local"),
    ("https://127.0.0.1:8000/api/v1/users", "loopback"),
    ("https://[::1]/", "loopback"),
    ("https://10.0.0.5/", "red privada"),
    ("https://192.168.1.1/", "red privada"),
    ("https://172.16.0.1/", "red privada"),
    ("https://0.0.0.0/", "no especificada"),
    ("https://[::ffff:127.0.0.1]/", "loopback"),
])
def test_los_destinos_internos_se_rechazan(url, fragmento):
    """`169.254.169.254` es el endpoint de metadatos de AWS/GCP/Azure: entrega
    credenciales de instancia a quien sepa pedirlas."""
    decision = is_egress_allowed(url)
    assert not decision.allowed
    assert fragmento in decision.reason


def test_una_ipv4_mapeada_en_ipv6_no_se_cuela():
    """El hueco clásico: `::ffff:127.0.0.1` no es `is_loopback` como IPv6, así que
    clasificar sin desmapear la deja pasar."""
    assert not is_egress_allowed("https://[::ffff:169.254.169.254]/").allowed


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://evil/", "ftp://interno/", "ldap://interno/",
    "https:///sin-host", "no-es-una-url",
])
def test_solo_pasan_los_esquemas_declarados(url):
    """No se enumeran los peligrosos: pasa lo que está en la lista y nada más, que
    es lo único que envejece bien."""
    assert not is_egress_allowed(url).allowed


def test_http_en_claro_esta_apagado_por_defecto():
    assert not is_egress_allowed("http://example.com/").allowed


def test_http_se_puede_habilitar(monkeypatch, resuelve):
    from app.core import config
    monkeypatch.setattr(config.settings, "EGRESS_ALLOW_HTTP", True, raising=False)
    resuelve(["93.184.216.34"])
    assert is_egress_allowed("http://example.com/").allowed


def test_un_destino_publico_pasa(resuelve):
    resuelve(["93.184.216.34"])
    decision = is_egress_allowed("https://example.com/")
    assert decision.allowed
    assert decision.addresses == ("93.184.216.34",)


def test_un_bloqueo_se_registra(caplog):
    """Un rechazo silencioso es indistinguible de un fallo de red."""
    import logging

    with caplog.at_level(logging.WARNING):
        with pytest.raises(EgressBlocked):
            egress.assert_safe_url("https://127.0.0.1/", quien="prueba")
    assert any("prueba" in r.message or "prueba" in str(r.args) for r in caplog.records)


# ── AC2: la IP resuelta, no el hostname ─────────────────────────────────────

def test_un_dominio_publico_que_apunta_a_una_ip_interna_se_rechaza(resuelve):
    """Es todo el motivo de resolver: filtrar por texto no ve esto, y registrar un
    dominio que apunte a `10.0.0.5` cuesta un minuto."""
    resuelve(["10.0.0.5"])
    decision = is_egress_allowed("https://parece-publico.example/")
    assert not decision.allowed
    assert "10.0.0.5" in decision.reason


def test_basta_una_ip_interna_entre_varias(resuelve):
    """Un hostname puede resolver a varias direcciones; quedarse con la primera
    deja pasar al que ponga una pública delante."""
    resuelve(["93.184.216.34", "127.0.0.1"])
    assert not is_egress_allowed("https://mixto.example/").allowed


def test_un_hostname_que_no_resuelve_se_rechaza(monkeypatch):
    def _falla(*a, **k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(egress.socket, "getaddrinfo", _falla)
    assert not is_egress_allowed("https://no-existe.example/").allowed


@pytest.mark.asyncio
async def test_una_redireccion_a_un_destino_interno_se_corta(monkeypatch, resuelve):
    """`follow_redirects=True` valida la primera URL y luego obedece a `Location:`
    sin preguntar. Es la forma más cómoda de saltarse la comprobación."""
    import httpx

    resuelve(["93.184.216.34"])

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta-data/"})

    transporte = httpx.MockTransport(_handler)
    original = httpx.AsyncClient

    def _cliente(*a, **k):
        k["transport"] = transporte
        return original(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", _cliente)

    # La primera URL es pública; el salto no lo es.
    def _resolver(host, port, *a, **k):
        destino = "93.184.216.34" if host == "publico.example" else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (destino, port))]

    monkeypatch.setattr(egress.socket, "getaddrinfo", _resolver)

    with pytest.raises(EgressBlocked) as error:
        await egress.safe_get("https://publico.example/", quien="prueba")
    assert "enlace-local" in str(error.value)


@pytest.mark.asyncio
async def test_una_cadena_de_redirecciones_no_es_infinita(monkeypatch, resuelve):
    import httpx

    resuelve(["93.184.216.34"])

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://publico.example/otra"})

    transporte = httpx.MockTransport(_handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: original(*a, **{**k, "transport": transporte}),
    )

    with pytest.raises(EgressBlocked) as error:
        await egress.safe_get("https://publico.example/", quien="prueba", max_redirects=2)
    assert "redirecciones" in str(error.value)


# ── AC4: allowlist / denylist ───────────────────────────────────────────────

def test_la_denylist_rechaza_aunque_sea_publico(monkeypatch, resuelve):
    from app.core import config
    monkeypatch.setattr(config.settings, "EGRESS_DENIED_DOMAINS", "malo.example", raising=False)
    resuelve(["93.184.216.34"])
    assert not is_egress_allowed("https://malo.example/").allowed
    assert not is_egress_allowed("https://sub.malo.example/").allowed


def test_una_allowlist_poblada_solo_deja_pasar_lo_suyo(monkeypatch, resuelve):
    from app.core import config
    monkeypatch.setattr(config.settings, "EGRESS_ALLOWED_DOMAINS",
                        "arxiv.org, en.wikipedia.org", raising=False)
    resuelve(["93.184.216.34"])
    assert is_egress_allowed("https://export.arxiv.org/api/query").allowed
    assert not is_egress_allowed("https://otro.example/").allowed


def test_la_coincidencia_es_por_etiqueta_y_no_por_sufijo(monkeypatch, resuelve):
    """Con un `endswith` a secas, registrar `noejemplo.com` bastaría para colarse
    en una allowlist que dice `ejemplo.com`."""
    from app.core import config
    monkeypatch.setattr(config.settings, "EGRESS_ALLOWED_DOMAINS", "ejemplo.com", raising=False)
    resuelve(["93.184.216.34"])
    assert is_egress_allowed("https://a.ejemplo.com/").allowed
    assert not is_egress_allowed("https://noejemplo.com/").allowed


def test_la_denylist_gana_a_la_allowlist(monkeypatch, resuelve):
    from app.core import config
    monkeypatch.setattr(config.settings, "EGRESS_ALLOWED_DOMAINS", "ejemplo.com", raising=False)
    monkeypatch.setattr(config.settings, "EGRESS_DENIED_DOMAINS", "malo.ejemplo.com", raising=False)
    resuelve(["93.184.216.34"])
    assert not is_egress_allowed("https://malo.ejemplo.com/").allowed


# ── Las herramientas ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_url_no_abre_conexion_contra_un_destino_interno(monkeypatch):
    """«Antes de la petición» del AC1, comprobado como tal: el cliente HTTP revienta
    si alguien lo usa, así que el test falla si la guarda se aplicara después."""
    import httpx

    from app.platform.capabilities import tools

    def _prohibido(*a, **k):
        raise AssertionError("se ha abierto una conexión hacia un destino bloqueado")

    monkeypatch.setattr(httpx, "AsyncClient", _prohibido)

    salida = await tools.execute_tool(
        "fetch_url", {"url": "https://169.254.169.254/latest/meta-data/"}
    )
    assert "no permitido" in salida.lower()


@pytest.mark.asyncio
async def test_el_motivo_del_bloqueo_no_vuelve_al_modelo(monkeypatch):
    """El motivo describe la red interna —«resuelve a 10.0.0.5»— y la respuesta de
    la herramienta entra en el contexto del modelo. El detalle va al log."""
    import httpx

    from app.platform.capabilities import tools

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no debería conectarse")))

    salida = await tools.execute_tool("fetch_url", {"url": "https://127.0.0.1:8000/"})
    assert "127.0.0.1" not in salida and "loopback" not in salida


# ── Guardas estructurales: AC3 y AC4 ────────────────────────────────────────

def test_ninguna_llamada_de_red_desactiva_la_verificacion_tls():
    """AC3. Estaba en dos sitios, justificado por «proxies corporativos con
    inspección TLS»: eso se arregla instalando la CA del proxy, no apagando la
    verificación para todo el mundo y para siempre."""
    import ast

    # Por AST y no por texto: explicar en una docstring por qué **no** se usa
    # `verify=False` no es usarlo, y un `grep` no sabe distinguirlo. Lo que se
    # busca es el argumento de verdad en una llamada de verdad.
    culpables = []
    for fichero in sorted(APP_DIR.rglob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"), filename=str(fichero))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            for palabra in nodo.keywords:
                if palabra.arg != "verify":
                    continue
                valor = palabra.value
                if isinstance(valor, ast.Constant) and valor.value is False:
                    culpables.append(f"{fichero.relative_to(ROOT_DIR)}:{nodo.lineno}")
    assert culpables == [], f"verificación TLS desactivada en: {culpables}"


def test_las_herramientas_no_tienen_cliente_http_propio():
    """AC4. Un `httpx.AsyncClient` en `tools.py` esquivaría la guarda, y el olvido
    no rompe nada visible: la herramienta seguiría funcionando, sin protección."""
    import ast

    arbol = ast.parse(TOOLS.read_text(encoding="utf-8"))
    culpables = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            objetivo = ast.unparse(nodo.func)
            if "AsyncClient" in objetivo or objetivo in ("requests.get", "requests.post"):
                culpables.append(f"{objetivo} (línea {nodo.lineno})")
    assert culpables == [], f"fetch saliente que esquiva la guarda: {culpables}"


def test_toda_salida_de_las_herramientas_pasa_por_la_guarda():
    fuente = TOOLS.read_text(encoding="utf-8")
    assert "from app.platform.egress import" in fuente
    assert fuente.count("safe_get(") >= 3, "hay tres fetch salientes: fetch_url, arXiv y Wikipedia"


def test_la_guarda_no_se_aplica_a_la_infraestructura():
    """Frontera de la spec (§2 No-objetivos): Ollama y Qdrant son `localhost`
    **a propósito** y su URL sale de la configuración, no de una entrada de
    usuario. Pasarlos por la guarda rompería el despliegue local sin cerrar nada."""
    for modulo in ("platform/llm.py", "shared/qdrant.py"):
        fuente = (APP_DIR / modulo).read_text(encoding="utf-8")
        assert "safe_get" not in fuente, f"{modulo} no debe pasar por la guarda de egress"
