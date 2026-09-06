# SPEC-014: Explicabilidad del pipeline y Evaluation-Driven Development (EDD)

- **Estado:** Ready
- **Autor:** Luis San Martín
- **Fecha:** 2026-06-30
- **Épica:** E9 (GitHub Project)
- **ADR relacionado:** [ADR-0006](../adr/0006-adopt-evaluation-driven-development.md) · apoya en [ADR-0004](../adr/0004-observability-and-ci.md)
- **Severidad:** 🟠

## 1. Problema

El pipeline editorial (LangGraph `StateGraph`, ver
[use_cases.py](../../backend/app/modules/agents/application/use_cases.py)) produce
artículos encadenando cinco agentes, pero su comportamiento es hoy una **caja
negra** en dos sentidos:

- **Sin explicabilidad.** La única traza es la tabla `agent_runs`, logs de texto
  con emojis y los eventos SSE efímeros. No queda registrado **por qué** salió un
  resultado: qué fuentes/chunks RAG citó cada paso, con qué score y comentarios
  rechazó el Revisor, por qué se disparó un HITL, ni cuántos tokens/latencia
  consumió cada agente. El usuario no puede auditar ni entender una ejecución.
- **Sin evaluación sistemática.** Cambiar un `prompt_template`, el `model` o la
  `temperature` de un agente puede **degradar la calidad en silencio**: no hay
  datasets de referencia, ni métricas de comportamiento, ni un gate que detecte
  regresiones. El desarrollo de los agentes no está dirigido por evaluación.

Ambos comparten el mismo sustrato de datos (la traza por paso de cada ejecución),
por eso se abordan juntos: la **explicabilidad** produce los datos y la **EDD**
los convierte en criterio de desarrollo.

## 2. Objetivos / No-objetivos

**Objetivos**
- Persistir y exponer una **traza de explicabilidad** estructurada por ejecución y
  por paso de agente (entrada/salida, fuentes RAG citadas, decisión + *rationale*,
  tokens y latencia).
- Establecer un **harness de EDD** que evalúe el **comportamiento de los agentes y
  modelos configurados en la plataforma**, con datasets versionados, métricas de
  comportamiento reproducibles y un gate de regresión en CI.
- Dejar **EDD como disciplina** de primer nivel (área `area/evaluation`, gobernanza
  análoga a SDD), lista para aplicarse en el desarrollo de agentes.

**No-objetivos**
- **No** evaluar modelos *foundation* globales ni hacer *benchmarking* general de
  proveedores LLM. El alcance es **solo** el comportamiento de los **agentes y
  modelos de esta plataforma** (perfiles en BD + `*.agent.md` + el modelo que cada
  uno usa vía `LLM_PROVIDER`).
- **No** reentrenar ni *fine-tunear* modelos.
- **No** cambiar la lógica funcional del pipeline (solo instrumentar, exponer y
  evaluar); el HITL y la revisión humana siguen igual.
- **No** sustituir la observabilidad operativa (E5): la complementa apoyándose en
  sus `correlation_id` (T5.1) y métricas (T5.2).

## 3. Criterios de aceptación (Given/When/Then)

