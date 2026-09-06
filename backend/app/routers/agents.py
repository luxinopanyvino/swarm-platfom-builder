"""Agents router: run and monitor agent orchestrations, manage agent prompts."""
import asyncio
import json
import logging
import re
import uuid
import yaml
from uuid import UUID
from pathlib import Path
from typing import Dict, Any

# Slugs that are always protected and cannot be deleted
_PROTECTED_SLUGS: frozenset[str] = frozenset({
    'investigador', 'redactor', 'revisor', 'formateador', 'publicador', 'orquestador',
    'arquitecto', 'backend-dev', 'frontend-dev', 'qa-tester', 'devops', 'code-reviewer',
    'estratega', 'copywriter', 'social-media', 'seo-specialist', 'analista',
    'clasificador', 'agente-soporte', 'escalador', 'resolutor', 'qa-calidad',
    'art-director', 'ui-designer', 'ux-researcher', 'revisor-visual', 'motion-designer',
})

import httpx

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ArticleModel, AgentRunModel, AgentRunRequest,
    AgentRunListResponse, AgentRunDetailResponse,
    AgentProfileModel, AgentProfileResponse,
    AgentRunStepModel, AgentRunStepResponse, ArticleExplainResponse,
)
from app.core.database import get_session
from app.platform.bus import get_bus
from app.platform.audit import AuditAction, record_audit
from app.routers.auth import get_current_user, require_redactor
from app.core.config import settings
from app.core.stream_auth import issue_ticket, consume_ticket
from app.modules.agents.application.use_cases import (
    Orchestrator, active_tasks, publish_event,
    submit_user_decision,
)
from app.platform.capabilities.rag import (
    extract_text, chunk_text, ensure_collection, upsert_chunks,
    list_documents, delete_document, get_rag_backend, backfill_doc_metadata,
    EmbeddingDimensionMismatch, embed_vector_size,
)
from app.modules.agents.adapters.doc_metadata import extract_doc_metadata
from app.platform.project_access import (
    get_optional_project_context, get_project_context, load_project_context,
    usuario_actual,
)
from app.platform.project_context import ProjectContext, bucket_of
from app.platform.projects.profiles import agents_dir as project_agents_dir
from app.platform import explainability

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

logger = logging.getLogger(__name__)


def get_claude_agents_dir() -> Path:
    """Directorio de perfiles del proyecto empaquetado (T8.4).

    Antes probaba `app/agents` y `../app/agents` según el directorio de trabajo.
    """
    return project_agents_dir()


def get_agent_profile_id(filepath: Path) -> str:
    filename = filepath.name
    if filename.endswith(".agent.md"):
        return filename[:-len(".agent.md")]
    return filepath.stem


def read_agent_profile(filepath: Path) -> dict[str, Any]:
    profile_id = get_agent_profile_id(filepath)
    with open(filepath, "r", encoding="utf-8") as file:
        raw = file.read()

    frontmatter: dict[str, Any] = {}
    content = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except Exception:
                frontmatter = {}
            content = parts[2]

    return {
        "id": profile_id,
        "name": profile_id.replace("-", " ").title(),
        "frontmatter": frontmatter,
        "content": content.lstrip(),
    }


def resolve_agent_rag_settings(
    agent_name: str,
    settings: Any,
    overrides: dict[str, Any] | None = None,
    project: ProjectContext | None = None,
) -> dict[str, Any]:
    """Ajustes RAG de un agente, con la colección **resuelta dentro del proyecto**.

    `rag_collection` es un campo que escribe la persona usuaria en el editor de
    agentes. Antes se usaba tal cual como nombre de colección de Qdrant, así que
    bastaba con teclear la del proyecto vecino para leer sus documentos. Ahora es
    un *bucket* y el proyecto compone el nombre real (SPEC-013 / T8.5 / AC6).
    """
    collection = settings.QDRANT_COLLECTION
    chunk_size = 500
    chunk_overlap = 50

    agents_dir = get_claude_agents_dir()
    profile_path = agents_dir / f"{agent_name}.agent.md"
    if profile_path.exists():
        profile = read_agent_profile(profile_path)
        frontmatter = profile["frontmatter"]
        collection = frontmatter.get("rag_collection", collection)
        chunk_size = int(frontmatter.get("rag_chunk_size", chunk_size) or chunk_size)
        chunk_overlap = int(frontmatter.get("rag_chunk_overlap", chunk_overlap) or chunk_overlap)

    if overrides:
        if overrides.get("rag_collection"):
            collection = str(overrides["rag_collection"]).strip()
        if overrides.get("rag_chunk_size") is not None:
            chunk_size = int(overrides["rag_chunk_size"])
        if overrides.get("rag_chunk_overlap") is not None:
            chunk_overlap = int(overrides["rag_chunk_overlap"])

    chunk_size = max(100, min(chunk_size, 4000))
    chunk_overlap = max(0, min(chunk_overlap, max(chunk_size - 1, 0)))

    return {
        "collection": project.collection(collection) if project else collection,
        "bucket": collection,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }


