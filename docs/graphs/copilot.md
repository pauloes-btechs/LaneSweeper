# `copilot` graph (generated)

Generated from the compiled LangGraph with `scripts/export_graphs.py`. Dotted edges are conditional.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	understand_request(understand_request)
	plan_tool_calls(plan_tool_calls)
	execute_tools(execute_tools)
	compose_answer(compose_answer)
	__end__([<p>__end__</p>]):::last
	__start__ --> understand_request;
	execute_tools --> compose_answer;
	plan_tool_calls -.-> compose_answer;
	plan_tool_calls -.-> execute_tools;
	understand_request --> plan_tool_calls;
	compose_answer --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
