"""RAG utilities: text extraction, chunking, embedding and Qdrant persistence."""
import asyncio
import hashlib
import io
import json
import logging
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx

logger = logging.getLogger(__name__)


def _local_rag_root() -> Path:
    for candidate in (Path("app/.rag_local"), Path("../app/.rag_local")):
        if candidate.parent.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    root = Path("app/.rag_local")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_collection_dir(collection: str) -> Path:
    # Sanitize: strip path separators, dots sequences, and control chars
    safe_name = collection.replace("/", "_").replace("\\", "_").strip()
    # Reject path traversal attempts (e.g. "..", "...", "../x")
    if not safe_name or safe_name in (".", "..") or ".." in safe_name.split("_"):
        safe_name = "rag_docs"
    # Only allow alphanumeric, hyphens, underscores
    import re as _re
    safe_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", safe_name) or "rag_docs"
    directory = _local_rag_root() / safe_name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _local_document_path(collection: str, doc_id: str) -> Path:
    return _local_collection_dir(collection) / f"{doc_id}.json"


def _load_local_document(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        logger.warning(f"Could not read local RAG document '{path}': {error}")
        return None


# Shared bucket where library-wide documents are stored (not scoped to one agent).
LIBRARY_AGENT = "__library__"


def _agent_name_clause(agent_name) -> Dict[str, Any]:
    """Build a Qdrant filter clause matching one or several agent buckets.

    ``agent_name`` may be a single string or a list of strings. Multiple names
    are matched with ``any`` so an agent can read both its own documents and the
    shared library.
    """
    names = [agent_name] if isinstance(agent_name, str) else list(agent_name)
    names = [n for n in names if n]
    if len(names) == 1:
        return {"key": "agent_name", "match": {"value": names[0]}}
    return {"key": "agent_name", "match": {"any": names}}


def _agent_name_matches(value: Optional[str], agent_name) -> bool:
    """Local-store equivalent of ``_agent_name_clause`` membership test."""
    names = [agent_name] if isinstance(agent_name, str) else list(agent_name)
    return value in [n for n in names if n]


async def is_qdrant_available(qdrant_url: str, api_key: Optional[str] = None) -> bool:
    """Return whether the configured Qdrant instance is reachable."""
    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=5.0, headers=headers) as client:
            response = await client.get("/collections")
            return response.status_code == 200
    except Exception:
        return False


async def get_rag_backend(qdrant_url: str, api_key: Optional[str] = None) -> str:
    return "qdrant" if await is_qdrant_available(qdrant_url, api_key) else "local"

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(filename: str, raw_bytes: bytes) -> str:
    """Return plain text from a .txt, .md, or .pdf file."""
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw_bytes))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            logger.warning(f"PDF extraction failed for {filename}: {e}")
            return ""
    # txt / md and anything else
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Split text into overlapping chunks of approximately chunk_size characters."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # If para itself is bigger than chunk_size, split by words
            if len(para) > chunk_size:
                words = para.split()
                buf = ""
                for word in words:
                    if len(buf) + len(word) + 1 <= chunk_size:
                        buf = (buf + " " + word).strip()
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = word
                if buf:
                    current = buf
                else:
                    current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    # Apply overlap: prepend last `overlap` chars of previous chunk to each chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap:]
            overlapped.append((tail + " " + chunks[i]).strip())
        return overlapped

    return chunks


# ---------------------------------------------------------------------------
# Embeddings — provider-aware (Ollama or OpenAI-compatible)
# ---------------------------------------------------------------------------

#: Proveedores que saben producir embeddings. Anthropic **no** está y no puede
#: estar: no ofrece API de embeddings. Que la generación sea Claude no implica que
#: los embeddings lo sean, y esa es toda la razón de que exista este módulo aparte.
EMBED_PROVIDERS = ("ollama", "openai")


