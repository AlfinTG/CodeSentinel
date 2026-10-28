"""API v1 master router."""

from fastapi import APIRouter

from app.api.v1.endpoints import analysis, auth, health, patches, projects, runs, webhooks

api_v1_router = APIRouter(prefix="/api/v1")

# Mount endpoints
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(runs.router)
api_v1_router.include_router(webhooks.router)
api_v1_router.include_router(analysis.router)
api_v1_router.include_router(patches.router)
