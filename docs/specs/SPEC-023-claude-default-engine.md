# SPEC-023: Claude por defecto como motor agéntico, multi-proveedor y multi-modelo por agente

- **Estado:** Ready
- **Autor:** Equipo de plataforma
- **Fecha:** 2026-08-09
- **Épica:** E12 (Motor LLM multi-proveedor)
- **ADR relacionado:** ADR-0009
- **Severidad:** 🟠

> **Ready** (pipeline ADR-0007): ambigüedades **resueltas** con `/speckit-clarify`
> (ver `## Clarifications`, sesión 2026-08-09): mapeo de modelos por agente
> confirmado, comportamiento sin API key y `max_tokens` fijados.
> `/speckit-checklist` queda como mejora opcional (no bloqueante). Al estar Ready,
> `/sdd-sync` siembra su épica E12 y tareas.

## 1. Problema

El dispatcher `backend/app/platform/llm.py` sólo conoce dos proveedores:
`ollama` (default, `LLM_PROVIDER="ollama"`,
[config.py:61](../../backend/app/core/config.py#L61)) y `openai`. No existe
proveedor **`anthropic`**: hoy es imposible usar Claude como motor sin un shim
OpenAI-compatible (que perdería el SDK oficial, thinking y el contrato nativo de
herramientas).

Además, el "modelo por agente" es **una única cadena que asume el proveedor
activo**: el frontmatter `.agent.md` fija modelos del namespace de Ollama
(investigador `mistral:7b`, redactor/revisor `llama3.2:3b`, formateador
`llama3.2:1b`). Si se cambiara el proveedor por defecto, esas cadenas dejarían de
ser válidas y todos los agentes caerían al modelo por defecto, perdiendo el
tiering por necesidad.

Se quiere que, **por defecto**, la plataforma use Claude con **modelos distintos
por agente según la necesidad**, y que **por configuración** se pueda conmutar a
Ollama, a otro motor compatible OpenAI o a **modelos on-prem cargados
directamente** — sin tocar código.

## 2. Objetivos / No-objetivos

- **Objetivos:** (a) proveedor `anthropic` en el dispatcher con el **SDK oficial**
  (generación, streaming, bucle de herramientas); (b) `LLM_PROVIDER` por defecto
  `anthropic`; (c) **modelo por agente consciente del proveedor** (bloque `models:`
  por proveedor en `.agent.md`, con cascada de resolución); (d) mapeo por defecto
  de modelos Claude por agente por necesidad; (e) conmutación a Ollama / OpenAI-
  compat (vLLM/LM Studio/on-prem) sólo por configuración; (f) API key protegida
  (nunca en logs/errores).
- **No-objetivos:** embeddings vía Anthropic (no existe API; el RAG sigue en
  Ollama/OpenAI); auto-selección dinámica de modelo por coste/latencia; router
  multi-modelo en caliente; migrar el proveedor de embeddings (posible
  `EMBED_PROVIDER` futuro, fuera de alcance).

## Clarifications

### Session 2026-08-09

- Q: ¿Mapeo de modelos Claude por agente para el default anthropic (§4)? → A: **El propuesto** — investigador→`claude-opus-5`, redactor→`claude-sonnet-5`, revisor→`claude-sonnet-5`, formateador→`claude-haiku-4-5`; orquestador/publicador sin LLM. Default global `ANTHROPIC_MODEL=claude-opus-5`. Calibrable con EDD sin cambiar la spec.
- Q: ¿Comportamiento si `LLM_PROVIDER=anthropic` pero falta `ANTHROPIC_API_KEY` (AC5)? → A: **Error perezoso** — arranca; la primera llamada al LLM falla con `RuntimeError` **permanente** (sin reintento, sin la key en el mensaje). No fail-fast al arranque: permite operar tareas no-LLM.
- Q: ¿Valor por defecto de `ANTHROPIC_MAX_TOKENS` (Anthropic lo exige)? → A: **4096** — suficiente para la mayoría de pasos y acota coste; sobreescribible por agente (el redactor puede subirlo para artículos largos).

## 3. Criterios de aceptación (Given/When/Then)

- [ ] **AC1** — *Given* `LLM_PROVIDER="anthropic"` y `ANTHROPIC_API_KEY` presente,
  *When* un agente llama a `call_llm` / `call_llm_stream` / `call_llm_with_tools`,
  *Then* la petición se enruta al **SDK oficial `anthropic`** (`AsyncAnthropic`),
  con `system` como parámetro (no como turno) y `max_tokens` fijado; verificable
  con el SDK mockeado (sin red).
- [ ] **AC2** — *Given* el default de configuración sin overrides, *Then*
  `settings.LLM_PROVIDER == "anthropic"` y `get_default_model() == ANTHROPIC_MODEL`
  (`claude-opus-5` por defecto); *When* se fija `LLM_PROVIDER=ollama` (o
  `openai`), *Then* el enrutado y el default vuelven a ese proveedor **sin cambios
  de código**.
- [ ] **AC3** — *Given* un agente con bloque `models:` en su `.agent.md`, *When* se
  resuelve su modelo bajo el proveedor activo, *Then* se aplica la cascada
  **`agent_settings[<agente>].model` → `models[<proveedor>]` → `model` legado (si
  su namespace coincide) → `get_default_model()`**; verificable con un test de
  resolución por proveedor (anthropic y ollama dan modelos distintos para el mismo
  agente).
  <br>*Alcance completado (#264)*: T12.3 lo aplicó a los **cuatro agentes del
  núcleo**; `adapters/generic.py` —el runner de los agentes **custom**, los que se
  definen con un `.agent.md`— se quedó fuera y leía un `profile["model"]` con un
  `or "llama3.2:1b"` de respaldo. Con el default `LLM_PROVIDER=anthropic` eso
  mandaba un identificador de Ollama a la API de Claude, y solo le pasaba a quien
  hubiera creado un agente propio. Este AC no excluye a los custom, así que ahora
  el runner llama a `resolve_agent_model`; y el diccionario del perfil **deja de
  llevar clave `model`**, porque cuál es el modelo depende del proveedor activo y
  dos sitios que lo resuelven es lo que causó el fallo.
- [ ] **AC4** — *Given* el mapeo por defecto (§4) con proveedor `anthropic`, *Then*
  investigador→`claude-opus-5`, redactor→`claude-sonnet-5`,
  revisor→`claude-sonnet-5`, formateador→`claude-haiku-4-5`, y
  orquestador/publicador **no** invocan LLM; verificable leyendo la resolución de
  cada adapter con el SDK mockeado.
- [ ] **AC5** — *Given* un error de autenticación de Anthropic (401), *Then* se
  trata como **permanente** (no se reintenta) y el mensaje **no** incluye la API
  key; *Given* una 429/5xx/timeout, *Then* es **transitorio** y entra en el retry
  con backoff existente (`_retry_async`/`_retry_stream`); *Given*
  `LLM_PROVIDER="anthropic"` **sin** `ANTHROPIC_API_KEY`, *Then* el arranque **no**
  falla (no fail-fast) y la **primera llamada** al LLM lanza `RuntimeError`
  permanente (sin la key en el mensaje) — error **perezoso**.
- [ ] **AC6** — *Given* `LLM_PROVIDER="anthropic"`, *Then* el RAG/embeddings
  **sigue** usando su proveedor propio (Ollama/OpenAI) sin fallar por ausencia de
  API de embeddings en Anthropic; documentado en `.env.example` y verificable por
  test de que la ruta de embeddings no depende de `LLM_PROVIDER`.
- [ ] **AC7** — Existen **tests** que cubren AC1–AC6 sin LLM real (SDK `anthropic`
  mockeado), incluidos el mapeo de mensajes (system/max_tokens), la cascada de
  resolución por proveedor y la clasificación de errores.

## 4. Diseño propuesto

- **Proveedor `anthropic`** en `platform/llm.py`: `_call_anthropic`,
  `_call_anthropic_stream`, `_tool_loop_anthropic`, usando `AsyncAnthropic`
  (paquete `anthropic`). Ramas nuevas en `call_llm` / `call_llm_stream` /
  `call_llm_with_tools` cuando `provider == "anthropic"`. Mapeo:
  `system_prompt` → parámetro `system`; `prompt`/mensajes → `messages`;
  `max_tokens` = `ANTHROPIC_MAX_TOKENS` (**default 4096**, sobreescribible por
  agente); thinking adaptativo
  (`thinking: {type: "adaptive"}`) en modelos que lo soportan; streaming con el
  helper del SDK. Clasificación de errores reutilizando `TransientLLMError`
  (401/permiso → `RuntimeError`; 429/5xx/conexión → `TransientLLMError`).
- **`get_default_model()`**: añade rama `anthropic` → `settings.ANTHROPIC_MODEL`.
- **Config** ([config.py](../../backend/app/core/config.py)): `LLM_PROVIDER`
  default `"anthropic"`; bloque `anthropic` en `Settings` y en `_build_settings`
  (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL="claude-opus-5"`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_MAX_TOKENS=4096`), con precedencia env > `config.yaml` > default. Key
  nunca logueada. La ausencia de key con proveedor `anthropic` **no** aborta el
  arranque (error perezoso en la primera llamada, ver AC5).
- **Modelo por agente consciente del proveedor**: utilidad central
  `resolve_agent_model(agent_name, agent_settings)` que aplica la cascada del AC3
  leyendo el bloque `models:` del `.agent.md` (fallback al `model:` legado sólo si
  su namespace coincide con el proveedor). Los adapters dejan de hacer
  `agent_cfg.get("model") or get_default_model()` y pasan a la utilidad central.
- **`.agent.md`**: se añade `models:` por proveedor a cada agente que invoca LLM
  (mapeo del AC4), conservando `model:` legado (Ollama) como fallback.
- **Requirements**: `anthropic` (SDK oficial) en `backend/requirements.txt`.
- **`.env.example` / `config.yaml`**: documentar el bloque `anthropic`, el modo
  por defecto (salida a un tercero) y el modo on-prem (conmutar a `ollama` u
  `openai`+`OPENAI_BASE_URL`), y que embeddings son independientes.

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Coste por token de API externa por defecto | Medio | Tiering por agente (Haiku/Sonnet donde basta) + presupuesto de contexto (ADR-0008) |
| Salida de datos a un tercero en modo por defecto | Alto (soberanía) | Conmutación on-prem documentada (`ollama`/`openai`+base_url); decisión explícita por despliegue |
| Cambiar el proveedor rompe el "modelo por agente" | Medio | Bloque `models:` por proveedor + cascada de resolución (AC3) |
| Fuga de `ANTHROPIC_API_KEY` en logs/errores | Alto | Nunca logueada; mensajes de error sin la key (igual que OpenAI) |
| Embeddings sin API en Anthropic | Medio | RAG desacoplado; embeddings siguen en Ollama/OpenAI (AC6) |
| Deriva de IDs de modelo Claude | Bajo | IDs documentados en config; `ANTHROPIC_MODEL` calibrable sin tocar código |

## 6. Plan de pruebas

Unit del proveedor `anthropic` con el SDK mockeado (mapeo system/max_tokens,
streaming, bucle de herramientas); unit de la cascada `resolve_agent_model` por
proveedor (mismo agente → modelo Claude vs modelo Ollama); unit de clasificación
de errores (401 permanente sin key en el mensaje; 429/5xx/timeout transitorios);
unit de que la ruta de embeddings no depende de `LLM_PROVIDER`; test de defaults
de config (`LLM_PROVIDER=="anthropic"`, `get_default_model()`), y de conmutación
por env var. Sin llamadas reales a la API.

## 7. Impacto operativo / observabilidad

Nueva dependencia `anthropic` y bloque de config documentado en `.env.example`;
proveedor y modelo efectivo por paso ya observables vía la traza (SPEC-014) y
métricas (SPEC-019) — se añade el proveedor a esos campos. Sin migraciones de BD.
*Rollback*: fijar `LLM_PROVIDER=ollama` restaura el comportamiento previo sin
desplegar código.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E12
  title: "Motor LLM multi-proveedor (Claude por defecto)"
  area: area/backend
tasks:
  - id: T12.1
    title: "Proveedor anthropic en el dispatcher (SDK oficial: generación, streaming, tools)"
    sev: high
    depends_on: []
    acceptance: [AC1, AC5]
  - id: T12.2
    title: "Config del proveedor anthropic + LLM_PROVIDER por defecto anthropic + get_default_model"
    sev: high
    depends_on: [T12.1]
    acceptance: [AC2]
  - id: T12.3
    title: "Resolución de modelo por agente consciente del proveedor (bloque models: + cascada)"
    sev: high
    depends_on: [T12.2]
    acceptance: [AC3, AC4]
  - id: T12.4
    title: "Mapeo de modelos Claude por agente en .agent.md + docs (.env.example/config.yaml)"
    sev: medium
    depends_on: [T12.3]
    acceptance: [AC4, AC6]
  - id: T12.5
    title: "Tests del proveedor anthropic, cascada de resolución y clasificación de errores"
    sev: medium
    depends_on: [T12.3]
    acceptance: [AC7]
```
