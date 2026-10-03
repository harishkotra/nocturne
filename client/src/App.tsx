import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Chat from "./components/Chat";
import DiffView from "./components/DiffView";
import ForgettingCurve from "./components/ForgettingCurve";
import MemoryInspector from "./components/MemoryInspector";
import Quiz from "./components/Quiz";
import SettingsPanel from "./components/Settings";
import { ApiError, fetchDiff, getHealth, getMemory, postSse, restoreSnapshot, resetAll, testConnection } from "./lib/api";
import { SETTINGS_KEY, loadSettings, mergeHealthDefaults, saveSettings } from "./lib/settings";
import type {
  ConnectionTest,
  DiffResult,
  FactsEvent,
  Health,
  MemoryPayload,
  PassEvent,
  QuizResult,
  Settings,
  SleepResult,
} from "./lib/types";
import { Badge, fmtClock, Spinner } from "./lib/ui";

const PASSES = [
  { name: "dedupe", label: "Dedupe", blurb: "identical facts collapse into one survivor" },
  { name: "merge", label: "Merge", blurb: "related facts fold into a traceable rewrite" },
  { name: "promote", label: "Promote", blurb: "durable facts move out of scratch" },
  { name: "forget", label: "Forget", blurb: "stale or over-capacity facts leave the active view" },
];

type SleepState = { running: boolean; dryRun: boolean; passes: PassEvent[]; result: SleepResult | null; error: string | null };
type WakeState = {
  running: boolean;
  progress: { kind: string; itemId: string; correct?: boolean; question?: string }[];
  result: QuizResult | null;
  error: string | null;
};

