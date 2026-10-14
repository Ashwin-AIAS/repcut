"use client";

import { useCallback, useEffect, useState } from "react";
import type { KeyboardEvent } from "react";
import { AiSuggested } from "@/components/primitives/AiSuggested";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { changeCaptions, getCaptions, transcribeClips } from "@/lib/api/client";
import type { ApiResult, CaptionChange } from "@/lib/api/client";
import type { CaptionLine, CaptionWord, Captions, JobEvent } from "@/lib/api/schemas";
import { cx } from "@/lib/cx";

export interface CaptionPanelProps {
  readonly projectId: string;
  /** The project's jobs; captions refetch when a transcription or the edit changes. */
  readonly jobs: readonly JobEvent[];
}

const REFRESH_ON = new Set(["transcribe", "edit_signals", "music_scan", "ingest"]);
const UNSURE = 0.5;

function clock(seconds: number): string {
  const whole = Math.max(0, seconds);
  const minutes = Math.floor(whole / 60);
  return `${minutes}:${(whole - minutes * 60).toFixed(1).padStart(4, "0")}`;
}

/**
 * Captions: the transcript on the edit's timeline, word by word.
 *
 * Transcription runs on this machine (P4); nothing here is sent anywhere. Every
 * word is the recogniser's suggestion until the person edits it, and the edit
 * is kept by the word's id, so it survives a re-cut (P2). Each change is one
 * taste event (P3). Caption text is rendered as text, never as markup: a word
 * that reads `<script>` is shown as those eight characters.
 */
