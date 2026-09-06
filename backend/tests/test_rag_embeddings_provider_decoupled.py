"""Embeddings desacoplados del proveedor de generación — SPEC-023 / AC6 y #265.

Anthropic no ofrece API de embeddings, así que con `LLM_PROVIDER=anthropic` el RAG
tiene que seguir usando su propio proveedor en vez de intentar —y fallar— vectorizar
vía Anthropic. Eso ya funcionaba (AC6), pero **derivado** de `LLM_PROVIDER`: un solo
ajuste decidía las dos cosas, así que generar con Claude y vectorizar con OpenAI era
imposible.

`EMBED_PROVIDER` (#265) lo separa. Lo que estos tests defienden no es solo que la
nueva combinación funcione, sino las dos propiedades que hacen seguro el cambio:

* **sin declararlo, el comportamiento es idéntico al de antes** — un despliegue que
  no toque nada no se entera;
* **una errata no manda los vectores a otro sitio en silencio**. Escribir mal el
  proveedor no da error: daría un RAG que no encuentra nada, semanas después y sin
  pista de por qué. Cae al camino heredado y **avisa**.
"""
import pytest

import app.platform.capabilities.rag as rag
from app.core import config


@pytest.fixture
def _spy_embedding_backends(monkeypatch):
    """Replace the two concrete embedders with spies that record the route taken."""
    called = {"route": None}

    async def fake_ollama(text, ollama_base_url, model):
        called["route"] = "ollama"
        return [0.1, 0.2, 0.3]

    async def fake_openai(text):
        called["route"] = "openai"
        return [0.4, 0.5, 0.6]

    monkeypatch.setattr(rag, "_get_embedding_ollama", fake_ollama)
    monkeypatch.setattr(rag, "_get_embedding_openai", fake_openai)
    return called


@pytest.mark.asyncio
async def test_anthropic_provider_embeds_via_ollama_not_anthropic(monkeypatch, _spy_embedding_backends):
    """With the default anthropic engine, embeddings route to Ollama (never Anthropic)."""
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "anthropic", raising=False)

    vec = await rag.get_embedding("hola", "http://localhost:11434", "nomic-embed-text")

    assert _spy_embedding_backends["route"] == "ollama"
    assert vec == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_openai_provider_embeds_via_openai(monkeypatch, _spy_embedding_backends):
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "openai", raising=False)

    await rag.get_embedding("hola", "http://localhost:11434", "nomic-embed-text")

    assert _spy_embedding_backends["route"] == "openai"


@pytest.mark.asyncio
async def test_ollama_provider_embeds_via_ollama(monkeypatch, _spy_embedding_backends):
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "ollama", raising=False)

    await rag.get_embedding("hola", "http://localhost:11434", "nomic-embed-text")

    assert _spy_embedding_backends["route"] == "ollama"


# ── EMBED_PROVIDER explícito (#265) ─────────────────────────────────────────

@pytest.fixture
def proveedores(monkeypatch):
    """Fija los dos proveedores por separado, que es de lo que va esta tarea."""
    def _fijar(generacion, embeddings=""):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", generacion, raising=False)
        monkeypatch.setattr(config.settings, "EMBED_PROVIDER", embeddings, raising=False)
    return _fijar


@pytest.mark.asyncio
async def test_claude_para_generar_y_openai_para_vectorizar(proveedores, _spy_embedding_backends):
    """La combinación que antes era imposible: es el criterio del issue."""
    proveedores("anthropic", "openai")

    await rag.get_embedding("hola", "http://localhost:11434", "nomic-embed-text")

    assert _spy_embedding_backends["route"] == "openai"


@pytest.mark.asyncio
async def test_openai_para_generar_y_ollama_para_vectorizar(proveedores, _spy_embedding_backends):
    """Y la simétrica: desacoplar es en los dos sentidos, no solo en el cómodo."""
    proveedores("openai", "ollama")

    await rag.get_embedding("hola", "http://localhost:11434", "nomic-embed-text")

    assert _spy_embedding_backends["route"] == "ollama"


@pytest.mark.parametrize("generacion,esperado", [
    ("anthropic", "ollama"),
    ("openai", "openai"),
    ("ollama", "ollama"),
    ("", "ollama"),
])
def test_sin_declararlo_el_comportamiento_es_el_de_antes(proveedores, generacion, esperado):
    """Compatibilidad, que es la mitad del criterio: quien no declare nada no puede
    notar este cambio. La tabla es literalmente la regla anterior."""
    proveedores(generacion, "")
    assert rag.get_embed_provider() == esperado


def test_una_errata_no_manda_los_vectores_a_otro_sitio_en_silencio(proveedores, caplog):
    """Escribir mal el proveedor no da error: daría un RAG que no encuentra nada,
    semanas después. Se cae al camino heredado **y se avisa**."""
    import logging

    proveedores("anthropic", "gemini")
    with caplog.at_level(logging.WARNING):
        assert rag.get_embed_provider() == "ollama"
    assert any("EMBED_PROVIDER" in r.getMessage() for r in caplog.records)


def test_el_valor_no_distingue_mayusculas_ni_espacios(proveedores):
    """Viene de una variable de entorno escrita a mano."""
    proveedores("anthropic", "  OpenAI ")
    assert rag.get_embed_provider() == "openai"


