"""Human-in-the-loop: nodo de aprobación humana + endpoint para resolverla.

El nodo `human_approval_node` llama a `interrupt(...)`, lo que congela la
ejecución del grafo en ese punto (el estado queda persistido en Redis vía el
checkpointer). La ejecución solo continúa cuando alguien llama al endpoint
POST /tasks/{job_id}/approve, que reanuda el grafo con `Command(resume=...)`.
"""
import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import interrupt
from pydantic import BaseModel

from app import job_store

router = APIRouter()


class ApprovalRequest(BaseModel):
    approved: bool
    comment: Optional[str] = None


def human_approval_node(state: dict) -> dict:
    decision = interrupt(
        {
            "message": "Se requiere aprobación humana para continuar: la tarea fue "
            "clasificada como crítica.",
            "reason": state.get("critical_reason"),
            "plan": state.get("plan"),
        }
    )
    if isinstance(decision, dict):
        return {
            "approved": bool(decision.get("approved")),
            "approval_comment": decision.get("comment"),
        }
    return {"approved": bool(decision), "approval_comment": None}


def route_after_approval(state: dict) -> Literal["executor", "rejected"]:
    return "executor" if state.get("approved") else "rejected"


def rejected_node(state: dict) -> dict:
    return {
        "tool_result": "Acción rechazada por el aprobador humano.",
        "final_report": (
            f"La tarea fue rechazada por un humano. "
            f"Motivo: {state.get('approval_comment') or 'sin comentario'}"
        ),
        "status": "rejected",
    }


@router.post("/tasks/{job_id}/approve")
async def approve_task(job_id: str, payload: ApprovalRequest, request: Request):
    from app.worker import resume_job  # import diferido: evita import circular con graph.py

    store = request.app.state.job_store
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job["status"] != job_store.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"El job no está esperando aprobación (status actual={job['status']})",
        )

    task = asyncio.create_task(
        resume_job(request.app.state.graph, store, job_id, payload.approved, payload.comment)
    )
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)

    return {"job_id": job_id, "status": job_store.RUNNING}
