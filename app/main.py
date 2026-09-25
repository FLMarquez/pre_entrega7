"""API FastAPI asíncrona que expone el orquestador multi-agente.

- POST /tasks           -> encola la tarea y devuelve un job_id sin bloquear.
- GET  /tasks/{job_id}  -> consulta el estado (PENDING/RUNNING/AWAITING_APPROVAL/DONE/REJECTED/FAILED).
- POST /tasks/{job_id}/approve -> resuelve una pausa human-in-the-loop (definido en app/hitl.py).
- GET  /health          -> healthcheck simple.
"""
import asyncio
import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

from app.graph import build_graph  # noqa: E402
from app.hitl import router as hitl_router  # noqa: E402
from app.job_store import JobStore  # noqa: E402
from app.observability import init_observability  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.worker import run_job  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_observability()

    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    stack = AsyncExitStack()
    checkpointer = await stack.enter_async_context(
        AsyncRedisSaver.from_conn_string(settings.redis_url)
    )
    await checkpointer.asetup()

    app.state.graph = build_graph(checkpointer)
    app.state.job_store = JobStore(settings.redis_url, ttl_seconds=settings.job_ttl_seconds)
    app.state.background_tasks = set()

    logger.info("API lista. REDIS_URL=%s", settings.redis_url)
    try:
        yield
    finally:
        await app.state.job_store.close()
        await stack.aclose()


app = FastAPI(title="Pre-Entrega 7 - API multi-agente", lifespan=lifespan)
app.include_router(hitl_router)


class TaskRequest(BaseModel):
    input: str


def _track(app: FastAPI, task: "asyncio.Task") -> None:
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


@app.post("/tasks", status_code=202)
async def create_task(payload: TaskRequest):
    job_id = uuid.uuid4().hex
    await app.state.job_store.create(job_id, payload.input)

    task = asyncio.create_task(
        run_job(app.state.graph, app.state.job_store, job_id, payload.input)
    )
    _track(app, task)

    return {"job_id": job_id, "status": "PENDING"}


@app.get("/tasks/{job_id}")
async def get_task(job_id: str):
    job = await app.state.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    return job


@app.get("/health")
async def health():
    return {"status": "ok"}
