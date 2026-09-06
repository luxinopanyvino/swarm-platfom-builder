# Tarea #223 — T9.6 Gobernanza EDD

## 2026-09-06 — Completada ✅

- **Rama:** `docs/223-edd-governance`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #223`)
- **Spec/ADR:** SPEC-014, Épica E9, ADR-0006. Criterio vinculante: **AC6**.
- **Dependencias:** ninguna. **Con esta se cierra la épica E9.**

## Qué faltaba de verdad

AC6 pide dos mitades. Al abrir la tarea, la primera —el alta de `area/evaluation`—
**ya estaba hecha en los cuatro sitios** que enumera el criterio: el validador
(`scripts/validate_specs.py`), la siembra del Project (`seed_github_project.py`),
GOVERNANCE §7 y §7.1, y el backlog. Se fue haciendo por el camino en T9.1–T9.5.

Lo que faltaba era la otra mitad, que es la que de verdad gobierna: **la
disciplina**. Cuándo hay que tocar los evals, cómo se añade uno, y qué se exige
antes de empezar y antes de cerrar. Sin eso, el gate de T9.5 es una herramienta que
nadie sabe cuándo usar ni qué hacer cuando se pone rojo.

## Qué se hizo

- **`docs/governance/edd-discipline.md`** — el documento de disciplina, con la misma
  forma que `speckit-authoring-aids.md` y `data-retention.md`: alcance (y la
  frontera con `model_benchmark`), cuándo tocar los evals, cómo se añade uno, DoR y
  DoD de evaluación, el estado del gate y cómo endurecerlo, y la propiedad.
- **GOVERNANCE §5 y §6** enganchan la DoR/DoD de evaluación. **No viven aparte a
  propósito**: una lista que solo existe en su propio documento se aplica cuando
  alguien se acuerda; enganchada a la DoR/DoD general, se aplica al leerla.
- **GOVERNANCE §7.1** y el backlog apuntan al documento.
- **CODEOWNERS**: `/backend/evals/` y `.github/workflows/edd-gate.yml` tienen dueño.

## Decisiones documentadas

- **La exigencia central de la DoD de evaluación es que una regresión aceptada se
  explique y el umbral se baje en la *misma* PR.** Es el fallo silencioso que este
  gate tiene por diseño: aceptar el rojo hoy y relajar el umbral la semana que viene
  en un commit que nadie relaciona con la causa. Bajarlo en la misma PR convierte el
  relajo en algo revisado.
- **Un agente nuevo llega con su conjunto `golden`.** Sin él, el gate no lo vigila y
  su comportamiento no lo defiende nada — y eso no se nota hasta que regresa.
- **La DoR de evaluación pregunta qué puede romperse y qué métrica lo mediría**, y
  dice que si ninguna lo mide, añadirla es parte de la tarea. Es lo que impide que
  el conjunto de métricas se quede congelado en las cinco de T9.4 mientras los
  agentes cambian.
- **La frontera con `model_benchmark` va en el documento, con tabla.** Es el
  «alcance limitado a modelos de la plataforma» de AC6, y mezclarlos haría
  incomparables los dos conjuntos de números.
- **Los evals tienen dueño.** Relajar un umbral relaja la garantía de comportamiento,
  y bajar uno «para que pase la PR» es indistinguible de arreglar la regresión si
  nadie lo mira. También el workflow: sin dueño, se podría desactivar sin revisión.
- **Rutas repo-relativas y completas.** El documento escribía unas rutas enteras y
  otras a medias (`app/platform/llm.py`); un documento de gobernanza que da
  medias rutas manda a quien lo siga a buscar. Unificadas, y el test las comprueba.

## Test nuevo

`backend/tests/test_edd_governance.py` (23 casos). Probar documentación no es
formalismo aquí, por el mismo motivo que en `test_data_retention.py`: **una política
que no coincide con lo que hace el código es peor que no tener política**, porque
genera confianza infundada. Los casos cruzan el documento con la realidad:

- **El alta del área** en los cuatro sitios de AC6, y que el validador la acepte de
  verdad —no que la cadena aparezca, sino que esté en la lista que decide—.
- **La disciplina** cubre lo que AC6 enumera: cuándo, cómo se añade, DoR y DoD; fija
  el alcance nombrando `model_benchmark` y los modelos *foundation*; y exige el
  conjunto `golden` para un agente nuevo.
- **Los enganches**: GOVERNANCE apunta al documento, y la DoR y la DoD **generales**
  enganchan la de evaluación —comprobado sección por sección, no en el fichero
  entero—; y la DoD exige bajar el umbral en la misma PR.
- **Que el documento diga la verdad**: el modo que declara (aviso) es el que tiene
  `thresholds.yaml`, y **cada ruta que nombra existe en el repo**.
- **Los evals tienen dueño**, ignorando comentarios para que un `#` no cuente.

Verificado por mutación: endurecer el gate sin actualizar la disciplina, quitar de
la DoD la exigencia de la misma PR, dejar los evals sin dueño, caer el área del
validador, perder la frontera con el benchmark, y desenganchar la DoR general. Cada
mutación tumba su test.

El primero es el que más me importa: si alguien pone `enforce: true` y no actualiza
la gobernanza, el test lo para. Es lo que evita que este documento envejezca en
silencio, que es como envejece la documentación.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 892 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python3 scripts/validate_specs.py         # → [OK]
```

## Definition of Done

- [x] **AC6** — disciplina documentada (cuándo y cómo añadir un eval, DoR/DoD de
  evaluación, alcance limitado a los modelos de la plataforma) y `area/evaluation`
  dada de alta en validador, seed, gobernanza y backlog.
- [x] Tests que cubren el cambio, en verde (23 nuevos).
- [x] Docs: es la tarea. SPEC-014 anotada; **AC1–AC6 completos, E9 cerrada**.
- [x] Sin secretos en el diff; sin dependencias nuevas; sin cambios de código.
- [x] Rama con prefijo `docs/` hacia `develop`.

## Seguimiento

- **E9 queda cerrada** con esta tarea: AC1 (T9.1), AC2 (T9.2), AC3 (T9.3), AC4
  (T9.4), AC5 (T9.5) y AC6 (T9.6). La épica #220 se puede cerrar cuando se mergeen
  las PRs.
- **Lo que sigue vivo no es una tarea de E9 sino una deuda declarada**: los
  conjuntos `golden` son `handwritten` y el gate corre en modo aviso. La secuencia
  para endurecerlo está escrita en el documento y en `thresholds.yaml`, y ahora hay
  un test que impide hacerlo a medias.
- **Este documento tiene una fecha de caducidad natural**: cuando el gate pase a
  `enforce: true`, su §6 hay que reescribirla. El test lo obligará.
