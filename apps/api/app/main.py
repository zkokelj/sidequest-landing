import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    admin,
    auth,
    chat,
    checkout,
    conferences,
    curate,
    events,
    health,
    me,
    webhooks,
)
from app.scraper.scheduler import Scheduler, build_scheduler
from app.services.admin_repo import get_events_admin_repo
from app.services.scheduler_settings_repo import get_scheduler_settings_repo
from app.services.scrape_sources_repo import get_scrape_sources_repo
from app.services.suggestions_repo import get_suggestions_repo

logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler: Scheduler = build_scheduler(
        settings,
        sources_repo=get_scrape_sources_repo(),
        events_repo=get_events_admin_repo(),
        settings_repo=get_scheduler_settings_repo(),
        suggestions_repo=get_suggestions_repo(),
    )
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="sidequest-api", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(conferences.router)
app.include_router(curate.router)
app.include_router(auth.router)
app.include_router(checkout.router)
app.include_router(me.router)
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(webhooks.router)
