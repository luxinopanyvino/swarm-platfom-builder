# Harness EDD — comportamiento de los agentes

SPEC-014 (épica E9) · ADR-0006 · tarea T9.3 (#222)

Evalúa **los agentes de esta plataforma**: sus perfiles, sus prompts y el modelo
que cada uno usa con el `LLM_PROVIDER` activo. No compara modelos *foundation* —
eso es `evals/model_benchmark/` (SPEC-025), y es un esfuerzo distinto con otra
pregunta.

## Por qué

Cambiar el `prompt_template`, el `model` o la `temperature` de un agente puede
**degradar la calidad en silencio**. Un artículo que cita fuentes inventadas se
lee igual de bien que uno que cita fuentes reales; un formateador que pierde las
citas numeradas de IEEE sigue produciendo texto correcto. Sin una medida, la
regresión aparece semanas después y ya nadie sabe qué la causó.

## Cómo se ejecuta

```bash
# Desde backend/. Reproducible y sin servicios externos.
python -m evals.agent_behavior.runner --dataset redactor-smoke --mode replay

# Contra el agente real, con el modelo y los parámetros de la plataforma.
python -m evals.agent_behavior.runner --dataset redactor-smoke --mode live

# Guardar el informe (JSON para máquinas, markdown para personas).
python -m evals.agent_behavior.runner --dataset redactor-smoke --out evals/results
```

Sale con **código 1** si algún caso falla, para que un gate pueda usarlo tal cual.

## Los dos modos, y por qué son dos

AC3 pide un informe **reproducible** «sin depender de servicios externos no
declarados». Son dos exigencias que tiran en direcciones opuestas: medir el
comportamiento real obliga a llamar al modelo, y un modelo generativo ni da dos
veces lo mismo ni está disponible en la CI.

| Modo | Qué mide | Determinista |
|---|---|---|
| `live` | El agente real, con su modelo y sus parámetros. **Mide el comportamiento.** | No |
| `replay` | Las métricas y el harness, sobre salidas grabadas. **No evalúa al modelo.** | Sí |

Confundirlos sería lo peor que le puede pasar a este harness —un `replay`
presentado como prueba de que el agente va bien—, así que el modo aparece en el
informe, en el nombre del fichero y con un aviso en la cabecera del markdown.

En `live` el informe no es byte a byte repetible, y no pretende serlo: lo que lo
hace comparable es que registra **con qué se hizo** (modelo, proveedor, versión y
hash del dataset, commit).

## Datasets

Un `.jsonl`: cabecera con `id` y `version`, y un caso por línea. JSONL porque un
dataset crece por líneas, y así el diff de una PR enseña qué caso se añadió.

| Dataset | Para qué |
|---|---|
| `<agente>-golden` | **Línea base.** Pasa entero, así que sirve de gate (T9.5). Uno por agente evaluable: investigador, redactor, revisor y formateador. |
| `<agente>-regressions` | **Falla a propósito**: un caso por regresión detectable. Documentación ejecutable de qué se mide, no un gate. |
| `redactor-smoke` | El mínimo de extremo a extremo, de T9.3. |

El publicador no tiene conjunto: publica, no genera texto ni decide, así que no
hay comportamiento probabilístico que medir.

### De dónde salen las salidas grabadas

La cabecera declara `provenance`, y el informe la repite:

| Valor | Qué significa |
|---|---|
| `recorded` | Salidas de una ejecución real del agente. Es evidencia del modelo. |
| `handwritten` | Salidas escritas a mano. Es evidencia de que **la métrica** funciona, no de cómo se comporta el modelo. |

Los conjuntos de este repo son **`handwritten`**: se escribieron para fijar qué
mide cada métrica, sin un modelo disponible donde se construyeron. Regrabarlos
con `--mode live` contra el modelo de la plataforma es lo primero que conviene
hacer antes de endurecer el gate de T9.5, y entonces la cabecera pasa a
`recorded`. El aviso del informe está para que nadie confunda las dos cosas
mientras tanto — mismo criterio que el aviso de `replay`.

Campos de un caso:

```jsonc
{
  "id": "cita-fiel",
  "agent": "redactor",
  "input": { /* claves del AgentState que el agente lee */ },
  "corpus": [ /* documentos que existen de verdad: la referencia de las citas */ ],
  "expect": {
    "min_citations": 1,
    "min_citation_fidelity": 100,
    "scientific_format": "apa",
    "required_sections": ["Introducción"],
    "max_tokens_out": 800
  },
  "recorded_output": "…",          // para --mode replay
  "recorded_usage": {"tokens_in": 320, "tokens_out": 210}
}
```

### Un caso que use RAG tiene que declarar su proyecto

En `live`, el estado del agente se construye a partir de `input`. Si el agente
recupera documentos, el `input` debe traer `project_id`: el nombre de la colección
de Qdrant **se deriva de él** (T8.5), y sin él la capacidad de RAG avisa y cae al
espacio heredado, que en una evaluación no es lo que se quiere medir.

## Métricas

Todas puntúan de **0 a 100**. La escala única hace comparables cosas de
naturalezas distintas y permite declarar umbrales (T9.5) sin traducir unidades.

| Métrica | Qué comprueba | Se salta cuando |
|---|---|---|
| `citation_fidelity` | Que cada `[Fuente: X]` exista en el corpus del caso. | El caso no trae corpus. |
| `format_compliance` | Que aparezca el estilo de cita pedido y las secciones exigidas. | El caso no declara ninguno. |
| `budget` | Margen frente al presupuesto de tokens y latencia. | El caso no declara presupuesto. |
| `reviewer_calibration` | Distancia del score del revisor a una referencia humana, **y si cae del mismo lado del umbral de 80**. | El caso no declara `reference_score`. |
| `coherence` | Coherencia interna del texto, según un juez con rúbrica fija. | El caso no declara `min_coherence`, o no hay veredicto. |

Las tres primeras son deterministas. Las dos últimas son las **asistidas** de
AC4, y son las que cazan lo que una expresión regular no ve: un revisor que
puntúa 85 lo que merece 62 rompe el pipeline con un número perfectamente formado,
y un texto puede citar bien, cumplir APA, caber en el presupuesto y contradecirse
entre la metodología y los resultados.

### El juez

`coherence` necesita un juicio, y ahí hay tres reglas que vienen de la spec:

* **El juez es un modelo de la plataforma** (§4.2), llamado por el mismo
  dispatcher que los agentes. No es un servicio externo de evaluación.
* **Rúbrica fija y `temperature=0`** (§5). Un juez que cambia de criterio entre
  ejecuciones convierte el gate en un generador de rojos aleatorios: la métrica
  dejaría de medir al agente para medir al juez. La rúbrica está versionada
  (`RUBRICA_VERSION`): cambiarla cambia lo que significan los números anteriores.
* **En `replay` no se llama a nadie.** El veredicto se graba en el caso
  (`recorded_judgement`) igual que la salida; sin él, la métrica **se salta con
  motivo**. Ni puntúa 100 —aprobar por no haber mirado— ni 0 —hacer fallar el
  gate por una evaluación que no se hizo—.

Quien llama al juez es el runner, no la métrica: ahí es donde se sabe el modo, y
así las métricas siguen siendo funciones puras. Es el mismo reparto que en T9.1.

### El revisor no escribe: decide

Su salida no es texto, así que `output` queda vacío y su caso graba
`recorded_decision` (`{score, coherent, hitl_outcome}`). El runner la pone en
`CaseResult.decision` leyéndola por el mismo `explainability.decision_of` que usa
la traza: si el revisor cambia de forma, cambia en un sitio.

Una métrica que no aplica **se salta con motivo**, no puntúa 100: aprobar por no
haber mirado es mentir con buena nota.

Añadir una métrica es añadir un módulo en `metrics/` que se registre; el runner no
se toca.

## El gate de regresión (T9.5)

```bash
python -m evals.agent_behavior.gate            # el modo lo dice thresholds.yaml
python -m evals.agent_behavior.gate --enforce  # forzar bloqueo
```

Corre el `golden` de cada agente y compara las medias por métrica contra
`thresholds.yaml`. Lo dispara `.github/workflows/edd-gate.yml` en los PRs que tocan
perfiles, prompts, adapters, el dispatcher, la siembra, el motor o este harness.

Distingue **dos desenlaces que se parecen y no lo son**:

| | Qué es | Modo aviso | Modo bloqueo |
|---|---|---|---|
| **Regresión** | una métrica baja de su umbral | informa, sale con 0 | sale con 1 |
| **Medición rota** | dataset que no carga, caso que revienta, umbral sobre una métrica que no se computa | **sale con 1** | **sale con 1** |

Lo segundo rompe siempre a propósito: no es que el agente haya empeorado, es que no
se ha llegado a medir, y tragárselo dejaría un gate verde que no mira nada.

Cada entrada de `thresholds.yaml` lleva `min` —lo que el gate exige, una decisión— y
`baseline` —lo que puntuaba al declararlo—. El margen entre los dos es deliberado:
con `min` = `baseline`, cualquier edición del dataset es roja y el gate se acaba
desactivando. Si una regresión es deliberada, baja el umbral **en la misma PR**: así
el relajo queda revisado en vez de ocurrir en silencio.

## Qué **no** hace todavía

- **El gate avisa, no bloquea** (`enforce: false`). Es lo que pide SPEC-014 §5, y
  aquí tiene un motivo concreto: los conjuntos son `handwritten`, así que bloquear
  con esa evidencia fijaría una línea base ficticia.
- Los conjuntos aún no se han **grabado contra un modelo real** (ver
  «procedencia»). Regrabarlos en `--mode live`, poner `provenance: recorded`,
  recalcular los `baseline` y cambiar `enforce` a `true` es, en ese orden, lo que
  convierte el gate en un gate de verdad.
