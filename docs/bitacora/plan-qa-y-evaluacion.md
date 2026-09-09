# Plan de QA y evaluación de modelos — E14 y E15

- **Fecha:** 2026-09-09
- **Rama:** `docs/qa-and-deepeval-plan`
- **Naturaleza:** autoría de specs (ADR-0007). No resuelve una tarea: **crea** dos.

## De dónde sale

El backlog quedó vacío al cerrar E6: cero tareas y cero épicas abiertas. La
pregunta «¿y ahora qué?» tiene una respuesta que el propio repo señala si se mide
en vez de opinar.

## Qué se encontró al medir (2026-09-09, sobre `develop`)

**QA.** La suite está sana —1027 tests, 61 ficheros— y eso escondía tres huecos
que no son de cobertura sino de **tipo de prueba**:

- **0 pruebas de frontend.** `frontend/package.json` no declara script `test` ni
  runner. El único test que abre navegador vive en el backend y mira accesibilidad.
- **12 de 61** ficheros ejercitan la app por HTTP (`httpx` + `ASGITransport`); los
  otros 49 llaman funciones directamente. Pirámide ancha abajo y estrecha justo
  donde viven los fallos de cableado.
- **0 herramientas de carga** (ni k6, ni locust, ni pytest-benchmark) y **0
  medición de cobertura** (no hay `pytest-cov` en `requirements*.in` ni en
  `ci.yml`). «1027 tests» es un número de tests, no de código ejercitado.
- Nada corre contra Qdrant o Postgres reales: la suite es sin red **por diseño**, y
  eso está bien para el gate rápido; el problema es que no hay ningún otro sitio.

**Evaluación.** El harness EDD funciona. Lo que falta es **qué mide**: las cinco
métricas actuales miran la salida del agente y **ninguna mira la recuperación**.
Este pipeline es RAG y su mitad RAG no se evalúa. La evidencia es que los dos
últimos defectos de RAG —#322 (vectores de hash por dimensión incorrecta) y #329
(`/ai/ingest` indexando un histograma de caracteres, sin `agent_name`)— se
detectaron **leyendo código**, no midiendo: ninguna métrica se habría movido.

## Qué se entrega

- **[ADR-0010](../adr/0010-adopt-deepeval-as-metrics-library.md)** — deepeval se
  adopta como **librería de métricas dentro del harness que ya existe**, no como
  framework. El runner, los datasets, `thresholds.yaml` y `gate.py` no cambian; el
  juez sale por `platform/llm.py`; telemetría desactivada y sin Confident AI.
- **[SPEC-026](../specs/SPEC-026-qa-test-strategy.md)** — épica **E14**
  (`area/qa`), 7 tareas: alta del área, documento de estrategia, cobertura con
  suelo, runner de frontend, integración contra servicios reales, carga con
  presupuestos, y separación de los tres niveles en CI.
- **[SPEC-027](../specs/SPEC-027-model-evaluation-deepeval.md)** — épica **E15**
  (`area/evaluation`), 5 tareas: adopción, puente del juez, métricas de
  recuperación sobre la traza de T9.1, dataset `recorded`, y umbrales en aviso.
- **Alta de `area/qa`** en los cuatro sitios que exige GOVERNANCE §7 (validador,
  seed, gobernanza, backlog), más las secciones E14/E15 del backlog.
- **`backend/tests/test_qa_area_registration.py`** (14 casos).

## Dos decisiones que conviene entender

**deepeval entra por el registro de métricas, no por la puerta grande.** Adoptarlo
entero traería su runner, su integración con pytest y su propio pass/fail: dos
sitios donde vive un umbral y dos que deciden si una PR pasa, además de perder el
modo `replay` —lo que hace la suite ejecutable sin Ollama— y la lectura de tokens y
fuentes por la traza. El seam elegido es el que el propio harness ya documenta:
*«añadir una métrica es añadir un módulo, no editar el runner»*. Eso también hace
la decisión **reversible**: cambiar de librería toca esos módulos y nada más.

**ADR-0006 rechazó los «servicios SaaS de evaluación», y esto no lo contradice.**
deepeval son dos cosas: una librería OSS y Confident AI, su plataforma alojada, a la
que el paquete manda telemetría por defecto. Se adopta la librería y se rechaza el
servicio, con el opt-out fijado y verificable. Esa distinción es el eje de
ADR-0010, no una nota al pie.

## Verificación

- `pytest tests/test_qa_area_registration.py` → 14 pasan.
- Suite backend completa en verde (ver PR).
- `scripts/validate_specs.py` → OK.
- **Mutación** (5 mutaciones, las 5 detectadas): volver E14 a E10; quitar `area/qa`
  del validador; etiqueta del seed sin color; dependencia hacia una tarea
  inexistente; un AC que ninguna tarea reclama.

## Un error propio, y lo que enseñó

Las dos specs nacieron con las épicas **E10 y E11, que ya eran de SPEC-021 y
SPEC-022**. Lo cazó `validate_specs.py` — pero de rebote: comprueba duplicados de
**tarea**, no de épica, y saltó solo porque los IDs de tarea heredan el número de la
épica. Si las tareas hubieran empezado en otro número, la colisión habría llegado al
Project.

El primer intento de test exigía que ninguna épica estuviera declarada por dos
specs, y **falló contra el repositorio**: E2 la declaran SPEC-002 (Superseded),
SPEC-016 y SPEC-024; E1 la comparten SPEC-001 y SPEC-015 por la adopción
retroactiva de ADR-0007. Compartir épica es deliberado aquí. El test se estrechó a
lo que de verdad casi rompe: que **una spec nueva** no se ponga encima de una
existente. Afirmar una regla que el repo no tiene habría sido peor que no probar.

## Lo que queda fuera, y hay que saberlo antes de empezar

- **T15.4 no se puede hacer en un entorno sin modelo.** Grabar un dataset con
  procedencia `recorded` exige Ollama local o clave de proveedor. Es la única tarea
  del plan con dependencia externa, y sin ella las métricas nuevas seguirán
  midiendo sobre salidas escritas a mano — es decir, seguirán diciendo que la
  métrica funciona, no que el modelo se comporte así.
- **Las specs se marcaron `Ready` sin pasar `/speckit-clarify` ni
  `/speckit-checklist`.** La DoR (§5) lo pide como paso **recomendado, no
  bloqueante** (ADR-0007), y se ha saltado para entregar el plan de una pieza. Es
  el candidato natural a hacerse antes de sembrar: si una spec está
  infra-especificada, sembrar sus tareas propaga el problema al Project.
- **Nada se ha sembrado todavía.** `/sdd-sync --apply` es lo que crea los issues, y
  no se ejecuta desde aquí sin decisión explícita.
