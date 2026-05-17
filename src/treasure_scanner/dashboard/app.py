from __future__ import annotations

from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..db import Database

log = structlog.get_logger(__name__)

BASE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE / "templates"))


def build_app(cfg: Config, db: Database) -> FastAPI:
    app = FastAPI(title="Treasure Scanner", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "index.html",
            {
                "stats_24h": db.stats(24),
                "stats_7d": db.stats(24 * 7),
                "alerts": db.recent_alerts(limit=10),
                "watcher_count": len(cfg.watchers),
                "adhoc_count": len(db.list_adhoc_watches()),
            },
        )

    @app.get("/listings", response_class=HTMLResponse)
    async def listings(request: Request, min_score: int = 0, limit: int = 100):
        items = db.recent_listings(limit=limit,
                                   min_score=min_score if min_score else None)
        return TEMPLATES.TemplateResponse(
            request, "listings.html",
            {"items": items, "min_score": min_score, "limit": limit},
        )

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts(request: Request, limit: int = 100):
        return TEMPLATES.TemplateResponse(
            request, "alerts.html",
            {"alerts": db.recent_alerts(limit=limit), "limit": limit},
        )

    @app.get("/watchers", response_class=HTMLResponse)
    async def watchers(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "watchers.html",
            {
                "watchers": cfg.watchers,
                "adhoc": db.list_adhoc_watches_full(),
            },
        )

    @app.post("/watchers/adhoc")
    async def add_adhoc_watcher(query: str = Form(...)):
        q = query.strip()
        if not q:
            raise HTTPException(400, "empty query")
        db.add_adhoc_watch(q, None)
        return RedirectResponse("/watchers", status_code=303)

    @app.post("/watchers/adhoc/{watch_id}/delete")
    async def delete_adhoc_watcher(watch_id: int):
        db.remove_adhoc_watch(watch_id)
        return RedirectResponse("/watchers", status_code=303)

    @app.get("/mutes", response_class=HTMLResponse)
    async def mutes(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "mutes.html", {"mutes": db.list_mutes()},
        )

    @app.post("/mutes")
    async def add_mute(pattern: str = Form(...)):
        p = pattern.strip()
        if not p:
            raise HTTPException(400, "empty pattern")
        db.add_mute(p, "text")
        return RedirectResponse("/mutes", status_code=303)

    @app.post("/mutes/delete")
    async def delete_mute(pattern: str = Form(...)):
        db.remove_mute(pattern)
        return RedirectResponse("/mutes", status_code=303)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app


async def run_dashboard(cfg: Config, db: Database) -> None:
    app = build_app(cfg, db)
    config = uvicorn.Config(
        app, host=cfg.dashboard_host, port=cfg.dashboard_port,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    log.info("dashboard_starting", host=cfg.dashboard_host, port=cfg.dashboard_port)
    await server.serve()