def get_embed_provider() -> str:
    """Proveedor de embeddings efectivo (#265, sobre SPEC-023/AC6).

    `EMBED_PROVIDER` es **independiente** de `LLM_PROVIDER`: permite generar con
    Claude y vectorizar con OpenAI, que antes era imposible porque la elección se
    derivaba de un solo ajuste.

    Vacío o desconocido → se **deriva** del proveedor de generación, que es el
    comportamiento heredado: `openai` si la generación es OpenAI, y Ollama en
    cualquier otro caso. Así un despliegue que no declare nada se comporta
    exactamente igual que antes; y uno que declare una errata cae al camino que
    funcionaba en vez de romperse — con un aviso, porque una errata silenciosa
    mandaría todos los vectores a un proveedor distinto del que se pidió, y eso no
    da error: da un RAG que no encuentra nada, mucho después.
    """
    from app.core.config import settings  # lazy to avoid circular imports

    declarado = (getattr(settings, "EMBED_PROVIDER", "") or "").strip().lower()
    if declarado in EMBED_PROVIDERS:
        return declarado
    if declarado:
        logger.warning(
            "EMBED_PROVIDER='%s' no es un proveedor de embeddings conocido (%s); "
            "se deriva de LLM_PROVIDER como antes.",
            declarado, ", ".join(EMBED_PROVIDERS),
        )
    return "openai" if settings.LLM_PROVIDER.lower() == "openai" else "ollama"


async def get_embedding(text: str, ollama_base_url: str, model: str) -> Optional[List[float]]:
    """
    Request an embedding vector from the embeddings provider.

    Embeddings are **independent of the generation provider** (SPEC-023/AC6, #265):
    the route comes from :func:`get_embed_provider`, not from ``LLM_PROVIDER``.
    Anthropic offers **no** embeddings API, so switching the agentic engine to
    Claude never routes an embedding to Anthropic nor breaks the RAG.
    """
    if get_embed_provider() == "openai":
        return await _get_embedding_openai(text)
    return await _get_embedding_ollama(text, ollama_base_url, model)


async def _get_embedding_ollama(text: str, ollama_base_url: str, model: str) -> Optional[List[float]]:
    """Embed via Ollama /api/embeddings."""
    try:
        async with httpx.AsyncClient(base_url=ollama_base_url, timeout=60.0) as client:
            resp = await client.post("/api/embeddings", json={"model": model, "prompt": text})
            if resp.status_code == 200:
                return resp.json().get("embedding")
    except Exception as e:
        logger.warning("Ollama embedding failed: %s", e)
    return None


async def _get_embedding_openai(text: str) -> Optional[List[float]]:
    """Embed via OpenAI Embeddings API (or any OpenAI-compatible endpoint)."""
    from app.core.config import settings
    try:
        import openai  # optional dependency — only needed when provider=openai
        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or None,
        )
        response = await client.embeddings.create(
            input=text,
            model=settings.OPENAI_EMBED_MODEL,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning("OpenAI embedding failed: %s", e)
    return None


def _fallback_vector(text: str, size: int) -> List[float]:
    """Deterministic pseudo-vector based on text hash. Used when Ollama is unavailable."""
    h = hashlib.sha256(text.encode()).digest()
    floats = []
    for i in range(size):
        byte_val = h[i % len(h)]
        floats.append((byte_val - 128) / 128.0)
    return floats


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

async def ensure_collection(
    qdrant_url: str,
    collection: str,
    vector_size: int,
    api_key: Optional[str] = None,
) -> None:
    """Create the Qdrant collection if it does not already exist."""
    if not await is_qdrant_available(qdrant_url, api_key):
        _local_collection_dir(collection)
        return

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=10.0, headers=headers) as client:
            check = await client.get(f"/collections/{collection}")
            if check.status_code == 404:
                payload = {
                    "vectors": {
                        "size": vector_size,
                        "distance": "Cosine",
                    }
                }
                await client.put(f"/collections/{collection}", json=payload)
                logger.info(f"Created Qdrant collection '{collection}' with size={vector_size}")
    except Exception as e:
        logger.warning(f"Could not ensure Qdrant collection '{collection}': {e}")


