"""Typed, least-privilege tool registry for the operator copilot and agents.

Every tool: strict Pydantic args (extra fields forbidden), a required permission, a side-effect class and a
plain description of its effect. The jurisdiction is NEVER an argument: it is bound from the verified principal
context, so a model cannot ask for another city's data. There is no SQL tool and no generic DB toolkit."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..auth import Principal
from ..policy.engine import SideEffect, enforce


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ToolCtx:
    principal: Principal
    jurisdiction_id: str
    run_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[Args]
    permission: str
    side_effect: SideEffect
    fn: Callable[[ToolCtx, Any], Any]
    effect: str   # precise description of side effects, shown to operators


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, *, permission: str, side_effect: SideEffect, effect: str):
    def deco(fn):
        args_model = fn.__annotations__["args"]
        REGISTRY[name] = ToolSpec(name, (fn.__doc__ or "").strip(), args_model, permission, side_effect, fn, effect)
        return fn
    return deco


class ToolDenied(Exception):
    pass


def invoke(name: str, ctx: ToolCtx, raw_args: dict) -> Any:
    """Single entry point: validate args, enforce policy (and audit writes), run, and record the invocation."""
    spec = REGISTRY.get(name)
    if not spec:
        raise ToolDenied(f"unknown tool {name}")
    args = spec.args.model_validate(raw_args or {})
    with db.tx() as cur:
        try:
            enforce(cur, ctx.principal, ctx.jurisdiction_id, spec.permission, spec.side_effect, target=name, trace_id=ctx.trace_id)
        except Exception as e:
            raise ToolDenied(str(e)) from e
    return spec.fn(ctx, args)


def as_langchain_tools(ctx: ToolCtx, allowed: set[str] | None = None) -> list:
    """Expose registry tools to a LangChain chat model (bind_tools). Each call still goes through invoke()."""
    from langchain_core.tools import StructuredTool
    out = []
    for spec in REGISTRY.values():
        if allowed and spec.name not in allowed:
            continue
        out.append(StructuredTool.from_function(
            func=lambda _spec=spec, **kw: invoke(_spec.name, ctx, kw), name=spec.name,
            description=f"{spec.description} Side effects: {spec.effect}", args_schema=spec.args))
    return out


# Shared argument types
Hazard = Literal["glass", "debris", "branch", "litter", "pothole", "water", "obstruction", "markings", "construction", "other"]
TicketStatus = Literal["new", "needs_evidence", "triaged", "assigned", "scheduled", "in_progress", "remediated_unverified",
                       "verified_closed", "reopened", "escalated", "dismissed"]


class TicketRef(Args):
    ticket: str = Field(description="Ticket number like LK-104 or a ticket UUID", max_length=64)
