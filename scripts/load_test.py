"""Prueba de carga: dispara 5 peticiones concurrentes contra la API y mide
latencia end-to-end desde el cliente.

Dos de las tareas incluyen palabras clave "críticas" a propósito, para que
disparen el flujo human-in-the-loop; el script las aprueba automáticamente
para poder medir la corrida completa de punta a punta.

El costo por ejecución y la latencia p95 "oficiales" (las que hay que
capturar en /screenshots) se leen del dashboard de LangSmith/Phoenix, no de
este script -- acá solo se generan las 5 trazas y se imprime una latencia
aproximada del lado del cliente como referencia.

Uso:
    python scripts/load_test.py
"""
import asyncio
import statistics
import time

import httpx

BASE_URL = "http://localhost:8000"

TASKS = [
    "Resumir las últimas noticias sobre inteligencia artificial en 3 puntos.",
    "Calcular el total de una compra de 3 productos a $10 cada uno.",
    "Eliminar la base de datos de producción del cliente Acme.",  # crítica -> HITL
    "Generar un resumen ejecutivo de un artículo técnico sobre RAG.",
    "Transferir $500 a la cuenta bancaria del proveedor.",  # crítica -> HITL
]

POLL_INTERVAL_S = 0.5
IN_PROGRESS_STATUSES = {"PENDING", "RUNNING", "AWAITING_APPROVAL"}


async def run_one(client: httpx.AsyncClient, text: str) -> dict:
    start = time.perf_counter()
    resp = await client.post(f"{BASE_URL}/tasks", json={"input": text})
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    status = "PENDING"
    while status in IN_PROGRESS_STATUSES:
        await asyncio.sleep(POLL_INTERVAL_S)
        job = (await client.get(f"{BASE_URL}/tasks/{job_id}")).json()
        status = job["status"]

        if status == "AWAITING_APPROVAL":
            await client.post(
                f"{BASE_URL}/tasks/{job_id}/approve",
                json={"approved": True, "comment": "auto-aprobado por load_test.py"},
            )
            status = "RUNNING"

    elapsed = time.perf_counter() - start
    return {"job_id": job_id, "input": text, "status": status, "latency_s": elapsed}


async def main() -> None:
    async with httpx.AsyncClient(timeout=120) as client:
        results = await asyncio.gather(*(run_one(client, t) for t in TASKS))

    latencies = sorted(r["latency_s"] for r in results)
    p95 = statistics.quantiles(latencies, n=100)[94] if len(latencies) > 1 else latencies[0]

    print("\n=== Resultados de la prueba de carga (5 peticiones concurrentes) ===")
    for r in results:
        print(
            f"- {r['job_id']}: status={r['status']} "
            f"latencia={r['latency_s']:.2f}s input=\"{r['input'][:60]}\""
        )

    print(f"\nLatencia p95 aproximada (medida en el cliente): {p95:.2f}s")
    print(
        "Para el costo por ejecución y la latencia p95 'oficiales' de esta corrida, "
        "revisá el dashboard de LangSmith/Phoenix y capturalo en /screenshots."
    )


if __name__ == "__main__":
    asyncio.run(main())