async def upsert_chunks(
    qdrant_url: str,
    collection: str,
    doc_id: str,
    agent_name: str,
    filename: str,
    chunks: List[str],
    ollama_base_url: str,
    embedding_model: str,
    vector_size: int,
    api_key: Optional[str] = None,
    doc_title: str = "",
    doc_authors: str = "",
) -> int:
    """Embed and upsert chunks into Qdrant. Returns count of inserted points.

    ``doc_title``/``doc_authors`` are stored on every chunk so retrieval can build
    real bibliographic citations.
    """
    if not await is_qdrant_available(qdrant_url, api_key):
        payload = {
            "doc_id": doc_id,
            "agent_name": agent_name,
            "filename": filename,
            "doc_title": doc_title,
            "doc_authors": doc_authors,
            "chunks": [
                {
                    "chunk_index": index,
                    "text": chunk,
                }
                for index, chunk in enumerate(chunks)
            ],
        }
        _local_document_path(collection, doc_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(chunks)

    headers = {"api-key": api_key} if api_key else {}
    points = []

    # Embed all chunks in parallel (batches of 20 to avoid overloading Ollama)
    _EMBED_BATCH = 20

    # Dos motivos distintos para caer al pseudovector, y **no** dan el mismo
    # problema: sin embedding es el proveedor caído (transitorio), y con un tamaño
    # que no cuadra es una **mala configuración** que no se arregla sola. Se cuentan
    # por separado para poder decirlo.
    caidas = {"sin_embedding": 0, "tamano": 0}

    async def _embed_one(idx: int, text: str):
        vec = await get_embedding(text, ollama_base_url, embedding_model)
        if vec is None:
            caidas["sin_embedding"] += 1
            vec = _fallback_vector(text, vector_size)
        elif len(vec) != vector_size:
            caidas["tamano"] += 1
            vec = _fallback_vector(text, vector_size)
        else:
            vec = vec[:vector_size]
        return idx, vec

    vectors: list = [None] * len(chunks)
    for batch_start in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[batch_start: batch_start + _EMBED_BATCH]
        results = await asyncio.gather(
            *[_embed_one(batch_start + j, chunk) for j, chunk in enumerate(batch)]
        )
        for idx, vec in results:
            vectors[idx] = vec

    # Se avisa una vez por documento, no por fragmento: un PDF de 200 chunks
    # llenaría el log y el aviso dejaría de leerse.
    if caidas["tamano"]:
        logger.warning(
            "%d/%d fragmentos de '%s' se han indexado con un pseudovector: el "
            "proveedor de embeddings ('%s') devuelve vectores de otra dimensión que "
            "la declarada en RAG_VECTOR_SIZE=%d. El documento queda indexado pero "
            "**no se podrá recuperar por significado**. Ajusta RAG_VECTOR_SIZE al "
            "modelo, o EMBED_PROVIDER al índice existente, y reindexa.",
            caidas["tamano"], len(chunks), filename, get_embed_provider(), vector_size,
        )
    if caidas["sin_embedding"]:
        logger.warning(
            "%d/%d fragmentos de '%s' se han indexado con un pseudovector porque el "
            "proveedor de embeddings no respondió. Reindexa cuando vuelva.",
            caidas["sin_embedding"], len(chunks), filename,
        )

    for i, chunk in enumerate(chunks):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{i}"))
        points.append({
            "id": point_id,
            "vector": vectors[i],
            "payload": {
                "doc_id": doc_id,
                "agent_name": agent_name,
                "filename": filename,
                "doc_title": doc_title,
                "doc_authors": doc_authors,
                "chunk_index": i,
                "text": chunk,
            },
        })

    if not points:
        return 0

    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=30.0, headers=headers) as client:
            resp = await client.put(
                f"/collections/{collection}/points?wait=true",
                json={"points": points},
            )
            if resp.status_code not in (200, 206):
                logger.error(f"Qdrant upsert failed: {resp.status_code} {resp.text}")
                return 0
    except Exception as e:
        logger.exception("Qdrant upsert error")
        return 0

    return len(points)


