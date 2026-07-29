from langgraph.graph import StateGraph, END
from app.core.handoffs import STATUS_CLAIMED
from app.graph.state import AgentState
from app.graph.nodes import (
    intent_understanding_node,
    interpret_reply_node,
    data_retrieval_node,
    decision_making_node,
    action_execution_node,
    response_generation_node,
    memory_load_node,
    memory_save_node,
    faq_node,
    escalation_check_node,
    handoff_hold_node,
)


def route_after_memory_load(state: AgentState) -> str:
    """Human ownership short-circuits everything (Phase 5).

    Checked here — the first thing after state is loaded — rather than in the route
    handler, for three reasons: `/test/message` and the webhook both enter through the
    graph so one check covers both; it runs before intent classification, so a
    human-owned conversation costs no LLM call at all; and business routing stays in
    the one place business routing is decided.

    Only a **claimed** handoff suppresses the bot. An `open` one means staff have been
    alerted but nobody has picked it up yet — going silent then would leave the
    customer with nothing while they wait, which is worse than the bot helping.
    """
    handoff = state.get("human_handoff")
    if handoff and handoff.get("status") == STATUS_CLAIMED:
        return "handoff_hold"
    return "intent_understanding"


def route_after_intent(state: AgentState) -> str:
    # Proactive loop takes precedence: if this customer has an open pending action,
    # their reply is a continuation of that intervention — route it into the corrective
    # path (interpret -> deterministic corrective decision) rather than reclassifying.
    if state.get("pending_action"):
        return "interpret_reply"
    if state.get("intent") in {"track_order", "delay_complaint"}:
        return "data_retrieval"
    if state.get("intent") == "faq":
        return "faq_node"
    return "response_generation"  # unclear intent


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
    graph.add_node("escalation_check", escalation_check_node)
    graph.add_node("interpret_reply", interpret_reply_node)
    graph.add_node("data_retrieval", data_retrieval_node)
    graph.add_node("decision_making", decision_making_node)
    graph.add_node("action_execution", action_execution_node)
    graph.add_node("response_generation", response_generation_node)
    graph.add_node("faq_node", faq_node)
    graph.add_node("handoff_hold", handoff_hold_node)
    graph.add_node("memory_save", memory_save_node)

    graph.set_entry_point("memory_load")

    # Before anything else: if a human owns this thread, hold — no classification, no
    # LLM call, no auto-reply.
    graph.add_conditional_edges(
        "memory_load",
        route_after_memory_load,
        {
            "handoff_hold": "handoff_hold",
            "intent_understanding": "intent_understanding",
        },
    )
    graph.add_edge("handoff_hold", "memory_save")
    graph.add_edge("intent_understanding", "escalation_check")

    
    graph.add_conditional_edges(
        "escalation_check",
        route_after_intent,
        {
            "interpret_reply": "interpret_reply",
            "data_retrieval": "data_retrieval",
            "faq_node": "faq_node",
            "response_generation": "response_generation",
        },
    )

    # Interpreted corrective reply flows into the deterministic corrective decision,
    # then the shared action_execution -> response_generation tail.
    graph.add_edge("interpret_reply", "decision_making")

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
    graph.add_edge("faq_node", "memory_save")
    graph.add_edge("memory_save", END)

    return graph.compile()

compiled_graph = build_graph()