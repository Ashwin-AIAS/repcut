"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { JobList } from "@/components/jobs/JobList";
import { MediaCard } from "@/components/library/MediaCard";
import { ProxyPlayer } from "@/components/player/ProxyPlayer";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { Dropzone } from "@/components/upload/Dropzone";
import { UploadQueue, type QueuedTransfer } from "@/components/upload/UploadQueue";
import { cancelJob, listMedia, reingest } from "@/lib/api/client";
import type { MediaFile, Project } from "@/lib/api/schemas";
import { useJobStream } from "@/lib/jobs/useJobStream";
import { transferFile } from "@/lib/upload";

export interface WorkspaceProps {
  readonly project: Project;
  /** Rendered by the server for the first paint; refreshed from here after that. */
  readonly initialClips: readonly MediaFile[];
}

/**
 * The editor shell: library on the left, preview in the middle, work at the
 * bottom. The inspector and timeline regions of the design system's layout are
 * not drawn — there are no scenes, beats or AI decisions to put in them until
 * Prompt 03, and an empty panel promising a feature is a dark pattern.
 *
 * One client component owns the whole screen because everything on it is one
 * piece of state: uploading a clip changes the library, which changes what the
 * player can show, and a job finishing changes all three. Splitting that across
 * server boundaries would mean a round trip per transition.
 */
export function Workspace({ project, initialClips }: WorkspaceProps) {
  const [clips, setClips] = useState<readonly MediaFile[]>(initialClips);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialClips[0]?.id ?? null,
  );
  const [transfers, setTransfers] = useState<readonly QueuedTransfer[]>([]);
  const [libraryError, setLibraryError] = useState<string | null>(null);

  const { jobs, status } = useJobStream();

  // Serial, matching the engine's own worker: two encodes on a four-core laptop
  // finish no sooner and make both progress bars lie. The chain is a ref so a
  // re-render cannot start a second queue.
  const queueRef = useRef<Promise<void>>(Promise.resolve());
  const abortRef = useRef(new Map<string, AbortController>());
  const keyRef = useRef(0);

  const projectJobs = jobs.filter(
    (job) => job.project_id === null || job.project_id === project.id,
  );

  /**
   * A refetch signal that changes only when something could have changed the
   * library: a job of this project's reaching a terminal state, or a transfer
   * finishing. Depending on `jobs` itself would refetch on every progress tick.
   */
  const finished = projectJobs
    .filter((job) => job.status !== "queued" && job.status !== "running")
    .map((job) => `${job.job_id}:${job.status}`)
    .sort()
    .join(",");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let stale = false;
    void listMedia(project.id).then((result) => {
      if (stale) return;
      if (result.ok) {
        setClips(result.data);
        setLibraryError(null);
      } else {
        setLibraryError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [project.id, finished, revision]);

  const updateTransfer = useCallback(
    (key: string, next: Partial<QueuedTransfer>): void => {
      setTransfers((current) =>
        current.map((transfer) =>
          transfer.key === key ? { ...transfer, ...next } : transfer,
        ),
      );
    },
    [],
  );

  const onFiles = useCallback(
    (files: readonly File[]): void => {
      for (const file of files) {
        keyRef.current += 1;
        const key = `${keyRef.current}`;

        setTransfers((current) => [
          ...current,
          {
            key,
            name: file.name,
            sizeBytes: file.size,
            state: { phase: "queued", progress: 0, resumed: false },
          },
        ]);

        queueRef.current = queueRef.current.then(async () => {
          const controller = new AbortController();
          abortRef.current.set(key, controller);
          try {
            const result = await transferFile(
              project.id,
              file,
              (state) => updateTransfer(key, { state }),
              controller.signal,
            );
            // The row exists as soon as finalize returns, before ingest has
            // derived anything — so refresh now and again when the job ends.
            if (result.ok) setRevision((value) => value + 1);
          } finally {
            abortRef.current.delete(key);
          }
        });
      }
    },
    [project.id, updateTransfer],
  );

  const cancelTransfer = useCallback((key: string): void => {
    abortRef.current.get(key)?.abort();
  }, []);

  const onCancelJob = useCallback((jobId: string): void => {
    // The terminal event arrives over the socket, so nothing is set here: the
    // stream is the single source of job state, and writing it locally too
    // would let the two disagree.
    void cancelJob(jobId);
  }, []);

  const selected = clips.find((clip) => clip.id === selectedId) ?? null;

  const onReingest = useCallback((): void => {
    if (selected === null) return;
    void reingest(selected.id).then((result) => {
      if (!result.ok) setLibraryError(result.message);
    });
  }, [selected]);

  return (
    <div className="flex min-h-screen flex-col gap-4 p-4">
      <header className="flex shrink-0 flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <Link
            href="/"
            className="font-display text-sm font-semibold tracking-tight text-fg-secondary transition-colors duration-micro ease-micro hover:text-fg-primary"
          >
            Repcut
          </Link>
          <h1 className="font-display text-lg font-semibold tracking-tight text-fg-primary">
            {project.name}
          </h1>
        </div>
        <Link
          href="/status"
          className="text-sm text-fg-secondary underline underline-offset-4 transition-colors duration-micro ease-micro hover:text-fg-primary"
        >
          Engine status
        </Link>
      </header>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[22rem_1fr]">
        <div className="flex min-h-0 flex-col gap-4">
          <Dropzone onFiles={onFiles} />

          <Panel
            title="Media library"
            action={
              <span className="font-mono text-xs text-fg-muted">
                {clips.length}
              </span>
            }
            scroll
          >
            {libraryError !== null && (
              <p role="alert" className="px-4 py-3 text-sm text-danger">
                {libraryError}
              </p>
            )}
            {clips.length === 0 ? (
              <p className="px-4 py-3 text-sm text-fg-muted">
                No clips yet. Drop some in above.
              </p>
            ) : (
              <ul className="grid grid-cols-1 gap-3 p-3 2xl:grid-cols-2">
                {clips.map((clip) => (
                  <li key={clip.id} className="contents">
                    <MediaCard
                      clip={clip}
                      selected={clip.id === selectedId}
                      onSelect={(chosen) => setSelectedId(chosen.id)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="flex min-h-0 flex-col gap-4">
          <Panel
            title="Preview"
            action={
              selected !== null && (
                <Button size="sm" onClick={onReingest}>
                  Re-ingest
                </Button>
              )
            }
          >
            <div className="h-[28rem] p-3">
              <ProxyPlayer clip={selected} />
            </div>
          </Panel>

          <Panel title="Transfers">
            <div className="p-3">
              {transfers.length === 0 ? (
                <p className="text-sm text-fg-muted">Nothing transferring.</p>
              ) : (
                <UploadQueue transfers={transfers} onCancel={cancelTransfer} />
              )}
            </div>
          </Panel>

          <Panel title="Engine jobs">
            <JobList jobs={projectJobs} status={status} onCancel={onCancelJob} />
          </Panel>
        </div>
      </div>
    </div>
  );
}
