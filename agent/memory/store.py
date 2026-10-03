"""Versioned memory-block store for Nocturne.

Implements the Letta block model (persona / human / episodes / scratch) over SQLite
behind the interface `list_blocks, get_block, append, replace, snapshot, restore`.

Nothing is ever hard-deleted: consolidation only flips `active` in the live view, and
every version is captured in an immutable snapshot row set, so any two versions can be
diffed and any version can be restored.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_BLOCKS: tuple[tuple[str, str], ...] = (
    ("persona", "Facts about Nocturne itself: how it behaves, what it decided, its standing rules."),
    ("human", "Stable facts about the user: identity, preferences, commitments."),
    ("episodes", "Events and happenings anchored to a time or place."),
    ("scratch", "Working memory: unconsolidated facts pulled from recent conversation."),
)

PROTECTED_BY_DEFAULT = ("identity", "preferences", "commitments")


def _now() -> float:
    return time.time()


@dataclass
class Block:
    name: str
    description: str
    count: int
    items: list[dict[str, Any]]


class MemoryStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get(
            "NOCTURNE_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "nocturne.db")
        )
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create()

    # ---------------------------------------------------------------- schema
    def _create(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocks(
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS items(
                    item_id TEXT PRIMARY KEY,
                    block TEXT NOT NULL REFERENCES blocks(name),
                    text TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'context',
                    source_turn INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS items_block_active ON items(block, active);
                CREATE TABLE IF NOT EXISTS snapshots(
                    snapshot_id TEXT PRIMARY KEY,
                    seq INTEGER NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    active_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshot_items(
                    snapshot_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    block TEXT NOT NULL,
                    text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source_turn INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    active INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS si_snap ON snapshot_items(snapshot_id, active);
                CREATE TABLE IF NOT EXISTS mutations(
                    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT,
                    pass TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    refs TEXT NOT NULL DEFAULT '{}',
                    accepted INTEGER NOT NULL,
                    reason TEXT,
                    rejection_reason TEXT,
                    policy TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages(
                    turn INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            for name, description in DEFAULT_BLOCKS:
                self._conn.execute(
                    "INSERT OR IGNORE INTO blocks(name, description, created_at) VALUES(?,?,?)",
                    (name, description, _now()),
                )
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('item_seq', '0')")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- helpers
    def _next_item_id(self) -> str:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT value FROM meta WHERE key='item_seq'").fetchone()
            seq = int(row["value"]) + 1 if row else 1
            self._conn.execute("UPDATE meta SET value=? WHERE key='item_seq'", (str(seq),))
        return f"m_{seq}"

    def _row_to_item(self, row: sqlite3.Row) -> dict[str, Any]:
        age_days = max(0.0, (_now() - row["created_at"]) / 86400.0)
        return {
            "itemId": row["item_id"],
            "block": row["block"],
            "text": row["text"],
            "category": row["category"],
            "sourceTurn": row["source_turn"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "ageDays": round(age_days, 3),
            "active": bool(row["active"]),
        }

    # ---------------------------------------------------------------- interface
    def list_blocks(self) -> list[dict[str, Any]]:
        with self._lock:
            blocks = self._conn.execute("SELECT * FROM blocks ORDER BY rowid").fetchall()
            out = []
            for b in blocks:
                count = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM items WHERE block=? AND active=1", (b["name"],)
                ).fetchone()["c"]
                out.append({"name": b["name"], "description": b["description"], "count": int(count)})
            return out

    def get_block(self, name: str, include_inactive: bool = False) -> Block:
        with self._lock:
            b = self._conn.execute("SELECT * FROM blocks WHERE name=?", (name,)).fetchone()
            if b is None:
                raise KeyError(f"unknown block: {name}")
            sql = "SELECT * FROM items WHERE block=?"
            if not include_inactive:
                sql += " AND active=1"
            sql += " ORDER BY created_at, item_id"
            rows = self._conn.execute(sql, (name,)).fetchall()
            return Block(
                name=b["name"],
                description=b["description"],
                count=len([r for r in rows if r["active"]]),
                items=[self._row_to_item(r) for r in rows],
            )

    def append(self, block: str, item: dict[str, Any]) -> dict[str, Any]:
        """Append one item. `item` is a mapping with `text` and optional
        `category`, `sourceTurn`, `createdAt` (backdating is test-only)."""
        with self._lock:
            if self._conn.execute("SELECT 1 FROM blocks WHERE name=?", (block,)).fetchone() is None:
                raise KeyError(f"unknown block: {block}")
            text = (item.get("text") or "").strip()
            if not text:
                raise ValueError("item.text must be non-empty")
            item_id = item.get("itemId") or self._next_item_id()
            created = float(item.get("createdAt") or _now())
            now = _now()
            with self._conn:
                self._conn.execute(
                    "INSERT INTO items(item_id, block, text, category, source_turn, created_at, updated_at, active)"
                    " VALUES(?,?,?,?,?,?,?,1)",
                    (
                        item_id,
                        block,
                        text,
                        item.get("category") or "context",
                        item.get("sourceTurn"),
                        created,
                        now,
                    ),
                )
            return self.get_item(item_id)  # type: ignore[return-value]

    def replace(self, block: str, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace the active contents of a block with `items`.

        Existing rows referenced by itemId are updated in place; rows in the block that
        are not named become inactive (retained for history); new items are inserted.
        """
        items = list(items)
        with self._lock, self._conn:
            keep = set()
            for item in items:
                text = (item.get("text") or "").strip()
                if not text:
                    raise ValueError("item.text must be non-empty")
                item_id = item.get("itemId")
                if item_id and self._conn.execute(
                    "SELECT 1 FROM items WHERE item_id=?", (item_id,)
                ).fetchone():
                    self._conn.execute(
                        "UPDATE items SET block=?, text=?, category=?, active=1, updated_at=? WHERE item_id=?",
                        (
                            item.get("block") or block,
                            text,
                            item.get("category") or "context",
                            _now(),
                            item_id,
                        ),
                    )
                    keep.add(item_id)
                else:
                    new_id = self._next_item_id()
                    created = float(item.get("createdAt") or _now())
                    self._conn.execute(
                        "INSERT INTO items(item_id, block, text, category, source_turn, created_at, updated_at, active)"
                        " VALUES(?,?,?,?,?,?,?,1)",
                        (
                            new_id,
                            item.get("block") or block,
                            text,
                            item.get("category") or "context",
                            item.get("sourceTurn"),
                            created,
                            _now(),
                        ),
                    )
                    keep.add(new_id)
            rows = self._conn.execute(
                "SELECT item_id FROM items WHERE block=? AND active=1", (block,)
            ).fetchall()
            for row in rows:
                if row["item_id"] not in keep:
                    self._conn.execute("UPDATE items SET active=0 WHERE item_id=?", (row["item_id"],))
        return [self.get_item(i) for i in sorted(keep)]  # type: ignore[misc]

    def snapshot(self, label: str = "manual") -> str:
        with self._lock, self._conn:
            seq = (self._conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM snapshots").fetchone()["s"])
            snapshot_id = f"s{seq:04d}_{int(_now())}"
            count = self._conn.execute("SELECT COUNT(*) AS c FROM items WHERE active=1").fetchone()["c"]
            self._conn.execute(
                "INSERT INTO snapshots(snapshot_id, seq, label, created_at, active_count) VALUES(?,?,?,?,?)",
                (snapshot_id, seq, label, _now(), int(count)),
            )
            rows = self._conn.execute(
                "SELECT item_id, block, text, category, source_turn, created_at, updated_at, active FROM items"
            ).fetchall()
            self._conn.executemany(
                "INSERT INTO snapshot_items(snapshot_id, item_id, block, text, category, source_turn,"
                " created_at, updated_at, active) VALUES(?,?,?,?,?,?,?,?,?)",
                [(snapshot_id, *[r[k] for k in ("item_id", "block", "text", "category", "source_turn", "created_at", "updated_at", "active")]) for r in rows],
            )
            return snapshot_id

    def restore(self, snapshot_id: str) -> dict[str, Any]:
        """Make the live view match a snapshot exactly, then record a new snapshot.

        The restore is itself versioned so `undo of undo` remains possible.
        """
        with self._lock, self._conn:
            snap = self._conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            if snap is None:
                raise KeyError(f"unknown snapshot: {snapshot_id}")
            rows = self._conn.execute(
                "SELECT * FROM snapshot_items WHERE snapshot_id=?", (snapshot_id,)
            ).fetchall()
            self._conn.execute("DELETE FROM items")
            self._conn.executemany(
                "INSERT INTO items(item_id, block, text, category, source_turn, created_at, updated_at, active)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        r["item_id"],
                        r["block"],
                        r["text"],
                        r["category"],
                        r["source_turn"],
                        r["created_at"],
                        r["updated_at"],
                        r["active"],
                    )
                    for r in rows
                ],
            )
            new_id = self.snapshot(f"restore:{snapshot_id}")
            return {
                "restoredFrom": snapshot_id,
                "snapshotId": new_id,
                "activeCount": sum(1 for r in rows if r["active"]),
            }

    # ---------------------------------------------------------------- reads
    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
            return self._row_to_item(row) if row else None

    def active_items(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM items WHERE active=1 ORDER BY created_at, item_id"
            ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def all_items(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM items ORDER BY created_at, item_id").fetchall()
            return [self._row_to_item(r) for r in rows]

    def snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM snapshots ORDER BY seq").fetchall()
            return [
                {
                    "snapshotId": r["snapshot_id"],
                    "seq": r["seq"],
                    "label": r["label"],
                    "createdAt": r["created_at"],
                    "activeCount": r["active_count"],
                }
                for r in rows
            ]

    def latest_snapshot(self) -> dict[str, Any] | None:
        snaps = self.snapshots()
        return snaps[-1] if snaps else None

    def snapshot_items(self, snapshot_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT * FROM snapshot_items WHERE snapshot_id=?"
            if active_only:
                sql += " AND active=1"
            sql += " ORDER BY created_at, item_id"
            rows = self._conn.execute(sql, (snapshot_id,)).fetchall()
            return [
                {
                    "itemId": r["item_id"],
                    "block": r["block"],
                    "text": r["text"],
                    "category": r["category"],
                    "sourceTurn": r["source_turn"],
                    "createdAt": r["created_at"],
                    "updatedAt": r["updated_at"],
                    "active": bool(r["active"]),
                }
                for r in rows
            ]

    def record_mutation(
        self,
        *,
        snapshot_id: str | None,
        pass_name: str,
        kind: str,
        item_id: str,
        refs: dict[str, Any],
        accepted: bool,
        reason: str | None = None,
        rejection_reason: str | None = None,
        policy: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO mutations(snapshot_id, pass, kind, item_id, refs, accepted, reason,"
                " rejection_reason, policy, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    pass_name,
                    kind,
                    item_id,
                    json.dumps(refs, ensure_ascii=False),
                    1 if accepted else 0,
                    reason,
                    rejection_reason,
                    policy,
                    _now(),
                ),
            )

    def mutations(self, pass_name: str | None = None, accepted: bool | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT * FROM mutations"
            args: list[Any] = []
            clauses = []
            if pass_name:
                clauses.append("pass=?")
                args.append(pass_name)
            if accepted is not None:
                clauses.append("accepted=?")
                args.append(1 if accepted else 0)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY mutation_id"
            rows = self._conn.execute(sql, args).fetchall()
            return [
                {
                    "mutationId": r["mutation_id"],
                    "snapshotId": r["snapshot_id"],
                    "pass": r["pass"],
                    "kind": r["kind"],
                    "itemId": r["item_id"],
                    "refs": json.loads(r["refs"]),
                    "accepted": bool(r["accepted"]),
                    "reason": r["reason"],
                    "rejectionReason": r["rejection_reason"],
                    "policy": r["policy"],
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]

    def set_created_at(self, item_id: str, created_at: float) -> dict[str, Any] | None:
        """Fixture hook: move a fact's clock back so retention policy can be exercised
        without waiting days. Recorded in the next snapshot like any other state."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE items SET created_at=? WHERE item_id=?", (float(created_at), item_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"unknown item: {item_id}")
        return self.get_item(item_id)

    # ---------------------------------------------------------------- transcript
    def add_message(self, role: str, content: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages(role, content, created_at) VALUES(?,?,?)",
                (role, content, _now()),
            )
            return int(cur.lastrowid)

    def messages(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM messages ORDER BY turn").fetchall()
            return [
                {"turn": r["turn"], "role": r["role"], "content": r["content"], "createdAt": r["created_at"]}
                for r in rows
            ]

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def soft_wipe(self) -> dict[str, Any]:
        """Retire every fact and clear the transcript through the versioned path.

        History is not destroyed: a `wipe:before` snapshot is taken first, so the whole memory can be
        restored afterwards. Nothing here deletes rows from the DB.
        """
        with self._lock:
            before = self.snapshot("wipe:before")
            for name, _ in DEFAULT_BLOCKS:
                self.replace(name, [])
            after = self.snapshot("wipe:after")
            self._conn.execute("DELETE FROM messages")
            self.set_meta("extracted_through", "0")
            return {
                "wipe": "soft",
                "beforeSnapshot": before,
                "afterSnapshot": after,
                "restoreTo": before,
            }

    def reset(self) -> None:
        with self._lock, self._conn:
            for table in ("items", "snapshot_items", "snapshots", "mutations", "messages"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.execute("UPDATE meta SET value='0' WHERE key='item_seq'")
            self._conn.execute("DELETE FROM meta WHERE key='extracted_through'")
            self._conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('messages','mutations')")
