"""`/api/v1/ai/ingest` indexa de verdad, no con un hash del texto (#329).

#322 quitó los pseudovectores de hash del camino bueno. El mismo fallo seguía vivo
en una **segunda puerta**, y esa sí estaba montada y autenticada: `routers/ai.py`
tenía su propio `_vectorize_text`, un histograma de caracteres —dos textos con las
mismas letras eran idénticos para él— y escribía en `p_<proyecto>__rag_docs`, que es
exactamente donde los agentes buscan con embeddings reales.

Lo peor no era el ruido: los puntos se escribían **sin `agent_name`**, y como toda
la recuperación filtra por ese campo, lo indexado no lo veía nadie nunca. El
endpoint respondía `{"status": "queued"}`. Un dato con aspecto de dato, invisible.

Estos casos fijan las tres propiedades del arreglo —embeddings reales, dimensión
derivada del modelo, y que lo ingerido sea legible— y una guardia estructural para
que no reaparezca un tercer vectorizador casero.
"""
import ast
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import app.routers.ai as ai_router
from app.platform.capabilities.rag import LIBRARY_AGENT
from app.platform.project_context import ProjectContext

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROUTER = BACKEND_DIR / "app" / "routers" / "ai.py"

PROYECTO = ProjectContext(project_id=UUID("11111111-1111-1111-1111-111111111111"))


class _Peticion:
    """El DTO de entrada, sin arrastrar la validación de Pydantic al test."""

    def __init__(self, text="Un texto cualquiera para indexar.", source_id="fuente-1"):
        self.article_id = uuid4()
        self.source_id = source_id
        self.text = text


@pytest.fixture
def espia(monkeypatch):
    """Sustituye la capacidad de RAG por un espía: registra con qué se la llama."""
    registro = {}

    async def fake_ensure(qdrant_url, collection, vector_size, api_key=None):
        registro["ensure"] = {"collection": collection, "vector_size": vector_size}

    async def fake_upsert(**kwargs):
        registro["upsert"] = kwargs
        return len(kwargs["chunks"])

    monkeypatch.setattr(ai_router, "ensure_collection", fake_ensure)
    monkeypatch.setattr(ai_router, "upsert_chunks", fake_upsert)
    monkeypatch.setattr(ai_router, "embed_vector_size", lambda: 1536)
    return registro


# ── Que indexe por la puerta buena ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_pasa_por_la_capacidad_de_rag(espia):
    """Si el router vuelve a escribir en Qdrant por su cuenta, el espía no ve nada."""
    await ai_router.ingest(_Peticion(), token_data=None, project=PROYECTO)
    assert "upsert" in espia, "el router no ha pasado por upsert_chunks"


@pytest.mark.asyncio
async def test_lo_ingerido_es_legible_por_los_agentes(espia):
    """El defecto silencioso: sin `agent_name` el punto existe y no lo ve nadie.

    Toda la recuperación filtra por ese campo, así que escribirlo vacío equivale a
    tirar el documento pero respondiendo que se ha indexado.
    """
    await ai_router.ingest(_Peticion(), token_data=None, project=PROYECTO)
    assert espia["upsert"]["agent_name"] == LIBRARY_AGENT


@pytest.mark.asyncio
async def test_escribe_en_el_espacio_del_proyecto(espia):
    """T8.5: la colección se deriva del proyecto, no se recibe."""
    await ai_router.ingest(_Peticion(), token_data=None, project=PROYECTO)
    esperada = PROYECTO.collection("rag_docs")
    assert espia["upsert"]["collection"] == esperada
    assert espia["ensure"]["collection"] == esperada


@pytest.mark.asyncio
async def test_la_dimension_sale_del_modelo_y_no_del_ajuste_global(espia, monkeypatch):
    """#322: `RAG_VECTOR_SIZE` solo manda para modelos que la tabla no conoce.

    Si el router volviera a leerlo, crearía la colección a 768 mientras el
    proveedor produce 1536 — y dejaría al camino bueno chocando con su propio 409.
    """
    from app.core import config
    monkeypatch.setattr(config.settings, "RAG_VECTOR_SIZE", 768, raising=False)
    await ai_router.ingest(_Peticion(), token_data=None, project=PROYECTO)
    assert espia["ensure"]["vector_size"] == 1536
    assert espia["upsert"]["vector_size"] == 1536


# ── Que los errores digan lo que pasa ────────────────────────────────────────

@pytest.mark.asyncio
async def test_una_dimension_que_no_cuadra_es_409_y_no_un_200_sobre_ruido(espia, monkeypatch):
    """Configuración, no fallo del servicio: el llamador puede arreglarlo."""
    from fastapi import HTTPException
    from app.platform.capabilities.rag import EmbeddingDimensionMismatch

    async def revienta(**kwargs):
        raise EmbeddingDimensionMismatch("768 != 1536")

    monkeypatch.setattr(ai_router, "upsert_chunks", revienta)
    with pytest.raises(HTTPException) as error:
        await ai_router.ingest(_Peticion(), token_data=None, project=PROYECTO)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_un_texto_sin_contenido_no_llega_a_qdrant(espia):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await ai_router.ingest(_Peticion(text="   "), token_data=None, project=PROYECTO)
    assert error.value.status_code == 422
    assert "upsert" not in espia


@pytest.mark.asyncio
async def test_la_respuesta_dice_cuanto_se_indexo(espia):
    """`{"status": "queued"}` no era cierto: no había cola, y no se podía leer."""
    respuesta = await ai_router.ingest(_Peticion(), token_data=None, project=PROYECTO)
    assert respuesta["status"] == "indexed"
    assert respuesta["chunks_indexed"] >= 1


# ── Guardia estructural: que no vuelva a aparecer un vectorizador casero ─────

def _funciones_del_modulo(ruta: Path):
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    return [n for n in ast.walk(arbol) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_el_router_no_tiene_su_propio_vectorizador():
    """Se comprueba sobre el AST, no sobre el texto.

    Buscar la cadena «vectorize» daría un falso positivo en cuanto un docstring
    —como el de este módulo— explique el defecto que se arregló. Ya ha pasado tres
    veces en este repo; aquí se mira la forma del código.
    """
    nombres = {f.name for f in _funciones_del_modulo(ROUTER)}
    sospechosas = {n for n in nombres if "vector" in n.lower() or "embed" in n.lower()}
    assert not sospechosas, (
        f"{ROUTER.name} define {sospechosas}: los embeddings los hace "
        "platform/capabilities/rag, que es la única puerta"
    )


def test_ningun_modulo_de_app_construye_vectores_a_mano():
    """El duplicado muerto de `modules/ai/adapters/http.py` hacía justo esto.

    Estaba sin montar, sin autenticación y sin espacio de proyecto: inofensivo
    hasta el día en que alguien lo enchufara. Se borró; esto impide que vuelva.
    """
    culpables = []
    for ruta in (BACKEND_DIR / "app").rglob("*.py"):
        for funcion in _funciones_del_modulo(ruta):
            if "vectorize" in funcion.name.lower():
                culpables.append(f"{ruta.relative_to(BACKEND_DIR)}::{funcion.name}")
    assert not culpables, f"vectorizadores caseros: {culpables}"
