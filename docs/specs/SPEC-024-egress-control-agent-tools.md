# SPEC-024: Control de egress de las herramientas de agente

- **Estado:** Draft
- **Autor:** Equipo de plataforma
- **Fecha:** 2026-09-04
- **Épica:** E2 (Seguridad: Aplicación / AppSec)
- **ADR relacionado:** [ADR-0003](../adr/0003-security-baseline-and-threat-model.md)
- **Severidad:** 🔴

> Sustituye a [SPEC-002](SPEC-002-scraper-ssrf-protection.md) (**Superseded**). Aquella
> protegía el scraper del Investigador, que se eliminó en `71e3923`; su nota de
> obsolescencia dice que **si se reintroduce fetch saliente se abra una spec nueva**
> en vez de reactivarla. Eso es lo que ha pasado: el fetch volvió por otra puerta —las
> capacidades de herramientas— y la superficie de hoy no es la que aquella describía.

## 1. Problema

`backend/app/platform/capabilities/tools.py` expone al modelo un catálogo de
herramientas invocables durante el bucle de *tool calling*
([`llm.py:984`](../../backend/app/platform/llm.py),
[`llm.py:1066`](../../backend/app/platform/llm.py)). Dos de ellas salen a la red y
**ninguna valida el destino**:

- **`fetch_url`** ([`tools.py:161`](../../backend/app/platform/capabilities/tools.py))
  descarga la URL que le pase el modelo y **devuelve el cuerpo como resultado de la
  herramienta**, que entra en el contexto y acaba en el artículo. Su única validación
  es `url.startswith(("http://", "https://"))`, y usa `follow_redirects=True`.
- **`ddg_search_with_urls`** ([`tools.py:213`](../../backend/app/platform/capabilities/tools.py)
  y [`tools.py:251`](../../backend/app/platform/capabilities/tools.py)) llama a arXiv
  y Wikipedia con **`verify=False`**, desactivando la verificación TLS.

Tres consecuencias, en orden de gravedad:

1. **SSRF con lectura.** `fetch_url` alcanza `http://169.254.169.254/latest/meta-data/`
   (credenciales de instancia en AWS/GCP/Azure), `http://127.0.0.1:8000/...` (la propia
   API), Qdrant y Ollama en la red interna, y `http://[::1]/`. Lo leído vuelve al
   modelo, así que no es un SSRF ciego: es exfiltración.
2. **La URL la elige el modelo, y el modelo lee texto no confiable.** El
   Investigador recibe documentos del RAG y contexto que escribe la persona usuaria;
   una instrucción incrustada en un PDF basta para dirigir la herramienta. No hace
   falta acceso a la API para explotarlo.
3. **`verify=False` invalida TLS** en dos llamadas, y el comentario que lo justifica
   («*corporate proxies with SSL inspection*») convierte un problema de despliegue en
   una desactivación permanente de la verificación para todo el mundo.

Las herramientas se activan por perfil de agente (`tools_enabled`, `tools`), editable
desde la interfaz, así que el vector se enciende sin tocar código.

## 2. Objetivos / No-objetivos

**Objetivos**
- Una **guarda central** de egress, reutilizada por todo fetch saliente cuyo destino
  pueda influir la persona usuaria o el modelo.
- Bloquear destinos internos **por IP resuelta**, no por aspecto del hostname.
- Revalidar **cada salto de redirección**, no solo la URL inicial.
- Restaurar la verificación TLS.
- Allowlist/denylist de dominios configurable, con un valor por defecto seguro.

**No-objetivos**
- **No** se aplica la guarda a los destinos de infraestructura que configura quien
  opera —Ollama, Qdrant, los proveedores de LLM—. Son `localhost` o red privada **a
  propósito**: pasarlos por la guarda rompería el despliegue local sin ganar nada, ya
  que su URL sale de la configuración y no de una entrada de usuario. La frontera de
  esta spec es «destino influido por usuario o modelo».
- **No** se reintroduce el scraper eliminado, ni se añaden herramientas nuevas.
- **No** se resuelve el *DNS rebinding* con fijación de socket (ver §5): se cierra la
  ventana práctica y se documenta el residuo.
- **No** se filtra el **contenido** devuelto (p. ej. inyección de prompt desde la
  página descargada). Es un problema distinto y merece su propia spec.

## 3. Criterios de aceptación (Given/When/Then)

- [ ] **AC1** — *Given* una URL que resuelve a loopback, red privada, enlace-local
  —incluida `169.254.169.254`— o sus equivalentes IPv6 (`::1`, `fc00::/7`,
  `fe80::/10`, y las IPv4 mapeadas `::ffff:127.0.0.1`), *When* una herramienta intenta
  obtenerla, *Then* se rechaza **antes de abrir la conexión** y se registra el bloqueo
  con su motivo.
- [ ] **AC2** — *Given* un hostname público que resuelve a una IP interna, *When* se
  valida, *Then* se comprueban **todas las IPs resueltas** y se rechaza si alguna es
  interna; y *Given* una redirección hacia un destino interno, *When* se sigue,
  *Then* se revalida cada salto y se rechaza igual que la URL inicial.
- [ ] **AC3** — *Given* cualquier llamada de red del código de la plataforma, *Then*
  ninguna usa `verify=False`; la verificación TLS está activa. Hay una comprobación
  automatizada que falla si alguien lo reintroduce.
- [ ] **AC4** — *Given* la configuración, *Then* existen una allowlist y una denylist
  de dominios y una única función `is_egress_allowed(url)` que las aplica; toda
  herramienta con fetch saliente pasa por ella, y hay una comprobación automatizada
  que falla si aparece un cliente HTTP nuevo en el módulo de herramientas que la
  esquive.
