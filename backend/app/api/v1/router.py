"""API v1 router — registers all endpoint modules under /api/v1."""

from fastapi import APIRouter

from app.api.v1.endpoints import health, platform

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(platform.router, prefix="/platform", tags=["platform"])
