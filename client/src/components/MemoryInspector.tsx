import { useState } from "react";
import type { MemoryItem, MemoryPayload, Policy } from "../lib/types";
import { ageLabel, Badge, Empty, Panel } from "../lib/ui";

type Props = { memory: MemoryPayload | null; policy: Policy };

export default function MemoryInspector({ memory, policy }: Props) {
  const [open, setOpen] = useState<Record<string, boolean>>({ scratch: true, human: true });

  if (!memory) return <Panel title="Memory"><Empty>Loading blocks…</Empty></Panel>;

  const protectedSet = new Set(policy.protectedCategories);

  return (
    <Panel
      title="Memory"
      subtitle={
        <>
          <span className="font-mono text-moon/80">{memory.backend === "letta" ? "letta :8283" : "sqlite fallback"}</span>
          {" · "}
          {memory.activeCount} active fact{memory.activeCount === 1 ? "" : "s"} across {memory.blocks.length} blocks,{" "}
          {memory.retiredCount} retired but retained in the DB
        </>
      }
    >
      {memory.blocks.every((b) => b.count === 0) ? (
        <Empty>
          All blocks are empty. Chat a few turns and facts land in <span className="font-mono text-moon/80">scratch</span>;
          Sleep then dedupes, merges, promotes and forgets.
        </Empty>
      ) : null}

      <div className="space-y-2">
        {memory.blocks.map((block) => {
          const expanded = Boolean(open[block.name]);
          return (
            <div key={block.name} className="rounded-lg border border-edge/70 bg-ink/40">
              <button
                type="button"
                onClick={() => setOpen((s) => ({ ...s, [block.name]: !s[block.name] }))}
                className="tip flex w-full items-center gap-3 px-3 py-2 text-left"
                data-tip={block.description}
              >
                <span className={`text-dim transition-transform ${expanded ? "rotate-90" : ""}`}>▸</span>
                <span className="font-mono text-sm text-white/85">{block.name}</span>
                <Badge tone={block.count ? "moon" : "neutral"}>{block.count} item{block.count === 1 ? "" : "s"}</Badge>
                <span className="ml-auto truncate text-[11px] text-dim/70">{block.items.length ? "" : "empty"}</span>
              </button>
              {expanded ? (
                <ul className="space-y-1 border-t border-edge/60 px-3 py-2">
                  {block.items.length === 0 ? <li className="py-2 text-xs text-dim/70">Nothing in this block.</li> : null}
                  {block.items.map((item) => (
                    <li key={item.itemId} className="flex flex-wrap items-start gap-2 rounded-md px-1 py-1 hover:bg-white/5">
                      <span className="mt-0.5 font-mono text-[11px] text-dim">{item.itemId}</span>
                      <span className="flex-1 text-[13px] leading-snug text-white/85">{item.text}</span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <Badge tone={protectedSet.has(item.category) ? "keep" : "neutral"} tip={`category: ${item.category}`}>
                          {item.category}
                          {protectedSet.has(item.category) ? " · protected" : ""}
                        </Badge>
                        <StalenessBadge item={item} staleAfterDays={policy.staleAfterDays} />
                        {item.sourceTurn !== null ? <Badge tone="neutral" tip={`extracted from transcript turn ${item.sourceTurn}`}>t{item.sourceTurn}</Badge> : null}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function StalenessBadge({ item, staleAfterDays }: { item: MemoryItem; staleAfterDays: number }) {
  const threshold = Number.isInteger(staleAfterDays) ? String(staleAfterDays) : staleAfterDays.toFixed(1);
  if (item.ageDays > staleAfterDays) {
    return (
      <Badge tone="gone" tip={`older than STALE_AFTER_DAYS=${threshold} — eligible for the Forget pass`}>
        stale {ageLabel(item.ageDays)}
      </Badge>
    );
  }
  if (item.ageDays > staleAfterDays / 2) {
    return (
      <Badge tone="warn" tip={`approaching the stale threshold (${item.ageDays.toFixed(1)} of ${threshold} days)`}>
        aging {ageLabel(item.ageDays)}
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" tip={`age ${item.ageDays.toFixed(2)} days`}>
      {ageLabel(item.ageDays)}
    </Badge>
  );
}
