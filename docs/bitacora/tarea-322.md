# Tarea #322 — La dimensión del índice sale del modelo, no de un ajuste global

## 2026-09-06 — Completada ✅

- **Rama:** `fix/embed-vector-size`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #322`)
- **Spec/ADR:** SPEC-023 / AC6 (E12). **Defecto**; seguimiento directo de #265.
- **Dependencias:** #265, ya integrada.

## Qué se hizo

`embed_vector_size()` deriva la dimensión del **modelo de embeddings activo** en vez
de leer el ajuste global; `ensure_collection` comprueba la dimensión de una colección
que ya existe; y `upsert_chunks` **deja de escribir pseudovectores** cuando la
dimensión no cuadra.

## El fallo

`RAG_VECTOR_SIZE` es global, con default `768` —la dimensión de `nomic-embed-text`—,
así que **no distingue** «lo he puesto a 768 a propósito» de «nadie lo ha tocado». Y
el respaldo de `upsert_chunks` convierte esa ambigüedad en datos:

```python
if vec is None or len(vec) != vector_size:
    vec = _fallback_vector(text, vector_size)   # hash del texto
```

Con `EMBED_PROVIDER=openai` (lo que #265 acaba de hacer posible) y el default,
`text-embedding-3-small` devuelve 1536, no coincide, y **cada vector se sustituye
por un hash**. La subida responde `200 {"status": "indexed"}`, el documento aparece
en la biblioteca, y el RAG no encuentra nada.

Es el peor tipo de fallo que hay en este repo: **no da error, y el dato resultante
parece bueno**. Nadie vuelve a mirar un documento que se subió bien.

Y había una segunda mitad que no vi en #265: `ensure_collection` **solo actúa cuando
la colección no existe**. Una colección creada hace meses con 768 conserva esa
dimensión para siempre, así que cambiar de modelo de embeddings mete en el mismo
agujero a un sistema que antes funcionaba — y tampoco se miraba.

## Decisiones documentadas

- **La dimensión del modelo manda sobre el ajuste global.** Es un dato del modelo,
  no una preferencia: `nomic-embed-text` produce 768 y no hay nada que configurar.
  El ajuste sigue mandando para modelos que la tabla no conoce —uno propio, o un
  servidor compatible—, que es el comportamiento anterior.
- **Cuando los dos se conocen y no coinciden, se avisa.** Seguir en silencio es
  exactamente lo que causó el problema.
- **Una dimensión que no cuadra corta la indexación.** Es determinista: o falla en
  todos los fragmentos o en ninguno, así que no es un fallo transitorio sino una
  mala configuración. Escribir ruido **y responder que fue bien** es la peor
  combinación posible.
- **Un proveedor caído sigue usando el respaldo.** Es la distinción que introdujo
  #265 y que este cambio no puede borrar: una caída es transitoria, y perder la
  subida entera por ella sería peor que indexar con respaldo y avisar.
- **El error se traduce a 409, no a 500.** Un 500 con traza no dice qué arreglar;
  el 409 lleva el proveedor, el modelo, la dimensión del índice y qué hacer.
- **La tabla de modelos no pretende ser exhaustiva.** Es el conjunto que el repo usa
  o documenta; lo desconocido cae al ajuste, no a un adivinado.
- **`nombre:etiqueta` se normaliza**: Ollama llama `nomic-embed-text:latest` al
  mismo modelo.

## Test nuevo

`backend/tests/test_embed_vector_size.py` (24 casos), sin red:

- **La derivación**: seis modelos conocidos con su dimensión; el caso que rompía
  (`openai` + 768 → 1536); la etiqueta no confunde; un modelo desconocido respeta el
  ajuste; se avisa cuando no coinciden y **no** se avisa cuando sí —un aviso que
  sale siempre deja de leerse—; y el modelo sale del proveedor de *embeddings*, no
  del de generación.
- **La colección existente**: se lee su dimensión en las tres formas en que Qdrant
  la devuelve —entero, `{"size": n}` y vectores con nombre, donde **no se adivina**—;
  una con otra dimensión avisa nombrando las dos; una correcta no avisa.
- **Dejar de indexar ruido**: la dimensión que no cuadra levanta
  `EmbeddingDimensionMismatch` con un mensaje que dice qué arreglar, y **un
  proveedor caído sigue indexando** con el respaldo.
- **Costura**: ningún llamador crea colecciones con el ajuste global, y los dos
  endpoints de subida traducen el error a 409.

Verificado por mutación (seis): volver al ajuste global, perder el aviso de
discrepancia, dejar de mirar la colección existente, volver a indexar ruido,
confundir una caída del proveedor con una mala configuración, y devolver un llamador
al ajuste global. Cada mutación tumba su test.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 979 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python3 scripts/validate_specs.py         # → [OK]
```

Comprobado a mano con seis combinaciones de proveedor × modelo × ajuste, incluido el
escenario roto de #265 y un modelo fuera de la tabla.

## Definition of Done

- [x] Cumple los criterios de #322.
- [x] Tests que cubren el cambio, en verde (24 nuevos).
- [x] Docs: `.env.example` y `CLAUDE.md`.
- [x] Sin secretos en el diff; sin dependencias nuevas.
- [x] Rama con prefijo `fix/` hacia `develop`.

## Seguimiento

- **Cambio visible para un despliegue hoy mal configurado.** Pasa de «las subidas
  funcionan y el RAG no encuentra nada» a «la subida falla diciendo qué arreglar».
  Es el efecto buscado, pero conviene que se sepa antes de mergear.
- **Cambiar de modelo de embeddings sigue exigiendo recrear la colección y
  reindexar.** Ahora se avisa en los dos sitios donde se puede ver —al resolver la
  dimensión y al tocar la colección— pero **no hay migración automática**, y no la
  añade esta tarea: mover vectores entre modelos no es convertir, es recalcular.
- **`routers/ai.py` y `modules/ai/adapters/http.py` no usan embeddings reales**:
  tienen su propio `_vectorize_text`, un vectorizador determinista «para RAG de
  demostración». Se han dejado como estaban — son otro camino, y arreglarlos es
  decidir si ese camino debe seguir existiendo.
- **La tabla de dimensiones habrá que ampliarla** cuando se use otro modelo. Un
  modelo ausente no rompe nada: cae al ajuste, como antes.
