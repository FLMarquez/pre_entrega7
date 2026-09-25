"""Inicialización de la capa de observabilidad (LangSmith o Arize Phoenix).

Se elige con la variable de entorno OBSERVABILITY_BACKEND=langsmith|phoenix|none.
Ambos backends instrumentan automáticamente cada llamada a LangChain/LangGraph
(incluyendo los nodos del grafo y las llamadas a LLM de cada agente), por lo que
cada nodo aparece como un span/trace propio en el dashboard.
"""
import logging
import os

logger = logging.getLogger(__name__)


def init_observability() -> str:
    backend = os.getenv("OBSERVABILITY_BACKEND", "langsmith").lower()

    if backend == "langsmith":
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault(
            "LANGSMITH_PROJECT", os.getenv("LANGSMITH_PROJECT", "pre-entrega7-multiagent")
        )
        if not os.getenv("LANGSMITH_API_KEY"):
            logger.warning(
                "OBSERVABILITY_BACKEND=langsmith pero falta LANGSMITH_API_KEY: "
                "las trazas no se van a poder enviar al dashboard."
            )
        logger.info("Observabilidad activa: LangSmith (proyecto=%s)", os.environ["LANGSMITH_PROJECT"])

    elif backend == "phoenix":
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
        project_name = os.getenv("PHOENIX_PROJECT_NAME", "pre-entrega7-multiagent")

        tracer_provider = register(project_name=project_name, endpoint=endpoint)
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

        logger.info(
            "Observabilidad activa: Arize Phoenix (endpoint=%s, proyecto=%s)", endpoint, project_name
        )

    else:
        logger.info("Observabilidad deshabilitada (OBSERVABILITY_BACKEND=%s)", backend)

    return backend
