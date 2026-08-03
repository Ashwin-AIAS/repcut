import Link from "next/link";
import { fetchPrompts, type PromptsTrackerResponse, type PromptStatusItem, type WaveSummary } from "@/lib/prompts";

export const dynamic = "force-dynamic";

type Tone = "positive" | "warning" | "neutral";

const toneText: Record<Tone, string> = {
  positive: "text-positive border-positive/30 bg-positive/10",
  warning: "text-warning border-warning/30 bg-warning/10",
  neutral: "text-fg-secondary border-line bg-raised",
};

function StatusPill({ status }: { status: PromptStatusItem["status"] }) {
  const tone: Tone = status === "PASSED" ? "positive" : status === "IN_PROGRESS" ? "warning" : "neutral";
  const label = status === "PASSED" ? "PASSED" : status === "IN_PROGRESS" ? "IN PROGRESS" : "PENDING";

  return (
    <span className={`inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold tracking-wide ${toneText[tone]}`}>
      {label}
    </span>
  );
}

function ErrorCard({ cause, fix, command }: { cause: string; fix: string; command?: string }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-5">
      <h2 className="text-base font-semibold text-danger">Engine not reachable</h2>
      <p className="text-sm text-fg-primary">{cause}</p>
      <p className="text-sm text-fg-secondary">{fix}</p>
      {command && (
        <code className="w-fit rounded-lg border border-line bg-raised px-3 py-1.5 font-mono text-sm text-fg-primary">
          {command}
        </code>
      )}
    </section>
  );
}

function WaveCard({ wave }: { wave: WaveSummary }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4 transition-colors duration-micro hover:border-line-strong">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg-primary">{wave.wave_title}</h3>
        <span className="rounded-full border border-line bg-raised px-2.5 py-0.5 text-xs text-fg-muted font-mono">
          Est: {wave.estimated_timeline}
        </span>
      </div>

      <div className="flex items-center justify-between text-xs text-fg-secondary">
        <span>{wave.passed_prompts} / {wave.total_prompts} Prompts Passed</span>
        <span className="font-mono font-medium text-fg-primary">{wave.completion_percentage.toFixed(1)}%</span>
      </div>

      <div className="h-2 w-full overflow-hidden rounded-full bg-raised border border-line">
        <div
          className="h-full bg-positive transition-all duration-300"
          style={{ width: `${Math.max(wave.completion_percentage, wave.passed_prompts > 0 ? 5 : 0)}%` }}
        />
      </div>
    </div>
  );
}

function PromptRow({ item }: { item: PromptStatusItem }) {
  const { metadata: meta, status, notes } = item;

  return (
    <div className="flex flex-col gap-3 border-b border-line p-4 last:border-b-0 hover:bg-raised/40 transition-colors">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-semibold text-fg-muted border border-line rounded px-2 py-0.5 bg-raised">
            Prompt {meta.id}
          </span>
          <h4 className="text-sm font-semibold text-fg-primary">{meta.name}</h4>
          {meta.human_review && (
            <span className="text-xs font-medium text-warning border border-warning/40 bg-warning/10 rounded px-2 py-0.5">
              {meta.human_review_type || "★ Human Review Gate"}
            </span>
          )}
        </div>
        <StatusPill status={status} />
      </div>

      <p className="text-sm text-fg-secondary">{meta.summary}</p>

      {meta.deliverables.length > 0 && (
        <ul className="flex flex-col gap-1 pl-4 text-xs text-fg-muted list-disc">
          {meta.deliverables.map((del, i) => (
            <li key={i}>{del}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-fg-muted">Gate Command:</span>
          <code className="rounded border border-line bg-raised px-2 py-0.5 font-mono text-fg-primary">
            {meta.gate_command}
          </code>
        </div>
        <span className="text-fg-muted font-mono italic">{notes}</span>
      </div>
    </div>
  );
}

function DashboardReport({ data }: { data: PromptsTrackerResponse }) {
  return (
    <div className="flex flex-col gap-8">
      {/* Overall Progress Banner */}
      <section className="rounded-xl border border-line bg-panel p-6 flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
          <div>
            <h2 className="text-lg font-semibold text-fg-primary">Overall Build Plan Progress</h2>
            <p className="text-xs text-fg-secondary mt-0.5">
              Reference: <span className="font-medium text-fg-primary">guide prompts.pdf (v1.0)</span>
            </p>
          </div>
          <div className="flex items-center gap-4 text-sm font-medium">
            <span className="text-positive">Passed: {data.passed_count}</span>
            <span className="text-warning">In Progress: {data.in_progress_count}</span>
            <span className="text-fg-muted">Pending: {data.pending_count}</span>
            <span className="font-mono text-base font-bold text-fg-primary border-l border-line pl-4">
              {data.overall_completion_percentage.toFixed(1)}%
            </span>
          </div>
        </div>

        <div className="h-3 w-full overflow-hidden rounded-full bg-raised border border-line">
          <div
            className="h-full bg-positive transition-all duration-500"
            style={{ width: `${Math.max(data.overall_completion_percentage, 2)}%` }}
          />
        </div>
      </section>

      {/* Waves Breakdown */}
      <section className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold tracking-wide text-fg-secondary uppercase">
          Build Waves Breakdown
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {data.waves.map((wave) => (
            <WaveCard key={wave.wave_number} wave={wave} />
          ))}
        </div>
      </section>

      {/* All Prompts Detailed View */}
      <section className="rounded-xl border border-line bg-panel overflow-hidden">
        <div className="border-b border-line px-4 py-3 bg-panel">
          <h3 className="text-sm font-semibold text-fg-primary">All Build Prompts ({data.total_prompts})</h3>
        </div>
        <div className="flex flex-col">
          {data.prompts.map((item) => (
            <PromptRow key={item.metadata.id} item={item} />
          ))}
        </div>
      </section>
    </div>
  );
}

export default async function PromptsDashboardPage() {
  const result = await fetchPrompts();

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-6 px-6 py-8">
      <header className="flex flex-col gap-2">
        <Link
          href="/"
          className="w-fit text-sm text-fg-secondary underline underline-offset-4 transition-colors duration-micro ease-micro hover:text-fg-primary"
        >
          Repcut
        </Link>
        <h1 className="text-2xl font-bold tracking-tight text-fg-primary">
          Prompt Completion Dashboard
        </h1>
        <p className="text-sm text-fg-secondary">
          Live verification status tracking across all 14 Repcut build prompts and 6 waves.
        </p>
      </header>

      {result.ok ? (
        <DashboardReport data={result.data} />
      ) : (
        <ErrorCard cause={result.cause} fix={result.fix} command={result.command} />
      )}
    </main>
  );
}
