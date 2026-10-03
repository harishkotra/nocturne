"""Nocturne server: chat, sleeptime consolidation, wake quiz, diff and restore.

Endpoints (all under /api):
    GET  /health            backend + provider + store summary
    GET  /memory            blocks, items, staleness, snapshots, forgetting curve
    POST /chat              streamed reply, then traceability-gated fact extraction
    POST /sleep             streamed per-pass consolidation progress
    POST /wake              streamed recall quiz progress + final report
    GET  /diff              per-item diff between two snapshots, with reasons
    POST /restore           roll the live view back to a snapshot
    POST /test-connection   real provider probe (status + body, key redacted)
    POST /reset             wipe transcript + active memory (history snapshots are kept)
"""

from __future__ import annotations

import json
import os
import queue
import threading
from typing import Any, Generator, Iterator

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from agent import inference as inf  # noqa: E402
from agent.memory import consolidate as cons  # noqa: E402
from agent.memory.store import DEFAULT_BLOCKS, MemoryStore  # noqa: E402
from agent.recall import run_quiz  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = FastAPI(title="Nocturne", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store = MemoryStore(os.environ.get("NOCTURNE_DB", os.path.join(ROOT, "nocturne.db")))
_backend_lock = threading.Lock()


# --------------------------------------------------------------------- helpers
def _provider_from(body: dict[str, Any]) -> inf.ProviderConfig:
    """Client settings override env; the API key is used in-memory and never echoed back."""
    provider = body.get("provider") or {}
    base_url = (provider.get("baseUrl") or os.environ.get("OPENAI_BASE_URL", "")).strip()
    model = (provider.get("model") or os.environ.get("MODEL", "")).strip()
    api_key = (provider.get("apiKey") or os.environ.get("OPENAI_API_KEY", "")).strip()
    return inf.ProviderConfig(base_url=base_url, model=model, api_key=api_key, timeout=float(provider.get("timeout", 240)))


def _policy_from(body: dict[str, Any]) -> cons.Policy:
    return cons.Policy.from_dict(body.get("policy"))


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _memory_context(store: MemoryStore) -> str:
    rows = []
    for block in store.list_blocks():
        items = store.get_block(block["name"]).items
        rows.append({"block": block["name"], "items": [{"itemId": i["itemId"], "text": i["text"], "category": i["category"]} for i in items]})
    return json.dumps({"memoryBlocks": rows}, ensure_ascii=False)


def _persona_prompt(store: MemoryStore) -> str:
    return (
        "You are Nocturne, a chat agent that consolidates its own memory while it sleeps. "
        "Answer from the memory blocks and the conversation only. Be brief and concrete. "
        "If a fact is not in your memory blocks, say that you do not have it rather than guessing.\n\n"
        + _memory_context(store)
    )


def _curve(store: MemoryStore) -> list[dict[str, Any]]:
    return [
        {"snapshotId": s["snapshotId"], "seq": s["seq"], "label": s["label"], "createdAt": s["createdAt"], "activeCount": s["activeCount"]}
        for s in store.snapshots()
    ]


def _memory_payload(store: MemoryStore) -> dict[str, Any]:
    blocks = []
    for block in store.list_blocks():
        detail = store.get_block(block["name"])
        blocks.append(
            {
                "name": detail.name,
                "description": detail.description,
                "count": detail.count,
                "items": detail.items,
            }
        )
    inactive = [i for i in store.all_items() if not i["active"]]
    return {
        "blocks": blocks,
        "activeCount": sum(b["count"] for b in blocks),
        "retiredCount": len(inactive),
        "snapshots": store.snapshots(),
        "curve": _curve(store),
        "messages": store.messages(),
        "backend": os.environ.get("LETTA_MODE", "sqlite"),
        "blocksExpected": [name for name, _ in DEFAULT_BLOCKS],
    }


def _diff(store: MemoryStore, from_id: str, to_id: str) -> dict[str, Any]:
    a_items = {i["itemId"]: i for i in store.snapshot_items(from_id)}
    b_items = {i["itemId"]: i for i in store.snapshot_items(to_id)}
    a_all = {i["itemId"]: i for i in store.snapshot_items(from_id, active_only=False)}
    b_all = {i["itemId"]: i for i in store.snapshot_items(to_id, active_only=False)}

    snaps = {s["snapshotId"]: s["seq"] for s in store.snapshots()}
    lo, hi = sorted((snaps.get(from_id, -1), snaps.get(to_id, -1)))
    reason_for: dict[str, dict[str, Any]] = {}
    for m in store.mutations():
        seq = snaps.get(m["snapshotId"])
        if seq is None or not (lo < seq <= hi):
            continue
        if m["accepted"]:
            reason_for[m["itemId"]] = m

    kept: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    forgotten: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    for item_id, before in a_items.items():
        after = b_items.get(item_id)
        if after is None:
            mutation = reason_for.get(item_id) or {}
            forgotten.append(
                {
                    "itemId": item_id,
                    "text": before["text"],
                    "block": before["block"],
                    "category": before["category"],
                    "kind": mutation.get("kind") or "retired",
                    "pass": mutation.get("pass"),
                    "policy": mutation.get("policy"),
                    "reason": mutation.get("reason") or "removed from the active view between these versions",
                }
            )
            continue
        if after["text"] != before["text"] or after["block"] != before["block"]:
            mutation = reason_for.get(item_id) or {}
            refs = mutation.get("refs") or {}
            merged = after["text"] != before["text"]
            block_moved = after["block"] != before["block"]
            kind = "merged" if merged else ("promoted" if mutation.get("kind") == "promote" else "moved")
            changed.append(
                {
                    "itemId": item_id,
                    "before": before,
                    "after": after,
                    "kind": kind,
                    "pass": mutation.get("pass"),
                    "reason": mutation.get("reason") or "changed between these versions",
                    "fromIds": refs.get("fromIds") or [],
                    "fromBlock": refs.get("fromBlock") or before["block"],
                    "toBlock": refs.get("toBlock") or after["block"],
                    "sources": [
                        a_all[fid]["text"] for fid in (refs.get("fromIds") or []) if fid in a_all
                    ],
                }
            )
        else:
            kept.append(after)
    for item_id, after in b_items.items():
        if item_id not in a_items:
            added.append(after)
    rejected = [
        m
        for m in store.mutations()
        if not m["accepted"] and lo < snaps.get(m["snapshotId"], -1) <= hi
    ]
    return {
        "from": from_id,
        "to": to_id,
        "fromMeta": next((s for s in store.snapshots() if s["snapshotId"] == from_id), None),
        "toMeta": next((s for s in store.snapshots() if s["snapshotId"] == to_id), None),
        "kept": kept,
        "changed": changed,
        "forgotten": forgotten,
        "added": added,
        "rejected": rejected,
        "counts": {
            "kept": len(kept),
            "changed": len(changed),
            "forgotten": len(forgotten),
            "added": len(added),
            "rejected": len(rejected),
        },
    }


# --------------------------------------------------------------------- routes
@app.get("/api/health")
def health() -> dict[str, Any]:
    snap = _store.latest_snapshot()
    return {
        "ok": True,
        "backend": os.environ.get("LETTA_MODE", "sqlite"),
        "lettaBaseUrl": os.environ.get("LETTA_BASE_URL", ""),
        "db": _store.path,
        "activeCount": len(_store.active_items()),
        "snapshotCount": len(_store.snapshots()),
        "latestSnapshot": snap,
        "provider": {
            "baseUrl": os.environ.get("OPENAI_BASE_URL", ""),
            "model": os.environ.get("MODEL", ""),
            "hasKey": bool(os.environ.get("OPENAI_API_KEY")),
        },
        "presets": inf.PRESETS,
        "defaults": {
            "policy": cons.Policy.from_dict(
                {
                    "maxActiveFacts": os.environ.get("MAX_ACTIVE_FACTS"),
                    "staleAfterDays": os.environ.get("STALE_AFTER_DAYS"),
                    "protectedCategories": os.environ.get("PROTECTED_CATEGORIES"),
                }
                if os.environ.get("MAX_ACTIVE_FACTS")
                else {}
            ).to_dict(),
            "settingsKey": "nocturne.settings.v1",
        },
    }


@app.get("/api/memory")
def memory() -> dict[str, Any]:
    return _memory_payload(_store)


@app.post("/api/diff")
async def diff(request: Request) -> Any:
    body = await request.json()
    snaps = _store.snapshots()
    from_id = body.get("from") or (snaps[-2]["snapshotId"] if len(snaps) > 1 else None)
    to_id = body.get("to") or (snaps[-1]["snapshotId"] if snaps else None)
    if not from_id or not to_id:
        return JSONResponse({"error": "not enough snapshots to diff"}, status_code=409)
    return _diff(_store, from_id, to_id)


@app.post("/api/restore")
async def restore(request: Request) -> Any:
    body = await request.json()
    snapshot_id = body.get("snapshotId")
    if not snapshot_id:
        return JSONResponse({"error": "snapshotId is required"}, status_code=400)
    with _backend_lock:
        try:
            result = _store.restore(snapshot_id)
        except KeyError as exc:
            return JSONResponse({"error": f"unknown snapshot {exc}"}, status_code=404)
        result["memory"] = _memory_payload(_store)
        return result


@app.post("/api/reset")
async def reset(request: Request) -> Any:
    """`hard: true` wipes every version too (development convenience); the default retires
    facts through the versioned path so the history stays restorable."""
    body = await request.json() if await request.body() else {}
    with _backend_lock:
        if body.get("hard"):
            _store.reset()
            outcome = {"wipe": "hard"}
        else:
            outcome = _store.soft_wipe()
        return {"ok": True, **outcome, "memory": _memory_payload(_store)}


@app.post("/api/test-connection")
async def test_connection(request: Request) -> Any:
    body = await request.json()
    cfg = _provider_from(body)
    if not cfg.base_url or not cfg.model:
        return JSONResponse({"error": "baseUrl and model are required"}, status_code=400)
    return inf.test_connection(cfg)


@app.post("/api/chat")
async def chat(request: Request) -> Any:
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)
    cfg = _provider_from(body)
    with _backend_lock:
        turn = _store.add_message("user", message)
        history = _store.messages()
        system = _persona_prompt(_store)

    def event_stream() -> Iterator[str]:
        collected: list[str] = []
        try:
            for delta in inf.chat_stream(cfg, [{"role": "system", "content": system}] + [
                {"role": m["role"], "content": m["content"]} for m in history if m["turn"] != turn
            ] + [{"role": "user", "content": message}]):
                collected.append(delta)
                yield _sse("delta", {"text": delta})
        except inf.InferenceError as exc:
            yield _sse("error", exc.to_dict())
            return
        reply = "".join(collected).strip()
        if not reply:
            try:
                reply = inf.chat(cfg, [{"role": "system", "content": system}] + [
                    {"role": m["role"], "content": m["content"]} for m in history if m["turn"] != turn
                ] + [{"role": "user", "content": message}])
                yield _sse("delta", {"text": reply})
            except inf.InferenceError as exc:
                yield _sse("error", exc.to_dict())
                return
        with _backend_lock:
            _store.add_message("assistant", reply)
            _store.snapshot("chat")
        yield _sse("reply", {"text": reply, "turn": turn})
        try:
            with _backend_lock:
                extraction = cons.extract_and_append(_store, cfg)
        except Exception as exc:  # noqa: BLE001 - surface anything the provider threw
            extraction = {"proposed": 0, "accepted": [], "rejected": [], "error": {"error": str(exc)}}
        yield _sse(
            "facts",
            {
                "proposed": extraction.get("proposed", 0),
                "accepted": extraction.get("accepted", []),
                "rejected": extraction.get("rejected", []),
                "error": extraction.get("error"),
            },
        )
        yield _sse("memory", {"activeCount": len(_store.active_items())})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/sleep")
