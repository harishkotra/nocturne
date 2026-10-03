import type { QuizResult } from "../lib/types";
import { Badge, Empty, Panel, Spinner } from "../lib/ui";

type Progress = { kind: string; itemId: string; correct?: boolean; question?: string };

type Props = {
  result: QuizResult | null;
  running: boolean;
  progress: Progress[];
  error: string | null;
  onRun: () => void;
};

export default function Quiz({ result, running, progress, error, onRun }: Props) {
  const verdict = result
    ? result.passed
      ? { tone: "keep", text: "recall verified" }
      : { tone: "gone", text: "recall FAILED" }
    : null;

  return (
    <Panel
      title="Wake"
      subtitle="Five questions only the consolidated store can answer, plus one about a fact the Forget pass removed. Expected answers come from the store, not from the model's own opinion."
      right={
        <>
          {verdict ? <Badge tone={verdict.tone}>{verdict.text}</Badge> : null}
          <button
            type="button"
            onClick={onRun}
            disabled={running}
            className="rounded-md border border-moon/40 bg-moon/10 px-2.5 py-1 text-[11px] font-semibold text-moon transition enabled:hover:bg-moon/20 disabled:opacity-40"
          >
            {running ? "quizzing…" : "run wake"}
          </button>
        </>
      }
    >
      {error ? <p className="mb-3 rounded-md border border-gone/40 bg-gone/10 px-3 py-2 text-xs text-gone">{error}</p> : null}

      {running && !result ? (
        <div className="space-y-2">
          <Spinner label="asking the agent about its own memory…" />
          <ul className="space-y-1">
            {progress.map((p, i) => (
              <li key={i} className="flex items-center gap-2 text-[12px] text-dim">
                <Badge tone={p.correct === undefined ? "neutral" : p.correct ? "keep" : "gone"}>{p.kind}</Badge>
                <span className="font-mono">{p.itemId}</span>
                <span className="truncate">{p.question ?? ""}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!running && !result && !error ? (
        <Empty>No quiz run yet. Sleep first — wake proves that the consolidated store still answers correctly and that forgotten facts are genuinely gone.</Empty>
      ) : null}

      {result ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <Badge tone={result.survivorsCorrect >= result.requiredSurvivors ? "keep" : "gone"}>
              survivors {result.survivorsCorrect}/{result.survivorsTotal}
            </Badge>
            <Badge tone={result.forgottenQuestion?.passed ? "keep" : "gone"}>
              forgotten probe {result.forgottenQuestion?.passed ? "passed" : "failed"}
            </Badge>
            <span className="font-mono text-[11px] text-dim">
              {result.provider.model} @ {result.provider.baseUrl}
            </span>
            <span className="ml-auto text-[11px] text-dim/70">report → {result.reportPath}</span>
          </div>

          <ol className="space-y-2">
            {result.questions.map((q, i) => (
              <li
                key={q.itemId}
                data-tip={`coverage ${q.coverage} against the store fact\nmissing tokens: ${q.missingTokens.join(", ") || "none"}\nquestion leak score: ${q.leakScore} (${q.origin})`}
                className={`tip cursor-help rounded-lg border px-3 py-2 ${
                  q.correct ? "border-keep/30 bg-keep/5" : "border-gone/30 bg-gone/5"
                }`}
              >
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="font-mono text-dim">{i + 1}.</span>
                  <Badge tone={q.correct ? "keep" : "gone"}>{q.correct ? "recall ok" : "wrong"}</Badge>
                  <span className="font-mono text-dim">{q.itemId}</span>
                  <Badge tone="neutral">{q.block}</Badge>
                  <Badge tone="neutral">{q.category}</Badge>
                  <span className="ml-auto font-mono text-dim">
                    coverage {q.coverage.toFixed(2)}
                    {q.attempts > 1 ? " · re-asked" : ""}
                  </span>
                </div>
                <p className="mt-1 text-[13px] text-white/90">{q.question}</p>
                <p className="mt-1 text-[12px] text-dim">
                  <span className="text-keep/80">store expects: </span>
                  {q.expectedAnswer}
                </p>
                <p className="mt-0.5 text-[12px] text-dim">
                  <span className="text-moon/80">agent answered: </span>
                  {q.modelAnswer}
                </p>
                {q.missingTokens.length ? (
                  <p className="mt-0.5 text-[11px] text-gone/80">missing from the answer: {q.missingTokens.join(", ")}</p>
                ) : null}
              </li>
            ))}
          </ol>

          {result.forgottenQuestion ? (
            <div
              data-tip="the fact was removed by the Forget pass; the store no longer holds it and the agent must say so"
              className={`tip rounded-lg border px-3 py-2 ${
                result.forgottenQuestion.passed ? "border-keep/30 bg-keep/5" : "border-gone/30 bg-gone/5"
              }`}
            >
              <div className="flex items-center gap-2 text-[11px]">
                <Badge tone="gone">forgotten</Badge>
                <span className="font-mono text-dim">{result.forgottenQuestion.itemId}</span>
                <Badge tone={result.forgottenQuestion.absentFromStore ? "keep" : "gone"}>
                  absent from store: {String(result.forgottenQuestion.absentFromStore)}
                </Badge>
                <Badge tone={result.forgottenQuestion.saidAbsent ? "keep" : "gone"}>
                  said “not in memory”: {String(result.forgottenQuestion.saidAbsent)}
                </Badge>
                <Badge tone={result.forgottenQuestion.leakedForgottenContent ? "gone" : "keep"}>
                  leaked the fact: {String(result.forgottenQuestion.leakedForgottenContent)}
                </Badge>
              </div>
              <p className="mt-1 text-[13px] text-white/90">{result.forgottenQuestion.question}</p>
              <p className="mt-0.5 text-[12px] text-dim">
                <span className="text-moon/80">agent answered: </span>
                {result.forgottenQuestion.modelAnswer}
              </p>
              <p className="mt-0.5 text-[11px] text-dim/70">
                forgotten detail was: {result.forgottenQuestion.text}
              </p>
            </div>
          ) : (
            <p className="text-[12px] text-orange-300">
              No forgotten fact was available to probe — the Forget pass removed nothing on this run.
            </p>
          )}

          {result.rejectedQuestions.length ? (
            <p className="text-[11px] text-dim/70">
              {result.rejectedQuestions.length} generated question(s) were refused for leaking the answer.
            </p>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}
