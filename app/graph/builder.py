"""LangGraph builder for the customer service orchestration."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.container import Container
from app.graph.state import AgentState


def route_branch(state: AgentState) -> str:
    return state.get("intent", "clarification")


def build_graph(container: Container):
    """Build and compile the customer service state graph.

    Nodes read ``session``, ``chat``, and ``providers`` from the runtime state so
    tests can inject fakes. ``container`` carries the production providers for
    the chat service to inject at invocation time.
    """
    graph = StateGraph(AgentState)

    graph.add_node("route", nodes.route_node)
    graph.add_node("greeting", nodes.greeting_node)
    graph.add_node("knowledge", nodes.knowledge_node)
    graph.add_node("catalog", nodes.catalog_node)
    graph.add_node("order", nodes.order_node)
    graph.add_node("after_sale", nodes.after_sale_node)
    graph.add_node("chitchat", nodes.chitchat_node)
    graph.add_node("deescalation", nodes.deescalation_node)
    graph.add_node("clarification", nodes.clarification_node)
    graph.add_node("handoff", nodes.handoff_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        route_branch,
        {
            "greeting": "greeting",
            "product_knowledge": "knowledge",
            "policy_knowledge": "knowledge",
            "price_stock": "catalog",
            "order": "order",
            "after_sale": "after_sale",
            "chit_chat": "chitchat",
            "deescalation": "deescalation",
            "clarification": "clarification",
            "handoff": "handoff",
        },
    )
    for node in (
        "greeting",
        "knowledge",
        "catalog",
        "order",
        "after_sale",
        "chitchat",
        "deescalation",
        "clarification",
        "handoff",
    ):
        graph.add_edge(node, END)

    return graph.compile()
