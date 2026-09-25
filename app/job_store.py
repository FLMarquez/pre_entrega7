"""Persistencia del estado de los jobs (trabajos) en Redis.

Esto es independiente del checkpointer de LangGraph (que persiste el estado
interno del grafo): aquí guardamos el "sobre" que ve la API -> status, input,
resultado final, error, y el payload de aprobación pendiente si corresponde.
"""
import json
import time
from typing import Any, Optional

import redis.asyncio as redis

JOB_KEY_PREFIX = "job:"

PENDING = "PENDING"
RUNNING = "RUNNING"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
DONE = "DONE"
REJECTED = "REJECTED"
FAILED = "FAILED"


class JobStore:
    def __init__(self, redis_url: str, ttl_seconds: int = 60 * 60 * 24) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    async def create(self, job_id: str, input_text: str) -> None:
        now = time.time()
        await self._write(
            job_id,
            {
                "job_id": job_id,
                "status": PENDING,
                "input": input_text,
                "result": None,
                "error": None,
                "awaiting_approval": None,
                "created_at": now,
                "updated_at": now,
            },
        )

    async def update(self, job_id: str, **fields: Any) -> None:
        data = await self.get(job_id)
        if data is None:
            return
        data.update(fields)
        data["updated_at"] = time.time()
        await self._write(job_id, data)

    async def get(self, job_id: str) -> Optional[dict]:
        raw = await self._redis.get(f"{JOB_KEY_PREFIX}{job_id}")
        return json.loads(raw) if raw else None

    async def _write(self, job_id: str, data: dict) -> None:
        await self._redis.set(f"{JOB_KEY_PREFIX}{job_id}", json.dumps(data), ex=self._ttl)

    async def close(self) -> None:
        await self._redis.aclose()
