import logging
import re
from typing import Dict, Any

from app.core.config import settings
from app.platform.capabilities.binding import provider
from app.platform.llm import call_llm, resolve_agent_model, resolve_agent_temperature

logger = logging.getLogger(__name__)

_FORMAT_LABELS = {
    "apa": "APA 7th edition",
    "ieee": "IEEE",
    "vancouver": "Vancouver",
    "chicago": "Chicago 17th edition",
    "nature": "Nature / NLM numbered",
    "acl": "ACL (conference)",
}

_FORMAT_INSTRUCTIONS = {
    "apa": "Use APA 7th edition style for in-text citations: (Author, Year). Do NOT generate the References list.",
    "ieee": "Use IEEE style for in-text citations: bracketed sequential numbers [1], [2]. Do NOT generate the References list.",
    "vancouver": "Use Vancouver style for in-text citations: sequential numbers. Do NOT generate the References list.",
    "chicago": "Use Chicago 17th edition style for in-text citations: (Author Year). Do NOT generate the References list.",
    "nature": "Use Nature numbered style for in-text citations: superscript sequential numbers. Do NOT generate the References list.",
    "acl": "Use ACL conference style for in-text citations: author-year, (Author et al., Year) or Author et al. (Year). Do NOT generate the References list.",
}


_EMPTY_VALUES = {"", "n/a", "none", "null", "na"}


def _clean_source_fields(s: dict) -> tuple:
    """Normalise a source dict: blank out placeholder values and resolve a real
    (http/doi) link, ignoring internal pseudo-URLs like local:// or synthesis://."""
    def _val(key: str) -> str:
        v = str(s.get(key, "") or "").strip()
        return "" if v.lower() in _EMPTY_VALUES else v

    title = _val("title") or "Documento sin título"
    authors = _val("authors")
    journal = _val("journal")
    year = _val("year")
    doi = _val("doi")
    url = _val("url")

    if doi:
        link = f"https://doi.org/{doi}"
    elif url.startswith("http://") or url.startswith("https://"):
        link = url
    else:
        link = ""
    return title, authors, journal, year, link


def _finalize(parts: list, link: str) -> str:
    """Join non-empty segments, tidy punctuation, ensure a terminal period, then
    append the link."""
    ref = " ".join(p for p in parts if p).strip()
    ref = ref.rstrip(", ")               # drop a dangling comma (e.g. empty year)
    ref = re.sub(r"\s*,\s*\.", ".", ref)  # ", ." -> "."
    # Already terminated if it ends with a period or a quoted period (e.g. Chicago title).
    if ref and not ref.endswith(".") and not ref.endswith('."'):
        ref += "."
    ref = re.sub(r"\.{2,}", ".", ref)     # collapse duplicate periods
    if link:
        ref += f" {link}"
    return ref


def format_source_deterministic(s: dict, style: str, index: int) -> str:
    """Format a single source into a bibliography citation deterministically.

    Missing fields (authors, year, journal) are omitted rather than rendered as
    'N/A', and only real http/doi links are appended.
    """
    title, authors, journal, year, link = _clean_source_fields(s)

    if style == "apa":
        if authors and year:
            lead = f"{authors} ({year})."
        elif authors:
            lead = f"{authors}."
        elif year:
            lead = f"({year})."
        else:
            lead = ""
        return _finalize([lead, f"*{title}*.", (f"{journal}." if journal else "")], link)
    elif style == "ieee":
        parts = [f"[{index}]"]
        if authors:
            parts.append(f"{authors},")
        parts.append(f"\"{title},\"")
        if journal:
            parts.append(f"*{journal}*,")
        if year:
            parts.append(year)
        return _finalize(parts, link)
    elif style == "vancouver":
        parts = [f"{index}."]
        if authors:
            parts.append(f"{authors}.")
        parts.append(f"{title}.")
        if journal:
            parts.append(f"{journal}.")
        if year:
            parts.append(f"{year}.")
        return _finalize(parts, link)
    elif style == "chicago":
        if authors and year:
            lead = f"{authors}. {year}."
        elif authors:
            lead = f"{authors}."
        elif year:
            lead = f"{year}."
        else:
            lead = ""
        return _finalize([lead, f"\"{title}.\"", (f"*{journal}*." if journal else "")], link)
    elif style == "acl":
        # ACL Anthology: autor-año sin numerar ("Autores. Año. *Título*. Journal.").
        # Sin esta rama caía al `else` numerado, contradiciendo su propia
        # instrucción de cita en texto —(Autor et al., Año)— y dejando el paper
        # con citas autor-año y una bibliografía [1], [2].
        parts = []
        if authors:
            parts.append(f"{authors}.")
        if year:
            parts.append(f"{year}.")
        parts.append(f"*{title}*.")
        if journal:
            parts.append(f"In {journal}.")
        return _finalize(parts, link)
    elif style == "nature":
        parts = [f"{index}."]
        if authors:
            parts.append(f"{authors}.")
        parts.append(f"{title}.")
        if journal:
            parts.append(f"*{journal}*")
        if year:
            parts.append(f"({year}).")
        return _finalize(parts, link)
    else:
        parts = [f"[{index}]"]
        if authors:
            parts.append(f"{authors}.")
        parts.append(f"{title}.")
        if year:
            parts.append(f"{year}.")
        return _finalize(parts, link)


