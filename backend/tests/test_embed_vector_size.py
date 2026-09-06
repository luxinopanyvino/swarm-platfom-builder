"""La dimensión del índice sale del modelo de embeddings, no de un ajuste global.

Continuación de #265, y de un hallazgo suyo: `RAG_VECTOR_SIZE` es un ajuste global
con default 768 —la dimensión de `nomic-embed-text`—, así que **no distingue** «lo
he puesto a 768 a propósito» de «nadie lo ha tocado». Con `EMBED_PROVIDER=openai`,
`text-embedding-3-small` devuelve 1536 y `upsert_chunks` hacía esto:

    if vec is None or len(vec) != vector_size:
        vec = _fallback_vector(text, vector_size)   # pseudovector de hash

Cada vector se descartaba y se sustituía por un hash del texto. **Sin error**: la
subida respondía «indexado», el documento aparecía en la biblioteca, y el RAG no
encontraba nada. Eso se descubre semanas después, sin ninguna pista que apunte aquí.

Tres cosas cambian, y las tres se prueban:

* la dimensión **se deriva del modelo** cuando se conoce, porque es un dato del
  modelo y no una preferencia;
* una colección que ya existe con otra dimensión **se avisa** — antes ni se miraba;
* y una dimensión que no cuadra **deja de indexarse**: escribir ruido y responder
  que fue bien es peor que fallar.
"""
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.core import config  # noqa: E402
from app.platform.capabilities import rag  # noqa: E402


@pytest.fixture
def entorno(monkeypatch):
    """Fija proveedor de embeddings, modelo y el ajuste global."""
    def _fijar(proveedor, modelo, configurada=768):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", "anthropic", raising=False)
        monkeypatch.setattr(config.settings, "EMBED_PROVIDER", proveedor, raising=False)
        monkeypatch.setattr(config.settings, "RAG_VECTOR_SIZE", configurada, raising=False)
        clave = "OPENAI_EMBED_MODEL" if proveedor == "openai" else "OLLAMA_EMBED_MODEL"
        monkeypatch.setattr(config.settings, clave, modelo, raising=False)
    return _fijar


# ── La dimensión se deriva del modelo ───────────────────────────────────────

@pytest.mark.parametrize("proveedor,modelo,esperada", [
    ("ollama", "nomic-embed-text", 768),
    ("ollama", "mxbai-embed-large", 1024),
    ("ollama", "all-minilm", 384),
    ("openai", "text-embedding-3-small", 1536),
    ("openai", "text-embedding-3-large", 3072),
    ("openai", "text-embedding-ada-002", 1536),
])
def test_cada_modelo_conocido_declara_su_dimension(entorno, proveedor, modelo, esperada):
    entorno(proveedor, modelo)
    assert rag.embed_vector_size() == esperada


def test_el_caso_que_rompia_ya_no_rompe(entorno):
    """`EMBED_PROVIDER=openai` con el `RAG_VECTOR_SIZE` por defecto: antes indexaba
    todo con pseudovectores; ahora la colección se crea con la dimensión buena."""
    entorno("openai", "text-embedding-3-small", configurada=768)
    assert rag.embed_vector_size() == 1536


def test_la_etiqueta_del_modelo_no_confunde(entorno):
    """Ollama los nombra `nombre:etiqueta`; `nomic-embed-text:latest` es el mismo."""
    entorno("ollama", "nomic-embed-text:latest")
    assert rag.embed_vector_size() == 768


def test_un_modelo_desconocido_respeta_el_ajuste_global(entorno):
    """Un modelo propio o un servidor compatible: no se puede saber su dimensión,
    así que manda lo configurado — el comportamiento anterior."""
    entorno("ollama", "modelo-propio-de-la-casa", configurada=1024)
    assert rag.embed_vector_size() == 1024


def test_cuando_no_coinciden_se_avisa(entorno, caplog):
    """Seguir en silencio es lo que causó el problema."""
    import logging

    entorno("openai", "text-embedding-3-small", configurada=768)
    with caplog.at_level(logging.WARNING):
        rag.embed_vector_size()
    mensajes = [r.getMessage() for r in caplog.records]
    assert any("RAG_VECTOR_SIZE" in m and "reindexa" in m for m in mensajes), mensajes


def test_cuando_coinciden_no_se_avisa(entorno, caplog):
    """Un aviso que sale siempre deja de leerse."""
    import logging

    entorno("ollama", "nomic-embed-text", configurada=768)
    with caplog.at_level(logging.WARNING):
        rag.embed_vector_size()
    assert not [r for r in caplog.records if "RAG_VECTOR_SIZE" in r.getMessage()]


def test_el_modelo_sale_del_proveedor_de_embeddings_y_no_del_de_generacion(entorno):
    """Es la lección de #265: son dos ajustes distintos."""
    entorno("openai", "text-embedding-3-small")
    assert rag.embed_model() == "text-embedding-3-small"
    entorno("ollama", "nomic-embed-text")
    assert rag.embed_model() == "nomic-embed-text"


# ── Una colección que ya existe con otra dimensión ──────────────────────────

@pytest.mark.parametrize("cuerpo,esperado", [
    ({"result": {"config": {"params": {"vectors": {"size": 768, "distance": "Cosine"}}}}}, 768),
    ({"result": {"config": {"params": {"vectors": 1536}}}}, 1536),
    ({"result": {"config": {"params": {"vectors": {"texto": {"size": 768}}}}}}, None),
    ({}, None),
    ({"result": {}}, None),
])
def test_se_lee_la_dimension_de_una_coleccion(cuerpo, esperado):
    """Qdrant devuelve `vectors` de tres formas. Con vectores con nombre no se
    adivina: `None` y sin aviso, que es mejor que avisar de lo que no se entendió."""
    assert rag._collection_vector_size(cuerpo) == esperado


