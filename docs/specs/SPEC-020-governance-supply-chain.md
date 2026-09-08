# SPEC-020: Gobernanza, CI y cadena de suministro

- **Estado:** In progress
- **Autor:** Equipo de plataforma
- **Fecha:** 2026-07-04
- **Épica:** E6 (Gobernanza y Calidad (SDD))
- **ADR relacionado:** ADR-0002, ADR-0004
- **Severidad:** 🟡

> Adopta formalmente las tareas T6.1–T6.6 sembradas por el bootstrap sin spec.
> T6.1 y buena parte de T6.6 están de facto implementadas
> (`.github/workflows/ci.yml`, `docs/governance/`, `docs/specs/`); sus AC lo
> reflejan y el cierre formal de los issues queda del lado de ejecución.

## 1. Problema

El backlog de gobernanza quedó sembrado sin spec: CI (ya existente) sin
criterios formales, sin escaneo de cadena de suministro ni Dependabot,
`requirements.txt` con rangos `>=` sin lockfile/hashes, sin audit log de
acciones sensibles (cambios de rol, publicación) y sin política de retención
de datos/PII.

## 2. Objetivos / No-objetivos

- **Objetivos:** gate de CI formalizado, dependencias escaneadas y pineadas,
  auditoría de acciones sensibles y política de datos escrita y aplicada.
- **No-objetivos:** SBOM firmado/attestations (evolución futura); DLP.

## 3. Criterios de aceptación

- [ ] **AC1** — *Given* una PR a `develop`, *When* corre la CI, *Then* ejecuta
  al menos pytest backend, build frontend, validación de specs y escaneo de
  secretos, y la PR no puede mergear en rojo (branch protection).
  *(T6.1: la CI cumple la primera mitad y la regla está versionada en
  `.github/rulesets/develop.json` con su guía en `docs/governance/branch-protection.md`.
  Queda un paso manual e inevitable: **importar el ruleset** en Settings → Rules.
  Marcar AC1 antes de eso sería dar por hecho lo único que este repo no puede
  comprobar desde dentro.)*
- [ ] **AC2** — *Given* la CI, *Then* incluye escaneo de dependencias
  (`pip-audit` + `npm audit`) y Dependabot está configurado para backend,
  frontend y GitHub Actions.
- [ ] **AC3** — *Given* las dependencias, *Then* están **pineadas** con
  lockfile/hashes (pip-tools/uv + `package-lock.json`) y la CI instala desde el
  lock, no desde rangos.
- [ ] **AC4** — *Given* una acción sensible (cambio de rol, publicación,
  borrado de documentos RAG, login fallido/lockout), *When* ocurre, *Then*
  queda registrada en un **audit log** consultable (quién, qué, cuándo, desde
  dónde) sin PII innecesaria.
- [ ] **AC5** — *Given* los datos personales y artefactos generados, *Then*
  existe una **política de retención** documentada (qué se guarda, cuánto y
  cómo se purga) y un mecanismo de purga aplicable.
- [x] **AC6** — *Given* el flujo de trabajo, *Then* SDD está formalizado:
  specs con DoR/DoD (GOVERNANCE §5–6), CODEOWNERS activo y el pipeline de
  autoría de specs documentado (ADR-0007).
  *(T6.6: los tres documentos ya existían; lo que faltaba era que siguieran
  siendo ciertos. `backend/tests/test_sdd_governance.py` los ata al repositorio y
  al hacerlo destapó una regla de CODEOWNERS muerta —cubría el riesgo de SSRF
  apuntando a un fichero borrado—, ya corregida. ADR-0007 pasa a **Aceptado**:
  la gobernanza lo invocaba como norma mientras él se declaraba provisional.)*

## 4. Diseño propuesto

AC1 formaliza `.github/workflows/ci.yml` existente y escribe la protección de
rama como artefacto versionado (`.github/rulesets/develop.json`) en vez de dejarla
en configuración invisible; un test cruza los checks obligatorios con los jobs
reales para que renombrar un job no desactive el gate en silencio.
AC2: jobs `pip-audit`/`npm audit` no bloqueantes al inicio, bloqueantes tras
la primera limpieza + `.github/dependabot.yml`. AC3: `requirements.in` →
compilado con hashes. AC4: tabla `audit_log` + helper en los routers
sensibles (se apoya en la correlación de SPEC-019/AC1). AC5: documento en
`docs/governance/` + job de purga. AC6: ya materializado en
GOVERNANCE/CODEOWNERS/ADR-0007; el AC exige que **se mantenga**, y eso no lo
sostiene un documento sino una prueba: `test_sdd_governance.py` cruza las tres
patas con el repositorio (que la DoR/DoD tengan criterios, que ninguna regla de
CODEOWNERS apunte a algo inexistente, que las skills y comandos que mapea el ADR
existan).

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| pip-audit rompe la CI por CVEs sin fix | Medio | Fase de aviso + allowlist temporal justificada |
| Pinning con hashes fricciona upgrades | Bajo | Dependabot + recompilado del lock en PR |

## 6. Plan de pruebas

CI verde/roja según cada gate; unit del audit log (acciones registradas, PII
enmascarada); revisión documental de la política de retención.

## 7. Impacto operativo / observabilidad

El audit log es en sí observabilidad de seguridad; retención define además el
ciclo de vida de logs y trazas de SPEC-019.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E6
  title: "Gobernanza y Calidad (SDD)"
  area: area/governance
tasks:
  - id: T6.1
    title: "CI en PRs: lint + pytest + build frontend"
    sev: high
    depends_on: []
    acceptance: [AC1]
  - id: T6.2
    title: Escaneo de dependencias (pip-audit/npm audit) + Dependabot
    sev: medium
    depends_on: [T6.1]
    acceptance: [AC2]
  - id: T6.3
    title: Pinear dependencias + lockfile/hashes
    sev: medium
    depends_on: [T6.2]
    acceptance: [AC3]
  - id: T6.4
    title: Audit log de acciones sensibles
    sev: medium
    depends_on: []
    acceptance: [AC4]
  - id: T6.5
    title: Política de retención de datos y PII
    sev: low
    depends_on: []
    acceptance: [AC5]
  - id: T6.6
    title: "Adoptar SDD: specs, DoR/DoD, CODEOWNERS"
    sev: medium
    depends_on: []
    acceptance: [AC6]
```
