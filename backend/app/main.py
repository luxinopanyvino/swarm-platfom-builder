from contextlib import asynccontextmanager
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.core.database import init_db
# Import models to ensure they are registered on Base.metadata
from app import models
from app.core.config import settings
from app.platform.project_context import ProjectContext
from app.core.errors import install_error_handling
from app.core.logging_config import configure_logging, request_id_middleware
from app.platform.metrics import metrics_middleware
from app.platform.tracing import setup_tracing, tracing_middleware
from app.core.security import hash_password

# Structured logging (JSON in prod, human-readable in debug) + correlation ids.
# Configure as early as possible so runtime logs use the central handler (SPEC-019/T5.1).
configure_logging()
logger = logging.getLogger(__name__)
from app.routers import auth, articles, ai, agents, flows, config, notifications, checkpoints, projects, audit, health
from app.routers.magazine import router as magazine_router
from app.core.database import AsyncSessionLocal
from app.shared.agents_seed import seed_agents_for_project
from app.models import (
    UserModel,
    UserRole,
    ProjectModel,
    ProjectUseCaseType,
    AgentProfileModel,
    ArticleModel,
    ArticleStatus,
    SavedFlowModel,
)
from app.platform.capabilities.rag import (
    chunk_text, embed_vector_size, ensure_collection, upsert_chunks,
)


def dev_seed_enabled() -> bool:
    """¿Puede sembrarse la demo? (SPEC-015 / T1.6 / AC5).

    Exige **flag explícito**, y la configuración además lo fuerza a `False` cuando
    `DEBUG=false` (ver `_disable_dev_only_flags`), así que aquí basta con leerlo.

    Antes la condición era «la base de datos es SQLite», un proxy de «esto es
    local» que no lo es: cualquier despliegue pequeño sobre SQLite sembraba un
    administrador de contraseña conocida.
    """
    return bool(settings.ENABLE_DEV_SEED)


async def ensure_local_admin_user() -> None:
    """Crear el administrador de desarrollo si no existe (solo con el flag de dev).

    **Crea, no reescribe.** La versión anterior reimponía contraseña, rol y estado
    en *cada arranque*, así que revertía en silencio un cambio de contraseña o una
    desactivación deliberada de la cuenta. Sembrar una credencial conocida es una
    comodidad de desarrollo; reimponerla es quitarle al operador el control de su
    propia cuenta.
    """
    if not dev_seed_enabled():
        return

    # Contraseña de desarrollo. Débil a propósito y por eso mismo encerrada tras el
    # flag: con `DEBUG=false` esta función ni siquiera llega aquí.
    admin_password = os.environ.get("DEV_ADMIN_PASSWORD", "admin123")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UserModel).where(UserModel.email == "admin@admin"))
        if result.scalars().first() is not None:
            return

        session.add(UserModel(
            email="admin@admin",
            hashed_password=hash_password(admin_password),
            full_name="Admin",
            role=UserRole.ADMIN,
            is_active=True,
        ))
        await session.commit()
        logger.warning(
            "Sembrado el administrador de desarrollo admin@admin con contraseña conocida. "
            "Solo ocurre con DEBUG=true y ENABLE_DEV_SEED=true.",
            extra={"event": "dev_seed_admin"},
        )


async def ensure_dev_users() -> None:
    """Crear los usuarios de prueba si faltan (solo con el flag de dev).

    Como el administrador: se crean, no se reescriben.
    """
    if not dev_seed_enabled():
        return

    dev_users = [
        {
            "email": "redactor@example.com",
            "password": "redactor123",
            "full_name": "Redactor de Pruebas",
            "role": UserRole.REDACTOR,
        },
        {
            "email": "revisor@example.com",
            "password": "revisor123",
            "full_name": "Revisor Académico",
            "role": UserRole.REDACTOR,
        },
        {
            "email": "publico@example.com",
            "password": "publico123",
            "full_name": "Público General",
            "role": UserRole.PUBLICO,
        },
    ]

    async with AsyncSessionLocal() as session:
        for user_info in dev_users:
            result = await session.execute(select(UserModel).where(UserModel.email == user_info["email"]))
            user = result.scalars().first()
            if user is None:
                session.add(UserModel(
                    email=user_info["email"],
                    hashed_password=hash_password(user_info["password"]),
                    full_name=user_info["full_name"],
                    role=user_info["role"],
                    is_active=True,
                ))
        await session.commit()


async def _get_default_admin_user(session) -> UserModel | None:
    result = await session.execute(select(UserModel).where(UserModel.email == "admin@admin"))
    return result.scalars().first()


