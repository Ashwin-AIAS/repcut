import { ENGINE_URL_DISPLAY } from "@/lib/env";

/**
 * What a page renders instead of its content when the engine did not answer.
 *
 * The engine is a separate process the user starts themselves, so "not running"
 * is a normal state, not an exceptional one — and the useful response is the
 * command that fixes it, not an apology. `message` is the engine client's own
 * sentence; nothing here rewrites it.
 *
 * Never a status code on its own and never a path: the failure text is read by
 * a person, and a path here would carry the OS username.
 */
export function EngineDown({ message }: { readonly message: string }) {
  return (
    <section
      aria-labelledby="engine-down-heading"
      className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4"
    >
      <h2
        id="engine-down-heading"
        className="font-display text-base font-semibold text-danger"
      >
        Engine not reachable
      </h2>
      <p className="text-sm text-fg-primary">{message}</p>
      <p className="text-sm text-fg-secondary">
        Repcut expects the engine at{" "}
        <span className="font-mono text-fg-primary">{ENGINE_URL_DISPLAY}</span>.
        Start it, then reload this page.
      </p>
      <code className="w-fit rounded-lg border border-line bg-raised px-2 py-1 font-mono text-sm text-fg-primary">
        make dev
      </code>
    </section>
  );
}
