# SPEC-002: Protección SSRF del scraper del Investigador

- **Estado:** Superseded
- **Autor:** Equipo de plataforma
- **Fecha:** 2026-06-26
- **Épica:** E2 (AppSec)
- **ADR relacionado:** ADR-0003
- **Severidad:** 🔴

> **Obsoleta (2026-07-02).** La superficie de ataque que esta spec mitigaba
> **dejó de existir**: el scraper de navegación web (`scraper.py`) se eliminó como
> código muerto en el commit `71e3923` (*"eliminar el scraper del Investigador"*,
> Refs #159). Se conserva como registro histórico. Si en el futuro se reintroduce
> fetch saliente, ábrase una **nueva spec** de control de egress en vez de reactivar
> esta. La tarea T2.1 queda sin efecto (el bloque `sdd-sync` de la sección 8 ya no se
> sincroniza al no estar en Ready).
>
> **Actualización (2026-09-04).** Eso es exactamente lo que pasó: el fetch saliente
> volvió por otra puerta —las capacidades de herramientas, `fetch_url` en
> `platform/capabilities/tools.py`— con la URL elegida por el modelo y sin ninguna
> validación de destino. La frase de arriba que decía que la guarda «no es necesaria
> con la arquitectura actual» **dejó de ser cierta** y se ha retirado. La sustituta,
> siguiendo la instrucción de esta misma nota, es
> [SPEC-024](SPEC-024-egress-control-agent-tools.md) (tarea T2.5), que sí implementa
> la guarda central.

## 1. Problema

El scraper valida las URLs solo por esquema y host
([scraper.py:129-134](../../backend/app/modules/agents/adapters/scraper.py#L129)):

```python
def _is_valid_url(url):
    p = urllib.parse.urlparse(url)
    return p.scheme in ("http","https") and bool(p.netloc)
```

No bloquea `localhost`, `127.0.0.0/8`, rangos privados (`10/8`, `172.16/12`,
`192.168/16`), enlace-local (`169.254.169.254`, metadata de cloud) ni IPv6
loopback/ULA. Como el scraper sigue enlaces de páginas externas y acepta
entrada influida por el usuario (keywords/contexto), puede ser inducido a
consultar **servicios internos** (Qdrant `:6333`, Redis, Ollama, metadata del
proveedor). Además usa `httpx.AsyncClient(verify=False)`, deshabilitando la
verificación TLS (MITM).

## 2. Objetivos / No-objetivos

- **Objetivos:** impedir peticiones salientes a destinos internos/privados;
  restaurar verificación TLS; centralizar el control de *egress*.
- **No-objetivos:** proxy de egress corporativo (futuro); cambiar las fuentes
  permitidas (arXiv, Wikipedia, etc.).

## 3. Criterios de aceptación

- [ ] **AC1** — *Given* una URL que resuelve a loopback/privada/enlace-local
  (incl. `169.254.169.254` y IPv6), *When* el scraper intenta obtenerla,
  *Then* se rechaza **antes** de la petición y se registra el bloqueo.
- [ ] **AC2** — La resolución DNS se valida (anti *DNS-rebinding*): se comprueba
  la **IP resuelta**, no solo el hostname.
- [ ] **AC3** — Ninguna llamada de red usa `verify=False`; la verificación TLS
  está activa por defecto.
- [ ] **AC4** — Existe una allowlist/denylist configurable de dominios y una
  función central `is_egress_allowed(url)` reutilizada por todo fetch saliente
  (scraper, robots.txt, RAG remoto).
- [ ] **AC5** — Tests que cubren AC1–AC3 con casos representativos.

## 4. Diseño propuesto

- Añadir `app/shared/egress.py` con `assert_safe_url(url)` que: parsea, resuelve
  el host (`socket.getaddrinfo`), y rechaza IPs en rangos no públicos usando
  `ipaddress.ip_address(...).is_private/is_loopback/is_link_local/is_reserved`.
- Invocarlo en `_is_valid_url`, en la descarga de `robots.txt` y en cualquier
  cliente httpx del scraper. Quitar `verify=False`.
- Allowlist opcional por configuración (`SCRAPER_ALLOWED_DOMAINS`).

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Falsos positivos (CDNs detrás de IPs raras) | Bajo | Allowlist explícita |
| Coste de resolución DNS por petición | Bajo | Cache corta de resolución |
| TLS estricto rompe sitios mal configurados | Bajo | Log + excepción controlada, no `verify=False` global |

## 6. Plan de pruebas

- Unit: `assert_safe_url` con loopback, privadas, link-local, IPv6, dominio
  público válido; mock de `getaddrinfo`.
- Integración: el scraper rechaza `http://127.0.0.1:6333` y
  `http://169.254.169.254/...`.

## 7. Impacto operativo / observabilidad

- Métrica/contador de URLs bloqueadas por egress.
- Documentar `SCRAPER_ALLOWED_DOMAINS` en `.env.example`.

## 8. Backlog (sincronización SDD)

```yaml
# sdd-sync v1
epic:
  id: E2
  title: "Seguridad: Aplicación (AppSec)"
  area: area/security
tasks:
  - id: T2.1
    title: Protección SSRF en el scraper + quitar verify=False
    sev: high
    depends_on: []
    acceptance: [AC1, AC2, AC3, AC4, AC5]
```
