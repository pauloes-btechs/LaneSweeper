"""`lanekeeper` command line: serve | worker | seed-embeddings | models-verify | demo | token."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .storage import REPO_ROOT


def _serve(a) -> None:
    import uvicorn
    uvicorn.run("lanekeeper.api.app:app", host=a.host, port=a.port, reload=False)


def _worker(a) -> None:
    from .jobs import enqueue, run_once
    if a.housekeeping:
        stamp = time.strftime("%Y%m%d%H")
        for k in ("send_outbox", "expire_approvals", "retention_sweep"):
            enqueue(k, {}, dedupe_key=f"{k}:{stamp}")
    rounds = 1 if a.once else a.rounds
    for _ in range(rounds):
        r = run_once(max_jobs=a.max_jobs)
        print(json.dumps(r))
        if r["leased"] == 0 and not a.once:
            time.sleep(a.sleep)


def _seed_embeddings(a) -> None:
    from .knowledge import embed_pending_chunks
    print(json.dumps(embed_pending_chunks(force=a.force)))


def _models_verify(a) -> None:
    """Probe each configured route with ONE minimal call (tiny text / 64x64 image). Respects the kill switch and budgets."""
    from .config import get_settings
    from .models.budget import billable_enabled
    from .models.google import GoogleClient, Image, ModelError
    from .models.router import routes
    s = get_settings()
    report: dict = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ai_mode": s.ai_mode, "has_key": bool(s.google_api_key),
                    "billable_enabled": None, "listed_models": None, "routes": {}}
    if not s.google_api_key:
        report["note"] = "No GOOGLE_API_KEY set: every route uses its offline substitute. Nothing was called."
        _write_verify(report)
        return
    report["billable_enabled"] = billable_enabled()
    if not report["billable_enabled"] and not a.force:
        report["note"] = "Billable calls are disabled (env or feature flag). Re-run with --force to probe anyway."
        _write_verify(report)
        return
    c = GoogleClient(s.google_api_key)
    try:
        listed = c.list_models()
        report["listed_models"] = sorted(m for m in listed if m.startswith(("gemini", "embedding")))
    except ModelError as e:
        report["list_error"] = f"{e.kind}: {e}"
        listed = []
    tiny = Image(_tiny_png(), "image/png")
    for name, cfg in routes().items():
        for model in cfg["candidates"]:
            t0 = time.monotonic()
            res = {"api": cfg["api"], "listed": model in listed if listed else None}
            try:
                if cfg["api"] == "decisions":
                    out = c.decide(model, "A test frame of an empty bike lane.", {"q": {"type": "noul", "instructions": "Is there broken glass?"}},
                                   [tiny], timeout=30)
                    res["sample"] = out["answers"]["q"]
                    res["usage"] = out.get("usage")
                elif cfg["api"] == "interactions":
                    text, usage = c.interact(model, "Return JSON {\"verdict\":\"clear\"} if no glass is visible.", [tiny],
                                             thinking_level="low", timeout=60)
                    res["sample"], res["usage"] = text[:160], usage
                elif cfg["api"] == "generate_content":
                    text, usage = c.generate(model, "Reply with the single word: ok", timeout=60)
                    res["sample"], res["usage"] = text[:40], usage
                elif cfg["api"] == "embed_content":
                    v = c.embed(model, "glass in bike lane", dimensions=int(cfg.get("dimensions", 768)))
                    res["sample"] = f"{len(v)} dims"
                res["callable"] = True
            except ModelError as e:
                res["callable"] = False
                res["error"] = f"{e.kind} {e.status or ''}: {str(e)[:200]}"
            res["latency_ms"] = int((time.monotonic() - t0) * 1000)
            report["routes"].setdefault(name, {})[model] = res
    _write_verify(report)


def _tiny_png() -> bytes:
    import io

    from PIL import Image as PI
    b = io.BytesIO()
    PI.new("RGB", (64, 64), (40, 110, 70)).save(b, format="PNG")
    return b.getvalue()


def _write_verify(report: dict) -> None:
    out = REPO_ROOT / ".lk" / "models-verify.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out.relative_to(REPO_ROOT)}", file=sys.stderr)


def _token(a) -> None:
    from .api.demo import SEEDED
    from .auth import mint_dev_token
    print(mint_dev_token(SEEDED[a.who][0]))


def _demo(a) -> None:
    from .demo import run_demo
    run_demo(fast=a.fast, out=Path(a.out) if a.out else None)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lanekeeper", description="LaneKeeper OS orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(fn=_serve)
    w = sub.add_parser("worker")
    w.add_argument("--once", action="store_true")
    w.add_argument("--rounds", type=int, default=10)
    w.add_argument("--max-jobs", type=int, default=10)
    w.add_argument("--sleep", type=float, default=2.0)
    w.add_argument("--housekeeping", action="store_true", help="enqueue outbox/approval-expiry/retention jobs first")
    w.set_defaults(fn=_worker)
    e = sub.add_parser("seed-embeddings")
    e.add_argument("--force", action="store_true")
    e.set_defaults(fn=_seed_embeddings)
    m = sub.add_parser("models-verify", help="probe configured Google models with minimal calls")
    m.add_argument("--force", action="store_true", help="probe even if billable calls are disabled")
    m.set_defaults(fn=_models_verify)
    t = sub.add_parser("token", help="local demo token for a seeded identity")
    t.add_argument("who")
    t.set_defaults(fn=_token)
    d = sub.add_parser("demo", help="scripted A→D→E→F→report walkthrough")
    d.add_argument("--fast", action="store_true")
    d.add_argument("--out")
    d.set_defaults(fn=_demo)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
