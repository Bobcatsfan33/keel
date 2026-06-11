import pytest
from keel.kir.schema import Graph, Node, Edge, NodeType


def test_rejects_dangling_edge_naming_node():
    with pytest.raises(ValueError, match="unknown node 'ghost'"):
        Graph(graph_id="g", nodes=[Node(id="a", type=NodeType.LLM_STEP)],
              edges=[Edge.model_validate({"from": "a", "to": "ghost"})])


def test_rejects_cycle_naming_node():
    with pytest.raises(ValueError, match="cycle through node"):
        Graph(graph_id="g",
              nodes=[Node(id="a", type=NodeType.LLM_STEP), Node(id="b", type=NodeType.LLM_STEP)],
              edges=[Edge.model_validate({"from": "a", "to": "b"}),
                     Edge.model_validate({"from": "b", "to": "a"})])


def test_tool_step_requires_tool():
    with pytest.raises(ValueError, match="tool_step requires"):
        Node(id="t", type=NodeType.TOOL_STEP)
