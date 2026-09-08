# Protección de `develop` (SPEC-020 / AC1)

AC1 tiene dos mitades y son de naturaleza distinta:

1. **Que la CI ejecute lo que debe** — pytest backend, build frontend, validación
   de specs y escaneo de secretos. Vive en `.github/workflows/ci.yml`, es código,
   entra por PR y la revisa cualquiera.
2. **Que una PR en rojo no se pueda mergear** — eso *no* vive en el repositorio:
   es configuración del servidor (Settings → Rules). Nadie la revisa en un diff,
   no deja rastro en el historial y se puede desactivar sin que se entere nadie.

Este documento y `.github/rulesets/develop.json` existen para cerrar esa asimetría:
la regla se escribe **como artefacto versionado**, se revisa como cualquier otro
cambio y un test la cruza con la realidad del workflow. Aplicarla sigue siendo un
acto manual —GitHub no lee reglas desde el repo—, pero deja de ser folclore.

## Aplicar la regla

1. GitHub → **Settings → Rules → Rulesets → New ruleset → Import a ruleset**.
2. Sube [`.github/rulesets/develop.json`](../../.github/rulesets/develop.json).
3. Comprueba que queda en **Active** (no *Evaluate*: en ese modo informa y no
   bloquea, que es justo lo que AC1 no quiere).

Si ya existe un ruleset previo para `develop`, **edítalo o bórralo**; dos rulesets
sobre la misma rama se acumulan y el resultado efectivo deja de ser legible.

## Qué impone y por qué

| Regla | Qué hace | De dónde sale |
|---|---|---|
| `required_status_checks` | Los 5 jobs de `ci.yml` deben estar en verde. | AC1 (los cuatro primeros) y GOVERNANCE §4 (que añade el escaneo de dependencias). |
| `strict_required_status_checks_policy` | La rama debe estar al día con `develop` antes de mergear. | Sin esto, dos PR verdes por separado pueden romper `develop` al juntarse: cada una se probó contra una base que ya no existe. Cuesta un botón de *Update branch* por PR; es el precio de que «verde» signifique algo. |
| `pull_request` | Prohíbe el push directo: todo entra por PR. | GOVERNANCE §3. Es la contraparte en servidor del hook local de `.claude/hooks/`, que solo protege a quien tenga el repo clonado con los hooks puestos. |
| `require_code_owner_review` | Las áreas de `.github/CODEOWNERS` piden a su owner. | GOVERNANCE §4. |
| `non_fast_forward` | Prohíbe el force-push sobre `develop`. | GOVERNANCE §3; también es lo que hace irreparable un historial compartido. |
| `deletion` | Prohíbe borrar la rama. | Idem. |
| `bypass_actors: []` | Nadie salta la regla. | Un bypass permanente para admins convierte el gate en una sugerencia. Para una emergencia real, un admin puede desactivar el ruleset: eso queda en el log de auditoría de la organización, y un bypass silencioso no. |

## Dos decisiones que conviene entender antes de tocarlas

**`required_approving_review_count` está en `0`, y GOVERNANCE §4 pide 1.**
No es un descuido ni una relajación de la política: GitHub no permite aprobar tu
propia PR, así que en un repositorio con un solo mantenedor un `1` bloquea el
100% de las PR y deja el proyecto sin salida. La política sigue siendo 1; lo que
falta es una segunda persona que pueda ejercerla. **En cuanto haya un segundo
revisor, sube ese número a `1`** —es una línea del JSON— y reimporta. Mientras
tanto la revisión efectiva la aportan la review automática de Claude
(`.github/workflows/claude-code-review.yml`) y los checks obligatorios.

**`edd-gate` NO está entre los checks obligatorios, y es deliberado.**
`.github/workflows/edd-gate.yml` tiene filtro de rutas: solo corre en las PR que
tocan perfiles, prompts, adapters, el dispatcher, la siembra, el motor o el propio
harness. Un check obligatorio que **no llega a ejecutarse** se queda en *Expected*
para siempre y bloquea la PR sin remedio, así que marcar `edd-gate` como requerido
no endurece nada: rompe todas las PR que no tocan evaluación. Lo mismo vale para
cualquier workflow futuro con `paths:`. Ver
[edd-discipline.md](edd-discipline.md) §6.

## Cuando cambies `ci.yml`

GitHub identifica un check obligatorio por su **nombre visible**, que es el `name:`
del job, no su identificador YAML. Renombrar `name: Backend · pytest` sin tocar el
ruleset produce el peor fallo posible aquí: el check requerido pasa a no existir
—la PR se queda esperando eternamente— o, si además se elimina del ruleset, el gate
deja de cubrir los tests **y todo sigue en verde**.

Por eso `backend/tests/test_branch_protection.py` cruza las dos fuentes: si el
nombre de un job y el `context` del ruleset dejan de coincidir, el test falla en la
misma PR que introduce el cambio. Si añades un job que deba ser obligatorio,
añádelo al JSON y **reimporta el ruleset** — el test verifica el fichero, no lo que
GitHub tenga aplicado.

## Verificar que está puesto

Ningún test puede comprobar esto desde dentro del repositorio: es estado del
servidor. Se comprueba a mano, y basta con mirar una PR abierta:

- En Settings → Rules → Rulesets, `develop` aparece **Active**.
- En una PR cualquiera contra `develop`, la caja de merge lista los cinco checks
  como *Required* y el botón de merge está deshabilitado mientras alguno esté rojo
  o pendiente.

Si algún día la respuesta a «¿está puesto?» vuelve a ser «creo que sí», la respuesta
correcta es abrir una PR de prueba y mirar la caja de merge.
