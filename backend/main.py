"""
RevPilot — Adaptive AI Revenue Recovery Controller
===================================================

FastAPI application entrypoint and dashboard server.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from backend.api.routes import router

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    yield


app = FastAPI(
    title="RevPilot",
    description="Adaptive AI revenue recovery controller for failed payments",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/")
async def root(request: Request):
    """Root endpoint: returns dashboard HTML if browser, or JSON API info if client request."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        html_file = FRONTEND_DIR / "index.html"
        if html_file.exists():
            return FileResponse(html_file)
    return {"app": "RevPilot", "version": "0.1.0", "status": "running"}


@app.get("/dashboard")
async def dashboard():
    """Explicit dashboard endpoint returning the HTML control room interface."""
    html_file = FRONTEND_DIR / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return JSONResponse(status_code=404, content={"error": "Dashboard template not found"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Serve the RevPilot binary ICO brand favicon."""
    ico_file = FRONTEND_DIR / "favicon.ico"
    if ico_file.exists():
        return FileResponse(ico_file, media_type="image/x-icon")
    svg_file = FRONTEND_DIR / "favicon.svg"
    if svg_file.exists():
        return FileResponse(svg_file, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"error": "Favicon not found"})


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    """Serve the RevPilot SVG brand favicon."""
    svg_file = FRONTEND_DIR / "favicon.svg"
    if svg_file.exists():
        return FileResponse(svg_file, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"error": "Favicon not found"})

