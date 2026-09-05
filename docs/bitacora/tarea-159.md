# Tarea #159 — T2.1 → Guarda de egress de las herramientas de agente

## 2026-09-04 — Completada ✅

- **Rama:** `sec/159-egress-ssrf-guard`
- **PR:** pendiente → `develop` (la abre el mantenedor con `Closes #159`)
- **Spec/ADR:** **SPEC-024** (nueva, Draft), Épica E2, ADR-0003. Sustituye a
  SPEC-002 (Superseded). Criterios vinculantes: **AC1–AC5**.
- **Dependencias:** ninguna.

## Lo primero: la premisa de #159 había caducado, pero al revés

#159 apuntaba a SPEC-002, marcada **Superseded** el 2026-07-02 con este motivo: el
scraper del Investigador se borró, el Investigador ya no hace fetch saliente, «no
hay vector SSRF que proteger y la guarda **nunca se implementó ni es necesaria** con
la arquitectura actual».

Eso **ya no es cierto**. El fetch saliente volvió por otra puerta: las capacidades
de herramientas. `backend/app/platform/capabilities/tools.py` expone al modelo una
herramienta `fetch_url` que descarga cualquier URL y **le devuelve el cuerpo**, con
`follow_redirects=True` y una sola validación —`url.startswith(("http://",
"https://"))`—; y dos llamadas más salían con `verify=False`.

No es un SSRF ciego: `http://169.254.169.254/latest/meta-data/` entrega las
credenciales de instancia y lo leído vuelve al contexto del modelo y acaba en el
artículo. Y la URL no la escribe una persona autenticada: **la elige el modelo**,
que lee documentos del RAG donde cabe una instrucción incrustada. Las herramientas
se encienden por perfil desde la interfaz, sin tocar código.

La propia nota de SPEC-002 decía qué hacer en este caso: «si en el futuro se
reintroduce fetch saliente, ábrase una **nueva spec** de control de egress en vez de
reactivar esta». Es lo que se ha hecho.

## Qué se hizo

- **SPEC-024** (`/speckit-specify`, estado **Draft** — Ready lo decide la revisión
  del PR, GOVERNANCE §5). Tarea **T2.5**, porque los IDs no se reutilizan y T2.1
  pertenece a una spec que no se reactiva.
- **`backend/app/platform/egress.py`**: `is_egress_allowed` (decisión con motivo),
  `assert_safe_url` (levanta) y `safe_get` (cliente con la guarda en cada salto).
- **`tools.py`**: las tres salidas —`fetch_url`, arXiv y Wikipedia— pasan por la
  guarda; fuera los dos `verify=False` y el cliente HTTP propio.
- **Configuración**: `EGRESS_ALLOWED_DOMAINS`, `EGRESS_DENIED_DOMAINS`,
  `EGRESS_ALLOW_HTTP` (apagado) y `EGRESS_MAX_REDIRECTS`, en los dos `config.yaml`.
- **SPEC-002**: se retira la frase que decía que la guarda «no es necesaria» y se
  apunta a la sustituta. Dejarla habría sido documentar lo contrario de la realidad.

## Decisiones documentadas

- **Se comprueba la IP resuelta, no el hostname.** Un dominio público puede apuntar
  a `10.0.0.5` y filtrar por texto no lo ve; registrar ese dominio cuesta un minuto.
  Se resuelven **todas** las direcciones y basta una interna para rechazar —quedarse
  con la primera deja pasar a quien ponga una pública delante—.
- **Se desmapea IPv4-en-IPv6 antes de clasificar.** `::ffff:127.0.0.1` no es
  `is_loopback` como IPv6: es el hueco de siempre, y tiene su test.
- **Las redirecciones se siguen a mano.** `follow_redirects=True` valida la primera
  URL y luego obedece a `Location:` sin preguntar: es la vía más cómoda de saltarse
  la comprobación, y no deja rastro de que se ha saltado.
- **La coincidencia de dominio es por etiqueta.** Con un `endswith` a secas,
  registrar `noejemplo.com` bastaría para colarse en una allowlist que dice
  `ejemplo.com`.
- **Solo pasa lo que está en la lista de esquemas.** No se enumeran los peligrosos
  (`file:`, `gopher:`, `ftp:`): eso envejece mal. `http://` está apagado por defecto.
- **El motivo del bloqueo no vuelve al modelo.** Describe la red interna —«resuelve
  a 10.0.0.5»— y la respuesta de la herramienta entra en su contexto. Al modelo se
  le dice que se ha bloqueado; el detalle va al log, que es donde hace falta.
- **La infraestructura queda fuera, a propósito.** Ollama, Qdrant y los proveedores
  de LLM son `localhost` o red privada **adrede**, y su URL sale de la configuración,
  no de una entrada de usuario. Pasarlos por la guarda rompería el despliegue local
  sin cerrar ningún vector. La frontera es «destino influido por usuario o modelo», y
  hay un test que la fija en los dos sentidos.
