import type { ReactNode } from "react";
import type { ConnectionTest, Health, Settings } from "../lib/types";
import { FALLBACK_PRESETS, PRESET_ORDER } from "../lib/settings";
import { Badge, Panel } from "../lib/ui";

const ALL_CATEGORIES = ["identity", "preferences", "commitments", "event", "project", "technical", "context", "ephemeral"];

type Props = {
  settings: Settings;
  onChange: (next: Settings) => void;
  health: Health | null;
  testing: boolean;
  testResult: ConnectionTest | null;
  testError: string | null;
  onTest: () => void;
  onReset: () => void;
};

export default function SettingsPanel({
  settings,
  onChange,
  health,
  testing,
  testResult,
  testError,
  onTest,
  onReset,
}: Props) {
  const { provider, policy, dryRun } = settings;
  const presets = PRESET_ORDER.map((key) => ({
    key,
    ...(health?.presets?.[key] ?? FALLBACK_PRESETS[key] ?? { label: key, baseUrl: "", model: "", needsKey: "true" }),
  }));

  const setProvider = (patch: Partial<Settings["provider"]>) =>
    onChange({ ...settings, provider: { ...provider, ...patch } });
  const setPolicy = (patch: Partial<Settings["policy"]>) => onChange({ ...settings, policy: { ...policy, ...patch } });

  return (
    <Panel
      title="Settings"
      subtitle={
        <>
          Stored in localStorage as <span className="font-mono text-moon/80">nocturne.settings.v1</span>. The API key
          travels with each request and is never returned to the client.
        </>
      }
      right={
        <>
          <Badge tone={dryRun ? "warn" : "keep"}>{dryRun ? "dry run" : "applies for real"}</Badge>
          <Badge tone="neutral">{health?.backend ?? "…"}</Badge>
        </>
      }
    >
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-moon">Inference</h3>
          <div className="flex flex-wrap gap-1.5">
            {presets.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => setProvider({ preset: p.key, baseUrl: p.baseUrl, model: p.model })}
                className={`rounded-md border px-2.5 py-1 text-[11px] transition ${
                  provider.preset === p.key
                    ? "border-moon/60 bg-moon/20 text-moon"
                    : "border-edge bg-ink/60 text-white/70 hover:border-moon/40"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <Field label="Base URL">
            <input
              value={provider.baseUrl}
              onChange={(e) => setProvider({ baseUrl: e.target.value, preset: "custom" })}
              placeholder="https://api.particle.ai/v1"
              className="w-full rounded-md border border-edge bg-ink/70 px-2 py-1.5 font-mono text-[12px] text-white/90 outline-none focus:border-moon/50"
            />
          </Field>

          <div className="grid grid-cols-2 gap-2">
            <Field label="Model">
              <input
                value={provider.model}
                onChange={(e) => setProvider({ model: e.target.value })}
                placeholder="deepseek-v4.1-flash"
                className="w-full rounded-md border border-edge bg-ink/70 px-2 py-1.5 font-mono text-[12px] text-white/90 outline-none focus:border-moon/50"
              />
            </Field>
            <Field label="API key" hint="never sent back to the client">
              <input
                type="password"
                value={provider.apiKey}
                onChange={(e) => setProvider({ apiKey: e.target.value })}
                placeholder={health?.provider.hasKey ? "••• (server env in use)" : "pa-…"}
                className="w-full rounded-md border border-edge bg-ink/70 px-2 py-1.5 font-mono text-[12px] text-white/90 outline-none focus:border-moon/50"
              />
            </Field>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onTest}
              disabled={testing || !provider.baseUrl || !provider.model}
              className="rounded-md border border-keep/40 bg-keep/10 px-3 py-1.5 text-[11px] font-semibold text-keep transition enabled:hover:bg-keep/20 disabled:opacity-40"
            >
              {testing ? "testing…" : "Test connection"}
            </button>
            {testResult ? (
              <Badge tone={testResult.ok ? "keep" : "gone"}>{testResult.ok ? "reachable" : "failed"}</Badge>
            ) : null}
            {testResult?.jsonModeAccepted === false ? (
              <Badge tone="warn" tip="this server rejects response_format json_object; the client falls back to prompt-only JSON">
                json mode unsupported
              </Badge>
            ) : null}
          </div>

          {testResult || testError ? <TestReport result={testResult} error={testError} /> : null}
        </div>

        <div className="space-y-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-moon">Retention policy</h3>
          <div className="grid grid-cols-2 gap-2">
            <Field label="MAX_ACTIVE_FACTS" hint="capacity the Forget pass enforces">
              <input
                type="number"
                min={1}
                value={policy.maxActiveFacts}
                onChange={(e) => setPolicy({ maxActiveFacts: Math.max(1, Number(e.target.value) || 1) })}
                className="w-full rounded-md border border-edge bg-ink/70 px-2 py-1.5 font-mono text-[12px] text-white/90 outline-none focus:border-moon/50"
              />
            </Field>
            <Field label="STALE_AFTER_DAYS" hint="age before a fact may be forgotten">
              <input
                type="number"
                min={1}
                value={policy.staleAfterDays}
                onChange={(e) => setPolicy({ staleAfterDays: Math.max(1, Number(e.target.value) || 1) })}
                className="w-full rounded-md border border-edge bg-ink/70 px-2 py-1.5 font-mono text-[12px] text-white/90 outline-none focus:border-moon/50"
              />
            </Field>
          </div>

          <Field
            label="PROTECTED_CATEGORIES"
            hint="the Forget pass refuses these, even when stale or over capacity"
            group
          >
            <div className="flex flex-wrap gap-1.5">
              {ALL_CATEGORIES.map((c) => {
                const on = policy.protectedCategories.includes(c);
                return (
                  <button
                    key={c}
                    type="button"
                    onClick={() =>
                      setPolicy({
                        protectedCategories: on
                          ? policy.protectedCategories.filter((x) => x !== c)
                          : [...policy.protectedCategories, c],
                      })
                    }
                    className={`rounded-md border px-2 py-0.5 text-[11px] transition ${
                      on ? "border-keep/50 bg-keep/15 text-keep" : "border-edge bg-ink/60 text-dim hover:border-keep/30"
                    }`}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          </Field>

          <label className="tip flex cursor-pointer items-start gap-2 rounded-lg border border-edge bg-ink/50 px-3 py-2" data-tip="Computes the whole four-pass plan and its rejections without writing anything to the store.">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => onChange({ ...settings, dryRun: e.target.checked })}
              className="mt-0.5 h-4 w-4 accent-[#c9b8ff]"
            />
            <span className="text-[12px] leading-snug text-white/85">
              <span className="font-semibold text-change">Dry run</span> — show what Sleep <em>would</em> keep, merge,
              promote and forget, with every rejected proposal and its reason, without applying it.
            </span>
          </label>

          <div className="rounded-lg border border-gone/30 bg-gone/5 px-3 py-2">
            <p className="text-[11px] leading-snug text-gone/90">
              Retained history lives in <span className="font-mono">{health?.db ?? "nocturne.db"}</span>. Clearing
              retires every fact through the versioned path and empties the transcript — the versions stay in the
              database, so a wipe is itself restorable from the Diff panel.
            </p>
            <button
              type="button"
              onClick={onReset}
              className="mt-2 rounded-md border border-gone/40 bg-gone/10 px-2.5 py-1 text-[11px] font-semibold text-gone transition hover:bg-gone/20"
            >
              clear conversation + retire all facts
            </button>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function Field({
  label,
  hint,
  children,
  group,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  group?: boolean;
}) {
  const head = (
    <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.12em] text-dim">{label}</span>
  );
  if (group) {
    return (
      <div className="tip block" role="group" aria-label={label} data-tip={hint}>
        {head}
        {children}
      </div>
    );
  }
  return (
    <label className="tip block" data-tip={hint}>
      {head}
      {children}
    </label>
  );
}

function TestReport({ result, error }: { result: ConnectionTest | null; error: string | null }) {
  if (error) {
    return (
      <div className="rounded-lg border border-gone/40 bg-gone/10 px-3 py-2 text-[11px] text-gone">
        <p className="font-semibold">request failed</p>
        <p className="mt-1 break-words">{error}</p>
      </div>
    );
  }
  if (!result) return null;
  return (
    <div className="rounded-lg border border-edge bg-ink/60 px-3 py-2">
      <p className={`text-[11px] font-semibold ${result.ok ? "text-keep" : "text-gone"}`}>
        {result.ok ? `OK · ${result.model}` : `FAILED · ${result.error}`}
      </p>
      {result.completion ? <p className="mt-1 font-mono text-[11px] text-white/70">{result.completion}</p> : null}
      <ul className="mt-2 space-y-2">
        {result.steps.map((s, i) => (
          <li key={i} className="text-[11px]">
            <div className="flex items-center gap-2">
              <Badge tone={s.status !== null && s.status < 400 ? "keep" : "gone"}>{s.status ?? "no response"}</Badge>
              <span className="truncate font-mono text-dim">{s.url}</span>
            </div>
            <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words rounded bg-ink/70 p-1.5 font-mono text-[10px] text-dim">
              {s.body || "(empty body)"}
            </pre>
          </li>
        ))}
      </ul>
    </div>
  );
}