export function CaptionPanel({ projectId, jobs }: CaptionPanelProps) {
  const [captions, setCaptions] = useState<Captions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const finished = jobs
    .filter((job) => REFRESH_ON.has(job.job_type) && job.status !== "queued" && job.status !== "running")
    .map((job) => `${job.job_id}:${job.status}`)
    .sort()
    .join(",");
  const transcribing = jobs.some(
    (job) => job.job_type === "transcribe" && (job.status === "queued" || job.status === "running"),
  );

  useEffect(() => {
    let stale = false;
    void getCaptions(projectId).then((result) => {
      if (stale) return;
      if (result.ok) {
        setCaptions(result.data);
        setError(null);
      } else {
        setError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [projectId, finished]);

  const apply = useCallback(
    (change: CaptionChange) => {
      setBusy(true);
      void changeCaptions(projectId, change).then((result: ApiResult<Captions>) => {
        setBusy(false);
        if (result.ok) {
          setCaptions(result.data);
          setError(null);
        } else {
          setError(result.message);
        }
      });
    },
    [projectId],
  );

  const begin = (word: CaptionWord) => {
    setEditing(word.id);
    setDraft(word.text);
  };

  const commit = (word: CaptionWord) => {
    setEditing(null);
    if (draft !== word.text) apply({ kind: "word", word_id: word.id, text: draft });
  };

  const onKey = (event: KeyboardEvent<HTMLInputElement>, word: CaptionWord) => {
    if (event.key === "Enter") commit(word);
    if (event.key === "Escape") setEditing(null);
  };

  if (captions === null) {
    return error === null ? null : (
      <Panel title="Captions">
        <p className="p-3 text-sm text-fg-muted">{error}</p>
      </Panel>
    );
  }

  const suggested = captions.styles.find((s) => s.id === captions.suggested_style_id);

  return (
    <Panel title="Captions" scroll>
      <div className="flex flex-col gap-4 p-3" aria-busy={busy}>
        {error !== null && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <label className="flex items-center gap-2 text-sm text-fg-primary">
          <input
            type="checkbox"
            checked={captions.enabled}
            disabled={busy}
            onChange={(event) => apply({ kind: "enabled", enabled: event.target.checked })}
          />
          Burn captions into the export
        </label>

        {!captions.model_installed && (
          <p className="text-sm text-warning">{captions.model_message}</p>
        )}
        {captions.model_installed && captions.clips_untranscribed.length > 0 && (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-fg-secondary">
              {captions.clips_untranscribed.length} clip(s) not transcribed yet. Speech is turned
              into text on this machine; no audio leaves it.
            </p>
            <Button size="sm" disabled={transcribing} onClick={() => void transcribeClips(projectId)}>
              {transcribing ? "Transcribing…" : "Transcribe"}
            </Button>
          </div>
        )}

        <AiSuggested
          label="Caption style"
          isSuggestion={captions.style_id === captions.suggested_style_id}
          onReset={() => apply({ kind: "style", style_id: captions.suggested_style_id })}
          suggestion={suggested?.name ?? captions.suggested_style_id}
        >
          <div role="radiogroup" aria-label="Caption style" className="flex flex-col gap-2">
            {captions.styles.map((style) => {
              const active = style.id === captions.style_id;
              return (
                <button
                  key={style.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={busy}
                  onClick={() => apply({ kind: "style", style_id: style.id })}
                  className={cx(
                    "flex flex-col gap-1 rounded-lg border p-2 text-left",
                    "transition-colors duration-micro ease-micro",
                    active ? "border-line-strong bg-raised" : "border-line hover:bg-raised",
                  )}
                >
                  <span className="text-sm text-fg-primary">{style.name}</span>
                  <span className="text-xs text-fg-secondary">{style.description}</span>
                </button>
              );
            })}
          </div>
        </AiSuggested>

        <section aria-label="Transcript" className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-sm text-fg-secondary">Transcript on the edit</h3>
            <span className="text-xs text-fg-muted">
              {captions.languages.length > 0 ? captions.languages.join(" + ") : "no speech yet"}
              {captions.dropped_segments > 0 &&
                ` · ${captions.dropped_segments} likely-invented line(s) left out`}
            </span>
          </div>
          {captions.lines.length === 0 ? (
            <p className="text-sm text-fg-muted">No captions in this edit.</p>
          ) : (
            <ol className="flex flex-col gap-1">
              {captions.lines.map((line: CaptionLine) => (
                <li
                  key={`${line.id}@${line.start}`}
                  data-line-id={line.id}
                  className={cx(
                    "flex items-start gap-2 rounded border border-line p-2",
                    line.hidden && "opacity-50",
                  )}
                >
                  <span className="w-12 shrink-0 font-mono text-xs text-fg-muted">
                    {clock(line.start)}
                  </span>
                  <span className="flex flex-1 flex-wrap gap-1">
                    {line.words.map((word) =>
                      editing === word.id ? (
                        <input
                          key={word.id}
                          aria-label={`Edit the word “${word.text}”`}
                          value={draft}
                          maxLength={80}
                          autoFocus
                          onChange={(event) => setDraft(event.target.value)}
                          onBlur={() => commit(word)}
                          onKeyDown={(event) => onKey(event, word)}
                          className="w-24 rounded border border-line-strong bg-panel px-1 text-sm text-fg-primary"
                        />
                      ) : (
                        <button
                          key={word.id}
                          type="button"
                          data-word-id={word.id}
                          title={`${clock(word.start)}–${clock(word.end)} · confidence ${Math.round(word.probability * 100)}%`}
                          aria-label={`${word.text || "(removed)"}, ${clock(word.start)}, click to edit`}
                          disabled={busy}
                          onClick={() => begin(word)}
                          className={cx(
                            "rounded px-1 text-sm hover:bg-raised",
                            word.edited ? "text-fg-primary underline" : "text-fg-secondary",
                            word.probability < UNSURE && !word.edited && "decoration-dotted underline",
                          )}
                        >
                          {word.text || "∅"}
                        </button>
                      ),
                    )}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {line.words.some((w) => w.edited) && <Badge>edited</Badge>}
                    <button
                      type="button"
                      aria-pressed={!line.hidden}
                      aria-label={`${line.hidden ? "Show" : "Hide"} the line at ${clock(line.start)}`}
                      disabled={busy}
                      onClick={() => apply({ kind: "line", line_id: line.id, hidden: !line.hidden })}
                      className="rounded border border-line px-1 text-xs text-fg-secondary hover:bg-raised"
                    >
                      {line.hidden ? "Off" : "On"}
                    </button>
                  </span>
                </li>
              ))}
            </ol>
          )}
          <p className="text-xs text-fg-muted">
            Dotted words are ones the recogniser was unsure of. Click a word to fix it; empty it
            to drop it.
          </p>
        </section>
      </div>
    </Panel>
  );
}
