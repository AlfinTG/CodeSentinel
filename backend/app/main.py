"""FastAPI application entrypoint for CodeSentinel."""

import time
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.logging import (
    clear_log_context,
    configure_logging,
    sanitize_correlation_id,
    set_log_context,
)
from app.core.telemetry import generate_metrics_payload, record_http_request

# Initialize structured logging
configure_logging(log_level=settings.LOG_LEVEL)

app = FastAPI(
    title="CodeSentinel API",
    description="Evidence-led regression diagnosis & repair verification",
    version="0.1.0",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id_and_security_headers(request: Request, call_next):
    """Attach correlation request_id, record latency metrics, and add baseline security headers."""
    raw_request_id = request.headers.get("X-Request-ID")
    request_id = sanitize_correlation_id(raw_request_id, default=str(uuid.uuid4()))

    raw_correlation_id = request.headers.get("X-Correlation-ID")
    correlation_id = sanitize_correlation_id(raw_correlation_id, default=request_id)

    request.state.request_id = request_id
    request.state.correlation_id = correlation_id
    set_log_context(request_id=request_id, correlation_id=correlation_id)

    start_time = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    finally:
        duration = time.perf_counter() - start_time
        path = request.url.path
        # Do not recursively instrument the /metrics endpoint itself to prevent metric noise
        if path != "/metrics":
            status_code = response.status_code if response is not None else 500
            # Bound metric cardinality by using the matched route path template instead of arbitrary client URL
            route = request.scope.get("route")
            if route and hasattr(route, "path"):
                raw_route = route.path
                endpoint = (
                    f"/api/v1{raw_route}"
                    if not raw_route.startswith("/api/v1") and request.url.path.startswith("/api/v1")
                    else raw_route
                )
            else:
                endpoint = "unmatched"
            record_http_request(request.method, endpoint, status_code, duration)
        clear_log_context()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure HTTP exceptions adhere to the canonical error shape."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
    elif isinstance(exc.detail, dict):
        content = {"error": {**exc.detail, "request_id": request_id}}
    else:
        content = {
            "error": {
                "code": "error",
                "message": str(exc.detail),
                "request_id": request_id,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Global exception handler returning the canonical safe error shape."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred. Request logged.",
                "request_id": request_id,
            }
        },
    )


# Mount Prometheus metrics endpoint
@app.get("/metrics", include_in_schema=False)
async def get_metrics():
    """Prometheus metrics endpoint collecting request latency, counters, and telemetry."""
    data, content_type = generate_metrics_payload()
    return Response(content=data, media_type=content_type)


# Mount API v1
app.include_router(api_v1_router)


@app.get("/")
async def root():
    return {
        "name": "CodeSentinel API",
        "version": "0.1.0",
        "documentation": "/docs",
    }
