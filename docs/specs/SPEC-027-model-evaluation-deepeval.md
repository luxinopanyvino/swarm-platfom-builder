# SPEC-027: Evaluación de modelos y agentes con deepeval

- **Estado:** Ready
- **Autor:** Equipo de plataforma
- **Fecha:** 2026-09-09
- **Épica:** E15 (GitHub Project)
- **ADR relacionado:** [ADR-0010](../adr/0010-adopt-deepeval-as-metrics-library.md) · complementa [ADR-0006](../adr/0006-adopt-evaluation-driven-development.md)
- **Severidad:** 🟠

## 1. Problema

El harness EDD (SPEC-014 / T9.3–T9.6) funciona: carga datasets versionados, ejecuta
el agente real por el `resolve_runner` del motor, le lee tokens y fuentes por la
traza de T9.1, puntúa con métricas registradas y compara contra
`thresholds.yaml` en un gate de CI. La infraestructura no es el problema.

**El problema es qué se mide.** Las cinco métricas actuales —`citations`,
`format_compliance`, `calibration`, `coherence`, `budget`— miran la salida del
agente. **Ninguna mira la recuperación.** Y este pipeline es RAG:
`platform/capabilities/rag.py` trocea, vectoriza, busca en el bucket del agente más
`__library__`, y el Redactor escribe sobre lo que le llegue. Hoy nada mide si lo
recuperado venía a cuento, si el texto se sostiene sobre él, o si el agente afirmó
cosas que el contexto no dice.

Las consecuencias son concretas y ya se han visto en este repo:

- **#322** — con `EMBED_PROVIDER=openai` y el `RAG_VECTOR_SIZE` por defecto, cada
  vector se sustituía por un hash. La subida respondía «indexado» y el RAG no
  encontraba nada. Se detectó leyendo el código, no midiendo.
- **#329** — `/ai/ingest` indexaba con un histograma de caracteres en la colección
  de los agentes, y sin `agent_name`, así que era irrecuperable. Igual: se detectó
  leyendo.

Ninguna métrica se habría movido en ninguno de los dos casos, porque no hay ninguna
que mire la recuperación. Un cambio de modelo de embeddings, de `chunk_size` o de
`top_k` puede degradarla y hoy pasa entero por debajo del radar.

Escribir esas métricas a mano es posible —`coherence` ya lo hace, con juez propio y
rúbrica fija— pero cada una cuesta diseñarla, escribirla y calibrarla.

## 2. Objetivos / No-objetivos

- **Objetivos:** métricas de calidad de recuperación y de fidelidad al contexto,
  tomadas de deepeval y **registradas en el harness que ya existe**; el juez
  enrutado por el dispatcher de la plataforma; telemetría desactivada; un dataset
  golden con procedencia real para el agente con RAG.
- **No-objetivos:** sustituir el runner, los datasets, `thresholds.yaml` o
  `gate.py`; usar Confident AI ni ningún servicio alojado; endurecer el gate con
  métricas de juez mientras los golden sean `handwritten`; extender deepeval a
  `evals/model_benchmark/`, que compara modelos *foundation* y es otra pregunta
  (SPEC-025, ADR-0006).

## 3. Criterios de aceptación (Given/When/Then)

- [ ] **AC1** — *Given* deepeval instalado, *When* se ejecuta el runner, *Then* sus
  métricas se obtienen del **mismo registro** que las demás (`metrics/`), en escala
  **0–100**, con `applies_to` que las salta con motivo cuando el caso no las
  alimenta; **`runner.py` no cambia** — verificable con un test que compare el
  fichero con su versión anterior o compruebe que no menciona deepeval.
- [ ] **AC2** — *Given* una métrica de deepeval que necesita juez, *When* se
  evalúa, *Then* la llamada sale por `platform/llm.py` con `temperature=0` y el
  modelo resuelto por la cascada de la plataforma; **no** se instancia ningún
  cliente de proveedor propio de deepeval — verificable con un test estructural que
  falle si aparece uno.
- [ ] **AC3** — *Given* el entorno de evaluación, *Then* la telemetría de deepeval
  está desactivada y no se usa login ni subida de resultados a Confident AI;
  documentado en `.env.example` y verificable sin red.
- [ ] **AC4** — *Given* un caso con corpus y fuentes recuperadas, *When* se evalúa,
  *Then* se computan al menos **fidelidad al contexto** y **relevancia de lo
  recuperado**, alimentadas por las fuentes que la traza de T9.1 ya registra
  (`agent_run_steps`), sin que el agente tenga que colaborar.
- [ ] **AC5** — *Given* el agente que usa RAG, *Then* existe un dataset
  `<agente>-golden` con `provenance: recorded` —salidas grabadas de una ejecución
  real, no escritas a mano— y su pareja `<agente>-regressions` que falla a
  propósito y documenta qué se detecta.
- [ ] **AC6** — *Given* `thresholds.yaml`, *Then* las métricas nuevas tienen umbral
  declarado **en modo aviso**, el gate distingue igual que hoy entre regresión
  (avisa) y medición rota (rompe siempre), y la disciplina queda escrita en
  `edd-discipline.md` junto con la frontera con `model_benchmark`.

## 4. Diseño propuesto

