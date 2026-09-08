# Tarea 175 — T6.1 · CI en PRs: lint + pytest + build frontend

- **Fecha:** 2026-09-08
- **Issue:** [#175](https://github.com/luxinopanyvino/swarm-platform-builder/issues/175) · Épica E6 · [SPEC-020](../specs/SPEC-020-governance-supply-chain.md) / AC1
- **Rama:** `chore/175-branch-protection`

## Qué pedía la tarea y qué se encontró

AC1 tiene dos mitades y no son de la misma naturaleza:

1. Que la CI ejecute pytest backend, build frontend, validación de specs y escaneo
   de secretos en cada PR a `develop`.
2. Que **una PR en rojo no se pueda mergear** (branch protection).

Al revisar el estado real, la **primera mitad ya estaba hecha**:
`.github/workflows/ci.yml` se dispara con `on: pull_request: branches: [develop]` y
tiene los cuatro elementos como jobs (`backend-tests`, `frontend-build`,
`specs-validate`, `secret-scan`), más `deps-audit` de AC2. Nada que implementar ahí.

La **segunda mitad no estaba**, y el propio workflow lo delataba: su comentario de
cabecera dice *«configurar branch protection aparte»*. Es decir, se dejó apuntada y
nunca se cerró.

El problema de fondo es que esa segunda mitad no vive en el repositorio: es
configuración del servidor de GitHub. No entra por PR, no aparece en un diff, nadie
la revisa y se puede desactivar sin dejar rastro visible. Un gate así es una
costumbre, no un control.

## Qué se hizo

**La regla, escrita como artefacto versionado.**
[`.github/rulesets/develop.json`](../../.github/rulesets/develop.json) en el formato
de import/export de rulesets de GitHub: los cinco checks obligatorios, PR obligatoria
(no push directo), revisión de CODEOWNERS, prohibido force-push y borrado de la rama,
y `bypass_actors` vacío. Se importa desde Settings → Rules → Import a ruleset.

**La guía.** [`docs/governance/branch-protection.md`](../governance/branch-protection.md):
cómo aplicarla, qué impone cada regla y de qué artículo de GOVERNANCE sale, las dos
decisiones que conviene entender antes de tocarlas, y cómo verificar a mano que está
puesta. GOVERNANCE §3 y §4 la enlazan.

**El test que la mantiene viva.** `backend/tests/test_branch_protection.py` (20 casos)
cruza el ruleset con el workflow. Persigue un fallo concreto y silencioso: GitHub
identifica un check obligatorio por el `name:` visible del job, así que renombrar un
job deja el check requerido apuntando a algo que ya no existe —la PR se queda en
*Expected* para siempre— o, si se limpia el ruleset sin pensar, el gate deja de
cubrir los tests **y todo sigue en verde**. Ahora ese renombrado falla en la misma PR
que lo introduce.

## Dos decisiones que no son obvias

**`required_approving_review_count` = 0, y GOVERNANCE §4 pide 1.** GitHub no permite
aprobar tu propia PR: con un solo mantenedor, un `1` bloquea el 100% de las PR y deja
el proyecto sin salida. La política sigue siendo 1; lo que falta es una segunda
persona que la ejerza. Está documentado, es una línea del JSON, y hay un test que
falla si alguien baja ese número **sin** dejar la justificación escrita — una
desviación explicada es una decisión; una desviación silenciosa es una política que
ha dejado de ser cierta.

**`edd-gate` NO es un check obligatorio, deliberadamente.** Tiene filtro de rutas: un
check requerido que no llega a ejecutarse se queda en *Expected* indefinidamente y
bloquea toda PR que no toque evaluación. Marcarlo no endurecería nada, rompería el
resto. Hay un test que lo impide.

## Verificación

- `pytest tests/test_branch_protection.py` → 20 pasan.
- Suite backend completa en verde (ver PR).
- `scripts/validate_specs.py` → OK.
- **Mutación** (6 mutaciones, las 6 detectadas): renombrar `name: Seguridad · gitleaks`
  en `ci.yml`; quitar gitleaks de los checks obligatorios; añadir `edd-gate` a los
  obligatorios; poner `enforcement: evaluate`; añadir un `bypass_actors` para admins;
  borrar del documento la justificación del número de aprobaciones.

## Cumplimiento del DoD

| Criterio (GOVERNANCE §6) | Estado |
|---|---|
| Criterios de aceptación cumplidos | **Parcial y a propósito.** La mitad que es código está hecha y probada; la mitad que es configuración de servidor está escrita, documentada y lista para importar, pero **importarla es un acto manual del mantenedor** — no hay herramienta de branch protection ni acceso a la API de GitHub en el entorno del agente. AC1 queda sin marcar hasta que se importe. |
| Tests que cubren el cambio, en verde | Sí (20 casos + 6 mutaciones). |
| Docs/spec/ADR actualizados | Sí: `branch-protection.md` nuevo, GOVERNANCE §3 y §4 enlazan, SPEC-020 AC1 y §4 anotados. |
| Sin secretos en el diff | Sí. |

## Lo que queda, y quién lo tiene que hacer

Un paso, del mantenedor, de un minuto:

1. Settings → Rules → Rulesets → New ruleset → **Import a ruleset** →
   `.github/rulesets/develop.json`.
2. Confirmar que queda en **Active** (no *Evaluate*: en ese modo informa y deja
   mergear igual).
3. Abrir cualquier PR contra `develop` y comprobar que la caja de merge lista los
   cinco checks como *Required* y que el botón está deshabilitado en rojo.

Hecho eso, AC1 está cumplido de verdad y #175 se puede cerrar.
