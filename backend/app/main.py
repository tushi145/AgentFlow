"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import __version__
from app.api.deps import limiter
from app.api.routes import agents, auth, system
from app.config.settings import Settings, get_settings
from app.llm.factory import build_llm_providers
from app.llm.registry import ModelRegistry
from app.database.session import create_schema, dispose_engine, init_engine, session_factory
from app.services.agent_config_service import seed_agent_configs
from app.services.auth_service import ensure_admin_user
from app.utils.errors import install_error_handlers
from app.utils.logging import configure_logging

log = logging.getLogger("agentflow")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        configure_logging(settings.log_level)
        if settings.app_env == "production" and \
                settings.jwt_secret.get_secret_value().startswith("dev-insecure"):
            raise RuntimeError("JWT_SECRET must be set in production.")
        if settings.jwt_secret.get_secret_value().startswith("dev-insecure"):
            log.warning("Using the built-in development JWT_SECRET; set JWT_SECRET in .env.")
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        init_engine(settings.resolved_database_url)
        if not hasattr(app.state, "model_registry"):
            app.state.model_registry = ModelRegistry(build_llm_providers(settings))
        await create_schema()
        async with session_factory()() as session:
            await ensure_admin_user(session, settings)
            await seed_agent_configs(session)
        log.info("AgentFlow %s started (env=%s, live_mode_available=%s)",
                 __version__, settings.app_env, settings.live_mode_available)
        yield
        await dispose_engine()

    app = FastAPI(title="AgentFlow API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    api = APIRouter(prefix="/api/v1")
    api.include_router(system.router)
    api.include_router(auth.router)
    api.include_router(agents.router)
    app.include_router(api)
    return app


app = create_app()
