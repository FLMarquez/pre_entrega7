# Pre-entrega 7 — API de producción y monitoreo activo

API REST asíncrona (FastAPI) que expone un orquestador multi-agente (LangGraph)
con persistencia de estado en Redis, observabilidad activa (LangSmith o Arize
Phoenix) y un flujo de aprobación humana (HITL) para tareas críticas.

## Arquitectura

```
POST /tasks  ──► encola job en Redis (PENDING) ──► asyncio.create_task (worker)
                                                        │
                                                        ▼
                                      planner ─► classifier ─┬─► executor ─► reporter ─► DONE
                                                              │                 (Redis)
                                                              └─► human_approval
                                                                   (interrupt(): pausa
                                                                    el grafo, status =
                                                                    AWAITING_APPROVAL)
                                                                        │
                                             POST /tasks/{id}/approve ──┘
                                             (Command(resume=...): reanuda desde
                                              el checkpoint guardado en Redis)
```

- **planner**: agente LLM que arma un plan breve para la tarea.
- **classifier**: detecta si la tarea es "crítica" (palabras clave asociadas a
  efectos secundarios o costo alto: eliminar datos, pagos, transferencias, etc.).
- **human_approval** ([app/hitl.py](app/hitl.py)): si es crítica, llama a
  `interrupt(...)` y el grafo queda pausado — persistido en Redis vía el
  checkpointer — hasta que llega la aprobación externa.
- **executor**: agente LLM que "ejecuta" (simulado) el plan/herramienta.
- **reporter**: agente LLM que redacta el reporte final.

Cada nodo que llama a un LLM está decorado con `@traceable` y además queda
instrumentado automáticamente por el backend de observabilidad activo, así
cada uno aparece como un span propio en el dashboard.

## Estructura del repo

```
app/
├── main.py            # FastAPI: POST /tasks, GET /tasks/{id}, GET /health
├── graph.py            # orquestador multi-agente + build_graph(checkpointer)
├── worker.py            # corre el grafo en background y actualiza Redis
├── observability.py      # init de LangSmith / Arize Phoenix
├── hitl.py                # nodo de aprobación humana + POST /tasks/{id}/approve
├── job_store.py             # persistencia del estado del job en Redis
└── settings.py                # configuración vía variables de entorno
scripts/load_test.py    # dispara 5 peticiones concurrentes
screenshots/             # capturas del dashboard de observabilidad
```

## Requisitos

- Python 3.12+
- Redis con módulos RedisJSON + RediSearch (Redis Stack) o Redis ≥ 8 —
  **un Redis "vanilla" (`redis:alpine`) no alcanza**, porque el checkpointer
  `langgraph-checkpoint-redis` los necesita para indexar checkpoints.