@router.get("/definitions")
async def get_agent_definitions(token_data=Depends(get_current_user)):
    """Get static descriptions of standard system agents."""
    return {
        "investigador": { "id": "investigador", "name": "Investigador", "description": "Busca contexto en Qdrant RAG y APIs científicas." },
        "redactor": { "id": "redactor", "name": "Redactor", "description": "Genera borrador académico en Markdown usando Ollama." },
        "revisor": { "id": "revisor", "name": "Revisor", "description": "Evalúa el borrador con un score 0-100 y genera feedback." },
        "formateador": { "id": "formateador", "name": "Formateador", "description": "Reformatea citas en APA, IEEE o Vancouver." },
        "publicador": { "id": "publicador", "name": "Publicador", "description": "Guarda el artículo final en DB y lo marca como PUBLISHED." }
    }


@router.get("/claude-defs", response_model=list[AgentProfileResponse])
async def get_claude_agent_definitions(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
    token_data=Depends(get_current_user),
):
    """Return all agent profiles for a given project."""
    result = await session.execute(
        select(AgentProfileModel)
        .where(AgentProfileModel.project_id == project_id)
        .order_by(AgentProfileModel.is_builtin.desc(), AgentProfileModel.created_at)
    )
    return result.scalars().all()


@router.get("/rag/collections")
async def get_rag_collections_overview(
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Documentos RAG del **proyecto activo** en el almacén local (sin Qdrant).

    Recorría todo el almacén y devolvía las colecciones de todos los proyectos:
    cualquier sesión autenticada veía los nombres de fichero de los demás. Ahora
    solo se listan las colecciones del proyecto (SPEC-013 / T8.5 / AC6).
    """
    from app.platform.capabilities.rag import _local_rag_root, _load_local_document

    rag_root = _local_rag_root()
    collections: dict[str, dict[str, Any]] = {}

    for collection_dir in sorted(rag_root.iterdir()):
        if not collection_dir.is_dir():
            continue
        if not project.owns_collection(collection_dir.name):
            continue
        collection_name = bucket_of(collection_dir.name)

        for doc_path in sorted(collection_dir.glob("*.json")):
            data = _load_local_document(doc_path)
            if not data:
                continue
            agent_name = data.get("agent_name", "unknown")
            doc_entry = {
                "doc_id": data.get("doc_id", doc_path.stem),
                "filename": data.get("filename", doc_path.stem),
                "agent_name": agent_name,
                "chunks": len(data.get("chunks", [])),
            }

            bucket = collections.setdefault(collection_name, {
                "name": collection_name,
                "agents": {},
                "total_documents": 0,
                "total_chunks": 0,
            })
            agent_bucket = bucket["agents"].setdefault(agent_name, {
                "id": agent_name,
                "name": agent_name.replace("-", " ").title(),
                "rag_enabled": True,
                "documents": [],
            })
            agent_bucket["documents"].append(doc_entry)
            bucket["total_documents"] += 1
            bucket["total_chunks"] += doc_entry["chunks"]

    # Convert agents dict to list for each collection
    result = []
    for col in collections.values():
        col["agents"] = list(col["agents"].values())
        result.append(col)

    return {
        "collections": sorted(result, key=lambda item: item["name"]),
        "storage_backend": "local",
    }


@router.get("/rag/library")
async def get_rag_library(
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Documentos del **proyecto activo** en Qdrant (o en el respaldo local).

    Igual que `/rag/collections`: antes recorría todas las colecciones de la
    instancia (SPEC-013 / T8.5 / AC6).
    """
    from app.core.config import settings
    from app.platform.capabilities.rag import list_library_documents

    docs = await list_library_documents(settings.QDRANT_URL, settings.QDRANT_API_KEY)

    # Group by collection
    collections: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if not project.owns_collection(doc["collection"]):
            continue
        col_name = bucket_of(doc["collection"])
        bucket = collections.setdefault(col_name, {
            "name": col_name,
            "documents": [],
            "total_chunks": 0,
        })
        bucket["documents"].append({
            "doc_id": doc["doc_id"],
            "filename": doc["filename"],
            "agent_name": doc.get("agent_name", ""),
            "chunks": doc["chunks"],
        })
        bucket["total_chunks"] += doc["chunks"]

    result = sorted(collections.values(), key=lambda c: c["name"])
    backend = await get_rag_backend(settings.QDRANT_URL, settings.QDRANT_API_KEY)
    return {"collections": result, "storage_backend": backend}


@router.post("/rag/library/upload")
async def upload_to_rag_library(
    file: UploadFile = File(...),
    collection: str | None = Form(None),
    chunk_size: int | None = Form(None),
    chunk_overlap: int | None = Form(None),
    token_data=Depends(require_redactor),
    project: ProjectContext = Depends(get_project_context),
):
    """Sube un documento a la biblioteca compartida **del proyecto activo**.

    «Compartida» significa compartida entre los agentes del proyecto, no entre
    proyectos: antes era una sola biblioteca global y el bucket `__library__` se
    leía desde cualquier pipeline (SPEC-013 / T8.5 / AC6).
    """
    from app.core.config import settings

    from app.platform.uploads import validate_upload

    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx. 10 MB)")

    # Extension + real content (magic bytes), before any parsing or indexing.
    validate_upload(file.filename, raw, {".txt", ".md", ".pdf"})

    text = extract_text(file.filename, raw)
    if not text.strip():
        raise HTTPException(status_code=422, detail="No se pudo extraer texto del archivo")

    col = project.collection(collection or settings.QDRANT_COLLECTION)
    c_size = max(100, min(int(chunk_size or settings.RAG_CHUNK_SIZE if hasattr(settings, "RAG_CHUNK_SIZE") else 500), 4000))
    c_overlap = max(0, min(int(chunk_overlap or 50), 499))

    doc_id = str(uuid.uuid4())
    metadata = extract_doc_metadata(file.filename, raw, text)
    chunks = chunk_text(text, chunk_size=c_size, overlap=c_overlap)

    await ensure_collection(settings.QDRANT_URL, col, embed_vector_size(), settings.QDRANT_API_KEY)
    # Una dimensión que no cuadra es configuración, no un fallo del servicio: 409
    # con el motivo, en vez de un 500 con traza o —peor— un 200 sobre ruido.
    try:
        count = await upsert_chunks(
            qdrant_url=settings.QDRANT_URL,
            collection=col,
            doc_id=doc_id,
            agent_name="__library__",
            filename=file.filename,
            chunks=chunks,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            embedding_model=settings.OLLAMA_EMBED_MODEL,
            vector_size=embed_vector_size(),
            api_key=settings.QDRANT_API_KEY,
            doc_title=metadata.get("title", ""),
            doc_authors=metadata.get("authors", ""),
        )
    except EmbeddingDimensionMismatch as error:
        raise HTTPException(status_code=409, detail=str(error))

    if count <= 0:
        raise HTTPException(status_code=502, detail="No se pudo indexar el documento")

    return {
        "status": "indexed",
        "doc_id": doc_id,
        "filename": file.filename,
        "collection": bucket_of(col),
        "chunks": count,
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", ""),
    }