def clean_references_section(text: str) -> str:
    """Strip any References/Bibliography section from the text.

    Cuts from the LAST heading line onward, whether or not it uses a markdown
    '#' heading — small models often emit a plain 'Referencias' line with their
    own (hallucinated) citations, which must be removed before the deterministic
    bibliography is appended.
    """
    if not text:
        return ""
    heading = re.compile(
        r"(?im)^[ \t]*#{0,6}[ \t]*(referencias|references|bibliograf[ií]a|bibliography)[ \t:]*$"
    )
    matches = list(heading.finditer(text))
    if matches:
        return text[:matches[-1].start()].rstrip()
    return text.strip()


async def run_formateador(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formateador Agent.
    Applies a specific scientific citation format (APA, IEEE, Vancouver, Chicago, Nature)
    to the draft. Uses native Python code to construct the References/Bibliography
    deterministically from the state's 'sources' list, preventing LLM inventions.
    """
    draft_text = state.get("draft_text") or ""
    if not draft_text:
        return {"formatted_text": ""}

    # Resolve format: agent_settings → state → default
    agent_cfg = (state.get("agent_settings") or {}).get("formateador", {})
    scientific_format = (
        agent_cfg.get("scientific_format")
        or state.get("scientific_format")
        or "apa"
    ).lower()

    # Resolve model: provider-aware per-agent resolution (SPEC-023/T12.3)
    model = resolve_agent_model("formateador", state.get("agent_settings"))
    # Temperatura del agente (perfil o ajustes de la ejecución). Hasta ahora se
    # guardaba y no llegaba a ningún proveedor: era un control de la interfaz
    # que no hacía nada.
    temperature = resolve_agent_temperature("formateador", state.get("agent_settings"))

    format_label = _FORMAT_LABELS.get(scientific_format, scientific_format.upper())
    instruction = _FORMAT_INSTRUCTIONS.get(
        scientific_format,
        "Format in-text citations in APA 7th edition style.",
    )
    log = state.get("_log") or (lambda msg, level="info": None)

    logger.info(f"Running Formateador — format: {scientific_format}, model: {model}")
    log(f"📄 Aplicando formato bibliográfico determinista ({format_label})...")

    # 1. Clean the body draft from any existing bibliography
    cleaned_draft = clean_references_section(draft_text)

    # 2. Format in-text citations in the body text using LLM
    prompt = (
        f"You are a scientific manuscript formatter. Rewrite the manuscript below applying "
        f"{format_label} in-text citation style.\n\n"
        f"MANDATORY RULES:\n"
        f"1. Copy the ENTIRE manuscript text verbatim, preserving every paragraph, section, and sentence.\n"
        f"2. Only modify in-text citations to match {format_label}.\n"
        f"3. Do NOT output a 'References', 'Referencias', 'Bibliography' or 'Bibliografía' section at the end. Completely omit the references list.\n"
        f"4. Do NOT write explanations, steps, guides, or commentary of any kind.\n"
        f"5. Do NOT start with 'I can', 'I will', 'Here is', 'Sure', or any similar phrase.\n"
        f"6. Begin your output directly with the first line of the manuscript.\n\n"
        f"In-text citation rules:\n{instruction}\n\n"
        f"--- MANUSCRIPT START ---\n"
        f"{cleaned_draft}\n"
        f"--- MANUSCRIPT END ---\n\n"
        f"Output the reformatted manuscript now (no bibliography list):"
    )

    _REFUSAL_PATTERNS = (
        "i can't provide",
        "i cannot provide",
        "i'm unable",
        "i am unable",
        "here's how to",
        "here is how to",
        "follow these steps",
        "to format the manuscript",
        "you can follow",
        "you should follow",
    )

    formatted_body = cleaned_draft
    try:
        formatear = provider(state, "llm", call_llm)
        result = await formatear(
            prompt, model=model, timeout=300.0, num_ctx=4096, keep_alive=0,
            temperature=temperature,
        )
        result_lower = result.lower() if result else ""
        refused = any(pat in result_lower for pat in _REFUSAL_PATTERNS) if result else True
        if not refused and result and len(result) >= len(cleaned_draft) * 0.5:
            formatted_body = result.strip()
        else:
            logger.warning("Formateador: LLM failed or refused; falling back to original text body.")
    except Exception as exc:
        logger.warning("Formateador LLM call failed: %s; falling back to original body.", exc)

    # The reformat step often re-introduces a (hallucinated) references section
    # despite instructions — strip it again so only the deterministic one remains.
    formatted_body = clean_references_section(formatted_body)

    # 3. Assemble references section deterministically using Python code.
    # Exclude non-citable placeholders: parametric LLM synthesis has no real
    # bibliographic value and would otherwise render as a bogus "N/A" reference.
    sources = [
        s for s in (state.get("sources") or [])
        if not str(s.get("url", "")).startswith("synthesis://")
    ]

    formatted_refs = []
    seen_urls = set()
    unique_sources = []
    for s in sources:
        url = s.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_sources.append(s)
        elif not url:
            unique_sources.append(s)

    for idx, source in enumerate(unique_sources, start=1):
        ref_str = format_source_deterministic(source, scientific_format, idx)
        formatted_refs.append(ref_str)

    if formatted_refs:
        ref_title = "Referencias" if scientific_format in ("apa", "vancouver") else "References"
        # Two trailing spaces force a markdown hard line-break so each reference
        # renders on its own line instead of collapsing into one paragraph.
        refs_md = "  \n".join(formatted_refs)
        bibliography_block = f"\n\n## {ref_title}\n\n{refs_md}"
        final_text = formatted_body + bibliography_block
        log(f"✅ Formato bibliográfico determinista aplicado: {len(formatted_refs)} fuentes indexadas.")
    else:
        final_text = formatted_body
        log("ℹ️ No se encontraron fuentes en el estado del artículo para generar la bibliografía.")

    return {"formatted_text": final_text, "scientific_format": scientific_format}
