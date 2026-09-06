"""Generic agent runner: executes custom agents defined via .agent.md profiles."""
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

from app.platform.capabilities.rag import fetch_agent_context
from app.platform.project_context import collection_for_state
from app.platform.llm import (
    call_llm, resolve_agent_model, resolve_agent_temperature,
)
from app.platform.projects.profiles import find as find_profile

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE = (
    "You are a specialized AI assistant called {agent_name}.\n\n"
    "Article title: {title}\n"
    "Keywords: {keywords}\n\n"
    "{context_section}"
    "Task: Process the article content and produce a useful output.\n\n"
    "Respond concisely and in the same language as the title."
)


def load_agent_profile(agent_name: str) -> Optional[Dict[str, Any]]:
    """Read and parse the .agent.md file for the given agent name, or None.

    La ruta la resuelve `platform/projects/profiles` desde los proyectos
    empaquetados (T8.4): antes se adivinaba con rutas relativas al directorio de
    trabajo, y no encontrarlas no fallaba — caía a un modelo por defecto.
    """
    filepath = find_profile(agent_name)
    if filepath is None:
        return None

    try:
        raw = filepath.read_text(encoding="utf-8")
        frontmatter: Dict[str, Any] = {}
        body = raw

        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
                body = parts[2]

        # Sin clave `model`: cuál es el modelo de un agente **depende del proveedor
        # activo**, así que un valor estático en este diccionario es incorrecto por
        # construcción. Lo resuelve `resolve_agent_model` (SPEC-023/AC3), que lee el
        # bloque `models:` de este mismo fichero. Antes había aquí un
        # `or "llama3.2:1b"` —un id de Ollama— que con `LLM_PROVIDER=anthropic`
        # viajaba tal cual a la API de Claude.
        return {
            "name": agent_name,
            "temperature": frontmatter.get("temperature", 0.7),
            "prompt_template": frontmatter.get("prompt_template", "").strip(),
            "rag_enabled": frontmatter.get("rag_enabled", False),
            "graph_rag_enabled": frontmatter.get("graph_rag_enabled", False),
            "semantic_search_enabled": frontmatter.get("semantic_search_enabled", False),
            "rag_collection": frontmatter.get("rag_collection", "rag_docs"),
            "tools_enabled": frontmatter.get("tools_enabled", False),
            "tools": frontmatter.get("tools") or [],
            "body": body.strip(),
        }
    except Exception as e:
        logger.warning(f"Could not load profile for agent '{agent_name}': {e}")
        return None


def _render_template(template: str, agent_name: str, state: Dict[str, Any], rag_context: str) -> str:
    """Substitute template variables with values from AgentState."""
    context_section = f"### Context from knowledge base:\n{rag_context}\n\n" if rag_context else ""

    variables = {
        "agent_name": agent_name,
        "title": state.get("title", ""),
        "keywords": ", ".join(state.get("keywords") or []),
        "research_data": state.get("research_data", ""),
        "draft_text": state.get("draft_text", ""),
        "feedback": "\n".join(f"- {f}" for f in (state.get("feedback") or [])),
        "scientific_format": state.get("scientific_format", "apa"),
        "context": rag_context,
        "context_section": context_section,
    }

    try:
        return template.format(**variables)
    except KeyError as e:
        logger.warning(f"Unknown template variable {e} in agent '{agent_name}' — using as-is")
        return template


