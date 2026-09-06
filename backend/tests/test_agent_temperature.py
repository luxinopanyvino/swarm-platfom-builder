"""La temperatura del perfil llega al modelo (deuda de #225/#264/#265).

Los ocho `.agent.md` declaran `temperature`, `agents_seed.py` la siembra en BD, el
router la funde en `agent_settings` y `POST /claude-defs` la acepta desde la
interfaz — y **no llegaba a ningún proveedor**. El único sitio del repo que pasaba
`temperature` a `call_llm` era el juez de las evals (T9.4).

O sea: un control que la interfaz ofrece por agente, que se guarda, que se muestra,
y que no hacía nada. No da error y no se nota mirando una ejecución: el modelo
genera con su default y el texto parece razonable. Se nota cuando alguien baja la
temperatura del revisor para que sea consistente y sigue siendo errático.

Lo que estos tests defienden, además de que el valor llegue:

* que **cada agente** llegue conectado, incluido el redactor —que genera por
  streaming, un camino aparte que es fácil dejarse— y los custom;
* que un valor fuera de rango se **recorte** en vez de reventar la generación: a
  Anthropic un 1.5 le da 400, y sería el mismo fallo que mandarle un id de Ollama;
* y que no declarar nada siga significando «el default del proveedor», que es el
  comportamiento anterior.
"""
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

import app.platform.llm as llm  # noqa: E402
from app.core import config  # noqa: E402

ADAPTERS = ROOT_DIR / "app" / "modules" / "agents" / "adapters"
PERFILES = ROOT_DIR / "projects" / "alejandria-magazine" / "agents"

#: Los agentes que invocan al LLM. Orquestador y publicador no (SPEC-023/AC4).
AGENTES_CON_LLM = ("investigador", "redactor", "revisor", "formateador")


@pytest.fixture
def proveedor(monkeypatch):
    def _fijar(nombre):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", nombre, raising=False)
    return _fijar


# ── La cascada ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("agente,esperada", [
    ("investigador", 0.3), ("redactor", 0.7), ("revisor", 0.2), ("formateador", 0.1),
])
def test_cada_agente_recupera_la_temperatura_de_su_perfil(agente, esperada):
    """No es un número cualquiera: el revisor la tiene baja para ser consistente y
    el redactor alta para escribir. Perder eso es perder el ajuste por agente."""
    assert llm.resolve_agent_temperature(agente, {}) == esperada


def test_lo_elegido_para_la_ejecucion_gana_al_perfil():
    """El router funde ahí el valor del perfil en BD, así que este escalón cubre
    tanto el override por ejecución como lo que edita la interfaz."""
    assert llm.resolve_agent_temperature("redactor", {"redactor": {"temperature": 0.15}}) == 0.15


def test_sin_declarar_nada_manda_el_default_del_proveedor():
    """`None` y no un número inventado: es lo que preserva el comportamiento
    anterior para cualquier agente que no la declare."""
    assert llm.resolve_agent_temperature("no-existe-este-agente", {}) is None


def test_un_valor_no_numerico_no_tumba_la_generacion():
    """Viene de un campo de la interfaz; que sea basura no puede impedir generar."""
    assert llm.resolve_agent_temperature("redactor", {"redactor": {"temperature": "alta"}}) is None


def test_el_cero_se_respeta_y_no_se_confunde_con_ausente():
    """`0.0` es falsy: un `or` en la cascada lo tomaría por «no declarada» y
    mandaría el default. Es justo el valor que se pone para ser determinista."""
    assert llm.resolve_agent_temperature("redactor", {"redactor": {"temperature": 0.0}}) == 0.0


# ── El recorte por proveedor ────────────────────────────────────────────────

