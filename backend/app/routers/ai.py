"""AI router: LLM assist, RAG ingest, formatting."""
from uuid import uuid4, UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.platform.project_access import get_project_context
from app.platform.project_context import ProjectContext
from app.models import AIAssistRequest, AIAssistResponse, AIIngestRequest, AIFormatRequest, AIFormatResponse, ScientificFormat
from app.platform.llm import call_llm, get_default_model
from app.platform.capabilities.rag import (
    LIBRARY_AGENT,
    EmbeddingDimensionMismatch,
    chunk_text,
    embed_vector_size,
    ensure_collection,
    upsert_chunks,
)
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/models")
async def list_models(token_data=Depends(get_current_user)):
    """List available models for the configured LLM provider."""
    if settings.LLM_PROVIDER.lower() == "openai":
        # Attempt to list models from the OpenAI API (or compatible endpoint).
        # Falls back to a curated static list if the API key is not configured.
        if settings.OPENAI_API_KEY:
            try:
                from openai import AsyncOpenAI
                client_kwargs: dict = {"api_key": settings.OPENAI_API_KEY}
                if settings.OPENAI_BASE_URL:
                    client_kwargs["base_url"] = settings.OPENAI_BASE_URL
                client = AsyncOpenAI(**client_kwargs)
                resp = await client.models.list()
                await client.close()
                names = sorted(m.id for m in resp.data)
                return {"provider": "openai", "models": names}
            except Exception:
                pass
        return {
            "provider": "openai",
            "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        }

    # Ollama
    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=10.0) as client:
            response = await client.get("/api/tags")
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="Could not query Ollama models")
            models = response.json().get("models", [])
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Ollama unavailable")

    names = [item.get("name") for item in models if item.get("name")]
    return {"provider": "ollama", "models": names}


@router.post("/assist", response_model=AIAssistResponse)
async def assist(
    req: AIAssistRequest,
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Sugerencia de escritura del modelo, **sin recuperación**.

    El nombre y el `sources` de la respuesta prometen RAG; hoy no lo hay: se llama
    al modelo con el prompt tal cual y se devuelve `sources=[]`. Antes se creaba
    aquí la colección del proyecto —una colección que esta ruta no lee—, lo que
    sostenía la apariencia de que sí buscaba. Se ha quitado: es preferible una
    promesa incumplida y visible a una plomería que la disimula. Quien quiera
    recuperación tiene la de los agentes, que es la buena.

    `project` se queda aunque el cuerpo no lo use: es la dependencia que resuelve y
    **autoriza** `X-Project-Id`. Quitarla haría que una petición sin cabecera —o con
    la de un proyecto ajeno— pasara a responder 200.
    """
    model = get_default_model()
    try:
        suggestion = await call_llm(req.user_prompt, model=model, timeout=45.0)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    
    return AIAssistResponse(
        run_id=uuid4(),
        suggestion=suggestion,
        sources=[],
        tokens_used=0,
        status="completed"
    )


@router.post("/ingest")
async def ingest(
    req: AIIngestRequest,
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Indexa texto en el RAG del proyecto activo, por la puerta de siempre.

    Este endpoint tenía plomería propia: troceaba, «vectorizaba» con un histograma
    de caracteres y escribía en Qdrant a mano. Eso no son embeddings —dos textos
    con las mismas letras eran idénticos para él— y aterrizaba en
    `p_<proyecto>__rag_docs`, que es justo la colección donde los agentes buscan
    con embeddings de verdad. Además escribía los puntos **sin `agent_name`**, y
    como toda la recuperación filtra por ese campo, lo indexado no lo veía nadie:
    ocupaba sitio y no aparecía jamás.

    Ahora delega en `platform/capabilities/rag`, que es la única puerta: usa el
    proveedor de embeddings activo, deriva la dimensión del modelo (#322) y
    distingue un proveedor caído —transitorio, se indexa con respaldo— de una
    dimensión que no cuadra, que es configuración y corta.

    Va al bucket compartido `__library__` porque esta ruta no recibe agente, y es
    el único destino desde el que el documento es legible para todos los del
    proyecto en vez de invisible para todos.
    """
    collection = project.collection(settings.QDRANT_COLLECTION)
    chunks = chunk_text(req.text)
    if not chunks:
        raise HTTPException(status_code=422, detail="No hay texto que indexar")

    doc_id = str(uuid4())
    await ensure_collection(
        settings.QDRANT_URL, collection, embed_vector_size(), settings.QDRANT_API_KEY
    )
    # Una dimensión que no cuadra es configuración, no un fallo del servicio: 409
    # con el motivo, en vez de un 200 sobre ruido.
    try:
        count = await upsert_chunks(
            qdrant_url=settings.QDRANT_URL,
            collection=collection,
            doc_id=doc_id,
            agent_name=LIBRARY_AGENT,
            filename=req.source_id,
            chunks=chunks,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            embedding_model=settings.OLLAMA_EMBED_MODEL,
            vector_size=embed_vector_size(),
            api_key=settings.QDRANT_API_KEY,
        )
    except EmbeddingDimensionMismatch as error:
        raise HTTPException(status_code=409, detail=str(error))

    if count <= 0:
        raise HTTPException(status_code=502, detail="No se pudo indexar el texto")

    return {"status": "indexed", "doc_id": doc_id, "chunks_indexed": count}


@router.post("/format", response_model=AIFormatResponse)
async def format_article(req: AIFormatRequest, token_data=Depends(get_current_user)):
    """Format text to scientific standard (APA, IEEE, Vancouver)."""
    format_instructions = {
        ScientificFormat.APA: "Format text as APA style.",
        ScientificFormat.IEEE: "Format text as IEEE style.",
        ScientificFormat.VANCOUVER: "Format text as Vancouver style.",
        ScientificFormat.NONE: "Keep text as is.",
    }
    
    instruction = format_instructions.get(req.format, "Keep text as is.")
    model = get_default_model()

    prompt = f"{instruction}\n\nText:\n{req.text}"

    try:
        formatted_text = await call_llm(prompt, model=model, timeout=45.0)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AIFormatResponse(
        run_id=uuid4(),
        formatted_text=formatted_text,
        status="completed"
    )
