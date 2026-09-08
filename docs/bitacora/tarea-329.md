# Tarea 329 — `/api/v1/ai/ingest` indexaba con vectores de hash

- **Fecha:** 2026-09-08
- **Issue:** [#329](https://github.com/luxinopanyvino/swarm-platform-builder/issues/329) · seguimiento de [#322](https://github.com/luxinopanyvino/swarm-platform-builder/issues/322)
- **Rama:** `fix/329-ai-router-real-embeddings`
- **Naturaleza:** defecto. No gestionado por `sdd-sync`.

## De dónde sale

El backlog del Project quedó vacío al cerrar E6. Repasando la deuda anotada sin
issue apareció esto, que no es deuda menor: **#322 quitó los pseudovectores de hash
del camino bueno y el mismo fallo seguía vivo en una segunda puerta**, esta montada
en `main.py:351`, autenticada y con `X-Project-Id`.

## Qué estaba mal

`routers/ai.py` tenía plomería propia de Qdrant y su propio «vectorizador»:

```python
def _vectorize_text(text: str, dim: int) -> list[float]:
    """Simple deterministic vectorizer for demo RAG (no external embeddings)."""
```

Un histograma de caracteres. Para él, dos textos con las mismas letras son idénticos
y dos sinónimos son ortogonales. Cuatro consecuencias, todas verificadas en el
código antes de tocar nada:

1. **Escribía donde leen los agentes.** `project.collection(QDRANT_COLLECTION)` da
   `p_<proyecto>__rag_docs`, y los tres perfiles de `alejandria-magazine` declaran
   `rag_collection: rag_docs`, que `collection_for_state` compone igual. El
   Investigador buscaba con embeddings reales en un índice contaminado de hashes.
2. **Y lo que escribía era irrecuperable.** El payload iba **sin `agent_name`**, y
   `fetch_agent_context`, `_fetch_agent_results` y la vía semántica filtran por ese
   campo salvo con `doc_ids` explícitos. Los puntos ocupaban la colección y no los
   veía nadie. El endpoint respondía `{"status": "queued"}`: ni había cola, ni se
   podía leer.
3. **Creaba la colección con el ajuste que #322 degradó.** `RAG_VECTOR_SIZE` en vez
   de `embed_vector_size()`. Con `EMBED_PROVIDER=openai` (1536) y el default (768),
   este endpoint creaba la colección a 768 y el camino bueno chocaba luego con su
   propio 409 — es decir, `/ai/ingest` podía dejar al Investigador fuera de su
   colección.
4. **Había un duplicado muerto y peor.** `modules/ai/adapters/http.py` repetía
   `_vectorize_text`, **no lo importaba nadie**, usaba `settings.QDRANT_COLLECTION`
   en crudo —saltándose el aislamiento de T8.5— y no tenía ninguna dependencia de
   autenticación. Inofensivo por estar desconectado; una trampa el día que alguien
   lo montara.

Ningún test tocaba `/api/v1/ai/*`.

## Qué se hizo

- **Puerta única.** El router delega en `platform/capabilities/rag`: `chunk_text`,
  `ensure_collection(..., embed_vector_size())` y `upsert_chunks(...)`. Se borran
  `_vectorize_text`, `_chunk_text` y `_ensure_qdrant_collection`, y con ellos el
  import de `qdrant_client`.
- **Que lo ingerido se lea.** Los puntos van al bucket compartido `__library__`,
  que es el que todos los agentes leen además del suyo. Esta ruta no recibe agente,
  así que es el único destino que no deja el documento invisible.
- **`EmbeddingDimensionMismatch` → 409**, el idioma que ya usa `routers/agents.py`.
- **Borrado** `modules/ai/adapters/http.py`.
- **`/assist`**: prometía RAG en el nombre y en `sources`, y no buscaba nada;
  además creaba una colección que no leía, lo que sostenía la apariencia. Se quita
  esa llamada y el docstring dice lo que la ruta hace. Añadir recuperación de
  verdad queda fuera de este arreglo. La dependencia `project` se queda aunque el
  cuerpo no la use: es la que resuelve y **autoriza** `X-Project-Id`, y quitarla
  haría que una petición sin cabecera pasara a responder 200.
- **`_generate_with_ollama`**: definida y nunca llamada — era el generador de la
  respuesta con contexto del `/assist` que nunca se cableó. Borrada, por lo mismo
  que el duplicado. El router queda con sus cuatro endpoints y ningún ayudante
  privado.

## Verificación

- `pytest tests/test_ai_router_real_embeddings.py` → 9 pasan.
- Suite backend completa en verde (ver PR).
- **Mutación** (5 mutaciones, las 5 detectadas):
  1. `agent_name=""` — el defecto silencioso original → cae
  2. volver a `RAG_VECTOR_SIZE` → cae
  3. colección sin espacio de proyecto → cae
  4. tragarse el 409 y responder 200 → cae
  5. reintroducir un `_vectorize_text` → caen 2 (guardia estructural)

La guardia estructural se hace **sobre el AST**, no buscando la cadena
«vectorize»: el docstring de este propio test explica el defecto y un `grep`
tropezaría con él. Es el mismo error que ya se cometió en #159, #264 y #265.

## Compatibilidad

Lo ya indexado por `/ai/ingest` son vectores de hash sin `agent_name`: ruido
invisible. Este cambio no lo borra ni lo migra —no hay nada que rescatar—, pero
sigue ocupando la colección. Si estorba, se limpia aparte.

## Lo que queda fuera

`/api/v1/ai/assist` sigue sin recuperación. Ahora está **dicho** en su docstring en
vez de insinuado por la plomería, que era lo peligroso. Si se quiere que haga RAG
de verdad, es trabajo propio con su issue: hay que decidir de qué agente lee.
