"""Regenerate docs/graphs/*.md from the compiled LangGraph graphs (so diagrams never drift from code)."""
from pathlib import Path

from lanekeeper.graphs import copilot, fleet, glasses, ticket

OUT = Path(__file__).resolve().parents[1] / "docs" / "graphs"
for name, build in [("ticket", ticket.build), ("fleet", fleet.build), ("glasses", glasses.build), ("copilot", copilot.build)]:
    m = build().get_graph().draw_mermaid()
    OUT.joinpath(f"{name}.md").write_text(f"# `{name}` graph (generated)\n\nGenerated from the compiled LangGraph with "
                                          f"`scripts/export_graphs.py`. Dotted edges are conditional.\n\n```mermaid\n{m}```\n")
    print("wrote", name)
