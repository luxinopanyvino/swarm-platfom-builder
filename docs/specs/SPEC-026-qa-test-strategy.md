# SPEC-026: Estrategia de pruebas — unitarias, integración y estrés

- **Estado:** Ready
- **Autor:** Equipo de plataforma
- **Fecha:** 2026-09-09
- **Épica:** E14 (GitHub Project)
- **ADR relacionado:** [ADR-0004](../adr/0004-observability-and-ci.md)
- **Severidad:** 🟠

## 1. Problema

La suite del backend está sana —1027 tests en verde, 61 ficheros— y eso ha
escondido tres huecos que no son de cobertura sino de **tipo de prueba**. Medido
sobre `develop` el 2026-09-09:

- **El frontend no tiene ni una prueba.** `frontend/package.json` no declara script
  `test` ni ningún runner. Las dos builds de Vite compilan; que el cliente HTTP, los
  stores de sesión y proyectos, o los registros de `platform/` (`setAgentCatalog`,
  `setProjectNavItems`, `setRunTarget`) hagan lo que dicen, no lo comprueba nadie.
  El único test que abre un navegador vive en el backend
  (`tests/test_modal_a11y.py`, Chromium) y mira accesibilidad, no comportamiento.
- **Casi todo es unitario.** Solo **12 de 61** ficheros ejercitan la aplicación por
  HTTP (`httpx` + `ASGITransport`); los otros 49 llaman funciones directamente. Es
  una pirámide sana en la base y estrecha justo donde viven los fallos de
  cableado: dependencias mal puestas, orden de middleware, `X-Project-Id` que no
  llega, un router que se deja de montar.
- **No hay ninguna prueba contra servicios reales, ni de carga.** La suite es sin
  red **por diseño**, y eso está bien para el gate rápido; el problema es que no
  hay ningún otro sitio. Qdrant, Postgres y el proveedor de embeddings solo existen
  como dobles, así que los caminos que importan cuando algo se rompe de verdad
  —una colección con otra dimensión, Qdrant caído a mitad de una indexación, las
  migraciones sobre Postgres en vez de SQLite— se prueban contra nuestra idea de
  esos servicios, no contra ellos. Y no hay ni `k6`, ni `locust`, ni
  `pytest-benchmark`: **nadie sabe qué pasa con dos ejecuciones SSE simultáneas.**
- **La cobertura no se mide.** No hay `pytest-cov` en `requirements*.in` ni en
  `ci.yml`. «1027 tests» es un número de tests, no de código ejercitado.

Importa ahora porque el backlog quedó vacío: hasta aquí la calidad la ha sostenido
la disciplina de escribir un test por cambio. Eso cubre lo que se toca, y deja sin
cubrir lo que nadie toca — que es justo donde se pudre.

## 2. Objetivos / No-objetivos

- **Objetivos:** una estrategia de pruebas escrita y **verificable**; medición de
  cobertura con suelo; pruebas de frontend; pruebas de integración contra servicios
  reales; pruebas de carga sobre los caminos caros, con presupuestos declarados.
- **No-objetivos:** perseguir un porcentaje de cobertura alto por sí mismo (el
  suelo es contra la regresión, no una meta); pruebas E2E de navegador sobre todos
  los flujos de producto; pruebas de carga distribuidas o en infraestructura
  dedicada; sustituir el gate EDD, que evalúa comportamiento probabilístico y es
  otra cosa (ADR-0006).

## 3. Criterios de aceptación (Given/When/Then)

- [ ] **AC1** — *Given* el repositorio, *Then* existe `docs/governance/qa-strategy.md`
  que define **qué es aquí** una prueba unitaria, una de integración y una de
  estrés, dónde vive cada una, cuál bloquea una PR y **qué se decide no probar y
  por qué**; enlazado desde GOVERNANCE §6.