type Tab = "memory" | "diff" | "curve" | "quiz" | "settings";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [memory, setMemory] = useState<MemoryPayload | null>(null);
  const [settings, setSettings] = useState<Settings>(() => loadSettings());
  const [tab, setTab] = useState<Tab>("memory");

  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [streaming, setStreaming] = useState("");
  const [thinking, setThinking] = useState(false);
  const [encoding, setEncoding] = useState(false);
  const [chatError, setChatError] = useState<{ error: string; status?: number | null; body?: string } | null>(null);
  const [facts, setFacts] = useState<FactsEvent | null>(null);

  const [sleep, setSleep] = useState<SleepState>({ running: false, dryRun: false, passes: [], result: null, error: null });
  const [wake, setWake] = useState<WakeState>({ running: false, progress: [], result: null, error: null });

  const [diff, setDiff] = useState<{ from: string | null; to: string | null; result: DiffResult | null; loading: boolean; error: string | null }>(
    { from: null, to: null, result: null, loading: false, error: null },
  );

  const [restoring, setRestoring] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTest | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const hadStoredSettings = useRef<boolean>(
    typeof localStorage !== "undefined" && localStorage.getItem(SETTINGS_KEY) !== null,
  );

  const refreshMemory = useCallback(async () => {
    try {
      setMemory(await getMemory());
    } catch (err) {
      setBootError(messageOf(err));
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [h, m] = await Promise.all([getHealth(), getMemory()]);
        setHealth(h);
        setMemory(m);
        if (!hadStoredSettings.current) {
          const merged = mergeHealthDefaults(settings, h);
          setSettings(merged);
          saveSettings(merged);
        }
        const snaps = m.snapshots;
        if (snaps.length >= 2) {
          setDiff((d) => ({ ...d, from: snaps[snaps.length - 2].snapshotId, to: snaps[snaps.length - 1].snapshotId }));
        }
      } catch (err) {
        setBootError(`backend unreachable: ${messageOf(err)} — start it with ./scripts/server.sh`);
      }
    })();
  }, []);

  useEffect(() => {
    saveSettings(settings);
  }, [settings]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!diff.from || !diff.to || diff.from === diff.to) return;
    let cancelled = false;
    setDiff((d) => ({ ...d, loading: true, error: null }));
    fetchDiff(diff.from, diff.to)
      .then((result) => {
        if (!cancelled) setDiff((d) => ({ ...d, result, loading: false }));
      })
      .catch((err) => {
        if (!cancelled) setDiff((d) => ({ ...d, error: messageOf(err), loading: false }));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diff.from, diff.to]);

  const payload = () => ({ provider: settings.provider, policy: settings.policy });
  const busy = sleep.running || wake.running || thinking;
  const blocked = sleep.running
    ? settings.dryRun
      ? "Dry run in progress — the store is untouched."
      : "Nocturne is asleep; the store is being rewritten."
    : wake.running
      ? "Wake quiz running; chat is paused."
      : null;

  // ------------------------------------------------------------------ chat
  async function sendChat(text: string) {
    setPendingUser(text);
    setStreaming("");
    setThinking(true);
    setChatError(null);
    setFacts(null);
    try {
      await postSse("/api/chat", { ...payload(), message: text }, (event, data) => {
        if (event === "delta") setStreaming((s) => s + String(data.text ?? ""));
        else if (event === "reply") {
          setPendingUser(null);
          setStreaming("");
          setThinking(false);
          setEncoding(true);
        } else if (event === "facts") {
          setFacts(data as unknown as FactsEvent);
          setEncoding(false);
        } else if (event === "error") {
          setChatError(data as { error: string; status?: number; body?: string });
          setEncoding(false);
        }
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") setChatError({ error: messageOf(err) });
    } finally {
      setPendingUser(null);
      setStreaming("");
      setThinking(false);
      setEncoding(false);
      await refreshMemory();
    }
  }

  // ------------------------------------------------------------------ sleep
  async function runSleep() {
    setTab("memory");
    setSleep({ running: true, dryRun: settings.dryRun, passes: [], result: null, error: null });
    const collected = new Map<string, PassEvent>();
    try {
      await postSse("/api/sleep", { ...payload(), dryRun: settings.dryRun }, (event, data) => {
        if (event === "start") setSleep((s) => ({ ...s, dryRun: Boolean(data.dryRun) }));
        else if (event.startsWith("pass:")) {
          const pass = data as unknown as PassEvent;
          collected.set(pass.pass, pass);
          setSleep((s) => ({ ...s, passes: PASSES.map((p) => collected.get(p.name)).filter(Boolean) as PassEvent[] }));
        } else if (event === "result") {
          const result = data as unknown as SleepResult;
          setSleep((s) => ({ ...s, result, passes: result.passes }));
          if (result.memory) setMemory(result.memory);
          const bounds = passBounds(result.passes);
          if (bounds) {
            setDiff((d) => ({ ...d, from: bounds.from, to: bounds.to, result: null, error: null }));
            setTab("diff");
          }
        } else if (event === "error") {
          setSleep((s) => ({ ...s, error: String(data.error ?? "consolidation failed") }));
        }
      });
    } catch (err) {
      setSleep((s) => ({ ...s, error: messageOf(err) }));
    } finally {
      setSleep((s) => ({ ...s, running: false }));
      await refreshMemory();
    }
  }

  // ------------------------------------------------------------------ wake
  async function runWake() {
    setTab("quiz");
    setWake({ running: true, progress: [], result: null, error: null });
    try {
      await postSse("/api/wake", { ...payload(), count: 5 }, (event, data) => {
        if (event === "question") {
          setWake((w) => ({
            ...w,
            progress: [...w.progress, { kind: "question", itemId: String(data.itemId), correct: Boolean(data.correct), question: String(data.question ?? "") }],
          }));
        } else if (event === "forgotten") {
          setWake((w) => ({ ...w, progress: [...w.progress, { kind: "forgotten", itemId: String(data.itemId), correct: Boolean(data.passed) }] }));
        } else if (event === "result") {
          setWake((w) => ({ ...w, result: data as unknown as QuizResult }));
        } else if (event === "error") {
          setWake((w) => ({ ...w, error: String(data.error ?? "quiz failed") }));
        }
      });
    } catch (err) {
      setWake((w) => ({ ...w, error: messageOf(err) }));
    } finally {
      setWake((w) => ({ ...w, running: false }));
    }
  }

  // ------------------------------------------------------------------ restore / settings
  async function doRestore(snapshotId: string) {
    setRestoring(snapshotId);
    try {
      const result = await restoreSnapshot(snapshotId);
      setMemory(result.memory);
      setToast(`restored ${result.restoredFrom} → new version ${result.snapshotId} (${result.activeCount} active facts)`);
      setTab("memory");
      await loadLatestDiff();
    } catch (err) {
      setToast(`restore failed: ${messageOf(err)}`);
    } finally {
      setRestoring(null);
    }
  }

  async function loadLatestDiff() {
    const m = await getMemory();
    setMemory(m);
    const snaps = m.snapshots;
    if (snaps.length >= 2) setDiff((d) => ({ ...d, from: snaps[snaps.length - 2].snapshotId, to: snaps[snaps.length - 1].snapshotId }));
  }

  async function runTest() {
    setTesting(true);
    setTestError(null);
    setTestResult(null);
    try {
      setTestResult(await testConnection(settings.provider));
    } catch (err) {
      setTestError(messageOf(err));
    } finally {
      setTesting(false);
    }
  }

  async function wipe() {
    if (!window.confirm("Clear the transcript and retire every active fact? The versions stay in the database.")) return;
    try {
      const result = await resetAll();
      setMemory(result.memory);
      setFacts(null);
      setDiff({ from: null, to: null, result: null, loading: false, error: null });
      setSleep({ running: false, dryRun: false, passes: [], result: null, error: null });
      setWake({ running: false, progress: [], result: null, error: null });
      setToast(
        result.wipe === "hard"
          ? "transcript, facts and versions deleted"
          : `facts retired — restore the pre-wipe version ${result.restoreTo ?? ""} from the Diff panel`,
      );
    } catch (err) {
      setToast(`wipe failed: ${messageOf(err)}`);
    }
  }

  const curve = memory?.curve ?? [];
  const lastLabel = useMemo(() => (curve.length ? curve[curve.length - 1].label : "no version"), [curve]);

  return (
    <div className="mx-auto flex h-full max-w-[1600px] flex-col gap-3 p-3 lg:p-4">
      <header className="flex flex-wrap items-center gap-3 rounded-xl border border-edge bg-panel/80 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="grid h-8 w-8 place-items-center rounded-lg border border-moon/40 bg-moon/10 font-mono text-moon">N</span>
          <div>
            <h1 className="text-[15px] font-semibold leading-none tracking-tight">Nocturne</h1>
            <p className="mt-1 text-[11px] text-dim">sleeptime consolidation of its own memory</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral" tip={health?.db}>{health?.backend ?? "…"}</Badge>
          <Badge tone="moon" tip={`${health?.provider.baseUrl ?? ""}`}>{settings.provider.model || "no model"}</Badge>
          <Badge tone="keep">{memory?.activeCount ?? 0} active facts</Badge>
          <Badge tone="neutral" tip={`${curve.length} versions stored`}>{curve.length} versions</Badge>
          <Badge tone="neutral" tip={`latest snapshot label: ${lastLabel}`}>{lastLabel}</Badge>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {settings.dryRun ? <Badge tone="warn" tip="Sleep computes a plan without applying it">dry run</Badge> : null}
          <button
            type="button"
            onClick={runSleep}
            disabled={busy || !memory}
            className={`tip rounded-lg border px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
              settings.dryRun ? "border-change/50 bg-change/15 text-change hover:bg-change/25" : "border-moon/50 bg-moon/15 text-moon hover:bg-moon/25"
            }`}
            data-tip={settings.dryRun ? "compute the plan, change nothing" : "dedupe → merge → promote → forget, with a snapshot before and after each pass"}
          >
            {sleep.running ? "Sleeping…" : settings.dryRun ? "Dry run" : "Sleep"}
          </button>
          <button
            type="button"
            onClick={runWake}
            disabled={busy || !memory || (memory?.activeCount ?? 0) === 0}
            className="rounded-lg border border-keep/40 bg-keep/10 px-4 py-2 text-sm font-semibold text-keep transition enabled:hover:bg-keep/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {wake.running ? "Quizzing…" : "Wake"}
          </button>
        </div>
      </header>

      {bootError ? (
        <div className="rounded-xl border border-gone/40 bg-gone/10 px-4 py-3 text-sm text-gone">
          <span className="font-semibold">Backend unreachable.</span> {bootError}
        </div>
      ) : null}

      {settings.dryRun ? (
        <div className="rounded-lg border border-change/40 bg-change/10 px-4 py-2 text-[12px] text-change">
          Dry run is on: Sleep computes the full plan — including every rejected proposal and its reason — and writes
          nothing to the active memory view.
        </div>
      ) : null}

      {sleep.running || sleep.result || sleep.error ? (
        <SleepProgress state={sleep} restoring={restoring} onRestore={doRestore} />
      ) : null}

      <main className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(360px,1fr)_1.35fr]">
        <div className="min-h-[420px] xl:min-h-0">
          <Chat
            messages={memory?.messages ?? []}
            streaming={streaming}
            pendingUser={pendingUser}
            thinking={thinking}
            encoding={encoding}
            blocked={blocked}
            error={chatError}
            facts={facts}
            onSend={sendChat}
          />
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <nav className="flex flex-wrap gap-1 rounded-xl border border-edge bg-panel/60 p-1">
            {(
              [
                ["memory", `Memory ${memory ? `(${memory.activeCount})` : ""}`],
                ["diff", "Diff"],
                ["curve", "Forgetting curve"],
                ["quiz", "Wake quiz"],
                ["settings", "Settings"],
              ] as [Tab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`rounded-lg px-3 py-1.5 text-[12px] font-medium transition ${
                  tab === key ? "bg-moon/15 text-moon" : "text-dim hover:bg-white/5 hover:text-white/80"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            {tab === "memory" && memory ? <MemoryInspector memory={memory} policy={settings.policy} /> : null}
            {tab === "diff" ? (
              <DiffView
                snapshots={memory?.snapshots ?? []}
                from={diff.from}
                to={diff.to}
                setFrom={(id) => setDiff((d) => ({ ...d, from: id }))}
                setTo={(id) => setDiff((d) => ({ ...d, to: id }))}
                result={diff.result}
                loading={diff.loading}
                error={diff.error}
                restoring={restoring}
                onRestore={doRestore}
              />
            ) : null}
            {tab === "curve" ? <ForgettingCurve curve={curve} maxActiveFacts={settings.policy.maxActiveFacts} /> : null}
            {tab === "quiz" ? (
              <Quiz result={wake.result} running={wake.running} progress={wake.progress} error={wake.error} onRun={runWake} />
            ) : null}
            {tab === "settings" ? (
              <SettingsPanel
                settings={settings}
                onChange={setSettings}
                health={health}
                testing={testing}
                testResult={testResult}
                testError={testError}
                onTest={runTest}
                onReset={wipe}
              />
            ) : null}
          </div>
        </div>
      </main>

      <footer className="flex flex-wrap items-center justify-center gap-2 px-4 pb-1 text-[11px] text-dim/70">
        <span>Built By</span>
        <a
          href="https://harishkotra.me"
          target="_blank"
          rel="noreferrer noopener"
          className="text-moon/80 underline-offset-2 hover:underline hover:text-moon"
        >
          Harish Kotra
        </a>
        <span className="text-dim/40">·</span>
        <a
          href="https://dailybuild.xyz"
          target="_blank"
          rel="noreferrer noopener"
          className="text-moon/80 underline-offset-2 hover:underline hover:text-moon"
        >
          Checkout my other builds
        </a>
      </footer>

      {toast ? (
        <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-moon/40 bg-panel px-4 py-2 text-[12px] text-moon shadow-xl">
          {toast}
        </div>
      ) : null}
    </div>
  );
}

function passBounds(passes: PassEvent[]): { from: string; to: string } | null {
  const withIds = passes.filter((p) => p.beforeSnapshot && p.afterSnapshot);
  if (!withIds.length) return null;
  const from = withIds[0].beforeSnapshot as string;
  const to = withIds[withIds.length - 1].afterSnapshot as string;
  return from === to ? null : { from, to };
}

function SleepProgress({
  state,
  restoring,
  onRestore,
}: {
  state: SleepState;
  restoring: string | null;
  onRestore: (id: string) => void;
}) {
  return (
    <section className={`rounded-xl border bg-panel/80 px-4 py-3 ${state.error ? "border-gone/40" : "border-moon/30"}`}>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-white/90">
          {state.dryRun ? "Dry run plan" : "Sleep"}
        </h2>
        {state.running ? <Spinner label="consolidating…" /> : null}
        {state.result ? (
          <Badge tone={state.result.failed ? "warn" : "keep"}>
            {state.result.totals.accepted} accepted · {state.result.totals.rejected} rejected
          </Badge>
        ) : null}
        {state.result ? (
          <span className="font-mono text-[11px] text-dim">
            {state.result.activeCountBefore} → {state.result.activeCountAfter} active facts
            {state.result.finishedAt ? ` · ${fmtClock(state.result.finishedAt)}` : ""}
          </span>
        ) : null}
        {state.error ? <span className="text-[12px] text-gone">{state.error}</span> : null}
        {state.result?.startSnapshot && !state.result.dryRun ? (
          <button
            type="button"
            onClick={() => onRestore(state.result!.startSnapshot as string)}
            disabled={Boolean(restoring)}
            className="tip ml-auto rounded-md border border-edge bg-ink/60 px-2.5 py-1 text-[11px] text-white/70 transition enabled:hover:border-gone/40 enabled:hover:text-gone disabled:opacity-40"
            data-tip={`live view currently holds ${state.result.activeCountAfter} facts; this rewinds it to the ${state.result.activeCountBefore} kept at ${state.result.startSnapshot}`}
          >
            {restoring === state.result.startSnapshot ? "restoring…" : "undo this sleep"}
          </button>
        ) : null}
      </div>

      <ol className={`mt-3 grid gap-2 ${state.running ? "sleeping" : ""} sm:grid-cols-2 lg:grid-cols-4`}>
        {PASSES.map((p) => {
          const ev = state.passes.find((x) => x.pass === p.name);
          const tone = !ev ? "idle" : ev.phase === "error" ? "error" : ev.phase === "start" ? "running" : "done";
          return (
            <li
              key={p.name}
              data-tip={p.blurb}
              className={`tip rounded-lg border px-3 py-2 text-[12px] transition ${
                tone === "error"
                  ? "border-gone/40 bg-gone/10"
                  : tone === "running"
                    ? "border-moon/50 bg-moon/10"
                    : tone === "done"
                      ? "border-keep/30 bg-keep/5"
                      : "border-edge bg-ink/40 opacity-60"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-white/85">{p.label}</span>
                <span className="font-mono text-[11px] text-dim">{ev ? `${ev.inputCount} in` : "waiting"}</span>
              </div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <Badge tone={tone === "error" ? "gone" : "neutral"}>{ev ? `proposed ${ev.proposed}` : "—"}</Badge>
                <Badge tone="keep">{ev ? `ok ${ev.accepted}` : "0"}</Badge>
                <Badge tone={ev && ev.rejected ? "warn" : "neutral"}>{ev ? `rej ${ev.rejected}` : "0"}</Badge>
              </div>
              {ev?.enforced ? <p className="mt-1 text-[11px] text-change">{ev.enforced} forced by the policy engine</p> : null}
              {ev ? (
                <div className="mt-1.5 h-1 overflow-hidden rounded bg-ink/70">
                  <div
                    className="bar h-full rounded bg-moon/60"
                    style={{ width: `${Math.min(100, ((ev.activeCountAfter ?? ev.inputCount) / Math.max(1, ev.inputCount)) * 100)}%` }}
                  />
                </div>
              ) : null}
              {ev ? (
                <p className="mt-1 truncate font-mono text-[10px] text-dim/70">
                  {ev.activeCountAfter} active{ev.afterSnapshot ? ` · ${ev.afterSnapshot}` : ""}
                </p>
              ) : null}
              {ev?.error ? <p className="mt-1 break-words text-[11px] text-gone">{ev.error}</p> : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.status ? `HTTP ${err.status}: ${err.message}` : err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
