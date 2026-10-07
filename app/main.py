"""FastAPI application: middleware, templates and router registration."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse

from modules.invoices.config import Environment, assert_production_ready, get_settings
from modules.invoices.web import admin, member, tasks
from modules.invoices.web.static import CachedStaticFiles
from modules.invoices.web.templates import templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Everything the browser needs is served from this origin. HTMX is vendored
# into modules/invoices/static rather than pulled from a CDN, so the policy can
# stay strict and there is no third party in the request path for a page
# showing fees.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Opener-Policy": "same-origin",
    # This app renders fees and account numbers. None of it should be cached by
    # an intermediary or left in the browser's back-forward cache.
    "Cache-Control": "no-store",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Fail fast on a half-configured production start."""
    settings = get_settings()
    if settings.environment is Environment.PRODUCTION:
        assert_production_ready(settings)
    logger.info("starting in %s", settings.environment.value)
    yield


app = FastAPI(
    title="Invoice Portal",
    docs_url=None,  # No public schema: every route is behind IAP and personal.
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.mount(
    "/static", CachedStaticFiles(directory="modules/invoices/static"), name="static"
)
# Admin first: every one of its routes carries the finance check (R-ACC-04).
app.include_router(admin.router)
app.include_router(member.router)
# Outside IAP by necessity: Cloud Scheduler is not a browser. Every route
# verifies its own OIDC token instead (R-JOB-*).
app.include_router(tasks.router)


# Paths the tasks-only service will answer. Everything else is a 404 there.
TASKS_ONLY_PREFIXES = ("/tasks/", "/healthz")


@app.middleware("http")
async def tasks_only_gate(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Serve nothing but the scheduled endpoints when this is the tasks service.

    Defence in depth, not the actual control: Cloud Run IAM already restricts
    that service to the scheduler's identity. But it runs the same image as
    the portal, so if the IAM were ever loosened the wizard and the admin
    screens would be sitting there without IAP. This makes that impossible.
    """
    settings = get_settings()
    if settings.tasks_only and not request.url.path.startswith(TASKS_ONLY_PREFIXES):
        return Response(status_code=404)
    return await call_next(request)


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """R-SEC-11: secure headers on every response, including error pages."""
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness for Cloud Run. Says nothing about who is logged in."""
    return {"status": "ok"}


@app.exception_handler(500)
async def internal_error(request: Request, exc: Exception) -> HTMLResponse:
    """Show a plain page and keep the detail in the log.

    An exception message here could carry a fee or an account number, and
    R-SEC-05 forbids putting either in front of a user.
    """
    logger.exception("unhandled error on %s", request.url.path, exc_info=exc)
    return templates.TemplateResponse(
        request=request, name="error.html", status_code=500
    )
