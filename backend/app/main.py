"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app import __version__
from app.api.deps import limiter
from app.api.routes import agents, approvals, auth, insights, reports, system, tasks
from app.config.settings import Settings, get_settings
from app.database.migrations import migrate_database
from app.database.session import dispose_engine, init_engine, session_factory
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_providers, build_search_provider
from app.llm.registry import ModelRegistry
from app.services.agent_config_service import seed_agent_configs
from app.services.auth_service import ensure_admin_user
from app.services.events import EventBus
from app.services.execution_manager import ExecutionManager
from app.tools.search import SearchProvider
from app.utils.errors import install_error_handlers
from app.utils.logging import configure_logging

log = logging.getLogger("agentflow")


def create_app(settings: Settings | None = None, *,
               live_llms: dict[str, LLMProvider] | None = None,
               live_search: SearchProvider | None = None) -> FastAPI:
    """`live_llms` / `live_search` let tests inject fakes; production builds them from env."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        configure_logging(settings.log_level)
        secret = settings.jwt_secret.get_secret_value()
        # Also reject the public .env.example placeholder: a copied example file must not
        # leave sessions signed with a well-known secret.
        if secret.startswith(("dev-insecure", "change-me")):
            if settings.app_env == "production":
                raise RuntimeError("JWT_SECRET must be set in production.")
            log.warning("Using the built-in development JWT_SECRET; set JWT_SECRET in .env.")
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        init_engine(settings.resolved_database_url)
        migration = await migrate_database(settings)  # backs up SQLite first if anything changes
        if migration.action in ("adopted", "upgraded"):
            log.warning("Schema migrated to %s (backup: %s).", migration.to_revision, migration.backup_path)
        async with session_factory()() as session:
            await ensure_admin_user(session, settings)
            await seed_agent_configs(session)

        llms = live_llms if live_llms is not None else build_llm_providers(settings)
        search = live_search if live_search is not None else build_search_provider(settings)
        if not hasattr(app.state, "model_registry"):
            app.state.model_registry = ModelRegistry(llms)
        async with AsyncExitStack() as stack:
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(
                str(settings.resolved_data_dir / "checkpoints.db")))
            app.state.bus = EventBus()
            app.state.manager = ExecutionManager(
                settings, app.state.bus, checkpointer=saver, registry=app.state.model_registry,
                live_llms=llms, live_search=search)
            interrupted = await app.state.manager.recover_after_restart()
            await app.state.manager.expire_stale_approvals()
            if interrupted:
                log.warning("Marked %d in-flight task(s) as interrupted after restart.", interrupted)
            log.info("AgentFlow %s started (env=%s, live_mode_available=%s)",
                     __version__, settings.app_env, settings.live_mode_available)
            yield
            await app.state.manager.shutdown()
        await dispose_engine()

    app = FastAPI(title="AgentFlow API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    limiter.enabled = settings.rate_limit_enabled
    limiter.reset()
    app.state.limiter = limiter
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    api = APIRouter(prefix="/api/v1")
    api.include_router(system.router)
    api.include_router(auth.router)
    api.include_router(agents.router)
    api.include_router(tasks.router)
    api.include_router(approvals.router)
    api.include_router(reports.router)
    api.include_router(insights.router)
    app.include_router(api)
    return app


app = create_app()
