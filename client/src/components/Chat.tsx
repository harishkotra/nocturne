import { useEffect, useRef, useState } from "react";
import type { FactsEvent, Message } from "../lib/types";
import { Badge, Empty } from "../lib/ui";

type Props = {
  messages: Message[];
  streaming: string;
  pendingUser: string | null;
  thinking: boolean;
  encoding: boolean;
  blocked: null | string;
  error: { error: string; status?: number | null; body?: string } | null;
  facts: FactsEvent | null;
  onSend: (text: string) => void;
};

export default function Chat({
  messages,
  streaming,
  pendingUser,
  thinking,
  encoding,
  blocked,
  error,
  facts,
  onSend,
}: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, streaming, pendingUser]);

  const submit = () => {
    const text = draft.trim();
    if (!text || thinking || blocked) return;
    setDraft("");
    onSend(text);
  };

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border border-edge bg-panel/80">
      <header className="flex items-center justify-between border-b border-edge/70 px-4 py-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-white/90">Conversation</h2>
        <Badge tone="neutral">{messages.length} message{messages.length === 1 ? "" : "s"}</Badge>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !pendingUser && !streaming ? (
          <Empty>
            No conversation yet. Say something — durable facts, preferences and commitments will be pulled into the
            <span className="mx-1 font-mono text-moon/80">scratch</span> block, then consolidated when you hit Sleep.
          </Empty>
        ) : null}

        {messages.map((m) => (
          <Bubble key={m.turn} role={m.role} text={m.content} turn={m.turn} />
        ))}

        {pendingUser ? <Bubble role="user" text={pendingUser} turn={null} dim /> : null}

        {streaming ? <Bubble role="assistant" text={streaming} turn={null} caret /> : null}

        {thinking && !streaming ? (
          <div className="flex items-center gap-2 pl-1 text-xs text-dim">
            <span className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="h-1.5 w-1.5 animate-pulse rounded-full bg-moon/70"
                  style={{ animationDelay: `${i * 180}ms` }}
                />
              ))}
            </span>
            Nocturne is thinking…
          </div>
        ) : null}

        {encoding && !facts ? (
          <div className="flex items-center gap-2 pl-1 text-[11px] text-moon/70">
            <span className="h-2 w-2 animate-pulse rounded-full bg-moon/70" />
            encoding this turn into <span className="font-mono">scratch</span> — traceability-gated…
          </div>
        ) : null}

        {facts ? <FactsNote facts={facts} /> : null}

        {error ? (
          <div className="rounded-lg border border-gone/40 bg-gone/10 px-3 py-2 text-xs text-gone">
            <div className="font-semibold">Provider error</div>
            <div className="mt-1 break-words">{error.error}</div>
            {error.status ? <div className="mt-1 font-mono text-[11px] opacity-80">HTTP {error.status}</div> : null}
            {error.body ? (
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] opacity-70">
                {error.body}
              </pre>
            ) : null}
          </div>
        ) : null}

        <div ref={endRef} />
      </div>

      <footer className="border-t border-edge/70 px-4 py-3">
        {blocked ? <p className="mb-2 text-[11px] text-change">{blocked}</p> : null}
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder={blocked ? "Busy…" : "Tell Nocturne something… (Enter to send, Shift+Enter for a new line)"}
            className="min-h-[52px] flex-1 resize-y rounded-lg border border-edge bg-ink/70 px-3 py-2 text-sm text-white/90 outline-none placeholder:text-dim/50 focus:border-moon/50"
            disabled={Boolean(blocked) || thinking}
          />
          <button
            type="button"
            onClick={submit}
            disabled={Boolean(blocked) || thinking || !draft.trim()}
            className="h-[52px] rounded-lg border border-moon/40 bg-moon/15 px-4 text-sm font-semibold text-moon transition enabled:hover:bg-moon/25 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </footer>
    </div>
  );
}

function Bubble({
  role,
  text,
  turn,
  dim,
  caret,
}: {
  role: "user" | "assistant";
  text: string;
  turn: number | null;
  dim?: boolean;
  caret?: boolean;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl border px-3 py-2 text-sm leading-relaxed ${
          isUser
            ? "border-moon/25 bg-moon/10 text-white/90"
            : "border-edge bg-ink/60 text-white/80"
        } ${dim ? "opacity-60" : ""}`}
      >
        <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-dim">
          {isUser ? "You" : "Nocturne"}
          {turn !== null ? <span className="font-mono normal-case tracking-normal">t{turn}</span> : null}
        </div>
        <p className={`whitespace-pre-wrap ${caret ? "caret" : ""}`}>{text}</p>
      </div>
    </div>
  );
}

function FactsNote({ facts }: { facts: FactsEvent }) {
  const accepted = facts.accepted?.length ?? 0;
  const rejected = facts.rejected?.length ?? 0;
  return (
    <div className="ml-auto max-w-[85%] rounded-xl border border-edge bg-ink/40 px-3 py-2 text-[11px] leading-relaxed text-dim">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold uppercase tracking-wider text-white/70">memory written</span>
        <Badge tone="keep">{accepted} traceable fact{accepted === 1 ? "" : "s"}</Badge>
        {rejected ? <Badge tone="warn">{rejected} refused by the encoder</Badge> : null}
        {facts.error ? <Badge tone="gone">encoder error</Badge> : null}
      </div>
      {facts.accepted?.length ? (
        <ul className="mt-1.5 space-y-1">
          {facts.accepted.map((f) => (
            <li key={f.itemId ?? f.text} className="flex gap-2">
              <span className="font-mono text-moon/70">{f.itemId}</span>
              <span className="text-white/70">{f.text}</span>
              <Badge tone="neutral">{f.category}</Badge>
            </li>
          ))}
        </ul>
      ) : null}
      {facts.rejected?.length ? (
        <ul className="mt-1.5 space-y-1 text-gone/80">
          {facts.rejected.slice(0, 3).map((r, i) => (
            <li key={i} className="break-words">
              ✗ {r.rejectionReason}
            </li>
          ))}
        </ul>
      ) : null}
      {facts.error ? <p className="mt-1 break-words text-gone">{facts.error.error}</p> : null}
    </div>
  );
}
