"""Municipal Knowledge Agent: SOP / policy retrieval with citations, tenant filtering and expiry.

Retrieved text is UNTRUSTED content. It is returned as quoted data with citations; it can never change
instructions, roles, tool scopes or policies (see copilot prompt and tests)."""
from __future__ import annotations

from collections import Counter

from . import db
from .models.router import ModelRouter


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embed_pending_chunks(router: ModelRouter | None = None, *, force: bool = False) -> dict:
    """(Re-)embed knowledge chunks. Records model/dim/version per row; a model change re-embeds everything
    rather than mixing vector spaces."""
    router = router or ModelRouter()
    rows = db.all_rows("select id::text, coalesce(heading,'') || '. ' || content as text, embed_model from knowledge_chunks")
    probe = router.embed("probe")
    target = probe.model
    done = 0
    for r in rows:
        if not force and r["embed_model"] == target:
            continue
        e = router.embed(f"title: {r['text'][:60]} | text: {r['text']}", prefer_model=target)
        db.one("""update knowledge_chunks set embedding=%s::extensions.vector, embed_model=%s, embed_dim=%s, embed_version=%s
                  where id=%s returning 1""", (_vec(e.vector), e.model, e.dim, e.version, r["id"]))
        done += 1
    return {"model": target, "embedded": done, "total": len(rows)}


def space_for(jurisdiction_id: str) -> str | None:
    rows = db.all_rows("""select embed_model from knowledge_chunks where embed_model is not null
                          and (jurisdiction_id is null or jurisdiction_id = %s)""", (jurisdiction_id,))
    c = Counter(r["embed_model"] for r in rows)
    return c.most_common(1)[0][0] if c else None


def retrieve(jurisdiction_id: str, query: str, *, k: int = 4, router: ModelRouter | None = None) -> list[dict]:
    router = router or ModelRouter()
    space = space_for(jurisdiction_id)
    vec = None
    if space:
        e = router.embed(f"task: search result | query: {query}", prefer_model=space, jurisdiction_id=jurisdiction_id)
        vec = _vec(e.vector) if e.model == space else None
    rows = db.all_rows("""select chunk_id::text, title, heading, content, source_url, published_on, text_rank, vector_rank,
                                 text_score, vector_score, fused
                            from lk.hybrid_search_knowledge(%s, %s, %s::extensions.vector, %s)""",
                       (jurisdiction_id, query, vec, k))
    return [{**r, "citation": f"{r['title']} › {r['heading']}", "untrusted": True} for r in rows]
