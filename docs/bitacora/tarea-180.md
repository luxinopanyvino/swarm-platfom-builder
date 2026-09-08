# Tarea 180 — T6.6 · Adoptar SDD: specs, DoR/DoD, CODEOWNERS

- **Fecha:** 2026-09-08
- **Issue:** [#180](https://github.com/luxinopanyvino/swarm-platform-builder/issues/180) · Épica E6 · [SPEC-020](../specs/SPEC-020-governance-supply-chain.md) / AC6
- **Rama:** `docs/180-sdd-governance-live`

## Qué pedía la tarea

AC6 nombra tres cosas: specs con DoR/DoD (GOVERNANCE §5–6), CODEOWNERS activo y el
pipeline de autoría documentado (ADR-0007). La sección 4 de la spec añade el matiz
que da sentido a la tarea: *«ya materializado …; el AC exige que **se mantenga**»*.

Comprobado uno por uno, **las tres existían**: §5 con 6 criterios de DoR, §6 con 7 de
DoD, un CODEOWNERS con 15 reglas y sin placeholders, ADR-0007 y
`speckit-authoring-aids.md` escritos, y las cuatro skills en `.claude/skills/`. Es
decir: no había documentación que redactar. Lo que no había era **nada que atara
esos documentos a la realidad**, que es exactamente lo que pide un AC de
mantenimiento.

## Lo que apareció al mirar

La realidad ya se había ido por su lado en dos sitios.

**1. Una regla de CODEOWNERS muerta, y precisamente la de seguridad.** El bloque
«Egress / scraper (riesgo SSRF)» apuntaba a
`backend/app/modules/agents/adapters/scraper.py`, un fichero **borrado en 71e3923**
(«eliminar el scraper del Investigador: código muerto»). GitHub acepta rutas
inexistentes sin rechistar y las deja ahí, verdes: el área figuraba cubierta
mientras el código que hoy lleva ese riesgo —`platform/egress.py` y
`capabilities/tools.py`, la única puerta de salida de SPEC-024— no tenía revisor de
área asignado.

El impacto **hoy** es limitado y conviene decirlo sin inflarlo: la regla por defecto
`*` asigna el repositorio entero al mismo owner, así que en la práctica el cambio no
se colaba sin revisor. Lo que se perdió fue la **intención**: el día que haya más de
un revisor, el egress se revisaría por descarte y no a propósito. Corregido
apuntando a las dos rutas reales.

**2. ADR-0007 se declaraba «Propuesto» mientras la gobernanza lo aplicaba como
norma.** GOVERNANCE §5 lo cita como el paso recomendado de la DoR y CLAUDE.md lo
documenta dentro del flujo obligatorio. Un ADR que gobierna y a la vez dice de sí
mismo que es una propuesta es una contradicción que vuelve inútil el campo *Estado*.
Pasa a **Aceptado**, con una nota que deja la evidencia de que la decisión ya opera
(skills y comandos presentes, E1–E6 adoptadas en SPEC-015…020, `## Clarifications`
en SPEC-021/022/023) y también lo que **no** se ha ejercido: `/speckit-checklist` no
ha generado ningún checklist todavía.

## Qué se hizo

- `.github/CODEOWNERS`: la regla del scraper borrado → `platform/egress.py` y
  `platform/capabilities/tools.py`, con el motivo escrito en el propio fichero.
  Cabecera actualizada (pedía sustituir unos placeholders que ya estaban puestos).
- `docs/adr/0007-…`: **Propuesto → Aceptado**, con nota de aceptación y evidencia.
- `backend/tests/test_sdd_governance.py` (19 casos): ata las tres patas de AC6 al
  repositorio. El caso central —`test_ninguna_regla_apunta_a_algo_que_ya_no_existe`—
  es el que habría detectado el defecto anterior el día que se borró el scraper.
- `docs/specs/SPEC-020`: AC6 marcado y §4 anotada.

## Verificación

- `pytest tests/test_sdd_governance.py` → 19 pasan.
- Suite backend completa en verde (ver PR).
- `scripts/validate_specs.py` → OK.
- **Mutación** (6 mutaciones, las 6 detectadas):
  1. restaurar el defecto real (la regla apuntando al scraper borrado) → caen 2 tests
  2. ADR-0007 de vuelta a «Propuesto» → cae
  3. una regla de CODEOWNERS sin owner → cae
  4. un owner mal formado (placeholder sin sustituir) → cae
  5. vaciar la sección de DoR dejando el título → caen 2
  6. cambiar el enlace de la DoR a otro ADR → cae

## Cumplimiento del DoD

| Criterio (GOVERNANCE §6) | Estado |
|---|---|
| Criterios de aceptación cumplidos | Sí. AC6 marcado. |
| Tests automatizados que cubren el cambio, en verde | Sí (19 casos + 6 mutaciones). |
| Sin secretos ni PII en el diff | Sí. |
| Docs/spec/ADR actualizados | Sí: CODEOWNERS, ADR-0007, SPEC-020. |
| Observabilidad | No aplica. |

## Lo que queda fuera y conviene decidir

**Siete de los nueve ADR están en «Propuesto»**; solo 0001 y 0002 están aceptados.
Cuatro de ellos (0003, 0006, 0007, 0008) se citan desde `docs/governance/` como si
fueran norma. Aquí se ha movido **solo el 0007**, porque es el que AC6 nombra;
mover los otros tres es una decisión del maintainer (GOVERNANCE §2) y no de esta
tarea. Si el criterio es que un ADR invocado por la gobernanza no puede declararse
provisional, merece un issue propio y un test que lo generalice.

**`/speckit-checklist` sigue sin uso**: `docs/specs/checklists/` solo tiene su
README. La decisión 2 del ADR lo deja recomendado y no bloqueante, así que no
impide nada, pero hoy la parte probada del pipeline de autoría es `clarify`.