async def _seed_default_project_content(session, project_id, author_id):
    """Seed sample articles and saved flows for the system project."""
    article_exists = await session.execute(
        select(ArticleModel).where(ArticleModel.project_id == project_id).limit(1)
    )
    if not article_exists.scalars().first():
        session.add_all([
            ArticleModel(
                title="Ejemplo de artículo publicado: AlejandrIA Magazine",
                body=(
                    "Este artículo de demostración muestra cómo la plataforma AlejandrIA Magazine "
                    "puede administrar contenidos científicos, desde la investigación hasta la publicación."
                ),
                status=ArticleStatus.PUBLISHED,
                author_id=author_id,
                project_id=project_id,
            ),
            ArticleModel(
                title="Borrador de ejemplo: flujo editorial rápido",
                body=(
                    "Empieza con este borrador y prueba tus flujos de agentes. "
                    "Puedes editar el contenido y ejecutar el pipeline para avanzar en el artículo."
                ),
                status=ArticleStatus.DRAFT,
                author_id=author_id,
                project_id=project_id,
            ),
        ])

    flow_exists = await session.execute(
        select(SavedFlowModel).where(SavedFlowModel.project_id == project_id).limit(1)
    )
    if not flow_exists.scalars().first():
        session.add_all([
            SavedFlowModel(
                name="Flujo editorial completo",
                author_id=author_id,
                project_id=project_id,
                nodes=[
                    {"id": "node-investigador", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"agentId": "investigador", "label": "Investigador"}},
                    {"id": "node-redactor", "type": "agent", "position": {"x": 260, "y": 0}, "data": {"agentId": "redactor", "label": "Redactor"}},
                    {"id": "node-revisor", "type": "agent", "position": {"x": 520, "y": 0}, "data": {"agentId": "revisor", "label": "Revisor"}},
                    {"id": "node-formateador", "type": "agent", "position": {"x": 780, "y": 0}, "data": {"agentId": "formateador", "label": "Formateador"}},
                    {"id": "node-publicador", "type": "agent", "position": {"x": 1040, "y": 0}, "data": {"agentId": "publicador", "label": "Publicador"}},
                ],
                edges=[
                    {"id": "edge-1", "source": "node-investigador", "target": "node-redactor", "animated": True},
                    {"id": "edge-2", "source": "node-redactor", "target": "node-revisor", "animated": True},
                    {"id": "edge-3", "source": "node-revisor", "target": "node-formateador", "animated": True},
                    {"id": "edge-4", "source": "node-formateador", "target": "node-publicador", "animated": True},
                ],
                flow_sequence=["investigador", "redactor", "revisor", "formateador", "publicador"],
            ),
            SavedFlowModel(
                name="Flujo rápido de publicación",
                author_id=author_id,
                project_id=project_id,
                nodes=[
                    {"id": "node-redactor", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"agentId": "redactor", "label": "Redactor"}},
                    {"id": "node-revisor", "type": "agent", "position": {"x": 260, "y": 0}, "data": {"agentId": "revisor", "label": "Revisor"}},
                    {"id": "node-publicador", "type": "agent", "position": {"x": 520, "y": 0}, "data": {"agentId": "publicador", "label": "Publicador"}},
                ],
                edges=[
                    {"id": "edge-1", "source": "node-redactor", "target": "node-revisor", "animated": True},
                    {"id": "edge-2", "source": "node-revisor", "target": "node-publicador", "animated": True},
                ],
                flow_sequence=["redactor", "revisor", "publicador"],
            ),
        ])

    await session.commit()


async def _seed_default_rag_document(project_id) -> None:
    """Seed a sample document into the RAG library for the local dev environment.

    Va al espacio del proyecto del sistema, no a la colección común: AC6 nombra
    explícitamente el documento de demo entre lo que no debe filtrarse a otros
    proyectos (SPEC-013 / T8.5).
    """
    sample_text = (
        "Bienvenido a AlejandrIA Magazine. Este documento de ejemplo demuestra cómo funciona "
        "la biblioteca de documentos y la búsqueda vectorial en la plataforma. Puedes usarlo "
        "para probar consultas, agentes de investigación y la búsqueda de contenidos."
    )
    collection = ProjectContext(project_id=project_id).collection(settings.QDRANT_COLLECTION)
    doc_id = "alejandria-welcome"
    filename = "bienvenida-alejandria-magazine.md"
    chunks = chunk_text(sample_text, chunk_size=settings.RAG_CHUNK_SIZE, overlap=settings.RAG_CHUNK_OVERLAP)
    await ensure_collection(settings.QDRANT_URL, collection, embed_vector_size(), settings.QDRANT_API_KEY)
    await upsert_chunks(
        settings.QDRANT_URL,
        collection,
        doc_id,
        "__library__",
        filename,
        chunks,
        settings.OLLAMA_BASE_URL,
        settings.OLLAMA_EMBED_MODEL,
        embed_vector_size(),
        settings.QDRANT_API_KEY,
    )