- [x] **AC1** — *Given* una ejecución del pipeline, *When* termina (o se cancela),
  *Then* existe una **traza persistida** con un registro por paso de agente que
  incluye: agente, modelo y parámetros (`temperature`, `num_ctx`), resumen de
  entrada/salida, **fuentes RAG citadas** (`doc_id`, `chunk_ids`, score), tokens
  in/out, latencia y, cuando aplique, **decisión + rationale** (score del Revisor,
  flag de coherencia, resultado del HITL).
  <br>*T9.1 (#221)*: tabla `agent_run_steps` (migración `0004`), escrita por el
  orquestador al cerrar cada nodo —también cuando **falla**—. Tres de los datos no
  están donde se escribe el paso: los tokens solo los conoce el proveedor del LLM,
  las fuentes solo las conoce la capacidad de RAG, y el agente se queda con el
  texto ya montado. Se recogen con variables de contexto
  (`platform/explainability.py`), igual que `current_agent_ctx` en T5.2, así que
  un agente nuevo queda trazado sin tocarlo. Retención de 90 días, la misma que
  `agent_runs`.
- [x] **AC2** — *Given* un artículo con ejecuciones, *When* se consulta
  `GET /api/v1/agents/{article_id}/explain`, *Then* devuelve la traza completa
  estructurada y la UI muestra un panel "Por qué este resultado" con fuentes,
  score y decisiones por paso.
  <br>*T9.2 (#224)*: el endpoint y el panel
  (`platform/components/explain/ExplainPanel.jsx`, montado en el detalle del
  artículo). Lo que no es evidente es **qué** se devuelve: un artículo se puede
  reejecutar, y entonces su traza tiene varias ejecuciones en la misma tabla;
  devolverlas mezcladas contaría una historia que no ocurrió, con el revisor
  aprobando un borrador que ya no existe. Se separan por el reinicio de
  `step_index` —una reanudación no reinicia, así que sigue siendo la misma
  ejecución— y por defecto se explica la última, la que produjo el texto que se
  está leyendo; `?scope=all` las devuelve todas. Las fuentes se agregan por
  documento a través de los pasos, porque la pregunta del panel es «¿en qué se
  apoya esto?» y la traza las guarda por paso. Mismo control de acceso que
  `/runs`, más la comprobación del proyecto activo cuando la cabecera viene.
- [x] **AC3** — *Given* un perfil de agente de la plataforma y un dataset de
  evaluación, *When* se ejecuta el harness EDD, *Then* produce un informe de
  métricas **reproducible** (modelo y parámetros fijados/registrados) sin depender
  de servicios externos no declarados.
  <br>*T9.3 (#222)*: `backend/evals/agent_behavior/`. El harness **no llama al LLM
  por su cuenta**: el modo `live` ejecuta el agente real por el mismo
  `resolve_runner` del motor (T8.3) y le lee tokens y fuentes por la traza de T9.1,
  porque una copia del prompt mediría algo parecido al agente y las regresiones
  viven justo en esa diferencia. El modo `replay` reproduce salidas grabadas: es lo
  que hace la suite ejecutable sin Ollama —y el informe lo grita, porque confundir
  un `replay` con una medición real es el peor fallo posible aquí—. Lo que hace
  comparables dos informes va en el encabezado: modelo, proveedor, parámetros,
  versión **y hash** del dataset, y el commit. Las métricas de AC4 quedan para T9.4;
  aquí van tres deterministas que dan sentido al armazón.
- [x] **AC4** — *Given* el harness, *Then* existen **datasets *golden* versionados**
  en el repo y se computan **métricas de comportamiento** sobre los agentes de la
  plataforma: fidelidad de citas (las fuentes citadas existen en el RAG, no
  alucinadas), cumplimiento de formato (estructura APA/IEEE/… verificable),
  calibración del score del Revisor frente a referencia, coherencia, y presupuesto
  de tokens/latencia por agente.
  <br>*T9.4 (#225)*: un `<agente>-golden` por agente evaluable —investigador,
  redactor, revisor y formateador— que **pasa entero**, para que T9.5 pueda usarlo
  de línea base, y un `<agente>-regressions` que falla a propósito documentando qué
  se detecta. Las dos métricas que faltaban son las que no se calculan con una
  expresión regular: **`reviewer_calibration`** mide la distancia a una referencia
  humana y, sobre todo, **el lado del umbral de 80** —un 78 frente a un 82 son
  cuatro puntos y una decisión distinta del pipeline—; y **`coherence`** la juzga un
  modelo de la plataforma con rúbrica fija y `temperature=0` (§5), que hubo que
  añadir al dispatcher porque no la aceptaba. El juez lo pide el **runner**, donde
  se sabe el modo, así que en `replay` no se llama a nadie y sin veredicto la
  métrica se salta con motivo. El revisor, que decide y no escribe, se evalúa por su
  `decision`, leída con el mismo `decision_of` de T9.1. Los conjuntos son
  `handwritten` y el informe lo dice: fija qué mide cada métrica, no cómo se
  comporta el modelo — regrabarlos en `live` es el paso previo a endurecer T9.5.
- [x] **AC5** — *Given* un PR que toca perfiles, *prompts* o modelos de agentes
  (`backend/app/agents/*.agent.md`, `shared/llm.py`, `shared/agents_seed.py`),
  *When* corre la CI, *Then* el **gate EDD** ejecuta la suite de evals y **falla o
  avisa** si alguna métrica regresa por debajo de su umbral declarado.
  <br>*T9.5 (#226)*: `evals/agent_behavior/gate.py` + `thresholds.yaml` +
  `.github/workflows/edd-gate.yml`. **Las rutas que enumera este AC estaban
  obsoletas**: `backend/app/agents/` y `shared/llm.py` se movieron en T8.3/T8.4, y
  un gate que vigila rutas inexistentes da verde por no mirar nada — el workflow
  vigila las de verdad y hay un test que lo comprueba. El gate distingue dos
  desenlaces que se parecen: una **regresión** avisa (§5, porque los golden son
  `handwritten` y bloquear con esa evidencia fijaría una línea base ficticia),
  mientras que una **medición rota** —dataset que no carga, caso que revienta,
  umbral sobre una métrica que no se computa— **rompe en los dos modos**: no es
  que el agente haya empeorado, es que no se ha medido. El modo lo decide
  `thresholds.yaml`, no el workflow, para que endurecerlo sea un diff junto a los
  umbrales que endurece.
- [x] **AC6** — *Given* la disciplina EDD, *Then* está **documentada** (cuándo y
  cómo añadir un eval, DoR/DoD de evaluación, alcance limitado a modelos de la
  plataforma) y el área `area/evaluation` está dada de alta en validador, seed,
  gobernanza y backlog.
  <br>*T9.6 (#223)*: el alta del área ya estaba hecha en los cuatro sitios desde
  E9; lo que faltaba era la **disciplina**, que es la mitad que de verdad gobierna.
  [`docs/governance/edd-discipline.md`](../governance/edd-discipline.md) dice cuándo
  hay que tocar los evals —incluida la regla de que **un agente nuevo llega con su
  conjunto `golden`**, porque si no, nada defiende su comportamiento—, cómo se añade
  uno, y la frontera con `model_benchmark`. La DoR y la DoD de evaluación se
  enganchan a GOVERNANCE §5 y §6 en vez de vivir aparte, y su exigencia central es
  que **una regresión aceptada se explica y se baja el umbral en la misma PR**: si
  no, el relajo ocurre después y en silencio. `backend/evals/` y el workflow del
  gate tienen dueño en CODEOWNERS, porque relajar un umbral relaja la garantía de
  comportamiento.

## 4. Diseño propuesto

### 4.1 Explicabilidad (sustrato de datos)
- **Modelo de datos.** Tabla `agent_run_steps` (1‑N con `agent_runs`) o extensión
  de `agent_runs`, con: `run_id`, `correlation_id` (reusa T5.1), `agent_name`,
  `model`, `params` (JSON), `input_digest`, `output_text`, `rag_sources` (JSON:
  `[{doc_id, chunk_ids, score, title, authors}]`), `tokens_in`, `tokens_out`,
  `latency_ms`, `decision` (JSON: `score`, `coherent`, `hitl_outcome`),
  `rationale` (texto). Se escribe desde el orquestador al cerrar cada nodo.
- **API.** `GET /api/v1/agents/{article_id}/explain` → traza ordenada por paso e
  iteración (incluye el bucle Revisor→Redactor y los HITL). Reutiliza el control
  de acceso de los endpoints de agentes.
- **UI.** Panel "Por qué este resultado" en el detalle del artículo: timeline por
  paso, fuentes citadas con enlace al documento del RAG, score y comentarios del
  Revisor, decisiones del HITL, y coste (tokens/latencia) por agente.

### 4.2 EDD (harness de evaluación)
- **Ubicación.** `backend/evals/` con `datasets/` (casos *golden* en `*.jsonl`/YAML
  versionados), `runner.py` (ejecuta un **perfil de agente de la plataforma** sobre
  un dataset), `metrics/` (chequeos) y `results/` (informes con modelo+params).
- **Alcance de modelos.** El runner evalúa únicamente los **agentes/modelos
  configurados en la plataforma** (los perfiles y el `LLM_PROVIDER` activo). El
  juez (LLM-as-judge, donde se use) es también un **modelo de la plataforma**, no
  un servicio externo de evaluación.
- **Métricas de comportamiento.**
  - *Deterministas:* fidelidad de citas (cada fuente citada existe en el RAG),
    cumplimiento estructural de formato (APA/IEEE/Vancouver/Chicago/Nature),
    presupuesto de tokens/latencia por agente.
  - *Asistidas:* calibración del score del Revisor vs. referencia, coherencia
    (juez = modelo de la plataforma con rúbrica fija).
- **Reproducibilidad.** Cada *run* fija y registra modelo, parámetros y versión del
  dataset; resultados comparables entre PRs.

### 4.3 Gate EDD en CI
- Workflow/marca de `pytest` que, en PRs que tocan agentes/modelos, ejecuta la
  suite mínima de evals y compara contra **umbrales declarados** (por agente y
  métrica); reporta el diff y bloquea/avisa ante regresión. Publica los scores como
  métrica (engancha con T5.2 cuando exista).

### 4.4 Gobernanza
- Alta del área `area/evaluation` en `scripts/validate_specs.py` (ALLOWED_AREAS),
  `scripts/seed_github_project.py` (LABELS), [GOVERNANCE §7](../governance/GOVERNANCE.md)
  y el [backlog](../backlog/security-hardening-backlog.md). Decisión de adopción en
  [ADR-0006](../adr/0006-adopt-evaluation-driven-development.md).

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Evals con LLM son **no deterministas** y lentos | Falsos rojos en CI; pipelines largos | Priorizar métricas deterministas; juez con `temperature=0` y rúbrica fija; suite mínima en CI, suite completa nocturna |
| Coste de cómputo de evaluar modelos locales en CI | CI lenta/cara | Datasets pequeños y representativos; *gate* solo en PRs que tocan agentes/modelos; cachear modelos |
| La traza de explicabilidad **filtra PII** o contenido sensible | Fuga de datos | Guardar *digests*/resúmenes y `doc_id` en vez de texto íntegro donde aplique; respetar retención (E6/§8) |
| Umbrales mal calibrados | Bloqueos o falsos OK | Empezar en modo **aviso** (no bloqueante) y endurecer tras recolectar línea base |

## 6. Plan de pruebas

- **Unitarias:** escritura/lectura de `agent_run_steps`; serialización de la traza;
  cada métrica determinista (fidelidad de citas, formato, presupuesto) con casos
  fixture.
- **Integración:** ejecutar el pipeline en modo headless y verificar que la traza
  queda completa (AC1) y que `/explain` la devuelve (AC2); ejecutar el runner sobre
  un dataset *golden* mínimo y validar el informe reproducible (AC3/AC4).
- **CI:** el gate EDD corre sobre un PR de prueba que altera un `prompt_template` y
  demuestra que detecta la regresión (AC5).
- Cada AC se valida con al menos un test automatizado (DoD §6).

## 7. Impacto operativo / observabilidad

- Reutiliza `correlation_id` (T5.1) para hilar traza ↔ logs ↔ métricas.
- Expone scores de evals como métricas (T5.2) para seguir la evolución del
  comportamiento de los agentes en el tiempo.
- Migración de esquema vía la estrategia de datos vigente (idealmente Alembic, T4.1);
  *rollout* incremental: primero persistencia (T9.1), luego API/UI (T9.2), luego
  harness y gate (T9.3–T9.5). *Rollback*: el gate arranca como **no bloqueante**.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E9
  title: Explicabilidad y Evaluation-Driven Development
  area: area/evaluation
tasks:
  - id: T9.1
    title: Traza de explicabilidad por paso (agent_run_steps) persistida desde el orquestador
    sev: high
    depends_on: [T5.1]      # logging estructurado (SPEC-019, antes "#171")
    acceptance: [AC1]
  - id: T9.2
    title: Endpoint GET /agents/{id}/explain + panel UI "Por qué este resultado"
    sev: medium
    depends_on: [T9.1]
    acceptance: [AC2]
  - id: T9.3
    title: Harness EDD (backend/evals) que evalúa perfiles de agente de la plataforma de forma reproducible
    sev: high
    depends_on: []
    acceptance: [AC3]
  - id: T9.4
    title: Datasets golden versionados + métricas de comportamiento (citas, formato, calibración, coherencia, presupuesto)
    sev: medium
    depends_on: [T9.3]
    acceptance: [AC4]
  - id: T9.5
    title: Gate EDD en CI para PRs que tocan agentes/modelos (umbrales de regresión)
    sev: medium
    depends_on: [T9.3, T9.4]
    acceptance: [AC5]
  - id: T9.6
    title: Gobernanza EDD (alta de area/evaluation, DoR/DoD de evaluación, CODEOWNERS)
    sev: low
    depends_on: []
    acceptance: [AC6]
```
