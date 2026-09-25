"""Orquestador multi-agente (LangGraph) con checkpointer de Redis.

Flujo:
    planner -> classifier -> [human_approval] -> executor -> reporter
                    (si no es crítico, classifier salta directo a executor)

- planner: agente que arma un plan breve para la tarea del usuario.
- classifier: detecta si la tarea es "crítica" (efectos secundarios o costo alto).
- human_approval (app/hitl.py): si es crítica, pausa el grafo con interrupt()
  hasta que un humano apruebe o rechace vía POST /tasks/{id}/approve.
- executor: agente que "ejecuta" (simulado) el plan/herramienta.
- reporter: agente que redacta el reporte final para el usuario.

Cada llamada a LLM está decorada con @traceable y además queda instrumentada
automáticamente por el backend de observabilidad activo (LangSmith o Phoenix),
así cada nodo aparece como un span propio en el dashboard de trazas.
"""
import os
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from app.hitl import human_approval_node, rejected_node, route_after_approval

CRITICAL_KEYWORDS = [
    "eliminar",
    "borrar",
    "delete",
    "drop table",
    "transferir",
    "pagar",
    "pago",
    "reembolso",
    "refund",
    "producción",
    "prod deploy",
    "comprar",
    "purchase",
    "enviar dinero",
    "cancelar suscripción",
]


class AgentState(TypedDict, total=False):
    input: str
    plan: str
    is_critical: bool
    critical_reason: str
    approved: Optional[bool]
    approval_comment: Optional[str]
    tool_result: str
    final_report: str
    status: str


def get_llm():
    provider = os.getenv("MODEL_PROVIDER", "anthropic").lower()
    model_name = os.getenv("MODEL_NAME", "claude-sonnet-5")

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name, temperature=0)

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model_name, temperature=0)


@traceable(name="planner_llm_call")
async def _call_planner(user_input: str) -> str:
    llm = get_llm()
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": "Sos un agente planificador. Da un plan breve (2 a 4 pasos) "
                "para resolver la tarea del usuario.",
            },
            {"role": "user", "content": user_input},
        ]
    )
    return response.content


async def planner_node(state: AgentState) -> dict:
    plan = await _call_planner(state["input"])
    return {"plan": plan}


def classifier_node(state: AgentState) -> dict:
    text = f"{state['input']} {state.get('plan', '')}".lower()
    hit = next((kw for kw in CRITICAL_KEYWORDS if kw in text), None)
    if hit:
        return {
            "is_critical": True,
            "critical_reason": f'Se detectó la palabra clave crítica "{hit}" en la tarea.',
        }
    return {"is_critical": False, "critical_reason": ""}


def route_after_classifier(state: AgentState) -> Literal["human_approval", "executor"]:
    return "human_approval" if state.get("is_critical") else "executor"


@traceable(name="executor_llm_call")
async def _call_executor(user_input: str, plan: str) -> str:
    llm = get_llm()
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": "Sos un agente ejecutor. Ejecutá (de forma simulada) el plan "
                "y devolvé el resultado concreto de la acción/herramienta usada.",
            },
            {"role": "user", "content": f"Tarea: {user_input}\nPlan: {plan}"},
        ]
    )
    return response.content


async def executor_node(state: AgentState) -> dict:
    result = await _call_executor(state["input"], state.get("plan", ""))
    return {"tool_result": result, "status": "executed"}


@traceable(name="reporter_llm_call")
async def _call_reporter(user_input: str, tool_result: str) -> str:
    llm = get_llm()
    response = await llm.ainvoke(
        [
            {
                "role": "system",
                "content": "Sos un agente que redacta un reporte final breve y claro "
                "para el usuario, a partir del resultado obtenido.",
            },
            {"role": "user", "content": f"Tarea: {user_input}\nResultado: {tool_result}"},
        ]
    )
    return response.content


async def reporter_node(state: AgentState) -> dict:
    report = await _call_reporter(state["input"], state.get("tool_result", ""))
    return {"final_report": report, "status": "done"}


def build_graph(checkpointer):
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("classifier", classifier_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("executor", executor_node)
    graph.add_node("rejected", rejected_node)
    graph.add_node("reporter", reporter_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "classifier")
    graph.add_conditional_edges(
        "classifier",
        route_after_classifier,
        {"human_approval": "human_approval", "executor": "executor"},
    )
    graph.add_conditional_edges(
        "human_approval",
        route_after_approval,
        {"executor": "executor", "rejected": "rejected"},
    )
    graph.add_edge("executor", "reporter")
    graph.add_edge("rejected", END)
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer)
