"""Ejecuta el grafo en segundo plano (patrón "worker" async simple, sin bloquear
el event loop de FastAPI) y mantiene el estado del job actualizado en Redis.

Si el agente falla, la excepción se captura acá y el job pasa a FAILED con el
mensaje de error, en vez de dejar al cliente esperando para siempre.
"""
import logging
from typing import Optional

from langgraph.types import Command

from app import job_store
from app.job_store import JobStore

logger = logging.getLogger(__name__)


async def run_job(graph, store: JobStore, job_id: str, user_input: str) -> None:
    config = {"configurable": {"thread_id": job_id}}
    await store.update(job_id, status=job_store.RUNNING)
    try:
        result = await graph.ainvoke({"input": user_input}, config=config)
        await _handle_result(store, job_id, result)
    except Exception as exc:  # noqa: BLE001 - se persiste cualquier falla del agente
        logger.exception("El job %s falló", job_id)
        await store.update(job_id, status=job_store.FAILED, error=str(exc))


async def resume_job(
    graph, store: JobStore, job_id: str, approved: bool, comment: Optional[str]
) -> None:
    config = {"configurable": {"thread_id": job_id}}
    await store.update(job_id, status=job_store.RUNNING, awaiting_approval=None)
    try:
        result = await graph.ainvoke(
            Command(resume={"approved": approved, "comment": comment}), config=config
        )
        await _handle_result(store, job_id, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("El job %s falló al reanudar", job_id)
        await store.update(job_id, status=job_store.FAILED, error=str(exc))


async def _handle_result(store: JobStore, job_id: str, result: dict) -> None:
    interrupts = result.get("__interrupt__")
    if interrupts:
        raw = interrupts[0]
        payload = raw.value if hasattr(raw, "value") else raw
        await store.update(job_id, status=job_store.AWAITING_APPROVAL, awaiting_approval=payload)
        return

    final_status = job_store.REJECTED if result.get("status") == "rejected" else job_store.DONE
    await store.update(
        job_id,
        status=final_status,
        result={
            "plan": result.get("plan"),
            "is_critical": result.get("is_critical", False),
            "tool_result": result.get("tool_result"),
            "final_report": result.get("final_report"),
        },
    )
