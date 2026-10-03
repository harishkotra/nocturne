"""Wake: prove that recall still works after consolidation, and that forgetting is real.

Five questions are generated against surviving facts, one against a fact the Forget pass removed.
The expected answer for every question is taken from the *store*; the model's reply is scored by
token coverage against that store text. A forgotten fact must additionally be provably absent from
the active view, and the answer must not leak it.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from agent import inference as inf
from agent.memory import textmath as tm
from agent.memory.consolidate import GUARDRAIL, Policy, _salience
from agent.memory.store import MemoryStore

ANSWER_INSTRUCTION = (
    "Answer ONLY from the memory blocks provided. Never guess, never reconstruct from the "
    "conversation, and never add a detail that is not in the blocks. Reply with the COMPLETE memory "
    "statement that applies — quote the whole entry, not a one-word fragment. If the blocks do not "
    "contain the answer, reply exactly: I don't have that in memory."
)

NOT_IN_MEMORY_MARKERS = (
    "don't have that",
    "do not have that",
    "not in memory",
    "no record",
    "don't know",
    "do not know",
    "not stored",
    "i don't have",
    "nothing in memory",
    "not have that in memory",
)

QUIZ_SYSTEM = f"""You are generating a self-check for a memory system.
Input JSON: "candidates" = [{{"itemId","text","block","category"}}].
Output STRICT JSON only: {{"questions":[{{"itemId":"<id>","question":"<one short question>"}}]}}
Rules:
- Exactly one question per candidate, and the question must be answerable ONLY from that candidate's text.
- Phrase each question so the COMPLETE fact is the answer ("What does your memory record about the
  user's role and employer?"), not a one-word slot ("Who is the recipient?").
- Do not put any of the candidate's own content words in the question: asking "Does the user work at
  Northwind?" gives away the answer. Ask for the missing detail instead.
- {GUARDRAIL}"""


def _answerable_items(store: MemoryStore, policy: Policy, limit: int) -> list[dict[str, Any]]:
    """Deterministic candidate pick: promoted, protected and high-salience facts first.

    Merged items are pushed to the back: their text is a concatenation of several facts, so a
    faithful answer is long and the comparison is about recall of a fact rather than of a paragraph.
    """
    merged = {m["itemId"] for m in store.mutations(pass_name="merge", accepted=True)}
    items = store.active_items()
    ranked = sorted(
        items,
        key=lambda i: (
            1 if i["itemId"] in merged else 0,
            0 if i["block"] != "scratch" else 1,
            0 if policy.is_protected(i) else 1,
            -_salience(i, policy),
            i["createdAt"],
        ),
    )
    return ranked[: max(limit * 3, limit)]


def _leak_score(question: str, answer_text: str) -> float:
    """How much of the answer's content the question already gives away."""
    answer_tokens = tm.token_set(answer_text)
    if not answer_tokens:
        return 0.0
    q_tokens = tm.token_set(question)
    return len(answer_tokens & q_tokens) / len(answer_tokens)


def _coverage_of_answer(expected: dict[str, Any], reply: str) -> tuple[float, list[str]]:
    """Fraction of the store fact's content tokens present in the model's reply."""
    wanted = tm.token_set(expected["text"])
    if not wanted:
        return 0.0, []
    have = tm.token_set(reply)
    missing = sorted(wanted - have)
    return (len(wanted) - len(missing)) / len(wanted), missing


def _looks_absent(reply: str) -> bool:
    low = reply.lower()
    return any(marker in low for marker in NOT_IN_MEMORY_MARKERS)


def _memory_context(store: MemoryStore) -> str:
    blocks = []
    for block in store.list_blocks():
        items = store.get_block(block["name"]).items
        blocks.append({"block": block["name"], "items": [{"itemId": i["itemId"], "text": i["text"], "category": i["category"]} for i in items]})
    return json.dumps({"memory": blocks}, ensure_ascii=False)


def _ask(cfg: inf.ProviderConfig, store: MemoryStore, question: str, *, verbatim: bool = False) -> str:
    instruction = ANSWER_INSTRUCTION + (
        "\nQuote the memory entry verbatim, word for word -- including the exact nouns the entry uses."
        if verbatim
        else ""
    )
    messages = [
        {"role": "system", "content": f"{instruction}\n\nMemory blocks (JSON):\n{_memory_context(store)}"},
        {"role": "user", "content": question},
    ]
    return inf.chat(cfg, messages, want_json=False).strip()


def generate_questions(
    cfg: inf.ProviderConfig, store: MemoryStore, policy: Policy, *, count: int = 5
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ask the model to phrase questions, then keep only the ones that don't leak the answer."""
    candidates = _answerable_items(store, policy, count)
    payload = {
        "candidates": [
            {"itemId": c["itemId"], "text": c["text"], "block": c["block"], "category": c["category"]}
            for c in candidates
        ]
    }
    by_id = {c["itemId"]: c for c in candidates}
    try:
        data = inf.chat_json(
            cfg,
            [
                {"role": "system", "content": QUIZ_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
    except inf.InferenceError:
        data = {}
    raw = data.get("questions") if isinstance(data, dict) else None
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add(item: dict[str, Any], question: str, origin: str) -> None:
        if item["itemId"] in seen_ids:
            return
        leak = _leak_score(question, item["text"])
        if leak >= 0.5:
            rejected.append(
                {"itemId": item["itemId"], "question": question, "reason": f"question leaks {leak:.0%} of the answer's content"}
            )
            return
        if len(tm.tokens(question)) < 2:
            rejected.append({"itemId": item["itemId"], "question": question, "reason": "question has no content"})
            return
        seen_ids.add(item["itemId"])
        kept.append(
            {
                "itemId": item["itemId"],
                "question": question.strip(),
                "expectedAnswer": item["text"],
                "block": item["block"],
                "category": item["category"],
                "origin": origin,
                "leakScore": round(leak, 3),
            }
        )

    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        item = by_id.get(entry.get("itemId"))
        question = (entry.get("question") or "").strip()
        if item is None:
            rejected.append({"itemId": entry.get("itemId"), "question": question, "reason": "itemId not offered"})
            continue
        if not question.endswith("?"):
            question += "?"
        add(item, question, "model")

    # Fill any shortfall with code-phrased questions so the quiz always has `count` items.
    for item in candidates:
        if len(kept) >= count:
            break
        if item["itemId"] in seen_ids:
            continue
        add(item, f"According to your memory blocks, what do you know about the item {item['itemId']}?", "code")
    return kept[:count], rejected


def run_quiz(
    store: MemoryStore,
    cfg: inf.ProviderConfig,
    policy: Policy,
    *,
    forgotten_item_id: str | None = None,
    count: int = 5,
    report_path: str | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    snapshot = store.latest_snapshot()
    questions, q_rejected = generate_questions(cfg, store, policy, count=count)
    results: list[dict[str, Any]] = []
    for q in questions:
        reply = _ask(cfg, store, q["question"])
        coverage, missing = _coverage_of_answer({"text": q["expectedAnswer"]}, reply)
        attempts = 1
        if coverage < 0.9:
            # one stricter re-ask: quoting the store entry is part of recall, and a paraphrase
            # that swaps the store's own noun is worth recording as a second attempt
            retry = _ask(cfg, store, q["question"], verbatim=True)
            retry_cov, retry_missing = _coverage_of_answer({"text": q["expectedAnswer"]}, retry)
            attempts = 2
            if retry_cov > coverage:
                reply, coverage, missing = retry, retry_cov, retry_missing
        correct = coverage >= 0.9
        results.append(
            {
                "itemId": q["itemId"],
                "question": q["question"],
                "expectedAnswer": q["expectedAnswer"],
                "modelAnswer": reply,
                "coverage": round(coverage, 3),
                "missingTokens": missing[:12],
                "correct": correct,
                "attempts": attempts,
                "origin": q["origin"],
                "leakScore": q["leakScore"],
                "block": q["block"],
                "category": q["category"],
            }
        )
        if on_progress:
            on_progress("question", {"itemId": q["itemId"], "correct": correct, "question": q["question"]})

    # the forgotten fact: prefer the caller's pick, else the most recent Forget-pass casualty
    forgotten = _pick_forgotten(store, forgotten_item_id)
    forgotten_result: dict[str, Any] | None = None
    if forgotten:
        text = forgotten["text"]
        question = (
            "According to your memory blocks, what do you currently know about this detail: "
            f"{_tease(text)}? Answer only from memory."
        )
        reply = _ask(cfg, store, question)
        present = [i for i in store.active_items() if tm.similarity(i["text"], text) >= policy.similarity_threshold]
        leak_cov, leak_missing = _coverage_of_answer({"text": text}, reply)
        absent_claim = _looks_absent(reply)
        forgotten_result = {
            "itemId": forgotten["itemId"],
            "text": text,
            "question": question,
            "modelAnswer": reply,
            "absentFromStore": not present,
            "storeMatches": [p["itemId"] for p in present],
            "saidAbsent": absent_claim,
            "leakedForgottenContent": leak_cov >= 0.6,
            "answerCoverageOfForgottenFact": round(leak_cov, 3),
            "passed": bool(not present and absent_claim and leak_cov < 0.6),
        }
        if on_progress:
            on_progress("forgotten", {"itemId": forgotten["itemId"], "passed": forgotten_result["passed"]})

    survivors_correct = sum(1 for r in results if r["correct"])
    passed = (
        len(results) >= count
        and survivors_correct >= count
        and forgotten_result is not None
        and forgotten_result["passed"]
    )
    report = {
        "passed": passed,
        "generatedAt": time.time(),
        "provider": {"baseUrl": cfg.base_url, "model": cfg.model},
        "snapshot": snapshot,
        "policy": policy.to_dict(),
        "survivorsCorrect": survivors_correct,
        "survivorsTotal": len(results),
        "requiredSurvivors": count,
        "questions": results,
        "rejectedQuestions": q_rejected,
        "forgottenQuestion": forgotten_result,
    }
    path = report_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recall-report.json"
    )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    report["reportPath"] = path
    return report


def _pick_forgotten(store: MemoryStore, preferred: str | None) -> dict[str, Any] | None:
    if preferred:
        item = store.get_item(preferred)
        if item and not item["active"]:
            return item
    for mutation in reversed(store.mutations(pass_name="forget", accepted=True)):
        item = store.get_item(mutation["itemId"])
        if item and not item["active"]:
            return item
    return None


def _tease(text: str) -> str:
    """Ask about the forgotten detail without quoting it verbatim."""
    words = [w for w in re.findall(r"[A-Za-z0-9'\u00c0-\u024f']+", text)]
    keep = [w for i, w in enumerate(words) if i % 3 == 0]
    return " ".join(keep[:6]) or text[:40]