async def list_documents(
    qdrant_url: str,
    collection: str,
    agent_name: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return unique documents (by doc_id) stored for a given agent."""
    if not await is_qdrant_available(qdrant_url, api_key):
        docs = []
        for path in _local_collection_dir(collection).glob("*.json"):
            data = _load_local_document(path)
            if not data or data.get("agent_name") != agent_name:
                continue
            docs.append({
                "doc_id": data.get("doc_id", path.stem),
                "filename": data.get("filename", "unknown"),
                "agent_name": agent_name,
                "chunks": len(data.get("chunks", [])),
            })
        return sorted(docs, key=lambda item: item["filename"].lower())

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=10.0, headers=headers) as client:
            # Check collection exists first
            check = await client.get(f"/collections/{collection}")
            if check.status_code != 200:
                return []

            payload = {
                "filter": {
                    "must": [{"key": "agent_name", "match": {"value": agent_name}}]
                },
                "limit": 500,
                "with_payload": True,
                "with_vector": False,
            }
            resp = await client.post(f"/collections/{collection}/points/scroll", json=payload)
            if resp.status_code != 200:
                return []

            points = resp.json().get("result", {}).get("points", [])
            seen: Dict[str, Dict] = {}
            for p in points:
                pl = p.get("payload", {})
                doc_id = pl.get("doc_id")
                if doc_id and doc_id not in seen:
                    seen[doc_id] = {
                        "doc_id": doc_id,
                        "filename": pl.get("filename", "unknown"),
                        "agent_name": agent_name,
                        "chunks": 0,
                    }
                if doc_id:
                    seen[doc_id]["chunks"] += 1

            return list(seen.values())
    except Exception as e:
        logger.warning(f"list_documents error: {e}")
        return []


async def delete_document(
    qdrant_url: str,
    collection: str,
    doc_id: str,
    api_key: Optional[str] = None,
) -> bool:
    """Delete all Qdrant points belonging to a doc_id."""
    if not await is_qdrant_available(qdrant_url, api_key):
        path = _local_document_path(collection, doc_id)
        if path.exists():
            path.unlink()
            return True
        return False

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=10.0, headers=headers) as client:
            payload = {
                "filter": {
                    "must": [{"key": "doc_id", "match": {"value": doc_id}}]
                }
            }
            resp = await client.post(f"/collections/{collection}/points/delete?wait=true", json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"delete_document error: {e}")
        return False


async def list_library_documents(
    qdrant_url: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return all collections and their unique documents (no agent filter).

    Each entry: {collection, doc_id, filename, agent_name, chunks}
    """
    if not await is_qdrant_available(qdrant_url, api_key):
        results: List[Dict[str, Any]] = []
        root = _local_rag_root()
        if not root.exists():
            return results
        for collection_dir in sorted(root.iterdir()):
            if not collection_dir.is_dir():
                continue
            for doc_path in sorted(collection_dir.glob("*.json")):
                data = _load_local_document(doc_path)
                if not data:
                    continue
                results.append({
                    "collection": collection_dir.name,
                    "doc_id": data.get("doc_id", doc_path.stem),
                    "filename": data.get("filename", doc_path.stem),
                    "agent_name": data.get("agent_name", ""),
                    "chunks": len(data.get("chunks", [])),
                })
        return results

    headers = {"api-key": api_key} if api_key else {}
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=10.0, headers=headers) as client:
            col_resp = await client.get("/collections")
            if col_resp.status_code != 200:
                return results
            collection_names = [c["name"] for c in col_resp.json().get("result", {}).get("collections", [])]

            for collection_name in collection_names:
                offset = None
                seen: Dict[str, Dict] = {}
                while True:
                    body: Dict[str, Any] = {
                        "limit": 250,
                        "with_payload": True,
                        "with_vector": False,
                    }
                    if offset is not None:
                        body["offset"] = offset
                    scroll_resp = await client.post(
                        f"/collections/{collection_name}/points/scroll", json=body
                    )
                    if scroll_resp.status_code != 200:
                        break
                    scroll_result = scroll_resp.json().get("result", {})
                    points = scroll_result.get("points", [])
                    next_offset = scroll_result.get("next_page_offset")
                    for p in points:
                        pl = p.get("payload", {})
                        doc_id = pl.get("doc_id")
                        if not doc_id:
                            continue
                        if doc_id not in seen:
                            seen[doc_id] = {
                                "collection": collection_name,
                                "doc_id": doc_id,
                                "filename": pl.get("filename", "unknown"),
                                "agent_name": pl.get("agent_name", ""),
                                "chunks": 0,
                            }
                        seen[doc_id]["chunks"] += 1
                    if not next_offset or not points:
                        break
                    offset = next_offset
                results.extend(seen.values())
    except Exception as error:
        logger.warning(f"list_library_documents error: {error}")
    return results


