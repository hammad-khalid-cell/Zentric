from langgraph.graph import StateGraph, END
from app.graph.state import AgentState
from app.graph.nodes import (
    intent_understanding_node,
    data_retrieval_node,
    decision_making_node,
    action_execution_node,
    response_generation_node,
    memory_load_node,
    memory_save_node
)


def route_after_intent(state: AgentState) -> str:
    if state.get("intent") in {"track_order", "delay_complaint"}:
        return "data_retrieval"
    return "response_generation"


def route_after_retrieval(state: AgentState) -> str:
    if state.get("clarification_needed"):
        return "response_generation"
    if state.get("intent") == "delay_complaint":
        return "decision_making"
    return "response_generation"

def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("memory_load", memory_load_node)
    graph.add_node("intent_understanding", intent_understanding_node)
    graph.add_node("data_retrieval", data_retrieval_node)
    graph.add_node("decision_making", decision_making_node)
    graph.add_node("action_execution", action_execution_node)
    graph.add_node("response_generation", response_generation_node)
    graph.add_node("memory_save", memory_save_node)

    graph.set_entry_point("memory_load")

    graph.add_edge("memory_load", "intent_understanding")

    graph.add_conditional_edges(
        "intent_understanding",
        route_after_intent,
        {
            "data_retrieval": "data_retrieval",
            "response_generation": "response_generation",
        },
    )

    graph.add_conditional_edges(
        "data_retrieval",
        route_after_retrieval,
        {
            "decision_making": "decision_making",
            "response_generation": "response_generation",
        },
    )

    graph.add_edge("decision_making", "action_execution")
    graph.add_edge("action_execution", "response_generation")
    graph.add_edge("response_generation", "memory_save")
    graph.add_edge("memory_save", END)

    return graph.compile()


compiled_graph = build_graph()