# ADR-0007: Adoptar Spec Kit como capa de autoría del SDD

- **Estado:** Aceptado
- **Fecha:** 2026-07-04
- **Decisores:** Equipo de plataforma
- **Relacionado:** ADR-0002 (SDD), [GOVERNANCE.md](../governance/GOVERNANCE.md),
  [speckit-authoring-aids.md](../governance/speckit-authoring-aids.md)

## Contexto

Este repo practica Spec-Driven Development propio (ADR-0002): la **definición**
vive en `docs/specs/` + `docs/adr/`, el **estado de ejecución** en el GitHub
Project, y el ciclo lo mueven `/sdd-sync`, `/resolve-task` y el gate
`scripts/validate_specs.py`. Tres skills de autoría tomadas de
[github/spec-kit](https://github.com/github/spec-kit) (`clarify`, `checklist`,
`analyze`) existían como complemento **opcional** sin uso registrado
(sin checklists generados ni secciones `## Clarifications`).

Dos síntomas mostraron el coste de saltarse la fase de autoría:

1. **Cobertura incompleta**: solo E7/E8/E9 tenían specs con bloque `sdd-sync`
   completo; 21 tareas de E1–E6 vivían solo como issues de bootstrap sin
   criterios de aceptación formales.
2. **Infra-especificación**: T8.1 (#207) incluía "unificar HTTP" en el título
   sin respaldo en ningún AC, y hubo que dejarlo fuera de alcance sobre la
   marcha. Un pase de `clarify`/`checklist` sobre SPEC-013 lo habría aflorado
   antes de sembrar la tarea.

## Decisión

1. **Adoptar Spec Kit como la capa de autoría del SDD**, con este mapeo (los
   artefactos de Spec Kit **no** se instalan; se reinterpretan sobre los
   nuestros):

   | Spec Kit | Aquí |
   |---|---|
   | *constitution* | `docs/governance/GOVERNANCE.md` |
   | `/speckit-specify` (spec.md) | `/speckit-specify` → `docs/specs/SPEC-NNN` desde `TEMPLATE.md` |
   | `/speckit-clarify` | `/speckit-clarify` (graba `## Clarifications` en la spec) |
   | `/speckit-checklist` | `/speckit-checklist` → `docs/specs/checklists/` |
   | `/speckit-analyze` | `/speckit-analyze` (SPEC ↔ ADR ↔ bloque `sdd-sync`) |
   | `plan.md` | Sección 4 (*Diseño propuesto*) de la spec |
   | `tasks.md` | Bloque `sdd-sync` (sección 8) de la spec |
   | `/speckit-implement` | `/resolve-task <#>` (agente `task-runner`) |

2. **El pipeline de autoría `specify → clarify → checklist → analyze` pasa a ser
   el paso recomendado de la Definition of Ready** (GOVERNANCE §5): una spec no
   debería declararse `Ready` sin al menos un pase de `clarify` y un `checklist`
   del dominio dominante. Sigue siendo **no bloqueante en CI**: el gate duro
   continúa siendo `scripts/validate_specs.py`.

3. **Toda épica del GitHub Project debe estar respaldada por una spec** con
   bloque `sdd-sync` que declare sus tareas. Las tareas sembradas por el
   bootstrap sin spec se **adoptan** retroactivamente en specs (SPEC-015…020);
   `/sdd-sync --apply` las reconcilia por título (`ADOPT`) sin tocar su estado.

4. **No se adopta** el andamiaje `.specify/`, ni `/speckit-plan`,
   `/speckit-tasks` o `/speckit-implement` como comandos separados: duplicarían
   la sección 4, el bloque `sdd-sync` y `/resolve-task`, creando dos fuentes de
   verdad.

## Consecuencias

- (+) Cobertura 1:1 épicas ↔ specs: el backlog completo queda gobernado por la
  fuente de verdad y reconciliable con `/sdd-sync`.
- (+) Menos retrabajo por specs ambiguas (caso "unificar HTTP").
- (+) La procedencia y las adaptaciones quedan documentadas por skill (nota de
  adaptación en cada `SKILL.md`), lo que hace auditable el fork de upstream.
- (−) Mantenimiento: actualizar las skills desde upstream exige re-aplicar las
  adaptaciones (ver [speckit-authoring-aids.md](../governance/speckit-authoring-aids.md)).
- (−) Un paso más antes de `Ready`; se mitiga manteniéndolo recomendado y no
  bloqueante.
- (~) `analyze` sigue siendo el de peor encaje (chequeo blando); no sustituye a
  `validate_specs.py`.
