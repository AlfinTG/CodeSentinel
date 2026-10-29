"""API v1 master router."""

from fastapi import APIRouter

<<<<<<< HEAD
from app.api.v1.endpoints import analysis, auth, health, patches, projects, runs, webhooks
=======
from app.api.v1.endpoints import analysis, auth, health, jobs, projects, runs, webhooks
>>>>>>> 10d1e22 (feat(phase-2): add distributed queue and worker architecture)

api_v1_router = APIRouter(prefix="/api/v1")

# Mount endpoints
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(runs.router)
api_v1_router.include_router(webhooks.router)
api_v1_router.include_router(analysis.router)
<<<<<<< HEAD
api_v1_router.include_router(patches.router)
=======
api_v1_router.include_router(jobs.router)
>>>>>>> 10d1e22 (feat(phase-2): add distributed queue and worker architecture)
