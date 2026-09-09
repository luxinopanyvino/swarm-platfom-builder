# Backlog de hardening y plataforma

Épicas y tareas derivadas de la auditoría transversal (junio 2026). Mapea los 6
ejes: **Seguridad (Identidad y AppSec), Infra, Datos, Observabilidad,
Gobernanza**. Severidad: 🔴 alto · 🟠 medio · 🟡 bajo.

Este documento es un **overview** de alto nivel. La **fuente de verdad** de la
definición de épicas/tareas son las specs (bloque `sdd-sync`, sección 8 del
[TEMPLATE](../specs/TEMPLATE.md)); el **estado de ejecución** vive en el GitHub
Project (ver [GOVERNANCE §7](../governance/GOVERNANCE.md)).

El **bootstrap inicial** del GitHub Project se hizo con
[`scripts/seed_github_project.py`](../../scripts/seed_github_project.py) (atómico
y jerárquico: crea el campo `Epic`, enlaza el proyecto al repo y crea relaciones
sub-issue épica→tareas). Es un **script de un solo uso**, no un sync incremental;
para mantener el backlog al día tras el bootstrap usa el comando **`/sdd-sync`**
(agente [`sdd-sync`](../../.claude/agents/sdd-sync.md)). Para limpiar todo:
[`scripts/delete_github_project.py`](../../scripts/delete_github_project.py).
Labels: `epic`, `task`, `area/*`, `sev/{high,medium,low}`.

---

## Mapa épica → spec (fuente de verdad)

| Épica | Spec(s) |
|-------|---------|
| E1 Identidad y Acceso | SPEC-001 · SPEC-015 |
| E2 AppSec | SPEC-002 (Superseded) · SPEC-016 |
| E3 Infraestructura | SPEC-017 |
| E4 Datos y Persistencia | SPEC-018 |
| E5 Observabilidad | SPEC-019 |
| E6 Gobernanza | SPEC-020 |
| E7 UX/UI | SPEC-003 |
| E8 Plataforma no-code | SPEC-013 |
| E9 Explicabilidad y EDD | SPEC-014 |
| E10 Memoria y contexto de agentes | SPEC-021 (Draft) |
| E11 Publicación y maquetación editable | SPEC-022 (Draft) |

> E8 y E10 no tienen sección propia en este overview histórico; su definición
> completa vive en sus specs.

## E1 — Seguridad: Identidad y Acceso  · `area/security`
> Reducir privilegios por defecto y endurecer la gestión de sesiones.

| ID | Tarea | Sev |
|----|-------|-----|
| T1.1 | Rol seguro por defecto en el registro (SPEC-001) | 🔴 |
| T1.2 | Rate limiting + bloqueo de cuenta en login/register | 🟠 |
| T1.3 | Revocación de JWT (almacén de `jti`/blacklist) y emisión de refresh token | 🟠 |
| T1.4 | Sacar el token JWT del *query string* del SSE (ticket de un solo uso / header) | 🔴 |
| T1.5 | `ENABLE_DEV_ROLE_PROMOTION` default `False` y gate de producción | 🟠 |
| T1.6 | Relegar seeds de credenciales débiles a un flag de dev explícito | 🟠 |

## E2 — Seguridad: Aplicación (AppSec)  · `area/security`
> Saneo de entradas no confiables y control de egress.

