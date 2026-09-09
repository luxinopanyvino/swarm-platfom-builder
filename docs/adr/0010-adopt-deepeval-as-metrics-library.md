# ADR-0010: Adoptar deepeval como **librería de métricas**, no como framework de evaluación

- **Estado:** Propuesto
- **Fecha:** 2026-09-09
- **Decisores:** Equipo de plataforma
- **Relacionado:** [ADR-0006](0006-adopt-evaluation-driven-development.md) (EDD),
  [SPEC-014](../specs/SPEC-014-explainability-and-edd.md),
  [SPEC-025](../specs/SPEC-025-model-benchmark-scientific-writing.md),
  [SPEC-027](../specs/SPEC-027-model-evaluation-deepeval.md),
  [edd-discipline.md](../governance/edd-discipline.md)

## Contexto

Este repo ya tiene **dos** superficies de evaluación y una frontera escrita entre
ellas (ADR-0006):

- `backend/evals/agent_behavior/` evalúa **los agentes de esta plataforma** —sus
  perfiles, sus prompts y el modelo activo— sobre datasets versionados, con un
  registro de métricas, un runner con modos `replay`/`live`, umbrales declarados en
  `thresholds.yaml` y un gate en CI (`edd-gate.yml`).
- `backend/evals/model_benchmark/` compara modelos *foundation* entre sí (SPEC-025).

Lo que falta no es infraestructura, es **cobertura de métrica**. Las cinco métricas
existentes —`citations`, `format_compliance`, `calibration`, `coherence`,
`budget`— miden la salida del agente. **Ninguna mide la recuperación.** El pipeline
es RAG y su mitad RAG no se evalúa: no hay fidelidad al contexto recuperado, ni
detección de afirmaciones no respaldadas, ni precisión/exhaustividad del *retrieval*.
Un cambio en el modelo de embeddings, en el *chunking* o en el `top_k` puede
degradar la recuperación sin mover ninguna métrica.

Escribir esas métricas a mano es posible —`coherence` ya lo hace con juez propio y
rúbrica fija— pero cada una cuesta un módulo, una rúbrica y su calibración. Es
trabajo que una librería madura ya ha hecho.

**La tentación, y el riesgo.** deepeval trae también su propio runner, su
integración con pytest, su manejo de datasets y su noción de pass/fail. Adoptarlo
entero significaría **dos fuentes de verdad** para lo mismo: dos sitios donde vive
un umbral, dos sitios que deciden si una PR pasa, y la pérdida del modo `replay`
—que es lo que hace la suite ejecutable sin Ollama— y de la lectura de tokens y
fuentes por la traza de T9.1.

**Y hay un precedente que respetar.** ADR-0006 rechazó explícitamente un «servicio
SaaS de evaluación de LLMs» por coste y dependencia externa. deepeval **es dos
cosas**: una librería OSS y Confident AI, su plataforma alojada, a la que el
paquete envía telemetría por defecto. Esa distinción es el eje de esta decisión, no
una nota al pie.

## Decisión

Adoptamos deepeval **como librería de métricas dentro del harness que ya existe**.
No se adopta como framework.

1. **Las métricas de deepeval entran por el registro que ya hay.**
   `evals/agent_behavior/metrics/__init__.py` documenta el punto de extensión:
   *«añadir una métrica sea añadir un módulo, no editar el runner»*. Una métrica de
   deepeval es un módulo más en `metrics/`, registrado por nombre, normalizado a la
   **escala 0–100** común y con su `applies_to` — que se salta con motivo cuando el
   caso no le da de qué hablar, en vez de puntuar 100.

2. **El runner, los datasets, `thresholds.yaml` y `gate.py` no cambian.** Sigue
   habiendo un solo sitio donde vive un umbral y un solo gate que decide.

3. **El juez sale por el dispatcher de la plataforma.** deepeval instancia por
   defecto su propio cliente de OpenAI. Aquí se le pasa un `DeepEvalBaseLLM` que
   llama a `platform/llm.py` con `temperature=0`, como manda ADR-0006 §2. Esto no
   es cosmético: es lo que mantiene una sola puerta de salida hacia proveedores, la
   política de egress (SPEC-024) y la cascada de resolución de modelo. Habrá un test
   estructural que falle si aparece un cliente propio.

4. **Sin telemetría y sin Confident AI.** `DEEPEVAL_TELEMETRY_OPT_OUT=1` fijado en
   el entorno de evaluación y documentado en `.env.example`; no se usa el login ni
   la subida de resultados. Lo que ADR-0006 rechazó —el servicio— sigue rechazado;
   lo que se adopta es la librería.

5. **Las métricas con juez no son deterministas, y eso ya tiene reglas.** No se
   ejecutan en `replay` —igual que `coherence` hoy, que se salta con motivo si no
   hay veredicto grabado— y **no pueden endurecerse como gate** mientras los
   datasets golden sigan siendo `handwritten`. El modo lo decide `thresholds.yaml`,
   no el workflow.

6. **La frontera con `model_benchmark` no se mueve.** deepeval entra en
   `agent_behavior` —los agentes de esta plataforma—; comparar modelos *foundation*
   sigue siendo asunto de SPEC-025. Si algún día conviene usarlo también allí, será
   otra decisión.

## Alternativas consideradas

- **Seguir escribiendo las métricas a mano.** Es lo que se ha hecho hasta ahora y
  funciona; el coste es que cada métrica de recuperación —fidelidad, alucinación,
  precisión contextual— hay que diseñarla, escribirla y calibrarla. Rechazada por
  coste, no por principio: si deepeval se volviera un problema, volver aquí es
  barato, porque el seam es el registro de métricas.
- **Adoptar deepeval entero** (runner, datasets, pytest, pass/fail). Rechazada:
  duplica el gate y los umbrales, pierde `replay` y la lectura de tokens/fuentes
  por la traza. El repo ya pagó una vez el precio de tener dos fuentes de verdad.
- **RAGAS** en lugar de deepeval. Cubre bien la parte RAG y la elección entre las
  dos es defendible en ambos sentidos. Se elige deepeval por catálogo más amplio
  (G-Eval además de las métricas RAG) y por permitir un juez propio con una
  interfaz explícita. **Lo importante de esta decisión no es cuál de las dos**, sino
  que entra por el registro de métricas: cambiar de librería no debería tocar el
  runner ni el gate.

## Consecuencias

- (+) El pipeline RAG pasa a tener métricas de recuperación, que hoy no tiene.
- (+) Añadir una métrica de juez deja de costar una rúbrica escrita a mano.
- (+) La sustitución es barata: el acoplamiento se limita a los módulos de
  `metrics/` y al puente del juez.
- (−) Una dependencia grande y de evolución rápida en `requirements-dev`. Se acota
  al harness: **nada de `app/` la importa**.
- (−) Las métricas con juez cuestan llamadas al modelo en `live` y no son
  reproducibles al 100 %. Es la misma naturaleza que `coherence`; la mitigación es
  la misma (rúbrica fija, `temperature=0`, y no gatear con ellas todavía).
- (~) Mientras los golden sean `handwritten`, un verde de estas métricas dice que
  **la métrica** funciona, no que el modelo se comporte así. El informe ya lo avisa
  y debe seguir haciéndolo.