# Generated by GitHub Copilot
async def ensure_alejandria_magazine_project():
    """Ensure the AlejandrIA Magazine system project always exists.

    Devuelve su id: es el proyecto bajo el que se siembra el contenido de demo.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ProjectModel).where(ProjectModel.is_system == True)  # noqa: E712
        )
        existing = result.scalars().first()
        if existing is None:
            project = ProjectModel(
                name="AlejandrIA Magazine",
                description="Proyecto de revista científica con flujo editorial completo.",
                use_case_type=ProjectUseCaseType.ALEJANDRIA_MAGAZINE,
                owner_id=None,
                is_system=True,
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)
            await seed_agents_for_project(project.id, ProjectUseCaseType.ALEJANDRIA_MAGAZINE)
            await _seed_demo_content_if_enabled(session, project.id)
            return project.id
        # Seed if not yet seeded
        await seed_agents_for_project(existing.id, ProjectUseCaseType.ALEJANDRIA_MAGAZINE)
        await _seed_demo_content_if_enabled(session, existing.id)
        return existing.id


async def _seed_demo_content_if_enabled(session, project_id) -> None:
    """Artículos y flujos de ejemplo, solo bajo el flag de dev (AC5).

    El proyecto del sistema y sus perfiles de agente **sí** se siembran siempre:
    son configuración de la plataforma, y sin ellos un despliegue limpio arranca
    inservible. Lo que no debe aparecer en producción es el contenido de muestra,
    que ensucia las referencias reales.
    """
    if not dev_seed_enabled():
        return
    admin_user = await _get_default_admin_user(session)
    if admin_user is not None:
        await _seed_default_project_content(session, project_id, admin_user.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables on startup
    await init_db()
    await ensure_local_admin_user()
    await ensure_dev_users()
    project_id = await ensure_alejandria_magazine_project()
    # The "bienvenida" document is a demo fixture for local development only.
    # Seeding it in production would pollute real article references, so it is
    # gated behind DEBUG.
    if settings.DEBUG and project_id is not None:
        await _seed_default_rag_document(project_id)
    yield


app = FastAPI(title="AlejandrIA Magazine API", version="0.1.0", lifespan=lifespan)

# Middleware order note: in Starlette the middleware added *last* ends up
# outermost. The three below are therefore added inner-to-outer.

# Global exception handling (SPEC-016/T2.4): opaque 500 + structured log with the
# correlation id. Added first so it sits inside the correlation middleware (the
# request id is bound) and inside CORS (the 500 carries CORS headers, so the
# browser can actually read the id instead of seeing an opaque network error).
install_error_handling(app)

# Tracing (SPEC-019/T5.3), apagado salvo que se active por configuración. Se añade
# el primero para que quede **por dentro** del resto: así el span de la petición
# envuelve lo que hacen los demás middleware, que es lo que se quiere ver medido.
setup_tracing()
app.middleware("http")(tracing_middleware)

# Métricas por petición (SPEC-019/T5.2). Va aquí, dentro de CORS y por fuera del
# manejador de errores, para que una excepción también quede contada: si solo se
# midieran las respuestas correctas, la latencia parecería mejorar justo cuando el
# servicio empieza a fallar.
app.middleware("http")(metrics_middleware)

# Correlation id per request (X-Request-ID → logs + response header). Added before
# CORS so every request — including those short-circuited later — gets an id.
app.middleware("http")(request_id_middleware)

# CORS: restrict origins in production via ALLOWED_ORIGINS env var
# Development default allows the local Vite dev server on ports 5173 and 5174.
_raw_origins = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
)
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Registrar routers
app.include_router(auth.router)
app.include_router(articles.router)
app.include_router(ai.router)
app.include_router(agents.router)
app.include_router(flows.router)
app.include_router(config.router)
app.include_router(notifications.router)
app.include_router(checkpoints.router)
app.include_router(projects.router)
app.include_router(audit.router)
# Liveness y readiness (SPEC-019/T5.4). Sin prefijo: los consulta el orquestador.
app.include_router(health.router)
app.include_router(magazine_router)