- **`verify=False` no vuelve.** El comentario que lo justificaba —«*corporate
  proxies with SSL inspection*»— convertía un problema de despliegue en una
  desactivación permanente para todo el mundo. Eso se arregla instalando la CA del
  proxy o apuntando `SSL_CERT_FILE`.

## Lo que apareció al escribir los tests

**`safe_get` abría el cliente antes de validar.** AC1 dice «se rechaza **antes** de
la petición», y el test lo comprueba como tal: sustituye `httpx.AsyncClient` por algo
que revienta si se usa. Falló — el `async with` estaba fuera del bucle de validación.
La validación de la URL inicial se movió antes de construir el cliente.

**Y el guarda estructural de AC3 se acusaba a sí mismo.** Buscaba `verify=False` como
texto, y la docstring que explica *por qué no se usa* lo contiene. Ahora busca por
AST el argumento de verdad en una llamada de verdad: explicar algo no es hacerlo.

## Test nuevo

`backend/tests/test_egress_guard.py` (34 casos):

- **AC1**: metadata de nube, loopback IPv4/IPv6, privadas 10/192.168/172.16,
  `0.0.0.0`, IPv4 mapeada en IPv6, esquemas `file:`/`gopher:`/`ftp:`/`ldap:`, URL sin
  host, `http://` apagado y habilitable, y que el bloqueo **se registra**.
- **AC2**: un dominio público que resuelve a interna se rechaza; basta una IP interna
  entre varias; un hostname que no resuelve se rechaza; **una redirección a un
  destino interno se corta en el salto**; y una cadena de redirecciones no es
  infinita.
- **AC4**: la denylist rechaza aunque sea público y gana a la allowlist; una allowlist
  poblada solo deja pasar lo suyo; la coincidencia es por etiqueta y no por sufijo.
- **Las herramientas**: `fetch_url` **no abre conexión** contra un destino interno, y
  el motivo del bloqueo no vuelve al modelo.
- **Estructurales (AC3, AC4)**: no hay `verify=False` en `backend/app/` (por AST);
  `tools.py` no tiene cliente HTTP propio; sus tres salidas pasan por la guarda; y la
  infraestructura **no** pasa por ella.

Verificado por mutación: no desmapear IPv4-en-IPv6, mirar solo la primera IP
resuelta, volver a `follow_redirects=True`, volver al `endswith` de dominio, devolver
a `fetch_url` su cliente propio y reintroducir `verify=False`. Cada mutación tumba su
test.

## Verificación

```
DEBUG=true SECRET_KEY=ci-secret-not-for-prod python -m pytest -q
# → 845 passed, 15 skipped (los 15 son los de Redis de #170, sin servidor aquí)

python3 scripts/validate_specs.py         # → [OK]
```

Comprobado además a mano contra la guarda: `169.254.169.254`, `127.0.0.1:8000`,
`localhost`, `[::1]`, `10.0.0.5`, `192.168.1.1`, `[::ffff:127.0.0.1]`, `0.0.0.0`,
`file://` y `gopher://` se bloquean con su motivo; `https://example.com/` pasa.

## Definition of Done

- [x] **AC1–AC5** de SPEC-024.
- [x] Tests que cubren el cambio, en verde (34 nuevos).
- [x] Docs: SPEC-024 nueva, SPEC-002 corregida, `CLAUDE.md` y los dos `config.yaml`.
- [x] Sin secretos en el diff; sin dependencias nuevas.
- [x] Rama con prefijo `sec/` hacia `develop`.

## Seguimiento

- **SPEC-024 nace `Draft`**, como manda el pipeline de autoría (ADR-0007): pasarla a
  `Ready` es decisión de la revisión de esta PR. Hasta entonces `/sdd-sync` no
  siembra T2.5 — y conviene que no lo haga, porque #159 ya cubre este trabajo.
- **#159 es T2.1 y esto entrega T2.5.** El ID no se reutiliza porque T2.1 pertenece a
  una spec que no se reactiva; el issue se cierra igual, que es el trabajo que pedía.
- **Queda residuo de *DNS rebinding*.** Se valida la IP resuelta y se revalida cada
  redirección, que es la ventana explotable en la práctica, pero entre la validación
  y la conexión hay un hueco teórico. Cerrarlo del todo exige fijar el socket a la IP
  validada, y eso rompe SNI y la verificación de certificado en `httpx` sin un
  transporte propio. Está documentado en SPEC-024 §5 en vez de fingir que está
  cerrado.
- **No se filtra el contenido devuelto.** Una página descargada puede traer una
  inyección de prompt; es un problema distinto y merece su propia spec.
- **`SCRAPER_SEMANTIC_RERANK` sigue en la configuración** apuntando a un scraper que
  ya no existe. Es ruido, no riesgo; limpiarlo es un `chore` aparte.
