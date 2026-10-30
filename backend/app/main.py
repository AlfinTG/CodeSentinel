"""FastAPI application entrypoint for CodeSentinel."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.lifecycle import initialize_application


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager ensuring secrets and DB are initialized on startup."""
    initialize_application()
    yield


app = FastAPI(
    title="CodeSentinel API",
    description="Evidence-led regression diagnosis & repair verification",
    version="0.1.0",
    lifespan=lifespan,
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
    """Attach correlation request_id and baseline security headers."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


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


# Mount API v1
app.include_router(api_v1_router)


@app.get("/")
async def root():
    return {
        "name": "CodeSentinel API",
        "version": "0.1.0",
        "documentation": "/docs",
    }