async def fetch_doc_head(
    qdrant_url: str,
    collection: str,
    doc_id: str,
    api_key: Optional[str] = None,
    max_chunks: int = 3,
) -> str:
    """Return the opening text of a document (its first chunks, ordered by
    chunk_index) so the title/author block can be parsed reliably."""
    if not await is_qdrant_available(qdrant_url, api_key):
        for path in _local_collection_dir(collection).glob("*.json"):
            data = _load_local_document(path)
            if data and data.get("doc_id") == doc_id:
                chunks = sorted(data.get("chunks", []), key=lambda c: c.get("chunk_index", 0))
                return "\n".join(c.get("text", "") for c in chunks[:max_chunks])
        return ""

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=10.0, headers=headers) as client:
            check = await client.get(f"/collections/{collection}")
            if check.status_code != 200:
                return ""
            payload = {
                "filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
                "limit": 50,
                "with_payload": True,
                "with_vector": False,
            }
            resp = await client.post(f"/collections/{collection}/points/scroll", json=payload)
            if resp.status_code != 200:
                return ""
            points = resp.json().get("result", {}).get("points", [])
            points.sort(key=lambda p: p.get("payload", {}).get("chunk_index", 0))
            return "\n".join(p.get("payload", {}).get("text", "") for p in points[:max_chunks])
    except Exception as error:
        logger.warning(f"fetch_doc_head failed for doc '{doc_id}': {error}")
        return ""


