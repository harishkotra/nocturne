import type { DiffResult, Snapshot } from "../lib/types";
import { Badge, Empty, fmtDateTime, Panel } from "../lib/ui";

type Props = {
  snapshots: Snapshot[];
  from: string | null;
  to: string | null;
  setFrom: (id: string) => void;
  setTo: (id: string) => void;
  result: DiffResult | null;
  loading: boolean;
  error: string | null;
  restoring: string | null;
  onRestore: (snapshotId: string) => void;
};

export default function DiffView({
  snapshots,
  from,
  to,
  setFrom,
  setTo,
  result,
  loading,
  error,
  restoring,
  onRestore,
}: Props) {
  const count = (id: string | null) => snapshots.find((s) => s.snapshotId === id)?.activeCount ?? 0;

  return (
    <Panel
      title="Diff"
      subtitle="Per-item consequences of a consolidation run. Every row carries the reason its mutation was accepted; refusals are listed too — nothing is silently dropped."
      right={
        <>
          <Badge tone="keep">{result ? result.counts.kept : 0} kept</Badge>
          <Badge tone="change">{result ? result.counts.changed : 0} changed</Badge>
          <Badge tone="gone">{result ? result.counts.forgotten : 0} forgotten</Badge>
          <Badge tone="warn">{result ? result.counts.rejected : 0} rejected</Badge>
        </>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <label className="text-dim">from</label>
        <VersionSelect snapshots={snapshots} value={from} onChange={setFrom} />
        <label className="text-dim">to</label>
        <VersionSelect snapshots={snapshots} value={to} onChange={setTo} />
        <span className="font-mono text-dim/80">
          {count(from)} → {count(to)} active
        </span>
        {from ? (
          <button
            type="button"
            onClick={() => onRestore(from)}
            disabled={Boolean(restoring)}
            className="ml-auto rounded-md border border-moon/40 bg-moon/10 px-2.5 py-1 text-[11px] font-semibold text-moon transition enabled:hover:bg-moon/20 disabled:opacity-40"
          >
            {restoring === from ? "restoring…" : `restore “${from}”`}
          </button>
        ) : null}
      </div>

      {error ? <p className="mb-3 rounded-md border border-gone/40 bg-gone/10 px-3 py-2 text-xs text-gone">{error}</p> : null}
      {loading ? <Empty>Computing the diff…</Empty> : null}

      {!loading && !result && snapshots.length < 2 ? (
        <Empty>Two versions are needed to diff. Chat a few turns and hit Sleep.</Empty>
      ) : null}

      {result ? (
        <div className="grid gap-3 lg:grid-cols-3">
          <Column tone="keep" title="Kept" hint="unchanged between these versions" rows={result.kept.map((i) => ({
            key: i.itemId,
            id: i.itemId,
            text: i.text,
            tag: i.block,
            reason: `untouched by every pass in this range (${i.category})`,
          }))} />

          <Column
            tone="change"
            title="Changed"
            hint="merged and promoted items"
            rows={result.changed.map((c) => ({
              key: c.itemId,
              id: c.itemId,
              text: c.after.text,
              tag: c.kind === "merged" ? "merged" : `promoted → ${c.toBlock}`,
              reason:
                `${c.pass ?? ""} · ${c.reason}` +
                (c.kind === "merged" && c.sources.length
                  ? `\nsources:\n${c.fromIds.map((f, i) => `${f}: ${c.sources[i] ?? ""}`).join("\n")}`
                  : c.kind === "promoted"
                    ? `\n${c.fromBlock ?? "scratch"} → ${c.toBlock}`
                    : ""),
              before: c.kind === "merged" ? c.before.text : undefined,
            }))}
          />

          <Column
            tone="gone"
            title="Forgotten"
            hint="removed from the active view (rows stay in the DB)"
            rows={result.forgotten.map((f) => ({
              key: f.itemId,
              id: f.itemId,
              text: f.text,
              tag: `${f.kind}${f.policy ? ` · ${f.policy}` : ""}`,
              reason: `${f.pass ?? "consolidation"} · ${f.reason}`,
            }))}
          />

          {result.added.length ? (
            <Column
              tone="moon"
              title="Added"
              hint="new items inside this version range"
              rows={result.added.map((i) => ({
                key: i.itemId,
                id: i.itemId,
                text: i.text,
                tag: i.block,
                reason: `appended after the “from” version (${i.category})`,
              }))}
            />
          ) : null}

          {result.rejected.length ? (
            <div className="lg:col-span-3">
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                Rejected mutations ({result.rejected.length})
              </h3>
              <ul className="space-y-1 rounded-lg border border-orange-400/25 bg-orange-400/5 px-3 py-2">
                {result.rejected.map((r) => (
                  <li key={r.mutationId} className="flex flex-wrap items-start gap-2 text-[12px]">
                    <Badge tone="warn">{r.pass}</Badge>
                    <span className="font-mono text-[11px] text-dim">{r.itemId}</span>
                    <span className="flex-1 text-orange-200/90">{r.rejectionReason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="lg:col-span-3 text-[11px] text-dim/70">
              No proposal in this range was refused by the code-side gates.
            </p>
          )}
        </div>
      ) : null}
    </Panel>
  );
}

function VersionSelect({
  snapshots,
  value,
  onChange,
}: {
  snapshots: Snapshot[];
  value: string | null;
  onChange: (id: string) => void;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className="max-w-[260px] rounded-md border border-edge bg-ink/70 px-2 py-1 font-mono text-[11px] text-white/85 outline-none focus:border-moon/50"
    >
      {snapshots.length === 0 ? <option value="">no versions</option> : null}
      {[...snapshots].reverse().map((s) => (
        <option key={s.snapshotId} value={s.snapshotId}>
          #{s.seq} {s.label} · {s.activeCount} · {fmtDateTime(s.createdAt)}
        </option>
      ))}
    </select>
  );
}

type Row = { key: string; id: string; text: string; tag: string; reason: string; before?: string };

function Column({
  tone,
  title,
  hint,
  rows,
}: {
  tone: "keep" | "change" | "gone" | "moon";
  title: string;
  hint: string;
  rows: Row[];
}) {
  const border = { keep: "border-keep/30", change: "border-change/30", gone: "border-gone/30", moon: "border-moon/30" }[tone];
  const text = { keep: "text-keep", change: "text-change", gone: "text-gone", moon: "text-moon" }[tone];
  return (
    <div className={`rounded-lg border ${border} bg-ink/40 p-3`}>
      <h3 className={`text-[11px] font-semibold uppercase tracking-[0.14em] ${text}`}>
        {title} <span className="font-mono text-dim">({rows.length})</span>
      </h3>
      <p className="mt-0.5 text-[11px] text-dim/70">{hint}</p>
      {rows.length === 0 ? (
        <p className="mt-3 text-[11px] text-dim/60">none</p>
      ) : (
        <ul className="mt-2 max-h-[280px] space-y-2 overflow-y-auto pr-1">
          {rows.map((r) => (
            <li
              key={r.key}
              data-tip={r.reason}
              className={`tip cursor-help rounded-md border ${border} bg-panel/60 px-2 py-1.5 text-[12px]`}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-dim">{r.id}</span>
                <Badge tone={tone}>{r.tag}</Badge>
              </div>
              <p className="mt-1 leading-snug text-white/85">{r.text}</p>
              {r.before ? <p className="mt-1 leading-snug text-dim/70 line-through">{r.before}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
