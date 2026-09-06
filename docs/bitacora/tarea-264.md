# Tarea #264 — Agentes custom conscientes del proveedor LLM

## 2026-09-06 — Completada ✅

- **Rama:** `feat/264-generic-provider-aware`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #264`)
- **Spec/ADR:** SPEC-023, Épica E12, ADR-0009. Criterio afectado: **AC3**.
- **Dependencias:** ninguna. Es el seguimiento que T12.3 dejó abierto.

## Qué se hizo

`adapters/generic.py` —el runner de los agentes **custom**, los que se definen con
un `.agent.md`— ahora resuelve su modelo con la misma cascada que los del núcleo:

```
agent_settings[<agente>].model  →  models[<proveedor>] del .agent.md
                                →  model legado (solo si su namespace coincide)
                                →  default del proveedor
```

## El fallo, y por qué no se veía

T12.3 hizo el modelo por agente consciente del proveedor **para los cuatro agentes
del núcleo**. `generic.py` se quedó fuera: leía `profile["model"]`, que salía del
frontmatter con un `or "llama3.2:1b"` de respaldo — un identificador de Ollama
escrito a mano en código compartido.

Con el default `LLM_PROVIDER=anthropic`, ejecutar un agente custom mandaba ese id a
la API de Claude. El error no es sutil —modelo desconocido— pero tiene dos
propiedades que lo hacen fácil de no ver:

- **llega en tiempo de ejecución del pipeline**, con el artículo a medias;
- **solo le pasa a quien haya creado un agente propio**. Los cinco del núcleo
  funcionan perfectamente, así que la función parece sana hasta que alguien la usa.

En el repo hay un caso real: `pepe.agent.md` declara `model: llama3.2:3b` y no tiene
bloque `models:`. Es exactamente el escenario.

## Decisiones documentadas

- **El perfil deja de llevar clave `model`.** El issue proponía que
  `load_agent_profile()` leyera también el bloque `models:`, pero eso deja **dos
  sitios** que resuelven el modelo —el perfil y `resolve_agent_model`—, que es
  justo la duplicación que causó este fallo. La resolución se delega entera a
  `resolve_agent_model`, que ya lee ese bloque. Cuál es el modelo de un agente
  depende del proveedor activo: un valor estático en un diccionario de perfil es
  incorrecto por construcción.
- **No se toca el `model:` heredado de los perfiles.** La cascada lo filtra por
  namespace, así que un `.agent.md` viejo sigue funcionando bajo Ollama y cae al
  default del proveedor bajo Anthropic. No hace falta migrar nada.
- **No se añade `models:` a `pepe` ni a `flowskill`.** Son perfiles de prueba
  (`prompt_template: flow-designer-persist-test`); ponerles configuración de
  producción sería maquillar el caso que este arreglo tiene que cubrir. Lo que se
  documenta es la convención, en el README.
- **La `temperature` del perfil sigue sin llegar al modelo.** Se lee y se descarta,
  igual que en los agentes del núcleo. Es la deuda que quedó anotada en #225: el
  dispatcher ya acepta el parámetro, pero conectarlo cambia cómo generan **todos**
  los agentes y necesita su propia verificación. No va de polizón aquí.

## Test nuevo

`backend/tests/test_generic_agent_model.py` (14 casos), con el mismo patrón que
`test_llm_agent_model_resolution.py`: sin LLM real.

- **El caso que rompía**, escrito como test: un agente custom no manda un id de
  Ollama a Claude, y sin `models:` cae al default del proveedor.
- **La cascada no se pasa de frenada**: el mismo agente conserva su `model:`
  heredado bajo Ollama; con bloque `models:` manda el bloque; un override de otro
  proveedor no secuestra la ejecución, y uno del proveedor activo sí manda.
- **La costura**: que `generic.py` **llame** de verdad a `resolve_agent_model` — sin
  esto la cascada podría estar perfecta y el runner seguir sin usarla— y que el
  perfil ya no lleve un modelo estático.
- **De extremo a extremo**, con `call_llm` sustituido: lo que llega al LLM es lo que
  dijo la cascada, y **el modelo elegido para esa ejecución llega**.
- **Regresión de T12.3**: los cuatro del núcleo siguen declarando su bloque
  `models:`; quitarlo los devolvería al default global en silencio.

Verificado por mutación. Dos hallazgos del proceso:

- **El primer intento del test de extremo a extremo no valía**: usé como override
  `claude-opus-5`, que es el default de Anthropic, así que pasaba igual aunque el
  runner ignorase los `agent_settings`. Con un valor distinto del default, la
  mutación cae. El test lleva ahora una aserción que lo impide volver a escribir mal.
- **El guarda de «ningún id de modelo escrito a mano» se acusaba a sí mismo**: la
  docstring del módulo dice «calling ollama» para explicar qué hace. Se busca por
  AST literales que parezcan un id (`nombre:etiqueta`), saltando docstrings —
  explicar algo no es hacerlo. Mismo tropiezo que en #159.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 906 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python3 scripts/validate_specs.py         # → [OK]
```

Comprobado a mano con los tres proveedores: bajo `anthropic`, `pepe` (custom, sin
`models:`) resuelve a `claude-opus-5` en vez de a `llama3.2:3b`; bajo `ollama`
conserva el suyo; bajo `openai` cae a `gpt-4o-mini`.

## Definition of Done

- [x] Cumple los criterios del issue: un agente custom con `models.anthropic`
  resuelve a ese modelo Claude; sin él, cae al default del proveedor y **no** a un
  id de Ollama. Test sin LLM real.
- [x] Tests que cubren el cambio, en verde (14 nuevos).
- [x] Docs: README (`models` frente a `model`, con la cascada) y SPEC-023 anotada.
- [x] Sin secretos en el diff; sin dependencias nuevas.
- [x] Rama con prefijo `feat/` hacia `develop`.

## Seguimiento

- **No se añade tarea al bloque `sdd-sync` de SPEC-023.** El issue dice que es
  seguimiento/tech-debt y no lo gestiona `sdd-sync`; y el trabajo **ya está definido
  por AC3**, que no excluye a los agentes custom. Crear un `T12.6` habría generado
  un issue duplicado de #264. Se anota el AC en su lugar.
- **Los AC de SPEC-023 siguen sin marcar**, incluido AC3. No los marco: marcar solo
  el que he verificado, dejando AC1/AC2/AC4-AC7 sin marcar pese a estar entregados,
  daría una imagen falsa del estado de la spec; y marcarlos todos sería afirmar una
  verificación que no he hecho. Es una decisión del mantenedor.
- **La `temperature` de los perfiles sigue sin llegar al modelo** (ver arriba). Es
  la siguiente pieza natural de E12 y merece su propia tarea.
- **#265** (desacoplar `EMBED_PROVIDER` de `LLM_PROVIDER`) es el otro seguimiento
  vivo de esta épica.