async def backfill_doc_metadata(
    qdrant_url: str,
    collection: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Re-derive doc_title/doc_authors from stored chunk text for documents in a
    collection that lack them, and update their Qdrant payloads.

    Only the text heuristic is available here (the original file bytes are gone),
    so embedded PDF metadata can't be recovered — re-upload for that. Returns a
    summary list of the documents that were updated.
    """
    from app.modules.agents.adapters.doc_metadata import extract_doc_metadata

    if not await is_qdrant_available(qdrant_url, api_key):
        return []

    headers = {"api-key": api_key} if api_key else {}
    docs: Dict[str, Dict[str, Any]] = {}
    updated: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=30.0, headers=headers) as client:
            check = await client.get(f"/collections/{collection}")
            if check.status_code != 200:
                return []

            offset = None
            while True:
                body: Dict[str, Any] = {"limit": 250, "with_payload": True, "with_vector": False}
                if offset is not None:
                    body["offset"] = offset
                resp = await client.post(f"/collections/{collection}/points/scroll", json=body)
                if resp.status_code != 200:
                    break
                result = resp.json().get("result", {})
                points = result.get("points", [])
                for p in points:
                    pl = p.get("payload", {})
                    did = pl.get("doc_id")
                    if not did:
                        continue
                    entry = docs.setdefault(did, {
                        "filename": pl.get("filename", ""),
                        "chunks": [],
                        "doc_title": pl.get("doc_title", ""),
                        "doc_authors": pl.get("doc_authors", ""),
                    })
                    entry["chunks"].append((pl.get("chunk_index", 0), pl.get("text", "")))
                offset = result.get("next_page_offset")
                if not offset or not points:
                    break

            for did, entry in docs.items():
                if entry["doc_title"] and entry["doc_authors"]:
                    continue  # already has metadata
                entry["chunks"].sort(key=lambda c: c[0])
                joined = "\n".join(t for _, t in entry["chunks"][:4])
                meta = extract_doc_metadata(entry["filename"], b"", joined)
                title = entry["doc_title"] or meta.get("title", "")
                authors = entry["doc_authors"] or meta.get("authors", "")
                if not title and not authors:
                    continue
                set_body = {
                    "payload": {"doc_title": title, "doc_authors": authors},
                    "filter": {"must": [{"key": "doc_id", "match": {"value": did}}]},
                }
                await client.post(f"/collections/{collection}/points/payload?wait=true", json=set_body)
                updated.append({"doc_id": did, "filename": entry["filename"], "title": title, "authors": authors})
    except Exception as error:
        logger.warning(f"backfill_doc_metadata failed for '{collection}': {error}")
    return updated


async def fetch_agent_context(
    qdrant_url: str,
    collection: str,
    agent_name: str,
    limit: int = 5,
    api_key: Optional[str] = None,
) -> str:
    """Return a compact text block with chunks for an agent from Qdrant or local fallback.
    Simple scroll by agent_name — no semantic ranking. Use semantic_search_context for
    keyword-based relevance ranking."""
    if not await is_qdrant_available(qdrant_url, api_key):
        texts: List[str] = []
        for path in sorted(_local_collection_dir(collection).glob("*.json")):
            data = _load_local_document(path)
            if not data or not _agent_name_matches(data.get("agent_name"), agent_name):
                continue
            for chunk in data.get("chunks", []):
                text = chunk.get("text", "")
                if text:
                    texts.append(text)
                if len(texts) >= limit:
                    return "\n\n---\n\n".join(texts)
        return "\n\n---\n\n".join(texts)

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=5.0, headers=headers) as client:
            check = await client.get(f"/collections/{collection}")
            if check.status_code != 200:
                return ""

            payload = {
                "filter": {"must": [_agent_name_clause(agent_name)]},
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            }
            resp = await client.post(f"/collections/{collection}/points/scroll", json=payload)
            if resp.status_code != 200:
                return ""

            points = resp.json().get("result", {}).get("points", [])
            chunks = [p.get("payload", {}).get("text", "") for p in points if p.get("payload", {}).get("text")]
            return "\n\n---\n\n".join(chunks)
    except Exception as error:
        logger.warning(f"RAG fetch failed for agent '{agent_name}': {error}")
        return ""


def _anotar_traza(collection: str, resultados: List[Dict[str, Any]]) -> None:
    """Anota lo recuperado en la traza del paso en curso (SPEC-014 / T9.1 / AC1).

    Aquí y no en el agente: lo que el agente conserva es el texto ya montado, y
    las citas que acaban en el artículo son una elaboración posterior. La pregunta
    que responde la traza —«¿de dónde salió esto?»— solo se puede contestar desde
    donde se hizo la búsqueda.
    """
    try:
        from app.platform.explainability import record_rag_hits

        record_rag_hits(collection, resultados)
    except Exception:  # pragma: no cover - defensivo
        logger.debug("No se pudo anotar la traza RAG", exc_info=True)


async def _fetch_agent_results(
    qdrant_url: str,
    collection: str,
    agent_name,
    limit: int,
    api_key: Optional[str] = None,
    doc_ids: Optional[list] = None,
) -> List[Dict[str, Any]]:
    """Non-semantic fallback that returns chunk payloads (with filename/doc_id).

    When ``doc_ids`` is provided (the user selected specific documents), the
    selection takes precedence and the agent-bucket filter is bypassed — exactly
    like the semantic path — so a fallback never leaks unselected documents.
    """
    doc_id_set = set(doc_ids) if doc_ids else None
    if not await is_qdrant_available(qdrant_url, api_key):
        out: List[Dict[str, Any]] = []
        for path in sorted(_local_collection_dir(collection).glob("*.json")):
            data = _load_local_document(path)
            if not data:
                continue
            if doc_id_set is not None:
                if data.get("doc_id") not in doc_id_set:
                    continue
            elif not _agent_name_matches(data.get("agent_name"), agent_name):
                continue
            for chunk in data.get("chunks", []):
                text = chunk.get("text", "")
                if text:
                    out.append({
                        "text": text,
                        "filename": data.get("filename", ""),
                        "doc_id": data.get("doc_id", path.stem),
                        # En el respaldo local no hay identificador de punto: el
                        # índice del fragmento dentro del documento cumple la
                        # misma función para la traza.
                        "chunk_id": chunk.get("chunk_index"),
                        "agent_name": data.get("agent_name", ""),
                        "doc_title": data.get("doc_title", ""),
                        "doc_authors": data.get("doc_authors", ""),
                    })
                if len(out) >= limit:
                    _anotar_traza(collection, out)
                    return out
        _anotar_traza(collection, out)
        return out

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=5.0, headers=headers) as client:
            check = await client.get(f"/collections/{collection}")
            if check.status_code != 200:
                return []
            # Explicit doc_ids take precedence and bypass the agent-bucket filter.
            if doc_id_set is not None:
                must = [{"key": "doc_id", "match": {"any": list(doc_id_set)}}]
            else:
                must = [_agent_name_clause(agent_name)]
            payload = {
                "filter": {"must": must},
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            }
            resp = await client.post(f"/collections/{collection}/points/scroll", json=payload)
            if resp.status_code != 200:
                return []
            out = []
            for p in resp.json().get("result", {}).get("points", []):
                pl = p.get("payload", {})
                if pl.get("text"):
                    out.append({
                        "text": pl.get("text", ""),
                        "filename": pl.get("filename", ""),
                        "doc_id": pl.get("doc_id", ""),
                        "chunk_id": p.get("id"),
                        "agent_name": pl.get("agent_name", ""),
                        "doc_title": pl.get("doc_title", ""),
                        "doc_authors": pl.get("doc_authors", ""),
                    })
            _anotar_traza(collection, out)
            return out
    except Exception as error:
        logger.warning(f"_fetch_agent_results failed for agent '{agent_name}': {error}")
        return []


async def semantic_search_results(
    query: str,
    qdrant_url: str,
    collection: str,
    agent_name,
    ollama_base_url: str,
    embedding_model: str,
    limit: int = 5,
    score_threshold: float = 0.0,
    api_key: Optional[str] = None,
    doc_ids: Optional[list] = None,
) -> List[Dict[str, Any]]:
    """Semantic search returning per-chunk payloads ({text, filename, doc_id,
    agent_name}) so callers can build real citations. Filters by agent bucket(s),
    or by doc_ids only when the user selected specific documents."""
    if not await is_qdrant_available(qdrant_url, api_key):
        return await _fetch_agent_results(qdrant_url, collection, agent_name, limit, api_key, doc_ids)

    query_vector = await get_embedding(query, ollama_base_url, embedding_model)
    if query_vector is None:
        logger.warning("semantic_search_results: embedding failed, falling back to scroll")
        return await _fetch_agent_results(qdrant_url, collection, agent_name, limit, api_key, doc_ids)

    headers = {"api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=qdrant_url, timeout=10.0, headers=headers) as client:
            check = await client.get(f"/collections/{collection}")
            if check.status_code != 200:
                return []

            # Explicit doc_ids take precedence and bypass the agent-bucket filter.
            if doc_ids:
                must = [{"key": "doc_id", "match": {"any": list(doc_ids)}}]
            else:
                must = [_agent_name_clause(agent_name)]

            payload: Dict[str, Any] = {
                "vector": query_vector,
                "filter": {"must": must},
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            }
            if score_threshold > 0.0:
                payload["score_threshold"] = score_threshold

            resp = await client.post(f"/collections/{collection}/points/search", json=payload)
            if resp.status_code != 200:
                logger.warning(f"Qdrant search returned {resp.status_code}: {resp.text[:200]}")
                return await _fetch_agent_results(qdrant_url, collection, agent_name, limit, api_key, doc_ids)

            out: List[Dict[str, Any]] = []
            for r in resp.json().get("result", []):
                pl = r.get("payload", {})
                if pl.get("text"):
                    out.append({
                        "text": pl.get("text", ""),
                        "filename": pl.get("filename", ""),
                        "doc_id": pl.get("doc_id", ""),
                        # Identificador del punto en Qdrant: es el `chunk_id` que
                        # la traza de explicabilidad necesita para decir **qué**
                        # fragmento se recuperó y no solo de qué documento.
                        "chunk_id": r.get("id"),
                        "agent_name": pl.get("agent_name", ""),
                        "doc_title": pl.get("doc_title", ""),
                        "doc_authors": pl.get("doc_authors", ""),
                        "score": r.get("score"),
                    })
            logger.info(f"semantic_search_results: {len(out)} chunks for query '{query[:60]}'")
            _anotar_traza(collection, out)
            return out
    except Exception as error:
        logger.warning(f"Semantic RAG search failed for agent '{agent_name}': {error}")
        return await _fetch_agent_results(qdrant_url, collection, agent_name, limit, api_key, doc_ids)


async def semantic_search_context(
    query: str,
    qdrant_url: str,
    collection: str,
    agent_name,
    ollama_base_url: str,
    embedding_model: str,
    limit: int = 5,
    score_threshold: float = 0.0,
    api_key: Optional[str] = None,
    doc_ids: Optional[list] = None,
) -> str:
    """Semantic search returning the joined chunk texts (thin wrapper over
    semantic_search_results for callers that only need the context block)."""
    results = await semantic_search_results(
        query=query,
        qdrant_url=qdrant_url,
        collection=collection,
        agent_name=agent_name,
        ollama_base_url=ollama_base_url,
        embedding_model=embedding_model,
        limit=limit,
        score_threshold=score_threshold,
        api_key=api_key,
        doc_ids=doc_ids,
    )
    return "\n\n---\n\n".join(r["text"] for r in results if r.get("text"))


async def graph_rag_search_context(
    query: str,
    qdrant_url: str,
    collection: str,
    agent_name: str,
    ollama_base_url: str,
    embedding_model: str,
    limit: int = 5,
    api_key: Optional[str] = None,
    doc_ids: Optional[list] = None,
) -> str:
    """
    Graph RAG: Retrieve seed chunks semantically, build a concept/entity graph
    of co-occurrences using NetworkX, expand the search to adjacent nodes
    (related entities), and compile the enhanced context.
    """
    # 1. Fetch initial semantic search chunks
    base_context = await semantic_search_context(
        query=query,
        qdrant_url=qdrant_url,
        collection=collection,
        agent_name=agent_name,
        ollama_base_url=ollama_base_url,
        embedding_model=embedding_model,
        limit=limit,
        api_key=api_key,
        doc_ids=doc_ids,
    )
    if not base_context:
        return ""

    chunks = [c.strip() for c in base_context.split("\n\n---\n\n") if c.strip()]
    if not chunks:
        return ""

    # 2. Extract entities/keywords from chunks using basic heuristics
    import re
    import networkx as nx

    def extract_entities(text: str) -> List[str]:
        # Find capitalized words or phrases (proper nouns/technical terms)
        candidates = re.findall(r'\b[A-Z][a-zA-Z0-9_\-]{2,}(?:\s+[A-Z][a-zA-Z0-9_\-]{2,})*\b', text)
        return list(set([c for c in candidates if len(c) > 3]))

    # Build Graph
    G = nx.Graph()
    
    for chunk in chunks:
        entities = extract_entities(chunk)
        for ent in entities:
            G.add_node(ent, label="Entity")
            if "chunks" not in G.nodes[ent]:
                G.nodes[ent]["chunks"] = []
            if chunk not in G.nodes[ent]["chunks"]:
                G.nodes[ent]["chunks"].append(chunk)

        # Add edges between entities co-occurring in the same chunk
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                ent1, ent2 = entities[i], entities[j]
                if G.has_edge(ent1, ent2):
                    G[ent1][ent2]["weight"] += 1
                else:
                    G.add_edge(ent1, ent2, weight=1)

    # 3. Find target entities from query
    query_entities = extract_entities(query)
    if not query_entities:
        query_entities = [w.strip(".,;:?!()[]").capitalize() for w in query.split() if len(w) > 4]

    # Find neighbors/related nodes in the graph
    related_entities = set()
    for q_ent in query_entities:
        if q_ent in G:
            related_entities.add(q_ent)
            neighbors = list(G.neighbors(q_ent))
            related_entities.update(neighbors[:3])  # Top 3 neighbors

    # 4. Gather chunks containing these entities
    expanded_chunks = list(chunks)
    expanded_set = set(chunks)
    
    for ent in related_entities:
        for chunk in G.nodes[ent]["chunks"]:
            if chunk not in expanded_set:
                expanded_chunks.append(chunk)
                expanded_set.add(chunk)
                if len(expanded_chunks) >= limit + 3:
                    break
        if len(expanded_chunks) >= limit + 3:
            break

    # 5. Format the context with graph details
    entity_list = list(G.nodes)[:15]
    graph_summary = (
        f"[Graph RAG Info: Construido grafo conceptual con {G.number_of_nodes()} entidades y "
        f"{G.number_of_edges()} relaciones. Entidades clave: {', '.join(entity_list)}]\n\n"
    )
    return graph_summary + "\n\n---\n\n".join(expanded_chunks)
