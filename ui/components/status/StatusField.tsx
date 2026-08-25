/**
 * The status page's row vocabulary, in one module so both halves can use it.
 *
 * `/status` is a Server Component; the job-stream probe has to be a Client
 * Component, because only the browser can answer whether the browser can open
 * the socket. A render prop across that boundary is not possible — functions do
 * not serialise from a server component to a client one, and the page answered
 * HTTP 500 — so the shared thing is these components rather than a callback.
 *
 * No `"use client"` here on purpose: with none, the module joins whichever graph
 * imports it, so the same `Field` renders on the server for the health rows and
 * in the browser for the probe row. Nothing here holds state, which is what
 * makes that safe.
 */

/**
 * Tone is semantic, never decorative. Every tone is paired with a word in the
 * pill, so the state survives colour-blindness and greyscale printing — colour
 * is the second channel here, not the only one.
 */
export type Tone = "positive" | "warning" | "danger" | "neutral";

const toneText: Record<Tone, string> = {
  positive: "text-positive",
  warning: "text-warning",
  danger: "text-danger",
  neutral: "text-fg-secondary",
};

/**
 * A shape per tone, so the pills differ by more than hue at a glance.
 * Decorative: the adjacent text carries the meaning, so it is hidden from
 * assistive tech rather than duplicated.
 */
export function ToneGlyph({ tone }: { tone: Tone }) {
  return (
    <svg
      viewBox="0 0 12 12"
      className="h-3 w-3 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {tone === "positive" ? <path d="M2.5 6.4 5 8.9l4.5-5.4" /> : null}
      {tone === "danger" ? <path d="M3 3l6 6M9 3l-6 6" /> : null}
      {tone === "warning" ? <path d="M6 2.5v4.2M6 9.3v.2" /> : null}
      {tone === "neutral" ? <circle cx="6" cy="6" r="2.5" fill="currentColor" /> : null}
    </svg>
  );
}

export function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-lg border border-line bg-raised px-2 py-1 text-sm ${toneText[tone]}`}
    >
      <ToneGlyph tone={tone} />
      {label}
    </span>
  );
}

/**
 * One labelled row. `<dl>`/`<dt>`/`<dd>` is the correct element for
 * name-value pairs: a screen reader announces the label with its value, which
 * a grid of divs would not do.
 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-3 last:border-b-0">
      <dt className="flex flex-col gap-1">
        <span className="text-sm text-fg-secondary">{label}</span>
        {hint === undefined ? null : (
          <span className="text-xs text-fg-muted">{hint}</span>
        )}
      </dt>
      <dd className="flex items-center">{children}</dd>
    </div>
  );
}
