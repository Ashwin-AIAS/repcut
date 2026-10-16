"use client";

import { Badge } from "@/components/primitives/Badge";
import { proxyUrl } from "@/lib/api/client";
import type { EditOverride } from "@/lib/api/client";
import type { Edit } from "@/lib/api/schemas";
import { cx } from "@/lib/cx";

export interface HookStripProps {
  readonly edit: Edit;
  readonly busy: boolean;
  readonly apply: (change: EditOverride) => void;
  /** Clip display names by digest. */
  readonly clipNames: Readonly<Record<string, string>>;
  /** Media file ids by digest, for each candidate's preview. */
  readonly clipIds: Readonly<Record<string, string>>;
}

const MODES = [
  { mode: "on", label: "AI pick", kind: "hook_on" },
  { mode: "off", label: "No teaser", kind: "hook_off" },
] as const;

function clock(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * The opening teaser: the AI's pick, no teaser, or one tap on another candidate.
 *
 * The candidates are the session's hero moments ranked by how striking their
 * first seconds look; each previews its own window of the proxy (a media
 * fragment, so nothing renders). The engine places the teaser and keeps the
 * edit's length; this strip only says which moment.
 */
export function HookStrip({ edit, busy, apply, clipNames, clipIds }: HookStripProps) {
  if (edit.hook_candidates.length === 0) return null;
  const teaser = edit.shots.find((shot) => shot.hook);
  return (
    <section aria-label="Opening teaser" className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm text-fg-secondary">
          Opening: the first two seconds decide whether people keep watching
        </h3>
        {teaser !== undefined && (
          <span className="font-mono text-xs text-fg-muted">
            {(teaser.timeline_out - teaser.timeline_in).toFixed(1)} s teaser
            {edit.hook_placement === "duplicated" ? ", plays again later" : ", moved to the start"}
          </span>
        )}
      </div>
      <div role="radiogroup" aria-label="Opening teaser mode" className="flex gap-2">
        {MODES.map(({ mode, label, kind }) => {
          const active = edit.hook_mode === mode;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={busy}
              onClick={() => apply({ kind })}
              className={cx(
                "rounded-lg border px-3 py-1 text-sm",
                "transition-colors duration-micro ease-micro",
                active ? "border-line-strong bg-raised text-fg-primary" : "border-line text-fg-secondary hover:bg-raised",
              )}
            >
              {label}
            </button>
          );
        })}
      </div>
      <ol aria-label="Opening candidates" className="grid grid-cols-3 gap-2">
        {edit.hook_candidates.map((candidate) => {
          const id = clipIds[candidate.sha256];
          const name = clipNames[candidate.sha256] ?? "clip";
          return (
            <li
              key={candidate.segment_id}
              data-hook-candidate={candidate.segment_id}
              data-chosen={candidate.chosen}
              className={cx(
                "flex flex-col gap-1 rounded-lg border p-1",
                candidate.chosen ? "border-line-strong bg-raised" : "border-line",
              )}
            >
              {id !== undefined && (
                <video
                  aria-label={`Preview of ${name} at ${clock(candidate.peak)}`}
                  className="aspect-video w-full rounded bg-panel object-cover"
                  src={`${proxyUrl(id)}#t=${candidate.start.toFixed(3)},${candidate.end.toFixed(3)}`}
                  muted
                  playsInline
                  preload="metadata"
                  onMouseEnter={(event) => void event.currentTarget.play().catch(() => undefined)}
                  onFocus={(event) => void event.currentTarget.play().catch(() => undefined)}
                  tabIndex={0}
                />
              )}
              <span className="flex items-center gap-1 text-xs text-fg-primary">
                #{candidate.rank} {name} {clock(candidate.peak)}
                {candidate.chosen && <Badge tone="ai">Opening</Badge>}
              </span>
              <span className="text-xs text-fg-muted" title={candidate.reasons.join("; ")}>
                score {Math.round(candidate.score * 100)}
              </span>
              <button
                type="button"
                disabled={busy || candidate.chosen}
                aria-label={`Open the edit with candidate ${candidate.rank}`}
                onClick={() => apply({ kind: "hook_choose", segment_id: candidate.segment_id })}
                className="rounded border border-line px-1 text-xs text-fg-secondary hover:bg-raised disabled:opacity-50"
              >
                {candidate.chosen ? "Opening" : "Use this"}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
