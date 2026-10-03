"""Sleeptime consolidation: four passes, each an LLM proposal run through a code gate.

    Dedupe  -> drop exact / near-identical facts          (gate: token-set similarity >= 0.9)
    Merge   -> fold several facts into one traceable one  (gate: every content token traceable,
                                                         no new proper nouns)
    Promote -> move a fact into persona/human/episodes     (gate: block whitelist, protected
                                                         categories get priority)
    Forget  -> apply the retention policy                  (gate: stale OR capacity qualified,
                                                         never a protected category)

Rejected proposals are logged with the code-side reason and surfaced to the UI; they are
never silently dropped. A snapshot is taken before and after every pass.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent import inference as inf
from agent.memory import textmath as tm
from agent.memory.store import DEFAULT_BLOCKS, MemoryStore

PASS_NAMES = ("dedupe", "merge", "promote", "forget")

# Verbatim, per the spec -- this string goes into every pass prompt.
GUARDRAIL = (
    "You may only reference itemIds that appear in the input. You may not invent facts, "
    "names, numbers or dates. If you are unsure, omit the item from all lists."
)

TARGET_BLOCKS = ("persona", "human", "episodes")
VALID_CATEGORIES = (
    "identity",
    "preferences",
    "commitments",
    "event",
    "context",
    "ephemeral",
    "project",
    "technical",
)
BLOCK_WEIGHT = {"persona": 60.0, "human": 50.0, "episodes": 30.0, "scratch": 10.0}


@dataclass
class Policy:
    max_active_facts: int = 120
    stale_after_days: float = 45.0
    protected_categories: tuple[str, ...] = ("identity", "preferences", "commitments")
    similarity_threshold: float = 0.9

    @staticmethod
    def from_dict(raw: dict[str, Any] | None) -> "Policy":
        raw = raw or {}
        protected = raw.get("protectedCategories") or raw.get("protected_categories")
        if isinstance(protected, str):
            protected = tuple(p.strip() for p in protected.split(",") if p.strip())
        return Policy(
            max_active_facts=int(raw.get("maxActiveFacts", raw.get("max_active_facts", 120))),
            stale_after_days=float(raw.get("staleAfterDays", raw.get("stale_after_days", 45))),
            protected_categories=tuple(protected) if protected else ("identity", "preferences", "commitments"),
            similarity_threshold=float(raw.get("similarityThreshold", 0.9)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxActiveFacts": self.max_active_facts,
            "staleAfterDays": self.stale_after_days,
            "protectedCategories": list(self.protected_categories),
            "similarityThreshold": self.similarity_threshold,
        }

    def is_protected(self, item: dict[str, Any]) -> bool:
        return item.get("category") in self.protected_categories


@dataclass
class PassLog:
    name: str
    input_count: int = 0
    proposed: int = 0
    accepted: int = 0
    rejected: int = 0
    enforced: int = 0
    before_snapshot: str | None = None
    after_snapshot: str | None = None
    active_after: int = 0
    error: str | None = None
    accepted_mutations: list[dict[str, Any]] = field(default_factory=list)
    rejected_mutations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.name,
            "inputCount": self.input_count,
            "proposed": self.proposed,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "enforced": self.enforced,
            "beforeSnapshot": self.before_snapshot,
            "afterSnapshot": self.after_snapshot,
            "activeCountAfter": self.active_after,
            "error": self.error,
            "acceptedMutations": self.accepted_mutations,
            "rejectedMutations": self.rejected_mutations,
        }


Progress = Callable[[str, dict[str, Any]], None]


class _Silent:
    def __call__(self, *_a: Any, **_k: Any) -> None:  # noqa: D102
        return None


_silent = _Silent()


# ------------------------------------------------------------------ working state
class WorkingState:
    """The active fact set as it evolves through a consolidation run."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items: dict[str, dict[str, Any]] = {i["itemId"]: dict(i) for i in items}

    def active(self) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda i: (i["createdAt"], i["itemId"]))

    def count(self) -> int:
        return len(self.items)

    def prompt_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "itemId": i["itemId"],
                "text": i["text"],
                "block": i["block"],
                "category": i["category"],
                "ageDays": i["ageDays"],
            }
            for i in self.active()
        ]


