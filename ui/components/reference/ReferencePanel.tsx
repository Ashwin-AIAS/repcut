"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import {
  analyseReference,
  detachReferencePart,
  getReferenceMatch,
  getReferenceProfile,
  matchReference,
  saveLook,
  setRole,
} from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/client";
import { referenceParts } from "@/lib/api/schemas";
import type {
  JobEvent,
  MediaFile,
  ReferenceMatch,
  ReferencePart,
  ReferenceProfile,
} from "@/lib/api/schemas";

export interface ReferencePanelProps {
  readonly projectId: string;
  readonly clips: readonly MediaFile[];
  readonly jobs: readonly JobEvent[];
  /** Called after a role change, so the library refetches. */
  readonly onChanged: () => void;
}

const PART_LABEL: Record<ReferencePart, string> = {
  look: "Look",
  pacing: "Pacing",
  captions: "Caption style",
};

const REFRESH_ON = new Set(["reference", "analysis", "ingest"]);

function on(match: ReferenceMatch, part: ReferencePart): boolean {
  if (match === null) return false;
  return part === "look" ? match.look_on : part === "pacing" ? match.pacing_on : match.captions_on;
}

/**
 * "Make mine look like that": a reference clip's look, pacing and caption style.
 *
 * The reference is one of the person's own files, uploaded like any clip and
 * marked as a reference here; Repcut never fetches a video from anywhere. It
 * is measured locally (its sampled frames are analysed like any clip's, P4),
 * never cut into the edit, and each matched part is a default the person can
 * detach on its own (P2), which puts their own choice back.
 */