@pytest.mark.parametrize("proveedor_llm,entrada,esperada", [
    ("anthropic", 1.5, 1.0),     # Anthropic corta en 1.0 y devuelve 400 por encima
    ("anthropic", 1.0, 1.0),
    ("openai", 1.5, 1.5),        # OpenAI y Ollama llegan a 2.0
    ("ollama", 1.5, 1.5),
    ("anthropic", -0.2, 0.0),
    ("openai", 3.0, 2.0),
])
def test_una_temperatura_fuera_de_rango_se_recorta(proveedor_llm, entrada, esperada):
    """Mandar 1.5 a Claude es el mismo tipo de fallo que mandarle un id de Ollama:
    la configuración es plausible y el error solo aparece al generar."""
    assert llm._clamp_temperature(entrada, proveedor_llm) == esperada


def test_el_recorte_avisa(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        llm._clamp_temperature(1.8, "anthropic")
    assert any("recorta" in r.getMessage() for r in caplog.records)


def test_sin_temperatura_no_se_recorta_nada():
    assert llm._clamp_temperature(None, "anthropic") is None


def test_el_recorte_esta_en_el_dispatcher_y_no_en_el_resolutor():
    """En la única puerta a los proveedores: así protege también a quien llame a
    `call_llm` directamente —el juez de las evals, por ejemplo—."""
    import ast
    import inspect
    import textwrap

    for funcion in (llm.call_llm, llm.call_llm_stream):
        arbol = ast.parse(textwrap.dedent(inspect.getsource(funcion)))
        cuerpo = arbol.body[0]
        codigo = "\n".join(
            ast.unparse(n) for n in (cuerpo.body[1:] if ast.get_docstring(cuerpo) else cuerpo.body)
        )
        assert "_clamp_temperature" in codigo, f"{funcion.__name__} no recorta"


# ── Que llegue de verdad, agente por agente ─────────────────────────────────

@pytest.mark.asyncio
async def test_el_revisor_manda_su_temperatura(proveedor, monkeypatch):
    from app.modules.agents.adapters import revisor

    proveedor("anthropic")
    recibido = {}

    async def _falso(prompt, **kwargs):
        recibido.update(kwargs)
        return '{"approval_score": 90, "coherent": true, "feedback": []}'

    monkeypatch.setattr(revisor, "call_llm", _falso)
    await revisor.run_revisor({"draft_text": "texto", "loop_count": 0,
                               "agent_settings": {}, "_log": lambda *a, **k: None})
    assert recibido["temperature"] == 0.2


@pytest.mark.asyncio
async def test_el_formateador_manda_su_temperatura(proveedor, monkeypatch):
    from app.modules.agents.adapters import formateador

    proveedor("anthropic")
    recibido = {}

    async def _falso(prompt, **kwargs):
        recibido.update(kwargs)
        return "# Texto\n\ncuerpo formateado suficientemente largo para no ser descartado."

    monkeypatch.setattr(formateador, "call_llm", _falso)
    await formateador.run_formateador({
        "draft_text": "# Texto\n\ncuerpo", "scientific_format": "apa",
        "agent_settings": {}, "_log": lambda *a, **k: None,
    })
    assert recibido["temperature"] == 0.1


@pytest.mark.asyncio
async def test_el_redactor_manda_su_temperatura_por_el_camino_de_streaming(proveedor, monkeypatch):
    """El redactor genera por streaming, que es un camino aparte del dispatcher.
    Es el agente cuya temperatura más se nota, y el más fácil de dejarse."""
    from app.modules.agents.adapters import redactor

    proveedor("anthropic")
    recibido = {}

    async def _falso(prompt, **kwargs):
        recibido.update(kwargs)
        for token in ("palabra " * 400).split():
            yield token + " "

    monkeypatch.setattr(redactor, "call_llm_stream", _falso)
    await redactor.run_redactor({
        "title": "T", "keywords": [], "research_data": "datos", "feedback": [],
        "agent_settings": {}, "_log": lambda *a, **k: None,
        "_emit_token": lambda t: None,
    })
    assert recibido["temperature"] == 0.7


@pytest.mark.asyncio
async def test_un_agente_custom_manda_la_suya(proveedor, monkeypatch):
    from app.modules.agents.adapters import generic

    proveedor("anthropic")
    recibido = {}

    async def _falso(prompt, **kwargs):
        recibido.update(kwargs)
        return "salida"

    monkeypatch.setattr(generic, "call_llm", _falso)
    await generic.run_generic_agent("pepe", {
        "title": "T", "keywords": [], "agent_settings": {}, "_log": lambda *a, **k: None,
    })
    assert recibido["temperature"] == 0.7


# ── El salto del dispatcher al proveedor ────────────────────────────────────
#
# Los tests de arriba sustituyen `call_llm`/`call_llm_stream`, así que comprueban
# que el **agente** la manda. Este tramo —del dispatcher a cada proveedor— queda
# fuera de ellos, y es donde más fácil se pierde el parámetro sin que nada falle:
# la generación sigue funcionando, con el default.

@pytest.mark.parametrize("proveedor_llm,funcion", [
    ("anthropic", "_call_anthropic_stream"),
    ("openai", "_call_openai_stream"),
    ("ollama", "_call_ollama_stream"),
])
@pytest.mark.asyncio
async def test_el_streaming_reenvia_la_temperatura_a_cada_proveedor(
    proveedor, monkeypatch, proveedor_llm, funcion,
):
    proveedor(proveedor_llm)
    recibido = {}

    async def _falso(**kwargs):
        recibido.update(kwargs)
        yield "token"

    monkeypatch.setattr(llm, funcion, _falso)

    async for _ in llm.call_llm_stream("p", model="m", temperature=0.42):
        pass

    assert recibido.get("temperature") == 0.42, (
        f"{funcion} no recibió la temperatura: se perdió en el dispatcher"
    )


@pytest.mark.parametrize("proveedor_llm,funcion", [
    ("anthropic", "_call_anthropic"),
    ("openai", "_call_openai"),
    ("ollama", "_call_ollama"),
])
@pytest.mark.asyncio
async def test_la_generacion_reenvia_la_temperatura_a_cada_proveedor(
    proveedor, monkeypatch, proveedor_llm, funcion,
):
    proveedor(proveedor_llm)
    recibido = {}

    async def _falso(**kwargs):
        recibido.update(kwargs)
        return "salida"

    monkeypatch.setattr(llm, funcion, _falso)

    await llm.call_llm("p", model="m", temperature=0.42)

    assert recibido.get("temperature") == 0.42, (
        f"{funcion} no recibió la temperatura: se perdió en el dispatcher"
    )


# ── Guardas estructurales ───────────────────────────────────────────────────

@pytest.mark.parametrize("agente", AGENTES_CON_LLM)
def test_ningun_agente_con_llm_se_queda_sin_conectar(agente):
    """El fallo original fue exactamente este: el ajuste existía en todas partes
    menos en la llamada. Un agente nuevo que se olvide rompe aquí."""
    fuente = (ADAPTERS / f"{agente}.py").read_text(encoding="utf-8")
    assert "resolve_agent_temperature" in fuente, f"{agente} no resuelve su temperatura"
    assert "temperature=temperature" in fuente, f"{agente} la resuelve y no la pasa"


@pytest.mark.parametrize("agente", AGENTES_CON_LLM)
def test_los_perfiles_siguen_declarando_su_temperatura(agente):
    perfil = (PERFILES / f"{agente}.agent.md").read_text(encoding="utf-8")
    assert "temperature:" in perfil


def test_el_resolutor_es_espejo_del_de_modelo():
    """Son el mismo tipo de dato —un ajuste por agente que viene de tres sitios— y
    tenerlos con formas distintas es lo que hizo que uno se quedara sin conectar."""
    import inspect

    firma_modelo = inspect.signature(llm.resolve_agent_model)
    firma_temp = inspect.signature(llm.resolve_agent_temperature)
    assert list(firma_modelo.parameters) == list(firma_temp.parameters)
