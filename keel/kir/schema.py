from __future__ import annotations
from enum import Enum
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class NodeType(str, Enum):
    LLM_STEP = "llm_step"
    TOOL_STEP = "tool_step"
    ROUTER = "router"
    MAP = "map"
    REDUCE = "reduce"
    HUMAN_GATE = "human_gate"
    SUBGRAPH = "subgraph"
    CREW = "crew"


class Retry(BaseModel):
    model_config = ConfigDict(frozen=True)
    max: int = 0
    backoff: Literal["none", "linear", "exp"] = "exp"
    on: list[str] = Field(default_factory=lambda: ["rate_limit", "transient"])


class NodeBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_tokens: Optional[int] = None
    max_usd: Optional[float] = None
    max_steps: Optional[int] = None


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)
    from_: str = Field(..., alias="from")
    to: str
    when: Optional[str] = None


class Region(BaseModel):
    """The bounded contract around a crew's autonomy. Inside, agents self-organize;
    the boundary is rigid."""

    model_config = ConfigDict(frozen=True)
    max_steps: int
    max_tokens: int
    allowed_tools: list[str] = Field(default_factory=list)
    output_schema: str
    nodes: list["Node"] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class Node(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    type: NodeType
    model_policy: str = "default"
    input_schema: Optional[str] = None
    output_schema: Optional[str] = None
    tool: Optional[str] = None
    retry: Retry = Field(default_factory=Retry)
    budget: NodeBudget = Field(default_factory=NodeBudget)
    region: Optional[Region] = None
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Node":
        if self.type in (NodeType.CREW, NodeType.SUBGRAPH) and self.region is None:
            raise ValueError(f"node {self.id}: {self.type.value} requires a 'region'")
        if self.type == NodeType.TOOL_STEP and not self.tool:
            raise ValueError(f"node {self.id}: tool_step requires 'tool'")
        return self


class Graph(BaseModel):
    model_config = ConfigDict(frozen=True)
    kir_version: str = "1.0"
    graph_id: str
    nodes: list[Node]
    edges: list[Edge]

    @model_validator(mode="after")
    def _validate_graph(self) -> "Graph":
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node ids")
        idset = set(ids)
        for e in self.edges:
            if e.from_ not in idset:
                raise ValueError(f"edge from unknown node '{e.from_}'")
            if e.to not in idset:
                raise ValueError(f"edge to unknown node '{e.to}'")
        self._assert_acyclic(ids, self.edges)
        return self

    @staticmethod
    def _assert_acyclic(ids: list[str], edges: list[Edge]) -> None:
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for e in edges:
            adj[e.from_].append(e.to)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {i: WHITE for i in ids}

        def visit(n: str) -> None:
            color[n] = GRAY
            for m in adj[n]:
                if color[m] == GRAY:
                    raise ValueError(f"cycle through node '{m}' (use a crew region for loops)")
                if color[m] == WHITE:
                    visit(m)
            color[n] = BLACK

        for i in ids:
            if color[i] == WHITE:
                visit(i)


Node.model_rebuild()
Region.model_rebuild()
