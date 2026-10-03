import { useState } from "react";
import type { Snapshot } from "../lib/types";
import { Badge, Empty, fmtClock, Panel } from "../lib/ui";

type Props = { curve: Snapshot[]; maxActiveFacts: number };

const W = 720;
const H = 180;
const PAD = { top: 16, right: 16, bottom: 26, left: 34 };

export default function ForgettingCurve({ curve, maxActiveFacts }: Props) {
  const [hover, setHover] = useState<number | null>(null);

  if (curve.length < 2) {
    return (
      <Panel title="Forgetting curve" subtitle="Active fact count per stored version, oldest to newest.">
        <Empty>
          {curve.length === 0
            ? "No versions stored yet. They appear as soon as you chat or sleep."
            : "One version so far — run Sleep to produce a second point on the curve."}
        </Empty>
      </Panel>
    );
  }

  const counts = curve.map((s) => s.activeCount);
  const peak = Math.max(...counts, maxActiveFacts > 0 ? Math.min(maxActiveFacts, Math.max(...counts, 1)) : 1, 1);
  const xFor = (i: number) => PAD.left + (i * (W - PAD.left - PAD.right)) / Math.max(1, curve.length - 1);
  const yFor = (v: number) => H - PAD.bottom - (v / peak) * (H - PAD.top - PAD.bottom);

  const line = curve.map((s, i) => `${xFor(i).toFixed(1)},${yFor(s.activeCount).toFixed(1)}`).join(" ");
  const area = `${PAD.left},${H - PAD.bottom} ${line} ${xFor(curve.length - 1)},${H - PAD.bottom}`;
  const capY = maxActiveFacts <= peak ? yFor(maxActiveFacts) : null;
  const active = hover !== null ? curve[hover] : curve[curve.length - 1];

  return (
    <Panel
      title="Forgetting curve"
      subtitle="Deliberate forgetting shrinks the active set while every version stays in the database."
      right={
        <>
          <Badge tone="moon">{counts[counts.length - 1]} active now</Badge>
          <Badge tone="neutral">{curve.length} versions</Badge>
          <Badge tone="change" tip="MAX_ACTIVE_FACTS from the retention policy">
            cap {maxActiveFacts}
          </Badge>
        </>
      }
    >
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="active memory items per snapshot">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={yFor(peak * f)}
              y2={yFor(peak * f)}
              stroke="#1f2739"
              strokeDasharray="2 4"
            />
            <text x={4} y={yFor(peak * f) + 3} fill="#8892a8" fontSize="9" fontFamily="monospace">
              {Math.round(peak * f)}
            </text>
          </g>
        ))}

        {capY !== null ? (
          <g>
            <line x1={PAD.left} x2={W - PAD.right} y1={capY} y2={capY} stroke="#e0b352" strokeDasharray="5 4" opacity="0.7" />
            <text x={W - PAD.right - 2} y={capY - 4} fill="#e0b352" fontSize="9" textAnchor="end" fontFamily="monospace">
              MAX_ACTIVE_FACTS
            </text>
          </g>
        ) : null}

        <polygon points={area} fill="#c9b8ff" opacity="0.07" />
        <polyline points={line} fill="none" stroke="#c9b8ff" strokeWidth="1.8" strokeLinejoin="round" />

        {curve.map((s, i) => (
          <g key={s.snapshotId} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <circle
              cx={xFor(i)}
              cy={yFor(s.activeCount)}
              r={hover === i ? 4.5 : 2.6}
              fill={hover === i ? "#c9b8ff" : "#121623"}
              stroke="#c9b8ff"
              strokeWidth="1.4"
            />
            <rect x={xFor(i) - 6} y={PAD.top} width="12" height={H - PAD.top - PAD.bottom} fill="transparent" />
          </g>
        ))}

        <text x={PAD.left} y={H - 8} fill="#8892a8" fontSize="9" fontFamily="monospace">
          #{curve[0].seq} {curve[0].label}
        </text>
        <text x={W - PAD.right} y={H - 8} fill="#8892a8" fontSize="9" textAnchor="end" fontFamily="monospace">
          #{curve[curve.length - 1].seq} {curve[curve.length - 1].label}
        </text>
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-dim">
        <Badge tone="moon">{active.label}</Badge>
        <span className="font-mono">{active.snapshotId}</span>
        <span>{active.activeCount} active facts</span>
        <span>{fmtClock(active.createdAt)}</span>
        <span className="ml-auto text-dim/70">hover a point for its version</span>
      </div>
    </Panel>
  );
}