- [ ] **AC2** — *Given* la CI, *When* corre el job de backend, *Then* mide
  cobertura y **falla si baja del suelo declarado**; el suelo se fija a la
  cobertura medida el día que se introduce (no a un número aspiracional) y bajarlo
  exige justificarlo en la misma PR.
- [ ] **AC3** — *Given* `frontend/`, *When* se ejecuta `npm test`, *Then* corre un
  runner de pruebas con casos que cubren el cliente HTTP, los stores y los
  registros de `platform/`; la CI lo ejecuta y bloquea la PR si falla.
- [ ] **AC4** — *Given* un entorno con Qdrant y la base de datos **reales**, *When*
  se ejecuta la suite de integración, *Then* cubre al menos: indexar y recuperar un
  documento de punta a punta, el rechazo por dimensión incorrecta (409), el
  comportamiento con Qdrant caído, y las migraciones aplicadas sobre el motor de
  producción; corre en un job propio, **no** en el gate rápido.
- [ ] **AC5** — *Given* los tres caminos caros —la ejecución del pipeline por SSE,
  la indexación RAG y el login—, *When* se ejecuta la prueba de carga con el perfil
  declarado, *Then* se obtienen latencia (p50/p95), tasa de error y uso de memoria,
  y el resultado se compara con un **presupuesto escrito**; una regresión avisa.
- [ ] **AC6** — *Given* una PR, *Then* la CI distingue los tres niveles: unitarias
  bloqueantes y rápidas, integración bloqueante en job aparte, carga **informativa**
  y no obligatoria (no puede ser un check requerido: tendría filtro de rutas o
  duración variable y bloquearía PRs para siempre, como ya documenta
  `branch-protection.md`).
- [ ] **AC7** — *Given* el documento de estrategia, *Then* un test lo cruza con la
  realidad del repositorio —que los directorios y comandos que nombra existan, que
  los niveles que declara sean los que la CI ejecuta— de modo que no pueda quedarse
  describiendo un repositorio que ya no es este.

## 4. Diseño propuesto

**Los tres niveles, definidos por lo que rompen y no por dónde están.**

| Nivel | Qué prueba | Dónde | En CI |
|---|---|---|---|
| Unitaria | Una decisión de código, con dobles. Sin red, sin disco compartido. | `backend/tests/`, `frontend/src/**/*.test.js` | Bloqueante, rápido |
| Integración | El cableado y los contratos con servicios **reales** | `backend/tests/integration/` (marca `integration`) | Bloqueante, job propio |
| Estrés | Comportamiento bajo concurrencia y presión | `qa/load/` | Informativo |

**Cobertura (AC2).** `pytest-cov` en `requirements-dev.in`, `--cov=app
--cov-fail-under=<suelo>` en el job de backend. El suelo se **mide primero y se fija
a lo que salga**: un número inventado o se incumple el primer día o no aprieta nunca.

**Frontend (AC3).** Vitest, que ya viene con Vite y no añade otra cadena de build.
Primeras pruebas donde hay lógica de verdad y no pintura: el cliente HTTP
(cabecera `X-Project-Id`, manejo de 401), los stores, y los registros de la capa
`platform/` — que son precisamente el mecanismo que sostiene la separación de T8.6
y hoy solo los cubre un test estructural del backend.

**Integración (AC4).** `docker-compose.test.yml` con Qdrant y Postgres, y una marca
`integration` que la suite rápida excluye por defecto. El valor está en probar
justo lo que los dobles no pueden: que la dimensión que rechaza `#322` la rechaza
Qdrant de verdad, y que las migraciones que pasan en SQLite pasan en Postgres —el
ciclo de FK `users` ↔ `projects` es un candidato claro a comportarse distinto.

**Carga (AC5).** `k6` por ser un binario sin dependencias de Python, que no
contamina el entorno del backend. Los tres caminos no son arbitrarios: SSE mantiene
conexiones abiertas y el pipeline retiene VRAM (`keep_alive=0` y `num_ctx` fijo por
agente); la indexación RAG llama al proveedor de embeddings por lotes; el login
tiene rate limit y bloqueo, y su coste es deliberado. Los presupuestos se escriben
en `qa/load/budgets.yaml` — declarados, como `thresholds.yaml`, no incrustados en
el script.

