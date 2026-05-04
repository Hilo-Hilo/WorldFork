"""FastAPI router for the Prometheus /metrics endpoint."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest

router = APIRouter()

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4"


@router.get("/metrics", tags=["observability"], include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus text-format metrics.

    Compatible with Prometheus scrapers and Grafana.
    """
    data = generate_latest()
    return Response(content=data, media_type=_PROMETHEUS_CONTENT_TYPE)