@router.post("/rag/backfill-metadata")
async def backfill_rag_metadata(
    collection: str | None = None,
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Re-derive title/authors for existing documents that were ingested before
    metadata extraction existed (text heuristic only; re-upload for PDF metadata).

    Reescribe payloads, así que la colección se resuelve dentro del proyecto: sin
    eso, `?collection=` de otro proyecto permitía tocar sus documentos.
    """
    from app.core.config import settings
    col = project.collection(collection or settings.QDRANT_COLLECTION)
    updated = await backfill_doc_metadata(settings.QDRANT_URL, col, settings.QDRANT_API_KEY)
    return {"collection": bucket_of(col), "updated": len(updated), "documents": updated}


@router.delete("/rag/library/{collection}/{doc_id}")
async def delete_from_rag_library(
    collection: str,
    doc_id: str,
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Borra un documento de la biblioteca del **proyecto activo**.

    El nombre de la ruta es el *bucket*; la colección real la compone el proyecto.
    Antes se pasaba tal cual a Qdrant, así que un `DELETE` con la colección del
    vecino borraba sus documentos (SPEC-013 / T8.5 / AC6).
    """
    from app.core.config import settings
    import re as _re

    # Sanitize path parameters to prevent traversal / injection
    if not _re.match(r"^[a-zA-Z0-9_\-]+$", collection):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    if not _re.match(r"^[a-zA-Z0-9_\-]+$", doc_id):
        raise HTTPException(status_code=422, detail="Invalid doc_id")

    ok = await delete_document(
        settings.QDRANT_URL, project.collection(collection), doc_id, settings.QDRANT_API_KEY
    )
    if not ok:
        raise HTTPException(status_code=500, detail="No se pudo eliminar el documento")
    return {"status": "deleted", "doc_id": doc_id}


@router.get("/tools")
async def list_available_tools(_token=Depends(get_current_user)):
    """Return the catalog of available tools for agent tool calling."""
    from app.platform.capabilities.tools import TOOL_CATALOG
    return {"tools": TOOL_CATALOG}


@router.put("/claude-defs/{agent_id}", response_model=AgentProfileResponse)
async def update_claude_agent_definition(
    agent_id: UUID,
    payload: dict,
    session: AsyncSession = Depends(get_session),
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Update an agent profile by its UUID, **dentro del proyecto activo**.

    Buscaba solo por id, sin comprobar a qué proyecto pertenece: con el
    identificador de un perfil ajeno se podía reescribir su prompt, su modelo o su
    `rag_collection` (SPEC-013 / T8.5 / AC6). El 404 en vez de 403 es deliberado:
    un 403 confirmaría que ese identificador existe.
    """
    result = await session.execute(
        select(AgentProfileModel).where(
            AgentProfileModel.id == agent_id,
            AgentProfileModel.project_id == project.project_id,
        )
    )
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    content = payload.get("content", "")

    # Detect JSON params vs markdown content
    is_json = False
    params = {}
    try:
        if content.strip().startswith("{") and content.strip().endswith("}"):
            params = json.loads(content)
            is_json = True
    except Exception:
        pass

    if is_json:
        for key in ("model", "temperature", "rag_enabled", "rag_collection",
                    "rag_chunk_size", "rag_chunk_overlap", "rag_doc_ids",
                    "graph_rag_enabled", "semantic_search_enabled",
                    "tools_enabled", "tools", "prompt_template",
                    "scientific_format", "output_language", "target_word_count"):
            if key in params:
                setattr(agent, key, params[key])
    else:
        agent.content = content

    await session.commit()
    await session.refresh(agent)
    return agent


@router.post("/claude-defs", response_model=AgentProfileResponse, status_code=201)
async def create_claude_agent_definition(
    payload: dict,
    session: AsyncSession = Depends(get_session),
    token_data=Depends(get_current_user),
):
    """Create a new custom agent profile in the given project."""
    project_id_raw = payload.get("project_id")
    if not project_id_raw:
        raise HTTPException(status_code=422, detail="project_id is required")
    try:
        project_id = UUID(str(project_id_raw))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid project_id")

    slug = payload.get("name", "").strip().lower()
    if not slug or not re.match(r"^[a-z0-9_-]+$", slug):
        raise HTTPException(status_code=422, detail="name must be lowercase alphanumeric/hyphens/underscores")

    # Check duplicate slug within project
    existing = await session.execute(
        select(AgentProfileModel).where(
            AgentProfileModel.project_id == project_id,
            AgentProfileModel.slug == slug,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Agent '{slug}' already exists in this project")

    description = payload.get("description", f"Agente personalizado: {slug}")
    body = f"# {slug.capitalize()}\n\n## Rol\n{description}\n\n## Dominio\nDefinido por el usuario.\n\n## Salida esperada\nDefinida por el usuario.\n"

    agent = AgentProfileModel(
        project_id=project_id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        content=body,
        model=payload.get("model", "llama3.2:1b"),
        temperature=float(payload.get("temperature", 0.7)),
        rag_enabled=bool(payload.get("rag_enabled", False)),
        rag_collection=payload.get("rag_collection", "rag_docs"),
        rag_chunk_size=max(100, min(int(payload.get("rag_chunk_size", 500)), 4000)),
        rag_chunk_overlap=max(0, min(int(payload.get("rag_chunk_overlap", 50)), 499)),
        prompt_template=payload.get("prompt_template", ""),
        is_builtin=False,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.delete("/claude-defs/{agent_id}", status_code=204)
async def delete_claude_agent_definition(
    agent_id: UUID,
    session: AsyncSession = Depends(get_session),
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Delete a custom agent profile. Built-in agents cannot be deleted.

    Mismo motivo que en la edición: sin el filtro por proyecto, el id de un perfil
    ajeno bastaba para borrarlo.
    """
    result = await session.execute(
        select(AgentProfileModel).where(
            AgentProfileModel.id == agent_id,
            AgentProfileModel.project_id == project.project_id,
        )
    )
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.slug in _PROTECTED_SLUGS:
        raise HTTPException(status_code=403, detail="Built-in agents cannot be deleted")
    await session.delete(agent)
    await session.commit()


@router.get("/models")
async def get_available_models(token_data=Depends(get_current_user)):
    """List models available for the configured LLM provider."""
    from app.core.config import settings
    if settings.LLM_PROVIDER.lower() == "openai":
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
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return {"provider": "ollama", "models": models or ["llama3.2:1b"]}
    except Exception:
        pass
    return {"provider": "ollama", "models": ["llama3.2:1b", "llama3.1", "mistral", "gemma2", "phi3"]}


@router.post("/{agent_name}/rag/upload")
async def upload_rag_document(
    agent_name: str,
    file: UploadFile = File(...),
    rag_collection: str | None = Form(None),
    rag_chunk_size: int | None = Form(None),
    rag_chunk_overlap: int | None = Form(None),
    token_data=Depends(require_redactor),
    project: ProjectContext = Depends(get_project_context),
):
    """Indexa un fichero en el bucket RAG de un agente **del proyecto activo**."""
    from app.core.config import settings

    from app.platform.uploads import validate_upload

    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:  # 10 MB guard
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    # Extension + real content (magic bytes), before any parsing or indexing.
    validate_upload(file.filename, raw, {".txt", ".md", ".pdf"})

    text = extract_text(file.filename, raw)
    if not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from file")

    doc_id = str(uuid.uuid4())
    metadata = extract_doc_metadata(file.filename, raw, text)

    rag_settings = resolve_agent_rag_settings(agent_name, settings, {
        "rag_collection": rag_collection,
        "rag_chunk_size": rag_chunk_size,
        "rag_chunk_overlap": rag_chunk_overlap,
    }, project=project)
    chunks = chunk_text(text, chunk_size=rag_settings["chunk_size"], overlap=rag_settings["chunk_overlap"])
    collection = rag_settings["collection"]

    await ensure_collection(settings.QDRANT_URL, collection, embed_vector_size(), settings.QDRANT_API_KEY)

    try:
        count = await upsert_chunks(
            qdrant_url=settings.QDRANT_URL,
            collection=collection,
            doc_id=doc_id,
            agent_name=agent_name,
            filename=file.filename,
            chunks=chunks,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            embedding_model=settings.OLLAMA_EMBED_MODEL,
            vector_size=embed_vector_size(),
            api_key=settings.QDRANT_API_KEY,
            doc_title=metadata.get("title", ""),
            doc_authors=metadata.get("authors", ""),
        )
    except EmbeddingDimensionMismatch as error:
        raise HTTPException(status_code=409, detail=str(error))

    if count <= 0:
        raise HTTPException(status_code=502, detail="No se pudo indexar el documento en Qdrant")

    logger.info(
        "Ingested '%s' — title=%r authors=%r",
        file.filename, metadata.get("title", ""), metadata.get("authors", ""),
    )
    return {
        "status": "indexed",
        "doc_id": doc_id,
        "filename": file.filename,
        "chunks": count,
        "collection": rag_settings["bucket"],
        "chunk_size": rag_settings["chunk_size"],
        "chunk_overlap": rag_settings["chunk_overlap"],
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", ""),
    }


@router.get("/{agent_name}/rag/documents")
async def list_rag_documents(
    agent_name: str,
    token_data=Depends(get_current_user),
    project: ProjectContext = Depends(get_project_context),
):
    """Documentos RAG del agente **dentro del proyecto activo**."""
    from app.core.config import settings

    rag_settings = resolve_agent_rag_settings(agent_name, settings, project=project)
    collection = rag_settings["collection"]
    backend = await get_rag_backend(settings.QDRANT_URL, settings.QDRANT_API_KEY)

    docs = await list_documents(settings.QDRANT_URL, collection, agent_name, settings.QDRANT_API_KEY)
    return {
        "documents": docs,
        "collection": rag_settings["bucket"],
        "chunk_size": rag_settings["chunk_size"],
        "chunk_overlap": rag_settings["chunk_overlap"],
        "storage_backend": backend,
    }


@router.delete("/{agent_name}/rag/documents/{doc_id}")
async def delete_rag_document(
    agent_name: str,
    doc_id: str,
    request: Request,
    token_data=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    project: ProjectContext = Depends(get_project_context),
):
    """Borra los puntos de un documento RAG **del proyecto activo**."""
    from app.core.config import settings

    rag_settings = resolve_agent_rag_settings(agent_name, settings, project=project)
    collection = rag_settings["collection"]

    ok = await delete_document(settings.QDRANT_URL, collection, doc_id, settings.QDRANT_API_KEY)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete document from the configured RAG store")

    # El borrado ya ocurrió en Qdrant y no es reversible desde aquí, así que la
    # auditoría se persiste por su cuenta: sin commit propio, un fallo posterior de
    # la petición dejaría el documento borrado y sin rastro de quién lo hizo.
    await record_audit(
        session,
        action=AuditAction.RAG_DOCUMENT_DELETED,
        actor=token_data,
        target_type="rag_document",
        target_id=doc_id,
        request=request,
        commit=True,
        detail={"agent": agent_name, "collection": collection},
    )
    return {"status": "deleted", "doc_id": doc_id}


@router.post("/{article_id}/run")
async def run_agent_pipeline(
    article_id: UUID,
    req: AgentRunRequest,
    background_tasks: BackgroundTasks,
    resume: bool = False,
    token_data=Depends(require_redactor),
    session: AsyncSession = Depends(get_session)
):
    """
    Run the multi-agent pipeline on a specific article in the background.
    Compiles and executes LangGraph nodes in the requested flow sequence.

    When ``resume`` is true, the run continues from the last persisted checkpoint
    (the agent that previously failed) instead of restarting from the first agent.
    """
    # Verify article exists and belongs to the authenticated author
    stmt = select(ArticleModel).where(ArticleModel.id == article_id)
    res = await session.execute(stmt)
    article = res.scalars().first()
    
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
        
    if str(article.author_id) != token_data["user_id"]:
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this article")

    # El proyecto de la ejecución sale del **artículo**, no de la cabecera: el
    # aislamiento de AC6 no puede depender de lo que mande el cliente. Sin
    # proyecto no hay dónde aislar, así que el artículo huérfano no se ejecuta.
    if article.project_id is None:
        raise HTTPException(
            status_code=409,
            detail="El artículo no pertenece a ningún proyecto; asígnalo antes de ejecutar el pipeline",
        )
    usuario = await usuario_actual(session, token_data)
    project = await load_project_context(session, usuario, article.project_id)
        
    # Use provided keywords if given; otherwise extract from title as fallback
    title = article.title
    if req.keywords:
        keywords = req.keywords
    else:
        keywords = [w.strip(".,;:?!") for w in title.lower().split() if len(w) > 4]
        if not keywords:
            keywords = ["scientific", "research"]

    # Prepend context description to the initial research data if provided
    if req.context_description:
        # Inject into agent_settings so investigador can use it
        req.agent_settings.setdefault("investigador", {})
        existing = req.agent_settings["investigador"].get("context_hint", "")
        if not existing:
            req.agent_settings["investigador"]["context_hint"] = req.context_description

    # Load stored agent profiles and merge into agent_settings as fallback.
    # This ensures persisted values (e.g. target_word_count, output_language)
    # are used even when the frontend doesn't forward them explicitly.
    agent_slugs = list(req.flow_sequence)
    # `slug` no es único entre proyectos: dos proyectos creados desde la misma
    # plantilla tienen ambos un `investigador`. Sin el filtro por proyecto, esta
    # consulta traía el perfil de **cualquiera** de los dos —con su modelo, su
    # prompt, su `rag_collection` y sus `rag_doc_ids`— según el orden que
    # devolviera la base (SPEC-013 / T8.5 / AC6).
    profiles_stmt = select(AgentProfileModel).where(
        AgentProfileModel.slug.in_(agent_slugs),
        AgentProfileModel.project_id == project.project_id,
    )
    profiles_res = await session.execute(profiles_stmt)
    for profile in profiles_res.scalars().all():
        slug = profile.slug
        req.agent_settings.setdefault(slug, {})
        s = req.agent_settings[slug]
        if "model" not in s and profile.model:
            s["model"] = profile.model
        if "temperature" not in s and profile.temperature is not None:
            s["temperature"] = profile.temperature
        if "output_language" not in s and profile.output_language:
            s["output_language"] = profile.output_language
        if "scientific_format" not in s and profile.scientific_format:
            s["scientific_format"] = profile.scientific_format
        if "target_word_count" not in s and profile.target_word_count:
            s["target_word_count"] = profile.target_word_count
        if "prompt_template" not in s and profile.prompt_template:
            s["prompt_template"] = profile.prompt_template
        if "rag_collection" not in s and profile.rag_collection:
            s["rag_collection"] = profile.rag_collection
        if "rag_doc_ids" not in s and profile.rag_doc_ids:
            s["rag_doc_ids"] = profile.rag_doc_ids
        if "graph_rag_enabled" not in s and profile.graph_rag_enabled is not None:
            s["graph_rag_enabled"] = profile.graph_rag_enabled
        if "semantic_search_enabled" not in s and profile.semantic_search_enabled is not None:
            s["semantic_search_enabled"] = profile.semantic_search_enabled
        if "tools_enabled" not in s and profile.tools_enabled:
            s["tools_enabled"] = profile.tools_enabled
        if "tools" not in s and profile.tools:
            s["tools"] = profile.tools

    # The user's document selection is attached to the investigador. Propagate it
    # to every other agent in the flow so RAG-enabled agents (redactor, generic…)
    # honour the same selection instead of searching the whole knowledge base and
    # pulling unselected documents (e.g. the seeded welcome doc) into the article.
    investigador_cfg = req.agent_settings.get("investigador", {})
    selected_doc_ids = investigador_cfg.get("rag_doc_ids")
    selected_collection = investigador_cfg.get("rag_collection")
    if selected_doc_ids:
        for slug in agent_slugs:
            s = req.agent_settings.setdefault(slug, {})
            s.setdefault("rag_doc_ids", selected_doc_ids)
            if selected_collection:
                s.setdefault("rag_collection", selected_collection)

    scientific_format = article.scientific_format.value if article.scientific_format else "apa"
    
    # Run Orchestrator flow as a background task to allow SSE to immediately stream
    background_tasks.add_task(
        Orchestrator.run,
        article_id=article_id,
        author_id=article.author_id,
        project=project,
        title=title,
        keywords=keywords,
        scientific_format=scientific_format,
        flow_sequence=req.flow_sequence,
        agent_settings=req.agent_settings,
        context_description=req.context_description,
        # Pass existing body so re-runs (e.g. only revisor+formateador) start with
        # the already-generated content instead of an empty draft.
        initial_draft_text=article.body or "",
        article_outline=req.article_outline,
        resume=resume,
    )

    action = "resumed" if resume else "started"
    return {"status": "accepted", "message": f"Agent execution pipeline {action}", "resumed": resume}


@router.delete("/{article_id}/run", status_code=200)
async def cancel_pipeline_run(
    article_id: UUID,
    token_data=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Cancel an in-progress pipeline run. The article remains in DRAFT."""
    # Verify ownership
    stmt = select(ArticleModel).where(ArticleModel.id == article_id)
    res = await session.execute(stmt)
    article = res.scalars().first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if str(article.author_id) != token_data["user_id"] and token_data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    task = active_tasks.get(article_id)
    if task is not None and not task.done():
        task.cancel()
        return {"status": "cancelled", "article_id": str(article_id)}

    # La tarea no está en este worker. Con el bus en proceso eso significa que no
    # existe; con Redis, que la ejecuta otro worker y hay que pedírselo por señal.
    if not await get_bus().request_cancel(article_id):
        raise HTTPException(status_code=409, detail="No active pipeline for this article")

    return {"status": "cancelled", "article_id": str(article_id)}


class PipelineDecisionDTO(BaseModel):
    # "add_source" → re-run research with a newly uploaded document
    # "continue"   → proceed with the current draft
    decision: str


@router.post("/{article_id}/decision", status_code=200)
async def submit_pipeline_decision(
    article_id: UUID,
    req: PipelineDecisionDTO,
    token_data=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Resolve a pending human-in-the-loop decision for a paused pipeline."""
    stmt = select(ArticleModel).where(ArticleModel.id == article_id)
    res = await session.execute(stmt)
    article = res.scalars().first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if str(article.author_id) != token_data["user_id"] and token_data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    decision = (req.decision or "").strip().lower()
    if decision not in ("add_source", "continue"):
        raise HTTPException(status_code=422, detail="decision must be 'add_source' or 'continue'")

    if not await submit_user_decision(article_id, decision):
        raise HTTPException(status_code=409, detail="No pending decision for this article")

    return {"status": "ok", "decision": decision}



@router.get("/{article_id}/runs", response_model=AgentRunListResponse)
async def get_article_agent_runs(
    article_id: UUID,
    token_data=Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
):
    """
    Retrieve history logs of agent executions for a specific article.
    """
    # Verify ownership of the article
    stmt = select(ArticleModel).where(ArticleModel.id == article_id)
    res = await session.execute(stmt)
    article = res.scalars().first()
    
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
        
    if str(article.author_id) != token_data["user_id"] and token_data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this article")
        
    # Query agent execution history
    stmt_runs = select(AgentRunModel).where(AgentRunModel.article_id == article_id).order_by(AgentRunModel.started_at.desc())
    res_runs = await session.execute(stmt_runs)
    runs = res_runs.scalars().all()
    
    return AgentRunListResponse(
        runs=[AgentRunDetailResponse.model_validate(r) for r in runs]
    )


@router.get("/{article_id}/explain", response_model=ArticleExplainResponse)
async def explain_article_run(
    article_id: UUID,
    scope: str = "last",
    token_data=Depends(get_current_user),
    project: ProjectContext | None = Depends(get_optional_project_context),
    session: AsyncSession = Depends(get_session),
):
    """Traza de explicabilidad del artículo (SPEC-014 / T9.2 / AC2).

    Contesta «por qué salió esto»: qué agentes corrieron y en qué orden, con qué
    modelo y parámetros, qué documentos recuperaron y con qué puntuación, qué
    decidió el revisor y por qué, y qué costó cada paso.

    `scope=last` (por defecto) devuelve **la ejecución que produjo el texto que
    se está leyendo**, que es la pregunta que trae a alguien al panel. Un
    artículo reejecutado tiene varias en la misma tabla, y mezclarlas contaría
    una historia que no ocurrió; `scope=all` las devuelve todas para auditar.
    """
    if scope not in ("last", "all"):
        raise HTTPException(status_code=400, detail="scope debe ser 'last' o 'all'")

    # Mismo control de acceso que `/runs`: la unidad de acceso es el artículo, y
    # la traza no dice nada que su dueño no pueda ver ya. Deliberadamente **no**
    # se exige `X-Project-Id`: los endpoints de artículo no la piden en este
    # repo, y volverla obligatoria solo aquí es una trampa para el siguiente
    # cliente. Pero si viene, se comprueba —ver más abajo—, que es lo que hace
    # `get_optional_project_context`: tolerar la ausencia no es tolerar que
    # alguien apunte a un proyecto que no es suyo.
    res = await session.execute(select(ArticleModel).where(ArticleModel.id == article_id))
    article = res.scalars().first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if str(article.author_id) != token_data["user_id"] and token_data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this article")

    # Con proyecto activo, el artículo tiene que ser de ese proyecto. 404 y no
    # 403: la respuesta no debe distinguir «existe pero es de otro proyecto» de
    # «no existe». Un artículo heredado (sin proyecto) no se bloquea por esto.
    if project is not None and article.project_id is not None:
        if article.project_id != project.project_id:
            raise HTTPException(status_code=404, detail="Article not found")

    # Cronológico: es el orden en el que ocurrieron. `step_index` desempata dos
    # pasos con la misma marca de tiempo, que en SQLite es posible.
    stmt = (
        select(AgentRunStepModel)
        .where(AgentRunStepModel.article_id == article_id)
        .order_by(AgentRunStepModel.created_at.asc(), AgentRunStepModel.step_index.asc())
    )
    pasos = list((await session.execute(stmt)).scalars().all())

    ejecuciones = explainability.group_executions(pasos)
    visibles = ejecuciones[-1] if (scope == "last" and ejecuciones) else pasos

    return ArticleExplainResponse(
        article_id=article_id,
        title=article.title,
        executions=len(ejecuciones),
        scope=scope,
        # Que el artículo exista no implica que haya traza: puede ser anterior a
        # T9.1 o estar purgada por retención. La interfaz necesita distinguirlo
        # de «no se ha ejecutado nunca», que se arregla de otra manera.
        available=bool(visibles),
        steps=[AgentRunStepResponse.model_validate(paso) for paso in visibles],
        sources=explainability.aggregate_sources(visibles),
        totals=explainability.totals_of(visibles),
    )


@router.post("/{article_id}/stream-ticket")
async def create_stream_ticket(
    article_id: UUID,
    token_data=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Issue a short-lived, single-use ticket to authenticate an SSE connection.

    The browser ``EventSource`` API cannot send an ``Authorization`` header, so
    instead of leaking the JWT in the stream query string (T1.4) the client
    exchanges its Bearer token here for an opaque ticket.
    """
    stmt = select(ArticleModel).where(ArticleModel.id == article_id)
    res = await session.execute(stmt)
    article = res.scalars().first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if str(article.author_id) != token_data["user_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    ticket = await issue_ticket(token_data["user_id"], str(article_id), settings.SSE_TICKET_TTL_SECONDS)
    return {"ticket": ticket, "expires_in": settings.SSE_TICKET_TTL_SECONDS}


@router.get("/{article_id}/stream")
async def stream_agent_runs(
    article_id: UUID,
    ticket: str | None = None,
    session: AsyncSession = Depends(get_session)
):
    """SSE endpoint for real-time monitoring of active agent runs.

    Authenticated with a single-use ticket from ``POST /{id}/stream-ticket``;
    the JWT is never placed in the query string (T1.4).
    """
    user_id = await consume_ticket(ticket, str(article_id)) if ticket else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or missing stream ticket")

    # Verify ownership of the article
    stmt = select(ArticleModel).where(ArticleModel.id == article_id)
    res = await session.execute(stmt)
    article = res.scalars().first()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if str(article.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    async def event_generator():
        # La suscripción va por el bus: con varios workers, este cliente puede estar
        # conectado a un proceso distinto del que ejecuta el pipeline, y aun así
        # recibe todos los eventos (SPEC-018/T4.3). El `async with` se encarga de
        # darse de baja cuando el cliente se desconecta.
        async with get_bus().subscribe(article_id) as q:
            try:
                while True:
                    event = await q.get()
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        break
            except asyncio.CancelledError:
                # client disconnected — re-raise so the generator is properly cancelled
                raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")