export function ReferencePanel({ projectId, clips, jobs, onChanged }: ReferencePanelProps) {
  const references = clips.filter((clip) => clip.role === "reference");
  const footage = clips.filter((clip) => clip.role === "footage");
  const [chosen, setChosen] = useState<string | null>(references[0]?.id ?? null);
  const [fetched, setProfile] = useState<ReferenceProfile | null>(null);
  const [match, setMatch] = useState<ReferenceMatch>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  const reference = references.find((clip) => clip.id === chosen) ?? references[0] ?? null;
  const referenceId = reference?.id ?? null;
  // A profile fetched for another reference is not this one's.
  const profile = fetched !== null && fetched.media_file_id === referenceId ? fetched : null;
  const measuring = jobs.some(
    (job) => job.job_type === "reference" && (job.status === "queued" || job.status === "running"),
  );
  const finished = jobs
    .filter((job) => REFRESH_ON.has(job.job_type) && job.status !== "queued" && job.status !== "running")
    .map((job) => `${job.job_id}:${job.status}`)
    .sort()
    .join(",");

  useEffect(() => {
    let stale = false;
    void getReferenceMatch(projectId).then((result) => {
      if (!stale && result.ok) setMatch(result.data);
    });
    if (referenceId !== null) {
      void getReferenceProfile(projectId, referenceId).then((result) => {
        if (!stale && result.ok) setProfile(result.data);
      });
    }
    return () => {
      stale = true;
    };
  }, [projectId, referenceId, finished]);

  const settle = useCallback(<T,>(result: ApiResult<T>, then?: (data: T) => void) => {
    setBusy(false);
    if (result.ok) {
      setError(null);
      then?.(result.data);
    } else {
      setError(result.message);
    }
  }, []);

  const markAsReference = (clipId: string) => {
    setBusy(true);
    void setRole(clipId, "reference").then((result) =>
      settle(result, () => {
        setChosen(clipId);
        onChanged();
        void analyseReference(projectId, clipId);
      }),
    );
  };

  const toggle = (part: ReferencePart, wanted: boolean) => {
    if (reference === null) return;
    setBusy(true);
    const call = wanted
      ? matchReference(projectId, reference.id, [part])
      : detachReferencePart(projectId, part);
    void call.then((result) => settle(result, setMatch));
  };

  const matchAll = () => {
    if (reference === null) return;
    setBusy(true);
    void matchReference(projectId, reference.id, referenceParts).then((result) =>
      settle(result, setMatch),
    );
  };

  const save = () => {
    if (reference === null) return;
    setBusy(true);
    const trimmed = name.trim();
    void saveLook(projectId, reference.id, trimmed === "" ? null : trimmed).then((result) =>
      settle(result, (theme) => setSaved(theme.name)),
    );
  };

  const matchedHere = match !== null && reference !== null && match.reference_media_file_id === reference.id;

  return (
    <Panel title="Match a reference" scroll>
      <div className="flex flex-col gap-4 p-3" aria-busy={busy}>
        <p className="text-xs text-fg-muted">
          Use a video you have the right to use. Repcut measures its colour, cut rhythm and
          captions on this machine and never downloads anything; its sampled frames are analysed
          like your clips&apos;. It is never cut into your edit.
        </p>
        {error !== null && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        {references.length === 0 ? (
          footage.length === 0 ? (
            <p className="text-sm text-fg-muted">Upload a reference clip, then mark it here.</p>
          ) : (
            <label className="flex flex-col gap-1 text-sm text-fg-secondary">
              Which uploaded clip is the reference?
              <select
                className="rounded border border-line bg-raised px-2 py-1 text-fg-primary"
                defaultValue=""
                disabled={busy}
                onChange={(event) => event.target.value && markAsReference(event.target.value)}
              >
                <option value="">Choose a clip…</option>
                {footage.map((clip) => (
                  <option key={clip.id} value={clip.id}>
                    {clip.display_name}
                  </option>
                ))}
              </select>
            </label>
          )
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm text-fg-secondary">
              Reference
              <select
                className="rounded border border-line bg-raised px-2 py-1 text-fg-primary"
                value={reference?.id ?? ""}
                disabled={busy}
                onChange={(event) => setChosen(event.target.value)}
              >
                {references.map((clip) => (
                  <option key={clip.id} value={clip.id}>
                    {clip.display_name}
                  </option>
                ))}
              </select>
            </label>

            {profile === null ? (
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-fg-secondary">
                  {measuring ? "Measuring the reference…" : "Not measured yet."}
                </p>
                {reference !== null && !measuring && (
                  <Button size="sm" onClick={() => void analyseReference(projectId, reference.id)}>
                    Measure
                  </Button>
                )}
              </div>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <dt className="text-fg-muted">Shots</dt>
                  <dd className="font-mono text-fg-primary">
                    {profile.rhythm.shot_count}, about {profile.rhythm.shot_median.toFixed(1)} s each
                  </dd>
                  <dt className="text-fg-muted">Cuts per 10 s</dt>
                  <dd className="font-mono text-fg-primary">{profile.rhythm.cuts_per_10s.toFixed(1)}</dd>
                  <dt className="text-fg-muted">On the beat</dt>
                  <dd className="font-mono text-fg-primary">
                    {profile.rhythm.beat_alignment === null
                      ? "no beat found"
                      : `${Math.round(profile.rhythm.beat_alignment * 100)}% of cuts`}
                  </dd>
                  <dt className="text-fg-muted">Captions</dt>
                  <dd className="text-fg-primary">
                    {profile.captions.present
                      ? `${profile.captions.position ?? "?"}, ${profile.captions.size ?? "?"} → ${profile.caption_match.style_id}`
                      : "none"}
                  </dd>
                </dl>
                {profile.look.capped.length > 0 && (
                  <p className="text-sm text-warning">
                    This reference pushes {profile.look.capped.join(", ")} past Repcut&apos;s limits;
                    the matched look holds them at the limit.
                  </p>
                )}

                {!matchedHere ? (
                  <div className="flex flex-col gap-1">
                    <Button onClick={matchAll} disabled={busy}>
                      Match this reference
                    </Button>
                    <p className="text-xs text-fg-muted">
                      Applies its look to every clip, its pacing to the edit and the nearest caption
                      style. Each part can be detached afterwards.
                    </p>
                  </div>
                ) : (
                  <fieldset className="flex flex-col gap-2">
                    <legend className="text-sm text-fg-secondary">Matched parts (each one detaches)</legend>
                    {referenceParts.map((part) => (
                      <label key={part} className="flex items-center gap-2 text-sm text-fg-primary">
                        <input
                          type="checkbox"
                          checked={on(match, part)}
                          disabled={busy}
                          onChange={(event) => toggle(part, event.target.checked)}
                        />
                        {PART_LABEL[part]}
                        {part === "look" && match?.theme_id && on(match, part) && (
                          <Badge tone="ai">{match.theme_id}</Badge>
                        )}
                      </label>
                    ))}
                    {match?.caption_differences.map((note) => (
                      <p key={note} className="text-xs text-fg-muted">
                        {note}
                      </p>
                    ))}
                  </fieldset>
                )}

                <div className="flex items-end gap-2">
                  <label className="flex flex-1 flex-col gap-1 text-sm text-fg-secondary">
                    Save this look as a theme
                    <input
                      className="rounded border border-line bg-raised px-2 py-1 text-fg-primary"
                      maxLength={40}
                      placeholder="Name (optional)"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </label>
                  <Button size="sm" onClick={save} disabled={busy}>
                    Save
                  </Button>
                </div>
                {saved !== null && (
                  <p className="text-sm text-positive">Saved “{saved}”: it is in the theme selector.</p>
                )}
              </>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