- Una API key de Anthropic (o OpenAI) para las llamadas de los agentes.
- Cuenta de [LangSmith](https://smith.langchain.com) **o** Docker para correr
  [Arize Phoenix](https://arize.com/docs/phoenix) localmente.
- Docker + Docker Compose (opcional, pero recomendado).

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
cp .env.example .env
# completar .env con tus API keys
```

## 1. Levantar Redis

Con Docker (recomendado, ya incluye los módulos necesarios):

```bash
docker compose up -d redis
```

Sin Docker: instalar [Redis Stack](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/)
localmente y correr `redis-stack-server`.

## 2. Configurar observabilidad

En `.env`, elegir uno de los dos backends:

**Opción A — LangSmith** (más simple, solo variables de entorno):

```
OBSERVABILITY_BACKEND=langsmith
LANGSMITH_API_KEY=<tu api key>
LANGSMITH_PROJECT=pre-entrega7-multiagent
```

Dashboard: https://smith.langchain.com → proyecto `pre-entrega7-multiagent`.

**Opción B — Arize Phoenix** (self-hosted):

```bash
docker compose --profile phoenix up -d phoenix
```

```
OBSERVABILITY_BACKEND=phoenix
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces
```

Dashboard: http://localhost:6006

## 3. Levantar la API

```bash
uvicorn app.main:app --reload --port 8000
```

O con Docker Compose (levanta Redis + API juntos):

```bash
docker compose up --build
```

## 4. Probar manualmente

```bash
# Tarea normal
curl -X POST http://localhost:8000/tasks -H "Content-Type: application/json" \
  -d "{\"input\": \"Resumir un articulo sobre RAG\"}"
# -> {"job_id": "...", "status": "PENDING"}

curl http://localhost:8000/tasks/<job_id>

# Tarea crítica (dispara HITL)
curl -X POST http://localhost:8000/tasks -H "Content-Type: application/json" \
  -d "{\"input\": \"Eliminar la base de datos de produccion\"}"

curl http://localhost:8000/tasks/<job_id>
# -> {"status": "AWAITING_APPROVAL", "awaiting_approval": {"message": "...", "reason": "...", "plan": "..."}}

curl -X POST http://localhost:8000/tasks/<job_id>/approve -H "Content-Type: application/json" \
  -d "{\"approved\": true, \"comment\": \"aprobado manualmente\"}"

curl http://localhost:8000/tasks/<job_id>
# -> {"status": "DONE", "result": {...}}
```

## 5. Prueba de carga (5 peticiones concurrentes)

Con la API y Redis corriendo:

```bash
python scripts/load_test.py
```

El script:

- Dispara 5 tareas en paralelo (dos de ellas contienen palabras clave
  "críticas" y disparan el flujo HITL, que el script auto-aprueba para poder
  medir la corrida completa).
- Hace polling de cada `job_id` hasta que termina.
- Imprime una latencia aproximada por tarea y un p95 calculado del lado del
  cliente (solo de referencia).

## 6. Lectura del dashboard y capturas

Con las 5 peticiones ya lanzadas, entrar al dashboard (LangSmith o Phoenix) y
revisar:

- Cómo se distribuyen las trazas de las 5 ejecuciones concurrentes.
- Dónde se concentra la latencia (qué nodo tarda más).
- Qué nodo consume más tokens (generalmente `executor` o `reporter`).
- El **costo por ejecución** (lo calcula la plataforma a partir de tokens de
  entrada/salida) y la **latencia p95** de la corrida.

Guardar esas capturas en [screenshots/](screenshots/) según se detalla en
[screenshots/README.md](screenshots/README.md).

## Manejo de errores en background

Si el agente falla en cualquier nodo (excepción de LLM, timeout, etc.), el
worker ([app/worker.py](app/worker.py)) captura la excepción y actualiza el
job en Redis a `FAILED` con el mensaje de error — el cliente que está haciendo
polling en `GET /tasks/{id}` ve el estado `FAILED` en vez de quedarse
esperando indefinidamente.

## Por qué no se bloquea el event loop

- Los endpoints solo escriben en Redis (`redis.asyncio`, no bloqueante) y
  lanzan un `asyncio.create_task(...)` — no esperan a que el agente termine.
- Todas las llamadas a LLM dentro del grafo usan `await llm.ainvoke(...)`
  (cliente async nativo de LangChain), nunca `.invoke()` síncrono.
- El checkpointer de Redis usa `AsyncRedisSaver` (`aget`/`aput` async).

## Checklist de la rúbrica

- [x] Endpoint asíncrono que encola y devuelve `job_id` sin bloquear.
- [x] Estado del job persistido en Redis, con transición a `FAILED` ante excepción.
- [x] Checkpointer de LangGraph con `AsyncRedisSaver` (Redis).
- [x] Observabilidad activa (LangSmith o Phoenix) instrumentando cada nodo.
- [x] Nodo human-in-the-loop (`interrupt` + `POST /tasks/{id}/approve`).
- [ ] Capturas de costo y p95 de la corrida de 5 peticiones — **a completar
      por el alumno en `/screenshots` luego de ejecutar `scripts/load_test.py`
      y revisar el dashboard**, ya que requiere una API key real y una cuenta
      de LangSmith/Phoenix.