**El seam es el registro de métricas, y eso es lo que hace reversible la decisión.**
`evals/agent_behavior/metrics/__init__.py` ya dice que añadir una métrica es añadir
un módulo. Cada métrica de deepeval es un módulo ahí: construye el caso de deepeval
a partir del `EvalCase` y del `CaseResult`, ejecuta la métrica, y **normaliza a
0–100**. Si mañana se cambia deepeval por otra librería, se tocan esos módulos y
nada más.

**El puente del juez (AC2)** es la pieza que hay que hacer bien.
`evals/agent_behavior/judge.py` ya tiene la forma —`RecordedJudge` para `replay`,
`PlatformJudge` que llama a `call_llm(..., temperature=0)`— y deepeval acepta un
`DeepEvalBaseLLM` propio. El puente une las dos: deepeval pide un veredicto, y lo
sirve el modelo de la plataforma. Que deepeval no abra su propio cliente no es
purismo: es lo que mantiene una sola puerta hacia proveedores, la política de
egress (SPEC-024) y la cascada de resolución de modelo y temperatura.

**Las fuentes salen de la traza, no del agente (AC4).** T9.1 ya anota las fuentes
RAG recuperadas con su score en variables de contexto que el orquestador recoge al
cerrar el paso. Las métricas de recuperación leen de ahí. Es la misma propiedad que
ya tiene el harness —«un agente nuevo queda trazado sin tocarlo»— aplicada a esto:
un agente nuevo queda **evaluado** sin tocarlo.

**Procedencia real (AC5).** Hoy todos los datasets son `handwritten`, y el informe
avisa de que un verde dice que la métrica funciona, no que el modelo se comporte
así. Una métrica de recuperación sobre salidas escritas a mano mide todavía menos,
porque el contexto también sería inventado. Por eso AC5 pide `recorded`: grabar una
ejecución real y versionarla. **Esta tarea necesita un proveedor de verdad**
(Ollama local o clave de API) — es la única del plan que no se puede completar en un
entorno sin modelo.

**Ficheros afectados:** `backend/evals/agent_behavior/metrics/` (módulos nuevos),
`judge.py` (puente), `thresholds.yaml`, `datasets/`, `requirements-dev.in`,
`.env.example`, `docs/governance/edd-discipline.md`.

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| deepeval arrastra su runner y aparecen dos gates | Dos fuentes de verdad para «¿pasa la PR?» | ADR-0010 lo decide explícitamente: solo métricas. AC1 lo prueba: `runner.py` no cambia |
| deepeval abre su propio cliente de OpenAI | Se salta el dispatcher, la política de egress y la cascada de modelo | AC2 con test estructural |
| Telemetría a Confident AI | Fuga de contenido evaluado; ADR-0006 rechazó el SaaS | AC3: opt-out fijado y documentado |
| Métricas con juez, no deterministas, usadas como gate duro | PRs que fallan al azar | No se endurecen mientras los golden sean `handwritten`; el modo lo decide `thresholds.yaml`, no el workflow |
| Coste de llamadas al juez en `live` | Gasto y lentitud | `replay` sigue siendo el modo por defecto de la suite; el juez solo en `live` |
| Dependencia grande y de evolución rápida | Roturas al actualizar | Va en `requirements-dev`, **nada de `app/` la importa**; se pinea con hashes como el resto |

## 6. Plan de pruebas

AC1, AC2 y AC3 se validan **sin red**: registro de métricas, test estructural sobre
el AST del puente del juez —no por búsqueda de texto, que tropieza con los
docstrings que explican el propio defecto, error ya cometido en #159, #264 y #265—
y comprobación del opt-out. AC4 se valida con una traza sintética que alimente las
fuentes. AC6 se valida ejecutando el gate contra un `thresholds.yaml` de prueba, con
un caso de regresión (avisa) y uno de medición rota (rompe). **AC5 solo se puede
validar con un proveedor real**, y esa es la dependencia externa de esta épica.

## 7. Impacto operativo / observabilidad

Sin cambios en producto: nada de `backend/app/` importa deepeval. El gate EDD gana
métricas en modo aviso y su duración crece solo en `live`. Los informes de
`evals/results/` siguen ignorados por git y siguen diciendo el modo en la cabecera
—presentar un `replay` como prueba de que el agente va bien sigue siendo el peor
fallo posible aquí, y añadir métricas mejores no lo cambia.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E15
  title: "Evaluación de modelos y agentes con deepeval"
  area: area/evaluation
tasks:
  - id: T15.1
    title: "Adoptar deepeval como librería de métricas (dependencia, telemetría off)"
    sev: medium
    depends_on: []
    acceptance: [AC1, AC3]
  - id: T15.2
    title: "Puente del juez: deepeval llama por el dispatcher de la plataforma"
    sev: high
    depends_on: [T15.1]
    acceptance: [AC2]
  - id: T15.3
    title: "Métricas de recuperación alimentadas por la traza de explicabilidad"
    sev: high
    depends_on: [T15.2]
    acceptance: [AC4]
  - id: T15.4
    title: "Dataset golden con procedencia recorded para el agente con RAG"
    sev: medium
    depends_on: [T15.3]
    acceptance: [AC5]
  - id: T15.5
    title: "Umbrales en modo aviso y disciplina escrita en edd-discipline.md"
    sev: medium
    depends_on: [T15.3]
    acceptance: [AC6]
```