async def run_generic_agent(agent_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a custom agent by loading its .agent.md profile, rendering its
    prompt_template with the current state, calling Ollama, and returning
    the result under the 'agent_output' key.

    Also injects RAG context if rag_enabled is set in the profile.
    """
    from app.core.config import settings  # imported here to avoid circular imports

    profile = load_agent_profile(agent_name)
    log = state.get("_log") or (lambda msg, level="info": None)

    if not profile:
        logger.warning(f"No profile found for '{agent_name}', running with defaults")
        profile = {
            "name": agent_name,
            "temperature": 0.7,
            "prompt_template": "",
            "rag_enabled": False,
            "rag_collection": settings.QDRANT_COLLECTION,
            "body": "",
        }

    # Fetch RAG context if enabled
    agent_cfg = state.get("agent_settings", {}).get(agent_name, {})
    rag_enabled = agent_cfg.get("rag_enabled") if agent_cfg.get("rag_enabled") is not None else profile.get("rag_enabled", False)
    graph_rag_enabled = agent_cfg.get("graph_rag_enabled") if agent_cfg.get("graph_rag_enabled") is not None else profile.get("graph_rag_enabled", False)
    semantic_search_enabled = agent_cfg.get("semantic_search_enabled") if agent_cfg.get("semantic_search_enabled") is not None else profile.get("semantic_search_enabled", False)
    rag_bucket = agent_cfg.get("rag_collection") or profile.get("rag_collection", settings.QDRANT_COLLECTION)
    rag_collection = collection_for_state(state, rag_bucket)
    rag_doc_ids = agent_cfg.get("rag_doc_ids") or profile.get("rag_doc_ids") or None

    rag_context = ""
    if rag_enabled:
        if graph_rag_enabled:
            log(f"🕸️ Buscando contexto mediante Graph RAG en colección '{rag_collection}'...")
            from app.platform.capabilities.rag import graph_rag_search_context
            title = state.get("title") or ""
            keywords = state.get("keywords") or []
            title_words = [w.strip(".,;:?!()[]") for w in title.split() if len(w) > 3] if title else []
            kw_words = [k for k in keywords if k.lower() not in {w.lower() for w in title_words}]
            combined_terms = title_words + kw_words
            query_str = " ".join(combined_terms) if combined_terms else "scientific research"

            rag_context = await graph_rag_search_context(
                query=query_str,
                qdrant_url=settings.QDRANT_URL,
                collection=rag_collection,
                agent_name=agent_name,
                ollama_base_url=settings.OLLAMA_BASE_URL,
                embedding_model=settings.OLLAMA_EMBED_MODEL,
                limit=5,
                api_key=settings.QDRANT_API_KEY,
                doc_ids=rag_doc_ids,
            )
        elif semantic_search_enabled:
            log(f"🔎 Buscando contexto mediante Búsqueda Semántica en colección '{rag_collection}'...")
            from app.platform.capabilities.rag import semantic_search_context
            title = state.get("title") or ""
            keywords = state.get("keywords") or []
            title_words = [w.strip(".,;:?!()[]") for w in title.split() if len(w) > 3] if title else []
            kw_words = [k for k in keywords if k.lower() not in {w.lower() for w in title_words}]
            combined_terms = title_words + kw_words
            query_str = " ".join(combined_terms) if combined_terms else "scientific research"

            rag_context = await semantic_search_context(
                query=query_str,
                qdrant_url=settings.QDRANT_URL,
                collection=rag_collection,
                agent_name=agent_name,
                ollama_base_url=settings.OLLAMA_BASE_URL,
                embedding_model=settings.OLLAMA_EMBED_MODEL,
                limit=5,
                api_key=settings.QDRANT_API_KEY,
                doc_ids=rag_doc_ids,
            )
        else:
            log(f"🔎 Buscando contexto RAG estándar en colección '{rag_collection}'...")
            rag_context = await fetch_agent_context(
                qdrant_url=settings.QDRANT_URL,
                collection=rag_collection,
                agent_name=agent_name,
                api_key=settings.QDRANT_API_KEY,
            )

        if rag_context:
            log(f"✅ RAG: {len(rag_context.split())} palabras de contexto encontradas.")
        else:
            log("ℹ️ RAG no devolvió contexto para este agente.")

    # Build the prompt
    template = profile["prompt_template"] or _DEFAULT_TEMPLATE
    prompt = _render_template(template, agent_name, state, rag_context)

    # Misma cascada que los agentes del núcleo (SPEC-023/AC3):
    # `agent_settings.model` → `models[<proveedor>]` del `.agent.md` → `model`
    # legado **si su namespace coincide** → default del proveedor. Un agente custom
    # quedaba fuera de esto y mandaba su `model:` de Ollama a cualquier proveedor.
    model = resolve_agent_model(agent_name, state.get("agent_settings"))
    # Un agente custom declara su `temperature` en el `.agent.md` igual que los
    # del núcleo; hasta ahora se leía y se descartaba.
    temperature = resolve_agent_temperature(agent_name, state.get("agent_settings"))
    output_text = ""
    log(f"🤖 Modelo: {model}")

    # ── Standard (no tools) path ─────────────────────────────────────────
    log("⏳ Generando respuesta...")
    try:
        output_text = await call_llm(
            prompt, model=model, timeout=300.0, temperature=temperature,
        )
        log(f"✅ Respuesta generada ({len(output_text.split())} palabras).")
    except Exception as e:
        logger.error(f"LLM call failed for agent '{agent_name}': {e}")
        log(f"⚠️ Error al llamar al LLM: {e}", "error")
        output_text = f"[{agent_name}] Error: {str(e)}"

    return {"agent_output": output_text}