@pytest.mark.asyncio
async def test_una_coleccion_con_otra_dimension_se_avisa(entorno, monkeypatch, caplog):
    """Una colección existente **no se recrea**, así que conserva la dimensión del
    día que se creó. Hasta ahora ni se miraba."""
    import logging

    import httpx

    entorno("openai", "text-embedding-3-small")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "result": {"config": {"params": {"vectors": {"size": 768}}}}
        })

    _mockear_qdrant(monkeypatch, _handler)

    with caplog.at_level(logging.WARNING):
        await rag.ensure_collection("http://qdrant:6333", "c", 1536)

    mensajes = [r.getMessage() for r in caplog.records]
    assert any("no será recuperable" in m for m in mensajes), mensajes
    assert any("768" in m and "1536" in m for m in mensajes), mensajes


@pytest.mark.asyncio
async def test_una_coleccion_con_la_dimension_correcta_no_avisa(entorno, monkeypatch, caplog):
    import logging

    import httpx

    entorno("ollama", "nomic-embed-text")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "result": {"config": {"params": {"vectors": {"size": 768}}}}
        })

    _mockear_qdrant(monkeypatch, _handler)

    with caplog.at_level(logging.WARNING):
        await rag.ensure_collection("http://qdrant:6333", "c", 768)

    assert not [r for r in caplog.records if "recuperable" in r.getMessage()]


# ── Dejar de indexar ruido ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_una_dimension_que_no_cuadra_deja_de_indexarse(entorno, monkeypatch):
    """Antes: pseudovectores de hash y respuesta «indexado». Escribir ruido y decir
    que fue bien es la peor combinación — el dato parece bueno y nadie vuelve."""
    entorno("openai", "text-embedding-3-small")

    async def _otra_dimension(text):
        return [0.1] * 1536

    monkeypatch.setattr(rag, "_get_embedding_openai", _otra_dimension)
    monkeypatch.setattr(rag, "is_qdrant_available", _si)

    with pytest.raises(rag.EmbeddingDimensionMismatch) as error:
        await rag.upsert_chunks(
            qdrant_url="http://qdrant:6333", collection="c", doc_id="d",
            agent_name="a", filename="paper.pdf", chunks=["uno", "dos"],
            ollama_base_url="http://localhost:11434",
            embedding_model="nomic-embed-text", vector_size=768,
        )
    # El mensaje dice qué arreglar, no solo que falló.
    assert "reindexa" in str(error.value)
    assert "paper.pdf" in str(error.value)


@pytest.mark.asyncio
async def test_un_proveedor_caido_sigue_indexando_con_el_respaldo(entorno, monkeypatch, caplog):
    """La distinción de #265, que este cambio **no** puede borrar: una caída es
    transitoria y perder la subida entera por ella sería peor."""
    import logging

    import httpx

    entorno("ollama", "nomic-embed-text")

    async def _sin_respuesta(text, ollama_base_url, model):
        return None

    monkeypatch.setattr(rag, "_get_embedding_ollama", _sin_respuesta)
    monkeypatch.setattr(rag, "is_qdrant_available", _si)
    monkeypatch.setattr(rag.httpx.AsyncClient, "put", _put_ok, raising=False)

    with caplog.at_level(logging.WARNING):
        insertados = await rag.upsert_chunks(
            qdrant_url="http://qdrant:6333", collection="c", doc_id="d",
            agent_name="a", filename="paper.pdf", chunks=["uno"],
            ollama_base_url="http://localhost:11434",
            embedding_model="nomic-embed-text", vector_size=768,
        )

    assert insertados == 1
    assert any("no respondió" in r.getMessage() for r in caplog.records)


# ── Que los llamadores usen la dimensión derivada ───────────────────────────

@pytest.mark.parametrize("modulo", ["app/routers/agents.py", "app/main.py"])
def test_ningun_llamador_crea_colecciones_con_el_ajuste_global(modulo):
    """Costura: dejar uno con `settings.RAG_VECTOR_SIZE` crearía colecciones con la
    dimensión equivocada justo en el camino que este arreglo protege."""
    fuente = (ROOT_DIR / modulo).read_text(encoding="utf-8")
    assert "embed_vector_size()" in fuente, f"{modulo} no usa la dimensión derivada"
    assert "settings.RAG_VECTOR_SIZE" not in fuente, (
        f"{modulo} sigue creando colecciones con el ajuste global"
    )


def test_el_error_de_dimension_se_traduce_a_una_respuesta_accionable():
    """Un 500 con traza no dice qué arreglar, y un 200 sobre ruido miente."""
    fuente = (ROOT_DIR / "app" / "routers" / "agents.py").read_text(encoding="utf-8")
    assert fuente.count("except EmbeddingDimensionMismatch") == 2, (
        "los dos endpoints de subida deben traducir el error"
    )
    assert "status_code=409" in fuente


async def _si(*a, **k):
    return True


async def _put_ok(self, url, json=None, headers=None, **kwargs):
    # Parcheo de un método de instancia: `self` llega como primer posicional.
    class _R:
        status_code = 200

        def json(self):
            return {"result": {"status": "completed"}}

    return _R()


def _mockear_qdrant(monkeypatch, handler):
    import httpx

    transporte = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(rag, "is_qdrant_available", _si)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: original(*a, **{**k, "transport": transporte}),
    )
