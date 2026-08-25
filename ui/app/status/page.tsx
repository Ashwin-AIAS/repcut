import Link from "next/link";
import type { ReactNode } from "react";
import { JobStreamProbe } from "@/components/status/JobStreamProbe";
import { Field, StatusPill, ToneGlyph } from "@/components/status/StatusField";
import { ENGINE_URL_DISPLAY } from "@/lib/env";
import { fetchHealth, type Health } from "@/lib/health";

/**
 * Health is a live probe of another process, so this page is never
 * prerendered. Without this, `next build` would try to reach an engine that is
 * not running and bake the failure into a static page.
 */
export const dynamic = "force-dynamic";

/** A null probe result is a fact, not a blank. Say so. */
function Unavailable() {
  return <span className="text-sm text-fg-muted">Unavailable</span>;
}

function TextValue({ value }: { value: string | null }) {
  if (value === null) {
    return <Unavailable />;
  }
  return <span className="font-mono text-sm text-fg-primary">{value}</span>;
}

const megabytes = new Intl.NumberFormat("en-US");

function MegabyteValue({ value }: { value: number | null }) {
  if (value === null) {
    return <Unavailable />;
  }
  return (
    <span className="font-mono text-sm text-fg-primary">
      {megabytes.format(Math.round(value))} MB
    </span>
  );
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel">
      <h2 className="border-b border-line px-4 py-3 text-sm font-semibold text-fg-primary">
        {title}
      </h2>
      <dl className="flex flex-col">{children}</dl>
    </section>
  );
}

function HealthReport({ health }: { health: Health }) {
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Engine">
        <Field label="Engine version">
          <TextValue value={health.engine_version} />
        </Field>
        <Field
          label="Data directory writable"
          hint="Where projects, renders and the local database are stored."
        >
          {health.data_dir_writable ? (
            <StatusPill tone="positive" label="Writable" />
          ) : (
            <StatusPill tone="danger" label="Not writable" />
          )}
        </Field>
      </Panel>

      <Panel title="Video pipeline">
        <Field label="FFmpeg version">
          <TextValue value={health.ffmpeg_version} />
        </Field>
        <Field
          label="Can start video tools"
          hint={
            health.event_loop_can_spawn
              ? `Engine event loop: ${health.event_loop}.`
              : `Engine event loop: ${health.event_loop}. It cannot start FFmpeg, so uploads will not finish. Restart the engine with \`make dev\`.`
          }
        >
          {health.event_loop_can_spawn ? (
            <StatusPill tone="positive" label="Yes" />
          ) : (
            <StatusPill tone="danger" label="No — restart the engine" />
          )}
        </Field>
        <Field
          label="libx264 encoder"
          hint="Required for exports. Previews can fall back to other encoders."
        >
          {health.ffmpeg_has_libx264 ? (
            <StatusPill tone="positive" label="Available" />
          ) : (
            <StatusPill tone="danger" label="Missing" />
          )}
        </Field>
      </Panel>

      {/*
        Two rows, because there are two ways the job stream dies and they have
        different fixes. The engine can be unable to serve it; or the engine can
        be perfectly healthy and the *browser* refused to open it, which is what
        happened — the page's Content-Security-Policy blocked `ws://` and this
        panel was green throughout. One row would have to pick a side.
      */}
      <Panel title="Job progress">
        <Field
          label="Engine can serve the job stream"
          hint={
            health.jobs_socket_ready
              ? "The engine's /ws/jobs route is mounted and its job worker is running."
              : "The engine cannot serve /ws/jobs. Restart it with `make dev` and check its output."
          }
        >
          {health.jobs_socket_ready ? (
            <StatusPill tone="positive" label="Yes" />
          ) : (
            <StatusPill tone="danger" label="No — restart the engine" />
          )}
        </Field>
        <JobStreamProbe />
      </Panel>

      <Panel title="Compute">
        <Field label="CUDA available">
          {health.cuda_available ? (
            <StatusPill tone="positive" label="Yes" />
          ) : (
            <StatusPill tone="warning" label="No — CPU fallback" />
          )}
        </Field>
        <Field label="GPU">
          <TextValue value={health.gpu_name} />
        </Field>
        <Field label="VRAM free">
          <MegabyteValue value={health.vram_free_mb} />
        </Field>
        <Field label="VRAM total">
          <MegabyteValue value={health.vram_total_mb} />
        </Field>
        <Field
          label="Active PyTorch device"
          hint="What the engine will actually run models on right now."
        >
          {health.torch_device_active === "cuda" ? (
            <StatusPill tone="positive" label="CUDA" />
          ) : (
            <StatusPill tone="warning" label="CPU" />
          )}
        </Field>
      </Panel>

      <Panel title="Services">
        <Field
          label="Gemini API key"
          hint="Only whether a key is configured. The value is never read by the browser."
        >
          {health.gemini_api_key_set ? (
            <StatusPill tone="positive" label="Configured" />
          ) : (
            <StatusPill tone="warning" label="Not configured" />
          )}
        </Field>
      </Panel>
    </div>
  );
}

const errorHeadings = {
  unreachable: "Engine not reachable",
  bad_status: "Engine reported a problem",
  invalid_response: "Unexpected response from the engine",
} as const;

function ErrorCard({
  heading,
  cause,
  fix,
  command,
}: {
  heading: string;
  cause: string;
  fix: string;
  command?: string;
}) {
  return (
    <section
      className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4"
      aria-labelledby="engine-error-heading"
    >
      <div className="flex items-center gap-2">
        <span className="text-danger">
          <ToneGlyph tone="danger" />
        </span>
        <h2
          id="engine-error-heading"
          className="text-base font-semibold text-danger"
        >
          {heading}
        </h2>
      </div>
      <p className="text-sm text-fg-primary">{cause}</p>
      <p className="text-sm text-fg-secondary">{fix}</p>
      {command === undefined ? null : (
        <code className="w-fit rounded-lg border border-line bg-raised px-2 py-1 font-mono text-sm text-fg-primary">
          {command}
        </code>
      )}
    </section>
  );
}

export default async function StatusPage() {
  const result = await fetchHealth();

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-8">
      <header className="flex flex-col gap-2">
        <Link
          href="/"
          className="w-fit text-sm text-fg-secondary underline underline-offset-4 transition-colors duration-micro ease-micro hover:text-fg-primary"
        >
          Repcut
        </Link>
        <h1 className="text-xl font-semibold tracking-tight text-fg-primary">
          Engine status
        </h1>
        <p className="text-sm text-fg-secondary">
          Live check of the local engine at{" "}
          <span className="font-mono text-fg-primary">{ENGINE_URL_DISPLAY}</span>.
        </p>
      </header>

      {result.ok ? (
        <HealthReport health={result.data} />
      ) : (
        <ErrorCard
          heading={errorHeadings[result.kind]}
          cause={result.cause}
          fix={result.fix}
          command={result.command}
        />
      )}
    </main>
  );
}
