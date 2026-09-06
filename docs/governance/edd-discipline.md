# Disciplina EDD — cuándo y cómo se evalúa un agente

Decisión: [ADR-0006](../adr/0006-adopt-evaluation-driven-development.md). Spec:
[SPEC-014](../specs/SPEC-014-explainability-and-edd.md). Área `area/evaluation`,
épica E9.

El SDD gobierna **qué** se construye. Para los componentes probabilísticos —los
agentes y el modelo que usan— eso no basta: un cambio puede cumplir su spec al pie
de la letra y **degradar la calidad en silencio**. Un artículo que cita fuentes
inventadas se lee igual de bien que uno que cita fuentes reales; un formateador que
pierde las citas numeradas de IEEE sigue produciendo texto correcto. Sin una medida,
la regresión aparece semanas después y ya nadie sabe qué la causó.

El EDD gobierna **cómo de bien se comporta** lo construido. Este documento dice
cuándo hay que evaluar, cómo se añade un eval, y qué se exige antes de empezar y
antes de cerrar.

## 1. Alcance: qué se evalúa y qué no

**Se evalúa el comportamiento de los agentes de esta plataforma**: sus perfiles, sus
*prompts* y el modelo que usan con el `LLM_PROVIDER` activo. El juez de las métricas
asistidas es también un **modelo de la plataforma**, no un servicio externo de
evaluación.

**No** se evalúan modelos *foundation* globales. Esa es otra pregunta y tiene su
propio sitio: `backend/evals/model_benchmark/` ([SPEC-025](../specs/SPEC-025-model-benchmark-scientific-writing.md)).
Comparten directorio y nada más; mezclarlos haría incomparables los dos conjuntos de
números.

| | `evals/agent_behavior/` | `evals/model_benchmark/` |
|---|---|---|
| Pregunta | ¿se comporta bien **este agente**? | ¿qué modelo elijo? |
| Spec | SPEC-014 (E9) | SPEC-025 (E13) |
| Corre en CI | sí, como gate | no |

## 2. Cuándo hay que tocar los evals

**Siempre que cambie algo que pueda mover el comportamiento**, aunque el cambio
parezca inocuo:

- el `prompt_template`, el `model` o la `temperature` de un agente
  (`backend/projects/*/agents/*.agent.md`, `backend/app/shared/agents_seed.py`);
- el código de un *adapter* de agente (`backend/app/modules/agents/adapters`), donde
  viven los prompts de verdad;
- el dispatcher de LLM (`backend/app/platform/llm.py`) o el motor
  (`backend/app/platform/engine`);
- el propio harness: una métrica, un dataset o un umbral
  (`backend/evals/agent_behavior`).

El [gate EDD](../../.github/workflows/edd-gate.yml) se dispara solo en esas rutas.
Que se dispare no significa que haya que tocar nada: significa que hay que **mirar
el informe** antes de mergear.

**Un agente nuevo llega con su conjunto `golden`.** Sin él, el gate no lo vigila y
su comportamiento no está defendido por nada — y eso no se nota hasta que regresa.

## 3. Cómo se añade un eval

1. **Escribe el caso** en `backend/evals/agent_behavior/datasets/<agente>-golden.jsonl`.
   Un caso por línea; la cabecera lleva `id`, `version` y `provenance`.
2. **Declara qué esperas** en `expect`: `min_citation_fidelity`,
   `scientific_format`, `required_sections`, `max_tokens_out`, `reference_score`,
   `min_coherence`… Solo se computan las métricas que el caso declara; el resto se
   **salta con motivo**, que no es lo mismo que aprobar.
3. **Graba la salida**. En `--mode live` la produce el agente; escrita a mano, el
   dataset debe declarar `provenance: handwritten`, porque entonces el eval mide la
   métrica y no al modelo, y el informe lo avisa.
4. **Si el caso debe fallar**, va a `<agente>-regressions`, nunca al `golden`: un
   conjunto de referencia con un rojo dentro no sirve de línea base.
5. **Ajusta `thresholds.yaml`** si el caso nuevo mueve la media, con su `baseline`.
6. **Corre el gate** (`python -m evals.agent_behavior.gate`) y comprueba que dice lo
   que esperas.

Para una **métrica** nueva: un módulo en `metrics/` que se registre. El runner no se
toca. Si necesita un juicio de modelo, el veredicto lo pide el **runner** —donde se
sabe el modo—, nunca la métrica, o `replay` dejaría de ser reproducible.

Ver `backend/evals/agent_behavior/README.md` para el detalle de cada campo.

## 4. DoR de evaluación — antes de implementar

Además de la [DoR general](GOVERNANCE.md#5-definition-of-ready-dor--antes-de-implementar),
cuando la tarea toca agentes o modelos:

- [ ] Está dicho **qué comportamiento puede romperse** con este cambio y qué métrica
      lo mediría. Si ninguna lo mide, la tarea incluye añadirla.
- [ ] Si el agente es nuevo, la tarea incluye su conjunto `golden`.
- [ ] Está claro si el eval podrá correr **sin modelo** (`replay`) o exigirá `live`;
      lo segundo no puede ser un requisito de la CI.

## 5. DoD de evaluación — para cerrar

Además de la [DoD general](GOVERNANCE.md#6-definition-of-done-dod--para-cerrar):

- [ ] El **gate EDD pasa**, o su regresión está **explicada y aceptada en la propia
      PR**. Si el comportamiento nuevo es el bueno, el umbral se baja **en esa misma
      PR**: así el relajo queda revisado en vez de ocurrir en silencio después.
- [ ] Los datasets tocados declaran su `provenance` y su `version`.
- [ ] Una métrica nueva viene con casos que demuestran que **muerde**: uno que pasa
      y uno que falla. Una métrica que nunca ha dado rojo no ha demostrado nada.

## 6. Estado del gate

Hoy corre en **modo aviso** (`enforce: false` en
`backend/evals/agent_behavior/thresholds.yaml`), como pide SPEC-014 §5. El motivo es
concreto: los conjuntos `golden` son `handwritten`, así que un verde dice que las
métricas funcionan, no que el modelo se comporte así. Bloquear PRs con esa evidencia
fijaría una línea base ficticia.

**Endurecerlo es una secuencia, y el orden importa:**

1. regrabar los `golden` con `--mode live` contra el modelo de la plataforma;
2. poner `provenance: recorded` en sus cabeceras;
3. recalcular los `baseline` de `thresholds.yaml`;
4. cambiar `enforce` a `true`.

Saltarse el primer paso deja una línea base ficticia bloqueando PRs, que es la forma
más rápida de que alguien desactive el gate.

> ⚠️ Mientras el gate tenga filtro de rutas, **no lo marques como check obligatorio**
> en la protección de rama: un check requerido que no se ejecuta deja la PR
> esperándolo para siempre.

## 7. Propiedad

`backend/evals/` y los umbrales tienen dueño en [CODEOWNERS](../../.github/CODEOWNERS):
relajar un umbral es relajar la garantía de comportamiento, y debe revisarlo alguien
igual que un cambio de seguridad o de gobernanza.