| ID | Tarea | Sev |
|----|-------|-----|
| ~~T2.1~~ | ~~Protección SSRF en el scraper + quitar `verify=False`~~ · **Obsoleta**: el scraper se eliminó (#159, commit `71e3923`); [SPEC-002](../specs/SPEC-002-scraper-ssrf-protection.md) → *Superseded* | — |
| T2.2 | Sanear URLs `javascript:`/`data:` en `paper_layout` y añadir `sandbox` al iframe | 🟠 |
| T2.3 | Validar subidas por *magic bytes*/content-type (no solo extensión) | 🟡 |
| T2.4 | Manejador global de excepciones (sin fugas de stack en 500) | 🟠 |

## E3 — Infraestructura y Despliegue  · `area/infra`
> No exponer servicios internos, gestionar secretos, contenedores no-root.

| ID | Tarea | Sev |
|----|-------|-----|
| T3.1 | Autenticar Qdrant (API key) y no publicar puertos internos al host | 🔴 |
| T3.2 | Gestión de secretos (Docker secrets/.env fuera de git) + rotación | 🔴 |
| T3.3 | Contenedores no-root + imagen backend slim (multi-stage, sin build-essential) | 🟠 |
| T3.4 | Separar compose dev/prod; TLS/HTTPS; cabeceras de seguridad + CSP en nginx | 🟠 |
| T3.5 | Límites de recursos + healthchecks de readiness por servicio | 🟡 |

## E4 — Datos y Persistencia  · `area/backend`
> Migraciones gestionadas y estado externalizable.

| ID | Tarea | Sev |
|----|-------|-----|
| T4.1 | Adoptar Alembic (reemplazar los `ALTER TABLE` caseros de `init_db`) | 🟠 |
| T4.2 | Sacar `backend/data/dev.db` de git y añadir a `.gitignore` | 🟡 |
| T4.3 | Externalizar estado en memoria (streams/tasks/decisiones) a Redis para multi-worker | 🟠 |

## E5 — Observabilidad  · `area/observability`
> Visibilidad de salud, rendimiento y uso de LLM.

| ID | Tarea | Sev |
|----|-------|-----|
| T5.1 | Logging estructurado (JSON) + `request_id`/correlation IDs | 🟠 |
| T5.2 | Métricas Prometheus (latencia, errores, duración de pipeline, tokens LLM) | 🟠 |
| T5.3 | Tracing OpenTelemetry (fase 2) | 🟡 |
| T5.4 | `/health` con liveness/readiness y chequeo de dependencias (DB/Qdrant/Ollama) | 🟠 |

## E6 — Gobernanza y Calidad (SDD)  · `area/governance`
> CI, cadena de suministro, auditoría y adopción de SDD.

| ID | Tarea | Sev |
|----|-------|-----|
| T6.1 | CI en PRs: lint + `pytest` + build frontend | 🔴 |
| T6.2 | Escaneo de dependencias (`pip-audit`/`npm audit`) + Dependabot | 🟠 |
| T6.3 | Pinear dependencias + lockfile/hashes (builds reproducibles) | 🟠 |
| T6.4 | Audit log de acciones sensibles (cambios de rol, publicación, accesos) | 🟠 |
| T6.5 | Política de retención de datos y tratamiento de PII | 🟡 |
| T6.6 | Adoptar SDD: plantillas de spec, DoR/DoD, CODEOWNERS (este PR) | 🟠 |

## E7 — Experiencia de Usuario (UX/UI)  · `area/ux`
> Consistencia visual con el design system y accesibilidad de la UI.
> Definición en [SPEC-003](../specs/SPEC-003-ux-design-system-accessibility.md);
> las tareas se materializan con `/sdd-sync --apply`.

| ID | Tarea | Sev |
|----|-------|-----|
| T7.1 | Unificar colores en tokens de diseño (sin hex hardcodeado) | 🟠 |
| T7.2 | Accesibilidad AA en modales (focus trap, foco visible, Esc) | 🟠 |
| T7.3 | Estados consistentes de carga / vacío / error | 🟡 |

## E9 — Explicabilidad y Evaluation-Driven Development (EDD)  · `area/evaluation`
> Hacer auditable el comportamiento del pipeline y dirigir el desarrollo de
> agentes/modelos **de la plataforma** por evaluación. Definición en
> [SPEC-014](../specs/SPEC-014-explainability-and-edd.md) y
> [ADR-0006](../adr/0006-adopt-evaluation-driven-development.md); las tareas se
> materializan con `/sdd-sync --apply`. La disciplina —cuándo tocar los evals, cómo
> se añade uno y la DoR/DoD de evaluación— está en
> [edd-discipline.md](../governance/edd-discipline.md).

| ID | Tarea | Sev |
|----|-------|-----|
| T9.1 | Traza de explicabilidad por paso (`agent_run_steps`) desde el orquestador | 🟠 |
| T9.2 | Endpoint `/agents/{id}/explain` + panel UI "Por qué este resultado" | 🟠 |
| T9.3 | Harness EDD (`backend/evals`) reproducible sobre perfiles de la plataforma | 🔴 |
| T9.4 | Datasets *golden* + métricas (citas, formato, calibración, coherencia, presupuesto) | 🟠 |
| T9.5 | Gate EDD en CI para PRs que tocan agentes/modelos (umbrales de regresión) | 🟠 |
| T9.6 | Gobernanza EDD (alta de `area/evaluation`, DoR/DoD de evaluación, CODEOWNERS) | 🟡 |

## E14 — Estrategia de pruebas (QA)  · `area/qa`
> Cubrir los tres huecos que la disciplina de «un test por cambio» no cubre:
> el frontend no tiene ninguna prueba, casi todo es unitario (12 de 61 ficheros
> ejercitan HTTP) y no hay nada contra servicios reales ni bajo carga. Definición
> en [SPEC-026](../specs/SPEC-026-qa-test-strategy.md); la estrategia, en
> [qa-strategy.md](../governance/qa-strategy.md).

| ID | Tarea | Sev |
|----|-------|-----|
| T14.1 | Alta del área `area/qa` (validador, seed, gobernanza, backlog) | 🟡 |
| T14.2 | Documento de estrategia de pruebas y su test de vigencia | 🟠 |
| T14.3 | Medir cobertura y fijar el suelo en CI | 🟠 |
| T14.4 | Runner de pruebas de frontend y primeras pruebas de `platform/` | 🔴 |
| T14.5 | Suite de integración contra Qdrant y base de datos reales | 🔴 |
| T14.6 | Pruebas de carga sobre SSE, indexación RAG y login, con presupuestos | 🟠 |
| T14.7 | Separar los tres niveles en la CI y ajustar la protección de rama | 🟠 |

## E15 — Evaluación con deepeval  · `area/evaluation`
> El harness EDD funciona; lo que falta es **qué se mide**: ninguna de las cinco
> métricas actuales mira la recuperación, y este pipeline es RAG. Definición en
> [SPEC-027](../specs/SPEC-027-model-evaluation-deepeval.md) y
> [ADR-0010](../adr/0010-adopt-deepeval-as-metrics-library.md): deepeval entra como
> **librería de métricas** en el registro que ya existe, no como framework.

| ID | Tarea | Sev |
|----|-------|-----|
| T15.1 | Adoptar deepeval como librería de métricas (dependencia, telemetría off) | 🟠 |
| T15.2 | Puente del juez: deepeval llama por el dispatcher de la plataforma | 🔴 |
| T15.3 | Métricas de recuperación alimentadas por la traza de explicabilidad | 🔴 |
| T15.4 | Dataset golden con procedencia `recorded` para el agente con RAG | 🟠 |
| T15.5 | Umbrales en modo aviso y disciplina escrita en `edd-discipline.md` | 🟠 |

---

### Orden recomendado de ejecución

1. **🔴 primero:** T1.1, T1.4, T3.1, T3.2, T6.1. (~~T2.1~~ obsoleta — scraper eliminado)
2. **🟠 baratos:** T1.5, T2.2, T2.4, T4.2, T5.4, T6.2.
3. Resto de 🟠 y 🟡 por capacidad.

Criterio de cierre de cada tarea: cumplir su Definition of Done
([GOVERNANCE §6](../governance/GOVERNANCE.md)).