def _lookup(
    state: WorkingState, pre_ids: set[str], item_id: Any, label: str = "itemId"
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve an itemId against the state, distinguishing 'never existed' from 'consumed in this
    pass' so a rejection reason tells the truth about which it was."""
    if not isinstance(item_id, str):
        return None, f"{label} {item_id!r} is not a string"
    item = state.items.get(item_id)
    if item is not None:
        return item, None
    if item_id in pre_ids:
        return None, f"{label} {item_id} was already removed by an earlier mutation in this pass"
    return None, f"{label} {item_id!r} does not exist in the pre-pass store"


def _persist(store: MemoryStore, state: WorkingState) -> None:
    """Write the working state back through the block interface (replace = full set)."""
    by_block: dict[str, list[dict[str, Any]]] = {}
    for item in state.active():
        by_block.setdefault(item["block"], []).append(item)
    for name, _ in DEFAULT_BLOCKS:
        rows = by_block.get(name, [])
        store.replace(
            name,
            [
                {
                    "itemId": r["itemId"],
                    "text": r["text"],
                    "category": r["category"],
                    "block": name,
                    "sourceTurn": r.get("sourceTurn"),
                    "createdAt": r["createdAt"],
                }
                for r in rows
            ],
        )


def _salience(item: dict[str, Any], policy: Policy) -> float:
    """Deterministic ranking used by the capacity rule (highest survives).

    block weight + protected-category bonus + recency (decays to 0 at stale_after_days).
    """
    score = BLOCK_WEIGHT.get(item["block"], 5.0)
    if policy.is_protected(item):
        score += 40.0
    score += max(0.0, 30.0 - 30.0 * item["ageDays"] / max(policy.stale_after_days, 1.0))
    return round(score, 4)


def _capacity_overflow(state: WorkingState, policy: Policy) -> set[str]:
    """itemIds beyond MAX_ACTIVE_FACTS, lowest salience first, never protected."""
    ranked = sorted(
        state.active(),
        key=lambda i: (-_salience(i, policy), i["createdAt"], i["itemId"]),
    )
    overflow = {i["itemId"] for i in ranked[policy.max_active_facts :] if not policy.is_protected(i)}
    return overflow


def _call_pass(cfg: inf.ProviderConfig, name: str, system: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    data = inf.chat_json(cfg, messages)
    if not isinstance(data, dict):
        raise inf.InferenceError(f"{name} pass returned {type(data).__name__}, expected a JSON object")
    return data


# ------------------------------------------------------------------ 1. dedupe
DEDUPE_SYSTEM = f"""You are the Dedupe pass of a memory consolidation engine.
Input: JSON with an "items" array (itemId, text, block, category, ageDays) and "candidatePairs"
([{{"dropId","keeperId","similarity"}}, ...]) that the engine's own normalised token comparison has
pre-flagged as possible duplicates.
Output: STRICT JSON only, of the shape
{{"drop":[{{"itemId":"<id>","duplicateOf":"<id>","reason":"<short reason>"}}]}}
Rules:
- Adjudicate every candidate pair: if the two facts say the same thing, emit a drop entry with
  itemId = dropId and duplicateOf = keeperId.
- You may also flag other duplicates you can see, but never pair an item with itself.
- Do not drop a fact that carries information the survivor lacks.
- Every entry needs a short reason.
- {GUARDRAIL}"""


def _gate_dedupe(
    proposals: list[dict[str, Any]], state: WorkingState, policy: Policy
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    dropped: set[str] = set()
    pre_ids = set(state.items)

    def reject(raw: dict[str, Any], why: str) -> None:
        rejected.append({"kind": "drop", "itemId": raw.get("itemId"), "proposal": raw, "rejectionReason": why})

    for raw in proposals:
        item_id = raw.get("itemId")
        dup_of = raw.get("duplicateOf")
        reason = (raw.get("reason") or "").strip()
        loser, err = _lookup(state, pre_ids, item_id)
        if err:
            reject(raw, err)
            continue
        keeper, err = _lookup(state, pre_ids, dup_of, "duplicateOf")
        if err:
            reject(raw, err)
            continue
        if item_id == dup_of:
            reject(raw, "itemId equals duplicateOf")
            continue
        if dup_of in dropped:
            reject(raw, f"duplicateOf {dup_of} was itself dropped; refusing a duplicate chain")
            continue
        score = tm.similarity(loser["text"], keeper["text"])
        if score < policy.similarity_threshold:
            reject(
                raw,
                f"normalised token-set similarity {score:.2f} < {policy.similarity_threshold:.2f}; "
                "not semantically identical",
            )
            continue
        if not reason:
            reject(raw, "no reason supplied")
            continue
        # keep the richer classification when the survivor lost it
        if policy.is_protected(loser) and not policy.is_protected(keeper):
            keeper["category"] = loser["category"]
        accepted.append(
            {
                "kind": "drop",
                "itemId": item_id,
                "refs": {"duplicateOf": dup_of, "similarity": round(score, 3)},
                "reason": reason,
            }
        )
        dropped.add(item_id)
        state.items.pop(item_id, None)
    return accepted, rejected


# ------------------------------------------------------------------ 2. merge
MERGE_SYSTEM = f"""You are the Merge pass of a memory consolidation engine.
Input: JSON with an "items" array; each item has itemId, text, block, category, ageDays.
Output: STRICT JSON only, of the shape
{{"merge":[{{"intoId":"<id>","fromIds":["<id>",...],"mergedText":"<text>","reason":"<short reason>"}}]}}
Rules:
- A merge folds fromIds into intoId. intoId's text is REPLACED by mergedText; fromIds disappear.
- mergedText must be a lossless rewrite: every content word in it must already appear in one of
  the source texts (intoId plus fromIds). Do not add detail, do not summarise away specifics.
- You may not introduce any name, place, product, number or date that is not spelled in the sources.
- Only merge items that are about the same subject.
- {GUARDRAIL}"""


def _gate_merge(
    proposals: list[dict[str, Any]], state: WorkingState, policy: Policy
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    consumed: set[str] = set()
    pre_ids = set(state.items)

    def reject(raw: dict[str, Any], why: str) -> None:
        rejected.append({"kind": "merge", "itemId": raw.get("intoId"), "proposal": raw, "rejectionReason": why})

    for raw in proposals:
        into_id = raw.get("intoId")
        from_ids = raw.get("fromIds") or []
        merged_text = (raw.get("mergedText") or "").strip()
        reason = (raw.get("reason") or "").strip()
        target, err = _lookup(state, pre_ids, into_id, "intoId")
        if err:
            reject(raw, err)
            continue
        if not isinstance(from_ids, list) or not from_ids:
            reject(raw, "fromIds must be a non-empty array")
            continue
        if into_id in consumed or any(f in consumed for f in from_ids):
            reject(raw, "one of these items was already consumed by an earlier merge in this pass")
            continue
        if into_id in from_ids:
            reject(raw, "intoId also appears in fromIds")
            continue
        sources = [target]
        missing = [f for f in from_ids if not isinstance(f, str) or f not in state.items]
        if missing:
            absent = [f for f in missing if f not in pre_ids]
            gone = [f for f in missing if f in pre_ids]
            detail = []
            if absent:
                detail.append(f"not in the pre-pass store: {absent}")
            if gone:
                detail.append(f"already removed by an earlier mutation in this pass: {gone}")
            reject(raw, "fromIds " + "; ".join(detail))
            continue
        sources.extend(state.items[f] for f in from_ids)
        if not merged_text:
            reject(raw, "mergedText is empty")
            continue
        if len(tm.tokens(merged_text)) < 2:
            reject(raw, "mergedText is too short to carry any fact")
            continue
        sources_text = [s["text"] for s in sources]
        cov, missing_tokens = tm.coverage(merged_text, sources_text)
        if cov < 1.0:
            reject(
                raw,
                f"mergedText is not traceable to its sources: {len(missing_tokens)} content token(s) "
                f"appear in none of them ({', '.join(missing_tokens[:8])})",
            )
            continue
        new_names = tm.proper_nouns(merged_text) - {n for s in sources for n in tm.proper_nouns(s["text"])}
        if new_names:
            reject(raw, f"mergedText introduces proper nouns absent from every source: {sorted(new_names)}")
            continue
        if not reason:
            reject(raw, "no reason supplied")
            continue
        if len({s["block"] for s in sources}) > 1:
            reject(raw, "sources span multiple blocks; refusing to merge across blocks")
            continue
        accepted.append(
            {
                "kind": "merge",
                "itemId": into_id,
                "refs": {
                    "fromIds": list(from_ids),
                    "mergedText": merged_text,
                    "tokenCoverage": cov,
                },
                "reason": reason,
            }
        )
        consumed.add(into_id)
        consumed.update(from_ids)
        target["text"] = merged_text
        target["updatedAt"] = time.time()
        for f in from_ids:
            state.items.pop(f, None)
    return accepted, rejected


# ------------------------------------------------------------------ 3. promote
PROMOTE_SYSTEM = f"""You are the Promote pass of a memory consolidation engine.
Input: JSON with an "items" array; each item has itemId, text, block, category, ageDays.
Output: STRICT JSON only, of the shape
{{"promote":[{{"itemId":"<id>","toBlock":"persona"|"human"|"episodes","reason":"<short reason>"}}]}}
Block meanings:
- persona: durable facts about the assistant itself (its rules, standing decisions, how it behaves).
- human: durable facts about the user (identity, preferences, commitments, long-running goals).
- episodes: events anchored in time or place.
Rules:
- Promote only facts that are durable enough to live in long-term memory, typically from "scratch".
- Identity, preference and commitment facts get priority over incidental context.
- Do not promote a fact that is already in the block you name.
- {GUARDRAIL}"""


def _gate_promote(
    proposals: list[dict[str, Any]], state: WorkingState, policy: Policy
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rejected: list[dict[str, Any]] = []
    pre_ids = set(state.items)

    def reject(raw: dict[str, Any], why: str) -> None:
        rejected.append({"kind": "promote", "itemId": raw.get("itemId"), "proposal": raw, "rejectionReason": why})

    valid: list[dict[str, Any]] = []
    for raw in proposals:
        item_id = raw.get("itemId")
        to_block = raw.get("toBlock")
        reason = (raw.get("reason") or "").strip()
        item, err = _lookup(state, pre_ids, item_id)
        if err:
            reject(raw, err)
            continue
        if to_block not in TARGET_BLOCKS:
            reject(raw, f"toBlock {to_block!r} is not one of {TARGET_BLOCKS}")
            continue
        if item["block"] == to_block:
            reject(raw, f"item is already in {to_block}")
            continue
        if not reason:
            reject(raw, "no reason supplied")
            continue
        valid.append((raw, item, to_block, reason))

    # protected-category facts are applied first: they win the slot if capacity bites later
    valid.sort(key=lambda t: (0 if policy.is_protected(t[1]) else 1, t[1]["createdAt"]))
    accepted: list[dict[str, Any]] = []
    for raw, item, to_block, reason in valid:
        accepted.append(
            {
                "kind": "promote",
                "itemId": item["itemId"],
                "refs": {
                    "fromBlock": item["block"],
                    "toBlock": to_block,
                    "priority": "protected" if policy.is_protected(item) else "standard",
                },
                "reason": reason,
            }
        )
        item["block"] = to_block
        cat = raw.get("category")
        if isinstance(cat, str) and cat in VALID_CATEGORIES:
            item["category"] = cat
    return accepted, rejected


# ------------------------------------------------------------------ 4. forget
FORGET_SYSTEM = f"""You are the Forget pass of a memory consolidation engine.
Input: JSON with an "items" array (itemId, text, block, category, ageDays) and a "policy" object
(maxActiveFacts, staleAfterDays, protectedCategories, overflowIds).
Output: STRICT JSON only, of the shape
{{"forget":[{{"itemId":"<id>","reason":"<short reason>","policy":"stale"|"capacity"}}]}}
Rules:
- "stale" means the item's ageDays is greater than policy.staleAfterDays.
- "capacity" means the item is in policy.overflowIds, i.e. it sits past maxActiveFacts in the
  engine's own salience ranking. Items not in overflowIds are NOT over capacity; do not use
  "capacity" for them.
- You must never propose forgetting an item whose category is in policy.protectedCategories.
- Prefer low-value, already-superseded, or trivially re-derivable items.
- {GUARDRAIL}"""


def _gate_forget(
    proposals: list[dict[str, Any]],
    state: WorkingState,
    policy: Policy,
    overflow: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    pre_ids = set(state.items)

    def reject(raw: dict[str, Any], why: str) -> None:
        rejected.append({"kind": "forget", "itemId": raw.get("itemId"), "proposal": raw, "rejectionReason": why})

    for raw in proposals:
        item_id = raw.get("itemId")
        reason = (raw.get("reason") or "").strip()
        claimed = raw.get("policy")
        item, err = _lookup(state, pre_ids, item_id)
        if err:
            reject(raw, err)
            continue
        if policy.is_protected(item):
            reject(
                raw,
                f"category {item['category']!r} is protected by the retention policy "
                f"({', '.join(policy.protected_categories)})",
            )
            continue
        qualifies_stale = item["ageDays"] > policy.stale_after_days
        qualifies_capacity = item_id in overflow
        if not qualifies_stale and not qualifies_capacity:
            reject(
                raw,
                f"item is {item['ageDays']:.1f}d old (staleAfterDays={policy.stale_after_days:g}) and not over "
                f"capacity, so no retention rule authorises forgetting it",
            )
            continue
        if not reason:
            reject(raw, "no reason supplied")
            continue
        verified_policy = "stale" if qualifies_stale else "capacity"
        accepted.append(
            {
                "kind": "forget",
                "itemId": item_id,
                "refs": {
                    "claimedPolicy": claimed,
                    "ageDays": item["ageDays"],
                    "block": item["block"],
                    "category": item["category"],
                    "enforcedBy": "model+code",
                },
                "policy": verified_policy,
                "reason": reason,
            }
        )
        state.items.pop(item_id, None)
    return accepted, rejected


def _enforce_duplicates(state: WorkingState, policy: Policy) -> list[dict[str, Any]]:
    """Code-side backstop for the duplicate rule itself.

    The gate in Dedupe is a deterministic measurement, not a judgement: if the engine's own
    normalised comparison says two active facts are the same fact (>= threshold) and the model left
    the pair un-adjudicated, the older fact keeps its place and the newer copy leaves the view.
    Marked `enforcedBy: engine-duplicate-rule` so it is never confused with a model proposal.
    """
    enforced: list[dict[str, Any]] = []
    for loser_id, keeper_id, score in tm.find_duplicate_pairs(state.active(), policy.similarity_threshold):
        loser = state.items.get(loser_id)
        keeper = state.items.get(keeper_id)
        if not loser or not keeper:
            continue
        if policy.is_protected(loser) and not policy.is_protected(keeper):
            keeper["category"] = loser["category"]
        enforced.append(
            {
                "kind": "drop",
                "itemId": loser_id,
                "refs": {
                    "duplicateOf": keeper_id,
                    "similarity": round(score, 3),
                    "enforcedBy": "engine-duplicate-rule",
                },
                "reason": (
                    f"duplicate rule: normalised token-set similarity {score:.2f} >= "
                    f"{policy.similarity_threshold:.2f} with {keeper_id}, which the model left un-adjudicated"
                ),
            }
        )
        state.items.pop(loser_id, None)
    return enforced


PROMOTION_TARGETS = {
    "identity": "human",
    "preferences": "human",
    "commitments": "human",
    "event": "episodes",
}


def _enforce_promotions(state: WorkingState, policy: Policy) -> list[dict[str, Any]]:
    """Code-side backstop: a durable-category fact must not be left rotting in scratch.

    Which block a fact belongs to is a judgement, so the model does it first; but the rule "identity,
    preference and commitment facts live in `human`, events live in `episodes`" is stated policy, so
    anything it leaves in scratch is moved by the engine and labelled `engine-promotion-rule`.
    """
    enforced: list[dict[str, Any]] = []
    for item in state.active():
        if item["block"] != "scratch":
            continue
        target = PROMOTION_TARGETS.get(item["category"])
        if not target:
            continue
        enforced.append(
            {
                "kind": "promote",
                "itemId": item["itemId"],
                "refs": {
                    "fromBlock": "scratch",
                    "toBlock": target,
                    "priority": "protected" if policy.is_protected(item) else "standard",
                    "enforcedBy": "engine-promotion-rule",
                },
                "reason": (
                    f"promotion rule: category {item['category']!r} belongs in {target!r}, not in scratch, "
                    "which the model left un-promoted"
                ),
            }
        )
        item["block"] = target
    return enforced


def _enforce_capacity(state: WorkingState, policy: Policy) -> list[dict[str, Any]]:
    """Code-side backstop: the cap is a policy, so it is applied even if the model under-proposes."""
    enforced: list[dict[str, Any]] = []
    if state.count() <= policy.max_active_facts:
        return enforced
    candidates = sorted(
        [i for i in state.active() if not policy.is_protected(i)],
        key=lambda i: (-_salience(i, policy), i["createdAt"], i["itemId"]),
    )
    excess = state.count() - policy.max_active_facts
    for item in candidates[:excess]:
        enforced.append(
            {
                "kind": "forget",
                "itemId": item["itemId"],
                "refs": {
                    "ageDays": item["ageDays"],
                    "block": item["block"],
                    "category": item["category"],
                    "salience": _salience(item, policy),
                    "enforcedBy": "policy-engine",
                },
                "policy": "capacity",
                "reason": (
                    f"retention policy: {state.count()} active facts exceed MAX_ACTIVE_FACTS="
                    f"{policy.max_active_facts}; lowest-salience unprotected fact dropped"
                ),
            }
        )
        state.items.pop(item["itemId"], None)
    return enforced


# ------------------------------------------------------------------ driver
def run_consolidation(
    store: MemoryStore,
    cfg: inf.ProviderConfig,
    policy: Policy,
    *,
    dry_run: bool = False,
    passes: tuple[str, ...] = PASS_NAMES,
    on_progress: Progress | None = None,
) -> dict[str, Any]:
    """Run the requested passes in order and return a full, per-mutation report."""
    progress = on_progress or _silent
    state = WorkingState(store.active_items())
    initial_count = state.count()
    started = time.time()
    logs: list[PassLog] = []
    # A dry run writes nothing at all: no live mutation and no snapshot.
    start_snapshot = None if dry_run else store.snapshot("consolidation:start")
    result_prefix = "dryrun" if dry_run else ""

    def emit(log: PassLog, phase: str, extra: dict[str, Any] | None = None) -> None:
        payload = {k: v for k, v in log.to_dict().items() if k not in ("acceptedMutations", "rejectedMutations")}
        payload["phase"] = phase
        payload.update(extra or {})
        progress(log.name, payload)

    for name in PASS_NAMES:
        if name not in passes:
            continue
        log = PassLog(name=name, input_count=state.count())
        logs.append(log)
        emit(log, "start", {"activeCountBefore": state.count()})
        before = None if dry_run else store.snapshot(f"pass:{name}:before")
        log.before_snapshot = before
        try:
            proposals = _run_pass(name, cfg, state, policy)
        except inf.InferenceError as exc:
            log.error = str(exc)
            log.after_snapshot = None if dry_run else store.snapshot(f"pass:{name}:error")
            emit(log, "error", {"detail": exc.to_dict()})
            continue
        log.proposed = len(proposals)
        accepted, rejected = _verify(name, proposals, state, policy)
        log.accepted = len(accepted)
        log.rejected = len(rejected)
        enforced: list[dict[str, Any]] = []
        if name == "dedupe":
            enforced = _enforce_duplicates(state, policy)
        elif name == "promote":
            enforced = _enforce_promotions(state, policy)
        elif name == "forget":
            enforced = _enforce_capacity(state, policy)
        if enforced:
            log.accepted += len(enforced)
            log.enforced = len(enforced)
            accepted.extend(enforced)
        log.accepted_mutations = accepted
        log.rejected_mutations = rejected
        log.active_after = state.count()
        after = None if dry_run else _persist_and_snapshot(store, state, f"pass:{name}:after")
        log.after_snapshot = after
        if not dry_run:
            for mutation in accepted + rejected:
                store.record_mutation(
                    snapshot_id=after,
                    pass_name=name,
                    kind=mutation["kind"],
                    item_id=mutation.get("itemId") or "unknown",
                    refs=mutation.get("refs") or {"proposal": mutation.get("proposal")},
                    accepted=mutation.get("rejectionReason") is None,
                    reason=mutation.get("reason"),
                    rejection_reason=mutation.get("rejectionReason"),
                    policy=mutation.get("policy"),
                )
        emit(
            log,
            "done",
            {
                "activeCountAfter": state.count(),
                "acceptedMutations": accepted,
                "rejectedMutations": rejected,
            },
        )

    return {
        "dryRun": dry_run,
        "resultId": f"{result_prefix}cons_{int(started)}",
        "startSnapshot": start_snapshot,
        "policy": policy.to_dict(),
        "startedAt": started,
        "finishedAt": time.time(),
        "activeCountBefore": initial_count,
        "activeCountAfter": state.count(),
        "passes": [log.to_dict() for log in logs],
        "totals": {
            "proposed": sum(log.proposed for log in logs),
            "accepted": sum(log.accepted for log in logs),
            "rejected": sum(log.rejected for log in logs),
            "enforced": sum(log.enforced for log in logs),
        },
        "failed": any(log.error for log in logs),
    }


def _persist_and_snapshot(store: MemoryStore, state: WorkingState, label: str) -> str:
    _persist(store, state)
    return store.snapshot(label)


def _run_pass(name: str, cfg: inf.ProviderConfig, state: WorkingState, policy: Policy) -> list[dict[str, Any]]:
    items = state.prompt_rows()
    payload: dict[str, Any] = {"items": items}
    if name == "dedupe":
        payload["candidatePairs"] = [
            {"dropId": loser, "keeperId": keeper, "similarity": round(score, 3)}
            for loser, keeper, score in tm.find_duplicate_pairs(state.active(), policy.similarity_threshold)
        ]
    if name == "forget":
        payload["policy"] = {
            "maxActiveFacts": policy.max_active_facts,
            "staleAfterDays": policy.stale_after_days,
            "protectedCategories": list(policy.protected_categories),
            "overflowIds": sorted(_capacity_overflow(state, policy)),
        }
    systems = {
        "dedupe": DEDUPE_SYSTEM,
        "merge": MERGE_SYSTEM,
        "promote": PROMOTE_SYSTEM,
        "forget": FORGET_SYSTEM,
    }
    data = _call_pass(cfg, name, systems[name], payload)
    key = {"dedupe": "drop", "merge": "merge", "promote": "promote", "forget": "forget"}[name]
    proposals = data.get(key, [])
    if not isinstance(proposals, list):
        raise inf.InferenceError(f"{name} pass returned {key} of type {type(proposals).__name__}, expected array")
    return [p for p in proposals if isinstance(p, dict)]


def _verify(
    name: str, proposals: list[dict[str, Any]], state: WorkingState, policy: Policy
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if name == "dedupe":
        return _gate_dedupe(proposals, state, policy)
    if name == "merge":
        return _gate_merge(proposals, state, policy)
    if name == "promote":
        return _gate_promote(proposals, state, policy)
    if name == "forget":
        overflow = _capacity_overflow(state, policy)
        return _gate_forget(proposals, state, policy, overflow)
    raise ValueError(f"unknown pass: {name}")


# ------------------------------------------------------------------ write-time encoder
EXTRACT_SYSTEM = f"""You are the write-time memory encoder of a chat agent.
Input: JSON with "turns": [{{"turnId":<int>,"role":"user"|"assistant","text":"<message>"}}]
Output: STRICT JSON only, of the shape
{{"facts":[{{"text":"<one atomic fact>","category":"identity"|"preferences"|"commitments"|"event"|"context"|"project"|"technical"|"ephemeral","turnId":<int>}}]}}
Rules:
- One atomic fact per entry, written in the third person ("User ...", "The assistant ...").
- Every content word in a fact must already appear in the turns. Do not add detail.
- If the transcript states the same thing twice, emit one entry per occurrence; never collapse
  repetitions into a single entry.
- Skip greetings, acknowledgements and questions that carry no information.
- categories: identity = who the user is; preferences = likes/dislikes; commitments = promises,
  agreements, deadlines; event = something that happened; project = ongoing work;
  technical = tools, stack, constraints; ephemeral = today-only noise; context = anything else.
- {GUARDRAIL}"""


def extract_and_append(store: MemoryStore, cfg: inf.ProviderConfig, *, dry_run: bool = False) -> dict[str, Any]:
    """Pull atomic facts out of the transcript into `scratch`, gate-checked for traceability.

    Incremental: only turns encoded since the last call are sent to the model, so the prompt does
    not grow with the conversation and known facts are not re-emitted. Duplication across turns is
    deliberately NOT filtered here -- append-only bloat is exactly what the Dedupe pass exists to
    fix. Invention is filtered here.
    """
    all_messages = store.messages()
    since = int(store.get_meta("extracted_through", "0") or 0)
    messages = [m for m in all_messages if m["turn"] > since]
    if not all_messages:
        return {"proposed": 0, "accepted": [], "rejected": [], "skipped": "no transcript"}
    if not messages:
        return {"proposed": 0, "accepted": [], "rejected": [], "skipped": "nothing new to encode"}
    turns = [{"turnId": m["turn"], "role": m["role"], "text": m["content"]} for m in messages]
    sources = [t["text"] for t in turns]
    transcript_names = {n for t in sources for n in tm.proper_nouns(t)} | {"User", "The", "Assistant"}
    payload = {"turns": turns}
    try:
        data = _call_pass(
            cfg,
            "extract",
            EXTRACT_SYSTEM,
            payload,
        )
    except inf.InferenceError as exc:
        return {
            "proposed": 0,
            "accepted": [],
            "rejected": [],
            "error": exc.to_dict(),
        }
    raw_facts = data.get("facts")
    if not isinstance(raw_facts, list):
        return {"proposed": 0, "accepted": [], "rejected": [], "error": {"error": "extract returned no facts array"}}

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    turn_ids = {t["turnId"] for t in turns}
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue
        text = (raw.get("text") or "").strip()
        category = raw.get("category") if raw.get("category") in VALID_CATEGORIES else "context"
        turn_id = raw.get("turnId") if isinstance(raw.get("turnId"), int) and raw.get("turnId") in turn_ids else None
        if len(tm.tokens(text)) < 2:
            rejected.append({"proposal": raw, "rejectionReason": "fact carries fewer than 2 content tokens"})
            continue
        cov, missing = tm.coverage(text, sources)
        if cov < 1.0:
            rejected.append(
                {
                    "proposal": raw,
                    "rejectionReason": (
                        f"fact is not traceable to the transcript: {len(missing)} content token(s) invented "
                        f"({', '.join(missing[:8])})"
                    ),
                }
            )
            continue
        new_names = tm.proper_nouns(text) - transcript_names
        if new_names:
            rejected.append(
                {
                    "proposal": raw,
                    "rejectionReason": f"fact introduces proper nouns absent from the transcript: {sorted(new_names)}",
                }
            )
            continue
        record = {"text": text, "category": category, "sourceTurn": turn_id}
        accepted.append(record)
        if not dry_run:
            item = store.append("scratch", record)
            record = {**record, "itemId": item["itemId"]}
            accepted[-1] = record
    through = max(m["turn"] for m in messages)
    if not dry_run:
        store.set_meta("extracted_through", str(through))
    return {
        "proposed": len(raw_facts),
        "accepted": accepted,
        "rejected": rejected,
        "encodedThroughTurn": through,
        "dryRun": dry_run,
    }