async def sleep(request: Request) -> Any:
    body = await request.json()
    cfg = _provider_from(body)
    policy = _policy_from(body)
    dry_run = bool(body.get("dryRun"))
    passes = tuple(body.get("passes") or cons.PASS_NAMES)
    events: "queue.Queue[dict[str, Any]]" = queue.Queue()
    result: dict[str, Any] = {}

    def on_progress(name: str, payload: dict[str, Any]) -> None:
        events.put({"event": "pass", "name": name, "payload": payload})

    def worker() -> None:
        try:
            with _backend_lock:
                result["data"] = cons.run_consolidation(
                    _store, cfg, policy, dry_run=dry_run, passes=passes, on_progress=on_progress
                )
        except Exception as exc:  # noqa: BLE001 - real error, real message
            result["error"] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            events.put({"event": "finished"})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream() -> Generator[str, None, None]:
        yield _sse("start", {"dryRun": dry_run, "policy": policy.to_dict(), "passes": list(passes)})
        while True:
            try:
                item = events.get(timeout=15.0)
            except queue.Empty:
                if not thread.is_alive():
                    break
                # a pass can run for minutes without producing a byte; keep the proxy awake
                yield ": keepalive\n\n"
                continue
            if item["event"] == "finished":
                break
            payload = item["payload"]
            yield _sse(
                f"pass:{item['name']}:{payload.get('phase')}",
                {"pass": item["name"], **payload},
            )
        thread.join(timeout=5)
        if "error" in result:
            yield _sse("error", result["error"])
        else:
            data = result.get("data", {})
            yield _sse("result", {**data, "memory": _memory_payload(_store)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/wake")
async def wake(request: Request) -> Any:
    body = await request.json()
    cfg = _provider_from(body)
    policy = _policy_from(body)
    count = int(body.get("count") or 5)
    forgotten_item_id = body.get("forgottenItemId")
    events: "queue.Queue[dict[str, Any]]" = queue.Queue()
    result: dict[str, Any] = {}

    def on_progress(kind: str, payload: dict[str, Any]) -> None:
        events.put({"kind": kind, "payload": payload})

    def worker() -> None:
        try:
            with _backend_lock:
                report = run_quiz(
                    _store,
                    cfg,
                    policy,
                    forgotten_item_id=forgotten_item_id,
                    count=count,
                    report_path=os.path.join(ROOT, "recall-report.json"),
                    on_progress=on_progress,
                )
            result["data"] = report
        except Exception as exc:  # noqa: BLE001
            result["error"] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            events.put({"kind": "finished"})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream() -> Generator[str, None, None]:
        yield _sse("start", {"count": count, "policy": policy.to_dict()})
        while True:
            try:
                item = events.get(timeout=15.0)
            except queue.Empty:
                if not thread.is_alive():
                    break
                yield ": keepalive\n\n"
                continue
            if item["kind"] == "finished":
                break
            yield _sse(item["kind"], item["payload"])
        thread.join(timeout=5)
        if "error" in result:
            yield _sse("error", result["error"])
        else:
            yield _sse("result", result.get("data", {}))

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/mutations")
def mutations(pass_name: str | None = None) -> dict[str, Any]:
    return {"mutations": _store.mutations(pass_name=pass_name)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 3001)))