- [ ] **AC5** — *Given* AC1–AC4, *Then* existen tests con casos representativos:
  metadata de nube, loopback IPv4/IPv6, privadas, redirección a interna, esquema no
  soportado, dominio en denylist y allowlist vacía frente a poblada.

## 4. Diseño propuesto

### 4.1 La guarda

`backend/app/platform/egress.py`, sin dependencias del dominio:

- `is_egress_allowed(url) -> Decision` — decisión con motivo legible; no lanza.
- `assert_safe_url(url)` — lo mismo, pero levanta `EgressBlocked`. Es la que usan las
  herramientas, para que el bloqueo no se pueda ignorar por olvido.
- `safe_get(url, ...)` — cliente HTTP que valida, **desactiva las redirecciones
  automáticas** y sigue los saltos a mano revalidando cada uno.

Orden de comprobación, de lo barato a lo caro: esquema → denylist → allowlist →
resolución DNS → clasificación de cada IP. Se resuelve una sola vez y se comprueban
**todas** las direcciones devueltas por `getaddrinfo`.

### 4.2 Qué se considera interno

Se delega en `ipaddress` en vez de comparar cadenas: `is_loopback`, `is_private`,
`is_link_local`, `is_reserved`, `is_multicast`, `is_unspecified`, y el desmapeo de
IPv4-en-IPv6 (`::ffff:a.b.c.d`) antes de clasificar, porque una IPv4 mapeada no es
`is_private` como IPv6 y ese es el hueco clásico.

### 4.3 Configuración

`EGRESS_ALLOWED_DOMAINS` y `EGRESS_DENIED_DOMAINS` (listas, vacías por defecto:
allowlist vacía = «todo lo público»), `EGRESS_ALLOW_HTTP` (por defecto `false`) y
`EGRESS_MAX_REDIRECTS`. El *matching* de dominio es por sufijo de etiqueta —
`ejemplo.com` cubre `a.ejemplo.com` pero no `noejemplo.com`.

### 4.4 Aplicación

En `tools.py`: `_fetch_url` pasa por `safe_get`; las llamadas a arXiv y Wikipedia
pierden `verify=False` y pasan también por la guarda. Los destinos de infraestructura
(§2) quedan fuera por decisión explícita.

### 4.5 Guardas estructurales

Dos comprobaciones que fallan en CI si alguien deshace esto: una busca `verify=False`
en `backend/app/` (AC3) y otra exige que todo cliente HTTP de `tools.py` salga de la
guarda (AC4). Sin ellas, la protección dura hasta el siguiente que tenga prisa.

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| *DNS rebinding* entre la validación y la conexión (TOCTOU) | Se alcanza una IP interna pese a la comprobación | Se valida la IP resuelta y se revalida cada redirección, que es la ventana explotable en la práctica. La fijación de socket a la IP validada rompe SNI/verificación de certificado en `httpx` sin un transporte propio; se documenta como residuo en vez de fingir que está cerrado |
| Un proxy corporativo con inspección TLS vuelve a fallar | Alguien reintroduce `verify=False` | El CA del proxy se instala en el sistema o se apunta `SSL_CERT_FILE`; el test estructural impide el atajo |
| La allowlist vacía deja pasar todo lo público | Menos restrictivo de lo ideal | Es el valor por defecto para no romper arXiv/Wikipedia; quien opera puede cerrarla, y lo interno queda bloqueado igualmente |
| Falsos positivos en despliegues donde el destino legítimo es privado | Una herramienta deja de funcionar | El bloqueo se registra con motivo, así que se diagnostica leyendo el log, y el dominio se añade a la allowlist |

## 6. Plan de pruebas

- **Unitarias de la guarda** (AC1, AC2, AC4): metadata de nube, loopback IPv4/IPv6,
  privadas, enlace-local, IPv4 mapeada en IPv6, `0.0.0.0`, esquema `file:`/`gopher:`,
  hostname público que resuelve a interna (con `getaddrinfo` sustituido), denylist,
  allowlist poblada y coincidencia por sufijo de etiqueta.
- **Integración de las herramientas** (AC1, AC2): `_fetch_url` rechaza una URL interna
  sin abrir conexión —con el cliente HTTP sustituido por uno que falla si se usa—, y
  una redirección a interna se corta en el salto.
- **Estructurales** (AC3, AC4): no hay `verify=False` en `backend/app/`; todo cliente
  HTTP de `tools.py` sale de la guarda.
- Cada AC se valida con al menos un test automatizado (DoD §6).

## 7. Impacto operativo / observabilidad

- Cada bloqueo se registra a nivel `WARNING` con la URL, el motivo y la herramienta.
  Un bloqueo silencioso sería indistinguible de un fallo de red.
- Sin migraciones ni cambios de datos. *Rollback*: la guarda es un módulo nuevo y su
  aplicación son cuatro llamadas; revertir el commit lo deshace.
- Si un despliegue necesita alcanzar un destino privado, se añade a
  `EGRESS_ALLOWED_DOMAINS`; el bloqueo previo queda en el log para diagnosticarlo.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E2
  title: "Seguridad: Aplicación (AppSec)"
  area: area/security
tasks:
  - id: T2.5
    title: Guarda central de egress para las herramientas de agente + restaurar verificación TLS
    sev: high
    depends_on: []
    acceptance: [AC1, AC2, AC3, AC4, AC5]
```
