# Tarea #226 — T9.5 Gate EDD de regresión en CI

## 2026-09-06 — Completada ✅

- **Rama:** `feat/226-edd-ci-gate`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #226`)
- **Spec/ADR:** SPEC-014, Épica E9, ADR-0006. Criterio vinculante: **AC5**.
- **Dependencias:** T9.3 (#222) y T9.4 (#225), ambas integradas. Con esta, E9 queda
  solo con T9.6 (#223).

## Qué se hizo

El gate que convierte el harness en algo que **defiende** el comportamiento en vez
de solo describirlo: `evals/agent_behavior/gate.py`, los umbrales declarados en
`thresholds.yaml`, y `.github/workflows/edd-gate.yml`, que lo dispara en los PRs
que tocan perfiles, prompts, adapters, el dispatcher, la siembra, el motor o el
propio harness.

```bash
python -m evals.agent_behavior.gate            # el modo lo dice thresholds.yaml
python -m evals.agent_behavior.gate --enforce  # forzar bloqueo
```

## Las rutas del AC ya estaban obsoletas

AC5 enumera `backend/app/agents/*.agent.md`, `shared/llm.py` y
`shared/agents_seed.py`. **Ninguna de las dos primeras existe**: se movieron en
T8.3/T8.4 — los perfiles viven en `backend/projects/<slug>/agents/` y el dispatcher
en `backend/app/platform/llm.py`. Copiar esa lista al workflow habría dado un gate
que no se dispara nunca, es decir, verde permanente por no mirar nada, que es
justamente el fallo que un gate debe no tener.

El workflow vigila las rutas reales, y hay un test que **comprueba que cada ruta
vigilada existe en el repo**. Es la guarda contra que esto vuelva a pasar: la
próxima reorganización romperá el test en vez de vaciar el gate en silencio.

## La distinción que hace útil al gate

Dos desenlaces se parecen y no son lo mismo:

| | Qué es | Aviso | Bloqueo |
|---|---|---|---|
| **Regresión** | una métrica baja de su umbral | informa, sale 0 | sale 1 |
| **Medición rota** | dataset que no carga, caso que revienta, umbral sobre una métrica que no se computa | **sale 1** | **sale 1** |

Lo segundo rompe **siempre**, a propósito. No es que el agente haya empeorado: es
que no se ha llegado a medir, y tragárselo en modo aviso dejaría un gate verde que
no mira nada — peor que no tenerlo, porque da confianza. El caso más sutil es el
tercero: declarar un umbral para una métrica que ningún caso computa es un gate que
cree vigilar algo y vigila el vacío.

## Decisiones documentadas

- **Arranca en modo aviso** (`enforce: false`), como pide SPEC-014 §5. Aquí la
  cautela no es formalismo: los conjuntos golden son **`handwritten`** (T9.4), así
  que un verde dice que las métricas funcionan, no que el modelo se comporte así.
  Bloquear una PR con esa evidencia sería fijar una línea base ficticia.
- **El modo lo decide `thresholds.yaml`, no el workflow.** Endurecer el gate debe
  ser un diff junto a los umbrales que endurece, revisable en la misma PR, no un
  cambio de infraestructura en otro fichero.
- **Cada umbral lleva `min` y `baseline`.** `min` es lo que el gate exige —una
  decisión—; `baseline`, lo que puntuaba al declararlo. Con `min` = `baseline` el
  gate se vuelve un trinquete donde cualquier edición del dataset es roja, y un gate
  que molesta sin distinguir se acaba desactivando. El margen es deliberado y
  distinto por métrica: cero en fidelidad de citas y cumplimiento de formato —o
  están o no—, ancho en coherencia, que la juzga un modelo.
- **Corre en `replay`.** Si llamara al modelo, la CI dependería de tener Ollama o
  una clave y el gate se caería por motivos ajenos al comportamiento. Hay un test
  que lo fija.
- **Workflow propio y no un job de `ci.yml`.** El filtro de rutas de GitHub Actions
  se aplica al workflow entero, no a un job: meterlo en `ci.yml` obligaría a correr
  todo el CI para filtrar después, o a añadir una acción de terceros solo para eso.
- **El informe va al resumen del job y a un artefacto**, además de a los logs: la
  tabla con el margen de cada métrica es lo que se mira, y buscarla en un log es
  fricción que hace que nadie la mire.
- **Si una regresión es deliberada, se baja el umbral en la misma PR.** El gate lo
  dice en su propia salida, para que el relajo quede revisado en vez de ocurrir en
  silencio en un commit aparte.

## Test nuevo

`backend/tests/test_edd_gate.py` (24 casos):

- **Los umbrales**: hay uno por cada agente con conjunto golden —uno sin umbral es
  un agente sin vigilar—; cada umbral vigila una métrica que **de verdad se
  computa**; la línea base no está ya por debajo del umbral —si lo estuviera, el
  gate nace rojo y nadie se fía—; y se declara de dónde salió esa línea base.
- **Los dos desenlaces**: el gate pasa sobre la línea base actual; una regresión se
  detecta; en aviso no rompe **pero se ve**; en bloqueo la misma regresión rompe; y
  el modo sale del fichero de umbrales.
- **Medición rota**: dataset que no carga, umbral de una métrica inexistente, agente
  sin métricas, y —el fallo silencioso más fácil de este diseño— **un gate que no
  compara nada no puede dar verde**. Todos rompen aunque el modo sea aviso.
- **La CLI** devuelve el código de salida del resultado: sin eso, el gate es
  decoración.
- **El workflow**: existe y ejecuta el gate, se dispara en PRs a `develop` con
  filtro de rutas, **cada ruta vigilada existe en el repo**, se vigilan los perfiles
  `.agent.md`, y el job no necesita modelo.

Verificado por mutación: degradar una medición rota a aviso, ignorar un umbral sobre
una métrica ausente, dar verde sin comparar nada, sacar el modo del fichero de
umbrales, dejar de vigilar los perfiles en el workflow, y poner el gate en `live`.
Cada mutación tumba su test.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 869 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python -m evals.agent_behavior.gate        # → ✅ 11 métricas, ninguna por debajo
python3 scripts/validate_specs.py          # → [OK]
```

Comprobados a mano los cuatro desenlaces con ficheros de umbrales sintéticos:
regresión en aviso (exit 0, con el aviso en stderr), la misma en bloqueo (exit 1),
dataset inexistente (exit 1 en aviso) y umbral de métrica no computada (exit 1).

## Definition of Done

- [x] **AC5** — el gate corre en los PRs que tocan agentes/modelos y avisa (o
  bloquea) ante regresión sobre umbrales declarados.
- [x] Tests que cubren el cambio, en verde (24 nuevos).
- [x] Docs: SPEC-014 anotada, `README.md` del harness y `CLAUDE.md`.
- [x] Sin secretos en el diff; sin dependencias nuevas (`pyyaml` ya estaba).
- [x] Rama con prefijo `feat/` hacia `develop`.

## Seguimiento

- **No marcar este check como obligatorio** en la protección de rama mientras tenga
  filtro de rutas: un check requerido que no se ejecuta deja la PR bloqueada
  esperándolo para siempre. Está avisado en el propio workflow y en `CLAUDE.md`.
- **Endurecer el gate es una secuencia, en este orden**: regrabar los golden con
  `--mode live` contra el modelo de la plataforma → poner `provenance: recorded` →
  recalcular los `baseline` → cambiar `enforce` a `true`. Saltarse el primero deja
  una línea base ficticia bloqueando PRs.
- **El gate solo mira los golden.** Los `*-regressions` fallan a propósito y no
  entran; si alguien los añadiera a `thresholds.yaml`, el gate nacería rojo.
- **T9.6 (#223)** cierra E9. Al mirarla, buena parte ya está hecha: `area/evaluation`
  está en el validador, en la siembra, en GOVERNANCE §7.1 y en el backlog. Lo que
  falta es CODEOWNERS para `/backend/evals/` y los criterios DoR/DoD de evaluación
  en §5/§6 — y ahora hay algo concreto que escribir en ellos, porque este gate
  define qué significa «pasar la evaluación».
