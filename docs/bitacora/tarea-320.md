# Tarea #320 — La `temperature` de los perfiles nunca llegaba al modelo

## 2026-09-06 — Completada ✅

- **Rama:** `fix/agent-temperature-never-applied`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #320`)
- **Spec/ADR:** SPEC-023 / ADR-0009 (E12). **Defecto**, no funcionalidad nueva.
- **Dependencias:** ninguna. Es la deuda anotada en las bitácoras de #225, #264 y
  #265.

## Qué se hizo

`temperature` existía en toda la cadena menos donde importa: los ocho `.agent.md` la
declaran, la siembra la escalona por agente, la BD la persiste, la interfaz la
ofrece, el router la funde en `agent_settings` y la traza de T9.1 la registra — y
**ningún adapter la pasaba a `call_llm`**. El único sitio del repo que enviaba
temperatura a un proveedor era el juez de las evals, que añadió el parámetro al
dispatcher para su propio uso (T9.4).

Ahora `resolve_agent_temperature` la resuelve con la misma cascada que el modelo y
los cinco adapters la pasan.

## Por qué esto es un defecto y no una mejora

El README documenta `temperature` como campo configurable del producto y la interfaz
tiene el control. Un ajuste que se ofrece, se guarda y se muestra, y que no tiene
efecto, es un defecto: la herramienta miente sobre lo que hace. Por eso la rama va
con prefijo `fix/` y no `feat/`.

**Y no se ve.** No da error y no se nota mirando una ejecución: el modelo genera con
su default y el texto parece razonable. Se nota cuando alguien baja la temperatura
del revisor para que sea consistente entre vueltas del bucle y sigue siendo
errático — y entonces el sitio donde mirar no es evidente.

## Decisiones documentadas

- **`resolve_agent_temperature` es espejo de `resolve_agent_model`**, hasta en la
  firma. Son el mismo tipo de dato —un ajuste por agente que puede venir de tres
  sitios— y tenerlos con formas distintas es justamente lo que hizo que uno se
  quedara sin conectar. Hay un test que compara las dos firmas.
- **`None` y no un número por defecto.** No declarar temperatura sigue significando
  «manda el default del proveedor», que es el comportamiento anterior. Inventar un
  0.7 global habría cambiado el comportamiento de los agentes que no la declaran.
- **La cascada distingue `0.0` de ausente.** Un `or` en el camino habría tomado el
  cero por «no declarada» y mandado el default — y `0.0` es precisamente el valor
  que se pone para ser determinista. Tiene su test.
- **El recorte vive en el dispatcher, no en el resolutor.** En la única puerta a los
  proveedores: así protege también a quien llame a `call_llm` directamente, como el
  juez de las evals. Anthropic acepta 0-1 y devuelve **400** por encima; mandarle un
  1.5 es el mismo tipo de fallo que mandarle un id de Ollama (#264): configuración
  plausible, error solo al generar.
- **Se recorta, no se rechaza.** El valor viene de un campo de interfaz sin límites;
  dejar de generar por una temperatura de 1.2 sería peor que generar con 1.0. Se
  avisa en el log.
- **`call_llm_stream` también lo acepta.** El redactor —el agente cuya temperatura
  más se nota— genera por streaming, un camino aparte del dispatcher. Era el más
  fácil de dejarse, y dejárselo no habría roto nada visible.

## Test nuevo

`backend/tests/test_agent_temperature.py` (36 casos), sin LLM real:

- **La cascada**: cada agente recupera la suya (0.3 / 0.7 / 0.2 / 0.1 — no son
  números cualesquiera); lo elegido para la ejecución gana; sin declarar nada da
  `None`; un valor no numérico no tumba la generación; y **el `0.0` se respeta**.
- **El recorte**, parametrizado por proveedor: 1.5 se recorta a 1.0 en Anthropic y
  se conserva en OpenAI/Ollama; los negativos van a 0; avisa; y está **en el
  dispatcher**, comprobado por AST sobre el cuerpo sin docstring.
- **Que llegue, agente por agente**, con el LLM sustituido: revisor, formateador,
  redactor **por el camino de streaming**, y un agente custom.
- **El salto dispatcher→proveedor**, para los seis caminos (tres proveedores ×
  generación y streaming). Es el tramo que los tests de agente no tocan y donde el
  parámetro se pierde sin que nada falle.
- **Guardas estructurales**: ningún agente con LLM se queda sin conectar —resolver
  y no pasar también falla—, los perfiles siguen declarándola, y las dos firmas de
  resolución coinciden.

Verificado por mutación (seis): usar `or` en la cascada, dejar de recortar, sacar el
recorte del dispatcher, que el redactor resuelva y no pase, y que `call_llm_stream` y
`call_llm` dejen de reenviarla. Cada mutación tumba su test.

**Un hallazgo del proceso**: la primera versión no detectaba que `call_llm_stream`
dejara de reenviar el parámetro a Anthropic, porque el test del redactor sustituye
`call_llm_stream` entera y nunca ejercita ese salto. De ahí salieron los seis casos
del tramo dispatcher→proveedor.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 955 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python -m evals.agent_behavior.gate       # → ✅ 11 métricas, ninguna por debajo
python3 scripts/validate_specs.py         # → [OK]
```

Comprobado a mano: los cuatro agentes del núcleo y uno custom resuelven la
temperatura de su perfil; el override de ejecución gana; un agente sin perfil da
`None`; y el recorte se comporta por proveedor.

## Definition of Done

- [x] Cumple los criterios de #320.
- [x] Tests que cubren el cambio, en verde (36 nuevos).
- [x] Docs: README (rango por proveedor y la cascada) y `CLAUDE.md`.
- [x] Sin secretos en el diff; sin dependencias nuevas.
- [x] Rama con prefijo `fix/` hacia `develop`.

## Seguimiento

- **Esto cambia cómo generan los cinco agentes**, y es el efecto buscado: hasta
  ahora todos usaban el default del proveedor. Con Anthropic, el default es 1.0 y
  los perfiles piden entre 0.1 y 0.7, así que las salidas serán **más
  deterministas** — sobre todo formateador y revisor. Es lo que los perfiles llevan
  años declarando.
- **El gate EDD no puede ver este cambio**, y conviene decirlo: los conjuntos
  `golden` corren en `replay`, donde no se llama al modelo, así que un cambio de
  temperatura no mueve ninguna métrica. Es un ejemplo concreto de por qué unos
  golden `handwritten` no son todavía una red de seguridad — refuerza la secuencia
  de `edd-discipline.md` §6: regrabar en `live` antes de endurecer.
- **`num_ctx` y `keep_alive` siguen escritos a mano en cada adapter**, no salen del
  perfil. Es el mismo patrón que acaba de arreglarse para modelo y temperatura, y
  sería la continuación natural si alguna vez importa.
- **La UI no valida el rango** (`float(payload.get("temperature", 0.7))` sin
  límites). El recorte del dispatcher lo cubre en el momento de generar, pero un
  aviso en el formulario sería mejor que descubrirlo en el log.
