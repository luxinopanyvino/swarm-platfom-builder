# Tarea #265 — `EMBED_PROVIDER` explícito

## 2026-09-06 — Completada ✅

- **Rama:** `feat/265-embed-provider`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #265`)
- **Spec/ADR:** SPEC-023 (§2 no-objetivos, AC6), ADR-0009. Seguimiento de E12.
- **Dependencias:** ninguna.

## Qué se hizo

El proveedor de **embeddings** deja de derivarse del de generación. Nuevo ajuste
`EMBED_PROVIDER` (`ollama` | `openai`), independiente de `LLM_PROVIDER`, resuelto en
un solo sitio: `platform/capabilities/rag.py::get_embed_provider`.

Con `LLM_PROVIDER=anthropic` + `EMBED_PROVIDER=openai`, la generación va a Claude y
la vectorización a OpenAI. Antes era imposible: un solo ajuste decidía las dos cosas.

## Las dos propiedades que hacen seguro el cambio

Esto toca el RAG de despliegues que ya funcionan, así que lo que hay que defender no
es solo que la combinación nueva funcione:

- **Sin declararlo, el comportamiento es idéntico al de antes.** El valor vacío no
  significa «ollama»: significa «derívalo como se derivaba», y la tabla de derivación
  es literalmente la regla anterior (`openai` si la generación es OpenAI, Ollama en
  cualquier otro caso, incluido `anthropic`). Un despliegue que no toque nada no se
  entera de este cambio.
- **Una errata no manda los vectores a otro sitio en silencio.** Escribir
  `EMBED_PROVIDER=gemini` no da error: daría un RAG que **no encuentra nada**,
  semanas después y sin pista de por qué, porque los vectores se habrían generado con
  otro modelo y no son comparables con los ya indexados. Un valor desconocido cae al
  camino heredado **y avisa** en el log.

## Decisiones documentadas

- **La ruta se decide en una función, no en el `if` de `get_embedding`.** Con la
  condición repartida, el ajuste nuevo se ignoraría en cuanto alguien añada un
  segundo camino de vectorización. Hay un test de costura que comprueba que
  `get_embedding` **no** mira `LLM_PROVIDER` por su cuenta.
- **`anthropic` no está en la lista de proveedores de embeddings, y no es un
  olvido**: no tiene API. Si alguien lo añadiera, cada vectorización fallaría y el
  RAG se quedaría vacío. Declararlo explícitamente cae al camino heredado con aviso,
  porque es el error más probable de escribir: poner el mismo valor que en generación.
- **El valor se normaliza** (minúsculas, sin espacios): viene de una variable de
  entorno escrita a mano.
- **El campo `EMBED_PROVIDER` no se lee directamente en ningún sitio.** Vacío no dice
  cuál es el proveedor; solo `get_embed_provider()` lo sabe. Está avisado en el
  comentario del propio campo.

## Lo que apareció al documentar el riesgo

Al ir a escribir la advertencia de «cambiar de proveedor obliga a reindexar»,
comprobé el código en vez de darla por buena — y **es peor de lo que iba a
escribir**. `upsert_chunks` hace esto:

```python
if vec is None or len(vec) != vector_size:
    vec = _fallback_vector(text, vector_size)   # pseudovector de hash
```

`RAG_VECTOR_SIZE` es **global** (768, la dimensión de `nomic-embed-text`), y
`text-embedding-3-small` devuelve 1536. Con `EMBED_PROVIDER=openai` y el
`RAG_VECTOR_SIZE` por defecto, **cada vector de OpenAI se descarta y se sustituye
por un hash del texto**. Sin error, sin aviso. El documento queda indexado, la
subida dice que fue bien, y el RAG no encuentra nada — y eso se descubre semanas
después, sin ninguna pista que apunte aquí.

No es un fallo que introduzca esta tarea: ya existía para
`LLM_PROVIDER=openai`. Pero esta tarea lo hace **fácil de alcanzar**, así que
entra en su radio.

**No se cambia el respaldo** —existe para que un proveedor caído no tumbe una
subida, y eso está bien—. Lo que se cambia es que no se pueda no enterarse: se
cuentan por separado los dos motivos, porque **no son el mismo problema** —un
proveedor caído se arregla solo cuando vuelve; una dimensión que no cuadra no se
arregla nunca hasta que alguien toca la configuración— y se avisa **una vez por
documento**, no una por fragmento, para que el aviso se siga leyendo en un PDF de
200 chunks.

