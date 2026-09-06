"""Agentes custom conscientes del proveedor (#264 · SPEC-023 / AC3).

T12.3 hizo el modelo por agente consciente del proveedor **para los cuatro agentes
del núcleo**. `adapters/generic.py` —el runner de los agentes **custom**, los que se
definen con un `.agent.md`— se quedó fuera: leía `profile["model"]`, que salía del
frontmatter con un `or "llama3.2:1b"` de respaldo.

Qué rompía eso: con el default `LLM_PROVIDER=anthropic`, ejecutar un agente custom
mandaba un identificador de Ollama a la API de Claude. El error no es sutil —modelo
desconocido— pero llega **en tiempo de ejecución del pipeline**, con el artículo a
medias, y solo a quien haya creado un agente propio; los cuatro del núcleo funcionan
perfectamente, así que nadie lo ve hasta que alguien usa la función.

AC3 no excluye a los agentes custom. Esto termina de cumplirlo.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

import app.platform.llm as llm  # noqa: E402
from app.core import config  # noqa: E402
from app.modules.agents.adapters import generic  # noqa: E402

PERFILES = ROOT_DIR / "projects" / "alejandria-magazine" / "agents"

#: Un agente custom real del repo: tiene `model:` de Ollama y **no** tiene bloque
#: `models:`. Es exactamente el caso que rompía.
CUSTOM_SIN_MAPA = "pepe"


@pytest.fixture
def proveedor(monkeypatch):
    def _fijar(nombre):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", nombre, raising=False)
    return _fijar


# ── El caso que rompía ──────────────────────────────────────────────────────

def test_un_agente_custom_no_manda_un_id_de_ollama_a_claude(proveedor):
    """El fallo de #264, escrito como test: sin bloque `models:`, el `model:`
    heredado es de Ollama y no puede viajar a la API de Anthropic."""
    proveedor("anthropic")
    resuelto = llm.resolve_agent_model(CUSTOM_SIN_MAPA, {})
    assert not resuelto.startswith("llama"), (
        f"un agente custom resolvió a '{resuelto}' bajo el proveedor anthropic"
    )
    assert resuelto == config.settings.ANTHROPIC_MODEL, (
        "sin `models:`, debe caer al default del proveedor, no a un id ajeno"
    )


def test_el_mismo_agente_custom_conserva_su_modelo_en_su_proveedor(proveedor):
    """La cascada no descarta el `model:` heredado: lo descarta **solo** cuando el
    namespace no coincide. Bajo Ollama sigue siendo el suyo."""
    proveedor("ollama")
    assert llm.resolve_agent_model(CUSTOM_SIN_MAPA, {}) == "llama3.2:3b"


def test_un_agente_custom_con_models_usa_el_suyo(proveedor, tmp_path, monkeypatch):
    """Y con el bloque declarado, manda el bloque — que es lo que se documenta que
    haga un `.agent.md` propio."""
    perfil = tmp_path / "miagente.agent.md"
    perfil.write_text(
        "---\nname: miagente\nmodel: llama3.2:1b\n"
        "models:\n  anthropic: claude-haiku-4-5\n  ollama: llama3.2:3b\n"
        "temperature: 0.5\n---\n\n# Mi agente\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.platform.projects.profiles.find",
        lambda nombre: perfil if nombre == "miagente" else None,
    )
    proveedor("anthropic")
    assert llm.resolve_agent_model("miagente", {}) == "claude-haiku-4-5"
    proveedor("ollama")
    assert llm.resolve_agent_model("miagente", {}) == "llama3.2:3b"


def test_un_override_de_otro_proveedor_no_secuestra_la_ejecucion(proveedor):
    """Un `model` guardado en BD desde un despliegue anterior no debe imponerse
    sobre el proveedor activo: la cascada lo filtra por namespace."""
    proveedor("anthropic")
    resuelto = llm.resolve_agent_model(CUSTOM_SIN_MAPA, {CUSTOM_SIN_MAPA: {"model": "llama3.2:1b"}})
    assert resuelto == config.settings.ANTHROPIC_MODEL


def test_un_override_del_proveedor_activo_si_manda(proveedor):
    proveedor("anthropic")
    resuelto = llm.resolve_agent_model(
        CUSTOM_SIN_MAPA, {CUSTOM_SIN_MAPA: {"model": "claude-opus-5"}}
    )
    assert resuelto == "claude-opus-5"


# ── Que el runner use de verdad la cascada ──────────────────────────────────

def test_el_runner_custom_resuelve_con_la_cascada():
    """La costura: `generic.py` tiene que **llamar** a `resolve_agent_model`. Sin
    esto, la cascada podría estar perfecta y el runner seguir sin usarla."""
    fuente = (ROOT_DIR / "app" / "modules" / "agents" / "adapters" / "generic.py").read_text(
        encoding="utf-8"
    )
    assert "resolve_agent_model(agent_name" in fuente


def test_el_perfil_ya_no_lleva_un_modelo_estatico():
    """Cuál es el modelo de un agente depende del proveedor activo, así que una
    clave `model` fija en el diccionario del perfil es incorrecta por construcción
    — y tener dos sitios que resuelven el modelo es lo que causó este fallo."""
    perfil = generic.load_agent_profile(CUSTOM_SIN_MAPA)
    assert perfil is not None, f"no se encontró el perfil de '{CUSTOM_SIN_MAPA}'"
    assert "model" not in perfil, (
        "el perfil vuelve a llevar un modelo estático: hay dos fuentes de verdad"
    )


def test_no_queda_ningun_modelo_escrito_a_mano_en_el_runner():
    """El respaldo era literalmente `or "llama3.2:1b"`. Un identificador de modelo
    dentro de código compartido es la forma de que esto vuelva.

    Por AST y no por texto: la docstring del módulo dice «calling ollama» para
    explicar qué hace, y explicar algo no es hacerlo. Se buscan **literales de
    cadena que parezcan un id de modelo** (`nombre:etiqueta`), saltando docstrings.
    """
    import ast

    ruta = ROOT_DIR / "app" / "modules" / "agents" / "adapters" / "generic.py"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))

    docstrings = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            cuerpo = getattr(nodo, "body", [])
            if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(cuerpo[0].value, ast.Constant):
                docstrings.add(id(cuerpo[0].value))

    ids_de_modelo = re.compile(r"^[a-z][\w.\-]*:[\w.\-]+$", re.IGNORECASE)
    culpables = [
        nodo.value for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
        and id(nodo) not in docstrings and ids_de_modelo.match(nodo.value.strip())
    ]
    assert culpables == [], f"identificadores de modelo escritos a mano: {culpables}"


@pytest.mark.asyncio
async def test_el_runner_llama_al_llm_con_el_modelo_resuelto(proveedor, monkeypatch):
    """De extremo a extremo, sin LLM real: lo que llega a `call_llm` es lo que dijo
    la cascada, no lo que ponía el frontmatter."""
    proveedor("anthropic")
    recibido = {}

    async def _falso(prompt, **kwargs):
        recibido["model"] = kwargs.get("model")
        return "salida"

    monkeypatch.setattr(generic, "call_llm", _falso)

    await generic.run_generic_agent(CUSTOM_SIN_MAPA, {
        "title": "Un título", "keywords": [], "agent_settings": {},
        "_log": lambda *a, **k: None,
    })

    assert recibido["model"] == config.settings.ANTHROPIC_MODEL
    assert not str(recibido["model"]).startswith("llama")


@pytest.mark.asyncio
async def test_el_runner_respeta_el_modelo_elegido_para_esa_ejecucion(proveedor, monkeypatch):
    """El primer escalón de la cascada es el override de la ejecución —lo que se
    elige en la interfaz al lanzar el pipeline—. Resolver sin pasarle los
    `agent_settings` deja ese escalón muerto **sin romper nada visible**: el agente
    sigue funcionando, con el modelo equivocado."""
    proveedor("anthropic")
    recibido = {}

    async def _falso(prompt, **kwargs):
        recibido["model"] = kwargs.get("model")
        return "salida"

    monkeypatch.setattr(generic, "call_llm", _falso)

    await generic.run_generic_agent(CUSTOM_SIN_MAPA, {
        "title": "Un título", "keywords": [],
        # Distinto del default del proveedor a propósito: con el mismo valor, el
        # test pasaría también si el runner ignorara el override.
        "agent_settings": {CUSTOM_SIN_MAPA: {"model": "claude-haiku-4-5"}},
        "_log": lambda *a, **k: None,
    })

    assert recibido["model"] != config.settings.ANTHROPIC_MODEL, (
        "el override coincide con el default: el test no distingue nada"
    )
    assert recibido["model"] == "claude-haiku-4-5", (
        "el modelo elegido para la ejecución no llegó al LLM"
    )


# ── Los perfiles del repo ───────────────────────────────────────────────────

@pytest.mark.parametrize("agente", ["investigador", "redactor", "revisor", "formateador"])
def test_los_agentes_del_nucleo_declaran_su_mapa(agente):
    """Regresión de T12.3: si alguien quita el bloque `models:` de uno del núcleo,
    cae al default global y se pierde el escalonado por agente en silencio."""
    perfil = (PERFILES / f"{agente}.agent.md").read_text(encoding="utf-8")
    assert "models:" in perfil, f"{agente}.agent.md perdió su bloque models:"
