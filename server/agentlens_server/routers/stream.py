from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..config import API_KEY
from ..live import broker, event_stream, live_store

router = APIRouter(tags=["live"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # tell nginx not to buffer the stream
}


@router.post("/ingest/event", status_code=202)
async def ingest_event(
    event: dict,
    authorization: str | None = Header(default=None),
):
    """
    Receive one span lifecycle event from a streaming SDK. Cheap on purpose:
    fold into memory, fan out, return. Persistence happens when the run
    finishes and posts its complete payload to /api/ingest/run.
    """
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    if event.get("type") not in ("run_start", "span_start", "span_end", "run_end"):
        raise HTTPException(status_code=422, detail=f"Unknown event type: {event.get('type')!r}")

    await live_store.apply(event)
    await broker.publish(event)
    return {"ok": True}


@router.get("/stream")
async def stream(run_id: str | None = Query(default=None, description="Only this run's events")):
    """Server-sent events for live runs. Point an EventSource here."""
    return StreamingResponse(event_stream(run_id), media_type="text/event-stream", headers=SSE_HEADERS)


# Namespaced under /live rather than /runs/live: the latter collides with
# /runs/{run_id} and only works if routers are registered in the right
# order, which is a fragile thing to depend on.
@router.get("/live/runs")
async def list_live():
    """Runs currently executing — including ones that may never finish."""
    return {"runs": live_store.snapshot(), "subscribers": broker.subscriber_count}
