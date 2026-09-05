import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel


class Settings(BaseModel):
    """Configuracion global de la aplicacion."""

    # Core
    APP_NAME: str = "Alejandria Magazine"
    VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/alejandria"

    # Security
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Brute-force / credential-stuffing protection for the auth endpoints.
    # Sliding-window rate limit per client IP (login & register) and per-account
    # lockout after consecutive failed logins.
    AUTH_RATELIMIT_MAX_ATTEMPTS: int = 10      # attempts per window per IP (0 disables)
    AUTH_RATELIMIT_WINDOW_SECONDS: int = 60    # rolling window length, seconds
    AUTH_LOCKOUT_MAX_FAILED: int = 5           # failed logins before lockout (0 disables)
    AUTH_LOCKOUT_SECONDS: int = 900            # lockout duration, seconds (15 min)

    # SSE stream auth — the JWT is never placed in the stream query string (T1.4);
    # clients exchange it for a single-use ticket valid for this many seconds.
    SSE_TICKET_TTL_SECONDS: int = 30

    # Retención de datos (SPEC-020/T6.5). Ventanas en días; **0 desactiva** la purga
    # de ese conjunto, que es lo que hay que poner cuando una obligación legal exige
    # conservar. Los valores por defecto están razonados en
    # docs/governance/data-retention.md; cambiarlos aquí sin actualizar el documento
    # deja la política mintiendo.
    RETENTION_AUDIT_LOG_DAYS: int = 365
    RETENTION_AGENT_RUNS_DAYS: int = 90
    # Traza de explicabilidad (SPEC-014/T9.1). Misma ventana que
    # `agent_runs`: describe las mismas ejecuciones y con el mismo detalle,
    # así que conservarla más tiempo alargaría por la puerta de atrás la
    # retención de lo que el usuario escribió.
    RETENTION_AGENT_RUN_STEPS_DAYS: int = 90
    RETENTION_CHECKPOINTS_DAYS: int = 30
    RETENTION_NOTIFICATIONS_DAYS: int = 90
    RETENTION_ORPHAN_ASSETS_DAYS: int = 30

    # Access controls — MUST be False in production
    ENABLE_DEV_ROLE_PROMOTION: bool = False
    # Siembra de demo: usuarios con credenciales conocidas y contenido de ejemplo
    # (SPEC-015/T1.6/AC5). Apagada por defecto y, además, **imposible de encender
    # con DEBUG=false** (ver `_disable_dev_only_flags`).
    ENABLE_DEV_SEED: bool = False

    # Default role assigned to users who self-register. Minimal privilege by
    # design: only "lector" or "publico" are accepted; anything else falls back
    # to "lector". Never grant "redactor"/"admin" on signup.
    DEFAULT_SIGNUP_ROLE: str = "lector"

    # Redis
    REDIS_URL: str = "redis://:password@localhost:6379/0"
    # Tracing OpenTelemetry (SPEC-019/T5.3). Apagado por defecto: exporta trazas a
    # un colector externo y no debe encenderse sin querer. Los nombres son los
    # estándar de OTel a propósito, para que quien ya opera un colector reconozca
    # las variables sin traducir nada.
    OTEL_ENABLED: bool = False
    OTEL_SERVICE_NAME: str = "alejandria-backend"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    # Coordinación entre workers (SPEC-018/T4.3). Apagada por defecto: con un solo
    # worker el bus en proceso basta y no obliga a levantar Redis para desarrollar.
    #
    # Hace falta un interruptor propio y no vale «¿hay REDIS_URL?»: esa variable
    # tiene un valor por defecto no vacío, así que la ausencia de configuración es
    # indistinguible de una configuración real y el fallback nunca se activaría.
    REDIS_ENABLED: bool = False

    # Motor de agentes (SPEC-013/T8.3/AC8). Conmuta cómo se resuelve la
    # infraestructura de cada agente:
    #   "adapters"     — el agente importa su RAG y su LLM directamente (camino
    #                    de siempre; es el valor por defecto).
    #   "capabilities" — el motor resuelve las capacidades que el agente declara
    #                    y se las inyecta, de modo que un proyecto pueda traer
    #                    sus propios proveedores.
    # Los dos caminos deben producir el mismo resultado: hay un test de paridad
    # que lo comprueba sobre el flujo completo, y nada del camino viejo se borra
    # hasta que ese test forme parte de la rutina.
    AGENT_ENGINE: str = "adapters"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:1b"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION: str = "rag_docs"
    RAG_VECTOR_SIZE: int = 768

    # Egress de las herramientas de agente (SPEC-024/T2.5). Solo aplica a los
    # destinos que elige el usuario o el modelo; Ollama, Qdrant y los proveedores
    # de LLM quedan fuera a propósito — son infraestructura configurada, y su URL
    # es privada adrede.
    #: Dominios separados por comas. Vacía = «todo lo público» (lo interno sigue
    #: bloqueado por IP). Poblada, solo pasa lo que esté aquí.
    EGRESS_ALLOWED_DOMAINS: str = ""
    #: Dominios separados por comas que se rechazan aunque estén en la allowlist.
    EGRESS_DENIED_DOMAINS: str = ""
    #: `http://` en claro. Apagado por defecto: una herramienta de lectura web no
    #: tiene por qué necesitarlo, y en claro la respuesta es manipulable.
    EGRESS_ALLOW_HTTP: bool = False
    #: Saltos de redirección que se siguen, revalidando cada uno.
    EGRESS_MAX_REDIRECTS: int = 3

    # LLM Provider — "anthropic" (default, Claude via official SDK) |
    # "ollama" (on-prem) | "openai" (OpenAI-compatible API, incl. vLLM/LM Studio).
    # Claude is the default agentic engine (SPEC-023/ADR-0009); switch to on-prem
    # by configuration (env var or config.yaml), no code change required.
    LLM_PROVIDER: str = "anthropic"

    # LLM resilience — automatic retry of transient failures (connection refused,
    # timeouts, 5xx, empty responses) with exponential backoff + jitter.
    LLM_MAX_RETRIES: int = 3          # extra attempts after the first try (0 disables)
    LLM_RETRY_BASE_DELAY: float = 1.0  # seconds; first backoff, doubles each retry
    LLM_RETRY_MAX_DELAY: float = 10.0  # cap for a single backoff wait

    # Pipeline auto-resume — when a node fails with a transient error even after the
    # LLM retries above, the orchestrator resumes from the last checkpoint this many
    # times (with backoff) before surfacing the failure for a manual retry.
    PIPELINE_AUTO_RESUME_ATTEMPTS: int = 2
    PIPELINE_AUTO_RESUME_DELAY: float = 3.0

    # OpenAI / OpenAI-compatible (Azure, vLLM, Groq, etc.)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    # Override to use Azure OpenAI, vLLM compat layer, Groq, etc.
    # Leave empty to use the official OpenAI endpoint.
    OPENAI_BASE_URL: Optional[str] = None

    # Anthropic (Claude) — motor agéntico (SPEC-023 / ADR-0009). El proveedor se
    # selecciona con LLM_PROVIDER="anthropic". La API key nunca se registra ni se
    # expone en errores; su ausencia con el proveedor activo NO aborta el arranque:
    # la primera llamada al LLM falla con un error permanente (error perezoso).
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-opus-5"
    ANTHROPIC_BASE_URL: Optional[str] = None  # gateway/proxy opcional; vacío = endpoint oficial
    ANTHROPIC_MAX_TOKENS: int = 4096          # Anthropic exige max_tokens; sobreescribible por agente

    # Investigador scraper
    SCRAPER_SEMANTIC_RERANK: bool = True

    # RAG global defaults
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 80
    RAG_TOP_K: int = 6
    RAG_SCORE_THRESHOLD: float = 0.0

    # MinIO
    MINIO_URL: str = "http://localhost:9000"
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "minioadmin"


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_yaml_config() -> dict[str, Any]:
    configured = os.getenv("CONFIG_YAML_PATH", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("config.yaml"),
            Path("../config.yaml"),
            Path("../../config.yaml"),
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            with candidate.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
    return {}


def _build_settings() -> Settings:
    yaml_config = _read_yaml_config()

    app = yaml_config.get("app", {})
    security = yaml_config.get("security", {})
    access = yaml_config.get("access_control", {})
    database = yaml_config.get("database", {})
    redis = yaml_config.get("redis", {})
    ollama = yaml_config.get("ollama", {})
    qdrant = yaml_config.get("qdrant", {})
    minio = yaml_config.get("minio", {})
    retention = yaml_config.get("retention", {})
    otel = yaml_config.get("otel", {})
    engine = yaml_config.get("engine", {})
    egress = yaml_config.get("egress", {})

    merged: dict[str, Any] = {
        "APP_NAME": app.get("name", "Alejandria Magazine"),
        "VERSION": app.get("version", "0.1.0"),
        "DEBUG": app.get("debug", False),
        "DATABASE_URL": database.get(
            "url", "postgresql+asyncpg://postgres:password@localhost:5432/alejandria"
        ),
        "SECRET_KEY": security.get("secret_key", "your-secret-key-change-in-production"),
        "ALGORITHM": security.get("algorithm", "HS256"),
        "ACCESS_TOKEN_EXPIRE_MINUTES": security.get("access_token_expire_minutes", 30),
        "REFRESH_TOKEN_EXPIRE_DAYS": security.get("refresh_token_expire_days", 7),
        "AUTH_RATELIMIT_MAX_ATTEMPTS": security.get("auth_ratelimit_max_attempts", 10),
        "AUTH_RATELIMIT_WINDOW_SECONDS": security.get("auth_ratelimit_window_seconds", 60),
        "AUTH_LOCKOUT_MAX_FAILED": security.get("auth_lockout_max_failed", 5),
        "AUTH_LOCKOUT_SECONDS": security.get("auth_lockout_seconds", 900),
        "SSE_TICKET_TTL_SECONDS": security.get("sse_ticket_ttl_seconds", 30),
        # Retención (SPEC-020/T6.5)
        "RETENTION_AUDIT_LOG_DAYS": retention.get("audit_log_days", 365),
        "RETENTION_AGENT_RUNS_DAYS": retention.get("agent_runs_days", 90),
        "RETENTION_AGENT_RUN_STEPS_DAYS": retention.get("agent_run_steps_days", 90),
        "RETENTION_CHECKPOINTS_DAYS": retention.get("checkpoints_days", 30),
        "RETENTION_NOTIFICATIONS_DAYS": retention.get("notifications_days", 90),
        "RETENTION_ORPHAN_ASSETS_DAYS": retention.get("orphan_assets_days", 30),
        # Fail-safe: when the key is absent the effective value is False.
        "ENABLE_DEV_ROLE_PROMOTION": access.get("enable_dev_role_promotion", False),
        "ENABLE_DEV_SEED": access.get("enable_dev_seed", False),
        "DEFAULT_SIGNUP_ROLE": access.get("default_signup_role", "lector"),
        "REDIS_URL": redis.get("url", "redis://:password@localhost:6379/0"),
        "REDIS_ENABLED": redis.get("enabled", False),
        "AGENT_ENGINE": engine.get("agents", "adapters"),
        # Tracing (SPEC-019/T5.3)
        "OTEL_ENABLED": otel.get("enabled", False),
        "OTEL_SERVICE_NAME": otel.get("service_name", "alejandria-backend"),
        "OTEL_EXPORTER_OTLP_ENDPOINT": otel.get("endpoint", ""),
        "OLLAMA_BASE_URL": ollama.get("base_url", "http://localhost:11434"),
        "OLLAMA_MODEL": ollama.get("default_model", "llama3.2:1b"),
        "OLLAMA_EMBED_MODEL": ollama.get("embed_model", "nomic-embed-text"),
        "QDRANT_URL": qdrant.get("url", "http://localhost:6333"),
        "QDRANT_API_KEY": qdrant.get("api_key"),
        "QDRANT_COLLECTION": qdrant.get("collection", "rag_docs"),
        "RAG_VECTOR_SIZE": qdrant.get("vector_size", 768),
        "MINIO_URL": minio.get("url", "http://localhost:9000"),
        "MINIO_ROOT_USER": minio.get("root_user", "minioadmin"),
        "MINIO_ROOT_PASSWORD": minio.get("root_password", "minioadmin"),
        # LLM provider
        "LLM_PROVIDER": yaml_config.get("llm", {}).get("provider", "anthropic"),
        "LLM_MAX_RETRIES": yaml_config.get("llm", {}).get("max_retries", 3),
        "LLM_RETRY_BASE_DELAY": yaml_config.get("llm", {}).get("retry_base_delay", 1.0),
        "LLM_RETRY_MAX_DELAY": yaml_config.get("llm", {}).get("retry_max_delay", 10.0),
        "PIPELINE_AUTO_RESUME_ATTEMPTS": yaml_config.get("llm", {}).get("auto_resume_attempts", 2),
        "PIPELINE_AUTO_RESUME_DELAY": yaml_config.get("llm", {}).get("auto_resume_delay", 3.0),
        "OPENAI_API_KEY": yaml_config.get("openai", {}).get("api_key", ""),
        "OPENAI_MODEL": yaml_config.get("openai", {}).get("model", "gpt-4o-mini"),
        "OPENAI_EMBED_MODEL": yaml_config.get("openai", {}).get("embed_model", "text-embedding-3-small"),
        "OPENAI_BASE_URL": yaml_config.get("openai", {}).get("base_url") or None,
        # Anthropic (Claude)
        "ANTHROPIC_API_KEY": yaml_config.get("anthropic", {}).get("api_key", ""),
        "ANTHROPIC_MODEL": yaml_config.get("anthropic", {}).get("model", "claude-opus-5"),
        "ANTHROPIC_BASE_URL": yaml_config.get("anthropic", {}).get("base_url") or None,
        "ANTHROPIC_MAX_TOKENS": yaml_config.get("anthropic", {}).get("max_tokens", 4096),
        # Investigador scraper — reads from agents.investigador first, then legacy investigador key
        "SCRAPER_SEMANTIC_RERANK": (
            yaml_config.get("agents", {}).get("investigador", {}).get("semantic_rerank")
            if yaml_config.get("agents", {}).get("investigador", {}).get("semantic_rerank") is not None
            else yaml_config.get("investigador", {}).get("semantic_rerank", True)
        ),
        # Egress (SPEC-024/T2.5)
        "EGRESS_ALLOWED_DOMAINS": egress.get("allowed_domains", ""),
        "EGRESS_DENIED_DOMAINS": egress.get("denied_domains", ""),
        "EGRESS_ALLOW_HTTP": egress.get("allow_http", False),
        "EGRESS_MAX_REDIRECTS": egress.get("max_redirects", 3),
        # RAG global defaults
        "RAG_CHUNK_SIZE": yaml_config.get("rag", {}).get("chunk_size", 800),
        "RAG_CHUNK_OVERLAP": yaml_config.get("rag", {}).get("chunk_overlap", 80),
        "RAG_TOP_K": yaml_config.get("rag", {}).get("top_k", 6),
        "RAG_SCORE_THRESHOLD": yaml_config.get("rag", {}).get("score_threshold", 0.0),
    }

    # Environment variables override config.yaml
    for key in list(merged.keys()):
        env_value = os.getenv(key)
        if env_value is None:
            continue
        if isinstance(merged[key], bool):
            merged[key] = _to_bool(env_value)
        elif isinstance(merged[key], int):
            merged[key] = int(env_value)
        else:
            merged[key] = env_value

    _disable_dev_only_flags(merged)
    return Settings(**merged)


# Flags que solo tienen sentido en desarrollo. Con `DEBUG=false` su valor efectivo
# es False **venga de donde venga** —variable de entorno o config.yaml—, en lugar de
# confiar en que nadie los deje encendidos al desplegar.
#
# Ser fail-safe *cuando el flag falta* no basta, que es donde estaba el hueco: un
# `config.yaml` con el flag a `true` viaja en el repositorio y se despliega tal cual,
# así que el caso peligroso no es el olvido de ponerlo sino el olvido de quitarlo.
#
# - `ENABLE_DEV_ROLE_PROMOTION` (SPEC-015/T1.5/AC4): atajo que permite a un usuario
#   cambiarse el rol a sí mismo. En producción es una escalada de privilegios.
# - `ENABLE_DEV_SEED` (SPEC-015/T1.6/AC5): siembra cuentas con contraseña conocida.
#
# Cualquier flag nuevo que abra un atajo de desarrollo va en esta tupla.
_DEV_ONLY_FLAGS = ("ENABLE_DEV_ROLE_PROMOTION", "ENABLE_DEV_SEED")


def _disable_dev_only_flags(merged: dict[str, Any]) -> None:
    if merged.get("DEBUG"):
        return
    for flag in _DEV_ONLY_FLAGS:
        merged[flag] = False


# Minimum entropy proxy for a production SECRET_KEY. `openssl rand -hex 32`
# yields 64 hex chars; we accept anything at/above this length that is not an
# obvious placeholder.
SECRET_KEY_MIN_LENGTH = 32

# Substrings that mark a value as an obvious non-production placeholder. Matched
# case-insensitively anywhere in the key so committed defaults (docker-compose,
# config.yaml, .env.example) cannot slip through as "valid" in production.
_INSECURE_SECRET_MARKERS = (
    "change-in-production",
    "change-me",
    "changeme",
    "cambia-esto",
    "cambia",
    "dev-secret",
    "local-dev-secret",
    "your-secret-key",
    "not-for-production",
    "not-for-prod",
    "placeholder",
    "example",
    "insecure",
    "secret-key",
    "password",
)


def _is_insecure_secret_key(value: str) -> bool:
    """True when SECRET_KEY is empty, too short, or a known weak placeholder."""
    key = (value or "").strip()
    if len(key) < SECRET_KEY_MIN_LENGTH:
        return True
    lowered = key.lower()
    return any(marker in lowered for marker in _INSECURE_SECRET_MARKERS)


def _validate_settings(s: Settings) -> None:
    """Fail fast on insecure production configurations."""
    if not s.DEBUG and _is_insecure_secret_key(s.SECRET_KEY):
        raise ValueError(
            "SECRET_KEY must be set to a strong random value in production "
            f"(>= {SECRET_KEY_MIN_LENGTH} chars, no committed placeholder). "
            "Generate one with: openssl rand -hex 32  (or: "
            "python -c \"import secrets; print(secrets.token_hex(32))\"). "
            "Inject it via the SECRET_KEY environment variable — never commit it."
        )


settings = _build_settings()
_validate_settings(settings)


def reload_settings() -> Settings:
    """Re-read configuration (env > config.yaml > defaults) into the live object.

    Without this, editing ``config.yaml`` — including saving from the Config
    screen — has no effect until the process restarts, because ``settings`` is
    built once at import time.

    **Mutates the existing instance in place instead of rebinding this module's
    global.** Modules bind the object at import (``from app.core.config import
    settings``), so rebinding the name here would leave those references
    pointing at the stale object while others saw the new one.

    The fresh configuration is validated *before* anything is applied, so an
    invalid file (e.g. an insecure production ``SECRET_KEY``) raises and leaves
    the running configuration untouched rather than half-updated.
    """
    fresh = _build_settings()
    _validate_settings(fresh)
    for field, value in fresh.model_dump().items():
        setattr(settings, field, value)
    return settings