**Ficheros afectados:** `docs/governance/qa-strategy.md` (nuevo), `GOVERNANCE.md`,
`.github/workflows/ci.yml`, `backend/requirements-dev.in`, `frontend/package.json`,
`frontend/vitest.config.js` (nuevo), `docker-compose.test.yml` (nuevo), `qa/load/`
(nuevo), `backend/tests/integration/` (nuevo).

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Un suelo de cobertura se convierte en meta y se persiguen líneas en vez de fallos | Tests que no prueban nada y cuestan mantenimiento | El suelo es **anti-regresión**: se fija a lo medido y solo sube cuando sube solo. No se declara objetivo |
| La suite de integración se vuelve lenta o intermitente y la gente la ignora | Se desactiva y el nivel desaparece | Job aparte, casos contados y elegidos por lo que **solo** se puede probar con el servicio real |
| Las pruebas de carga en un runner compartido dan cifras que no se pueden comparar | Falsas alarmas, ruido | Informativas por definición; el presupuesto se declara con margen y la comparación es entre ejecuciones del mismo perfil |
| `k6` o Vitest añaden herramienta nueva que nadie mantiene | Deuda | Ambas se ejecutan desde la CI y desde un comando documentado; si un nivel deja de correrse, AC7 lo detecta |
| Solapamiento con el gate EDD | Dos sitios midiendo lo mismo | La frontera se escribe en `qa-strategy.md`: QA mide **lo determinista**; EDD, el comportamiento probabilístico (ADR-0006) |

## 6. Plan de pruebas

AC1 y AC7 se validan con un test de gobernanza que cruza el documento con el
repositorio, en la línea de `test_sdd_governance.py`. AC2, AC3 y AC6 se validan
leyendo `ci.yml` y comprobando que los jobs existen, que el suelo está puesto y que
el de carga **no** es obligatorio. AC4 y AC5 se validan por su propia ejecución:
son pruebas, y su criterio es que corran y produzcan el informe declarado.

## 7. Impacto operativo / observabilidad

Sin cambios en producto. La CI gana dos jobs (integración, carga) y el de backend
gana la medición de cobertura; el tiempo del gate rápido no debe crecer, porque
integración y carga van aparte. Si se añade el job de integración a los checks
obligatorios, hay que reimportar el ruleset de
[`branch-protection.md`](../governance/branch-protection.md) — y **nunca** añadir
ahí el de carga.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E14
  title: "Estrategia de pruebas (QA): unitarias, integración y estrés"
  area: area/qa
tasks:
  - id: T14.1
    title: "Alta del área area/qa (validador, seed, gobernanza, backlog)"
    sev: low
    depends_on: []
    acceptance: [AC1]
  - id: T14.2
    title: "Documento de estrategia de pruebas y su test de vigencia"
    sev: medium
    depends_on: [T14.1]
    acceptance: [AC1, AC7]
  - id: T14.3
    title: "Medir cobertura y fijar el suelo en CI"
    sev: medium
    depends_on: []
    acceptance: [AC2]
  - id: T14.4
    title: "Runner de pruebas de frontend y primeras pruebas de platform/"
    sev: high
    depends_on: []
    acceptance: [AC3]
  - id: T14.5
    title: "Suite de integración contra Qdrant y base de datos reales"
    sev: high
    depends_on: [T14.2]
    acceptance: [AC4]
  - id: T14.6
    title: "Pruebas de carga sobre SSE, indexación RAG y login, con presupuestos"
    sev: medium
    depends_on: [T14.2]
    acceptance: [AC5]
  - id: T14.7
    title: "Separar los tres niveles en la CI y ajustar la protección de rama"
    sev: medium
    depends_on: [T14.3, T14.4, T14.5, T14.6]
    acceptance: [AC6]
```