def test_anthropic_no_puede_ser_proveedor_de_embeddings():
    """No es una omisión: no tiene API de embeddings. Si alguien lo añadiera a la
    lista, cada vectorización fallaría y el RAG se quedaría vacío."""
    assert "anthropic" not in rag.EMBED_PROVIDERS


def test_declarar_anthropic_cae_al_camino_heredado(proveedores, caplog):
    """El error más probable de escribir: poner el mismo valor que en generación."""
    import logging

    proveedores("anthropic", "anthropic")
    with caplog.at_level(logging.WARNING):
        assert rag.get_embed_provider() == "ollama"
    assert any("EMBED_PROVIDER" in r.getMessage() for r in caplog.records)


def test_la_ruta_se_decide_en_un_solo_sitio():
    """Costura: si `get_embedding` volviera a mirar `LLM_PROVIDER` por su cuenta,
    habría dos reglas y el ajuste nuevo se ignoraría en uno de los caminos.

    Se mira el **cuerpo sin la docstring**: la docstring dice «not from
    `LLM_PROVIDER`» justo para explicar esto, y explicar algo no es hacerlo.
    """
    import ast
    import inspect
    import textwrap

    arbol = ast.parse(textwrap.dedent(inspect.getsource(rag.get_embedding)))
    funcion = arbol.body[0]
    cuerpo = funcion.body[1:] if ast.get_docstring(funcion) else funcion.body
    codigo = "\n".join(ast.unparse(nodo) for nodo in cuerpo)

    assert "get_embed_provider()" in codigo
    assert "LLM_PROVIDER" not in codigo


# ── La trampa que este ajuste hace alcanzable ───────────────────────────────

@pytest.mark.asyncio
async def test_una_dimension_que_no_cuadra_se_avisa_en_vez_de_degradarse_callando(
    proveedores, monkeypatch, caplog,
):
    """El riesgo real de poder elegir proveedor: `nomic-embed-text` da 768 y
    `text-embedding-3-small` 1536, pero `RAG_VECTOR_SIZE` es **global**. Con la
    dimensión equivocada, cada vector se descartaba y se sustituía por un
    pseudovector de hash — sin error, sin aviso—: el RAG queda indexado y **no
    encuentra nada**, y eso se descubre semanas después.

    No se cambia el respaldo, que existe para que un proveedor caído no tumbe la
    subida. Se cambia que no se pueda no enterarse.
    """
    import logging

    proveedores("anthropic", "openai")

    async def _otra_dimension(text):
        return [0.1] * 1536          # OpenAI devuelve 1536; el índice espera 768

    monkeypatch.setattr(rag, "_get_embedding_openai", _otra_dimension)
    monkeypatch.setattr(rag, "is_qdrant_available", _si)
    enviados = {}

    async def _upsert_falso(url, json=None, headers=None, **kwargs):
        enviados["puntos"] = (json or {}).get("points", [])
        return _RespuestaOk()

    monkeypatch.setattr(rag.httpx.AsyncClient, "put", _upsert_falso, raising=False)

    with caplog.at_level(logging.WARNING):
        await rag.upsert_chunks(
            qdrant_url="http://qdrant:6333", collection="c", doc_id="d",
            agent_name="investigador", filename="paper.pdf",
            chunks=["uno", "dos", "tres"],
            ollama_base_url="http://localhost:11434",
            embedding_model="nomic-embed-text", vector_size=768,
        )

    avisos = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("dimensión" in a for a in avisos), avisos
    assert any("RAG_VECTOR_SIZE" in a for a in avisos), avisos
    # Y se avisa **una vez por documento**, no una por fragmento: tres chunks, un aviso.
    assert sum(1 for a in avisos if "dimensión" in a) == 1


@pytest.mark.asyncio
async def test_un_proveedor_caido_se_distingue_de_una_mala_configuracion(
    proveedores, monkeypatch, caplog,
):
    """No son el mismo problema: uno se arregla solo cuando el proveedor vuelve, el
    otro no se arregla nunca hasta que alguien cambia la configuración."""
    import logging

    proveedores("anthropic", "ollama")

    async def _sin_respuesta(text, ollama_base_url, model):
        return None

    monkeypatch.setattr(rag, "_get_embedding_ollama", _sin_respuesta)
    monkeypatch.setattr(rag, "is_qdrant_available", _si)

    async def _upsert_falso(url, json=None, headers=None, **kwargs):
        return _RespuestaOk()

    monkeypatch.setattr(rag.httpx.AsyncClient, "put", _upsert_falso, raising=False)

    with caplog.at_level(logging.WARNING):
        await rag.upsert_chunks(
            qdrant_url="http://qdrant:6333", collection="c", doc_id="d",
            agent_name="investigador", filename="paper.pdf", chunks=["uno"],
            ollama_base_url="http://localhost:11434",
            embedding_model="nomic-embed-text", vector_size=768,
        )

    avisos = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no respondió" in a for a in avisos), avisos
    assert not any("RAG_VECTOR_SIZE" in a for a in avisos), (
        "un proveedor caído no es un problema de configuración"
    )


async def _si(*a, **k):
    return True


class _RespuestaOk:
    status_code = 200

    def json(self):
        return {"result": {"status": "completed"}}