## Test nuevo

Se extiende `backend/tests/test_rag_embeddings_provider_decoupled.py` (16 casos, 13
nuevos), sin red — los dos backends concretos están sustituidos por espías que
registran la ruta tomada:

- **La combinación del issue**: Claude para generar y OpenAI para vectorizar. Y la
  simétrica —OpenAI para generar, Ollama para vectorizar—, porque desacoplar es en
  los dos sentidos, no solo en el cómodo.
- **Compatibilidad**, como tabla parametrizada con los cuatro valores de generación:
  sin declarar nada, la ruta es la de antes.
- **La errata**: cae al heredado y avisa; `anthropic` no puede estar en la lista;
  declararlo cae al heredado; y el valor tolera mayúsculas y espacios.
- **La costura**: `get_embedding` resuelve por `get_embed_provider()` y no por
  `LLM_PROVIDER`.
- **La trampa de la dimensión**: un vector de 1536 con `vector_size=768` avisa
  nombrando `RAG_VECTOR_SIZE`, y avisa **una sola vez** para tres fragmentos; y un
  proveedor caído produce un aviso **distinto**, que no menciona la configuración.

Verificado por mutación (nueve): ignorar `EMBED_PROVIDER`, aceptar una errata tal
cual, perder el aviso de la errata, romper la compatibilidad (default fijo a
`openai`), volver a mirar `LLM_PROVIDER` en `get_embedding`, meter `anthropic` en la
lista, perder el aviso de dimensión, mezclar los dos motivos de respaldo en uno, y
avisar por fragmento en vez de por documento. Cada mutación tumba su test.

**Un tropiezo mío, por tercera vez en esta serie**: el test de costura buscaba
`LLM_PROVIDER` como texto en el fuente de `get_embedding`, y la docstring dice «not
from `LLM_PROVIDER`» justo para explicar el diseño. Ahora compara el **cuerpo sin la
docstring** (AST). Explicar algo no es hacerlo — misma lección que en #159 y #264;
merece dejar de repetirse.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 919 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python3 scripts/validate_specs.py         # → [OK]
```

Comprobado a mano con las ocho combinaciones de `LLM_PROVIDER` × `EMBED_PROVIDER`,
incluida la precedencia de la variable de entorno sobre `config.yaml`.

## Definition of Done

- [x] Cumple los criterios del issue: `anthropic` + `openai` vectoriza con OpenAI y
  genera con Claude; sin `EMBED_PROVIDER`, comportamiento idéntico; tests sin red que
  extienden el fichero existente.
- [x] Tests que cubren el cambio, en verde (11 nuevos, 14 en el fichero).
- [x] Docs: `.env.example`, los dos `config.yaml` y SPEC-023 §2 anotada.
- [x] Sin secretos en el diff; sin dependencias nuevas.
- [x] Rama con prefijo `feat/` hacia `develop`.

## Seguimiento

- **SPEC-023 §2 declaraba esto fuera de alcance** («posible `EMBED_PROVIDER`
  futuro»). Se anota ahí que ya existe, en vez de crear una spec nueva: el
  no-objetivo de fondo —no hay embeddings vía Anthropic— **sigue siendo cierto**, y
  lo que cambia es de dónde sale la elección del proveedor. Si el mantenedor prefiere
  respaldarlo con una spec propia, el cambio es pequeño y está contenido en una
  función.
- **Cambiar de proveedor de embeddings exige tocar `RAG_VECTOR_SIZE` y reindexar.**
  `nomic-embed-text` da 768 y `text-embedding-3-small` 1536, y `RAG_VECTOR_SIZE` es
  **global**: si no cuadra, todo se indexa con pseudovectores (ahora, al menos, con
  un aviso que lo dice). Y aunque cuadre, los vectores de dos modelos distintos no
  son comparables entre sí, así que un índice existente hay que rehacerlo. **No hay
  migración automática y esta tarea no la añade**: es la razón de que el default no
  cambie nada. Un `EMBED_VECTOR_SIZE` derivado del proveedor —en vez del
  `RAG_VECTOR_SIZE` global— sería el arreglo de fondo, y merece su propia tarea.
- **La `temperature` de los perfiles sigue sin llegar al modelo** (deuda de #225 y
  #264). Es lo que queda vivo de E12.
