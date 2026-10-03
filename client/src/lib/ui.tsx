import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  right,
  children,
  className = "",
}: {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-edge bg-panel/80 backdrop-blur-sm ${className}`}>
      <header className="flex items-start justify-between gap-3 border-b border-edge/70 px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-white/90">{title}</h2>
          {subtitle ? <p className="mt-1 text-xs leading-relaxed text-dim">{subtitle}</p> : null}
        </div>
        {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  neutral: "bg-white/5 text-white/70 border-white/10",
  keep: "bg-keep/10 text-keep border-keep/30",
  change: "bg-change/10 text-change border-change/30",
  gone: "bg-gone/10 text-gone border-gone/30",
  moon: "bg-moon/10 text-moon border-moon/30",
  warn: "bg-orange-400/10 text-orange-300 border-orange-400/30",
};

export function Badge({
  children,
  tone = "neutral",
  tip,
  className = "",
}: {
  children: ReactNode;
  tone?: keyof typeof TONES | string;
  tip?: string;
  className?: string;
}) {
  const tipProps = tip ? ({ "data-tip": tip } as Record<string, string>) : {};
  return (
    <span
      {...tipProps}
      className={`tip inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-tight ${
        TONES[tone] ?? TONES.neutral
      } ${tip ? "cursor-help" : ""} ${className}`}
    >
      {children}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-dim">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-moon/30 border-t-moon" />
      {label}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-xs leading-relaxed text-dim/80">{children}</p>;
}

export function fmtClock(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

export function ageLabel(days: number): string {
  if (days < 1 / 24) return `${Math.round(days * 1440)}m`;
  if (days < 1) return `${Math.round(days * 24)}h`;
  return `${days.toFixed(days < 10 ? 1 : 0)}d`;
}
