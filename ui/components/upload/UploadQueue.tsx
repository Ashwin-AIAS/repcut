"use client";

import { Progress } from "@/components/primitives/Progress";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import type { TransferState } from "@/lib/upload";
import { formatBytes } from "@/lib/format";

export interface QueuedTransfer {
  /** Stable across re-renders; `File` identity is not enough after a retry. */
  readonly key: string;
  readonly name: string;
  readonly sizeBytes: number;
  readonly state: TransferState;
}

const PHASE_LABEL: Record<TransferState["phase"], string> = {
  queued: "Waiting",
  hashing: "Checksumming",
  uploading: "Transferring",
  finalizing: "Verifying and storing",
  succeeded: "Done",
  failed: "Failed",
};

/**
 * The transfer list.
 *
 * Every phase is named. Checksumming a 2GB clip takes ten seconds before a
 * single byte is sent, and a progress bar that sits at 0% with no explanation
 * for ten seconds is where a user decides the app is broken — the reason the
 * design system forbids an uninformative wait.
 *
 * A resumed transfer says so. It is the visible evidence that killing the
 * engine mid-upload did not cost the user their transfer, and it is the only
 * place amendment 004 §7's resume path is legible from the outside.
 */
/**
 * Cancellable while the bytes are still ours to stop sending.
 *
 * `finalizing` is deliberately not in this set: the engine is hashing and
 * moving the assembled file at that point, and aborting the request would not
 * un-run it — it would only cost the UI the answer.
 */
const CANCELLABLE: ReadonlySet<TransferState["phase"]> = new Set([
  "queued",
  "hashing",
  "uploading",
]);

export function UploadQueue({
  transfers,
  onCancel,
}: {
  readonly transfers: readonly QueuedTransfer[];
  readonly onCancel: (key: string) => void;
}) {
  if (transfers.length === 0) return null;

  return (
    <ul className="flex flex-col gap-3">
      {transfers.map((transfer) => {
        const { state } = transfer;
        const tone =
          state.phase === "failed"
            ? "failed"
            : state.phase === "succeeded"
              ? "succeeded"
              : "running";

        return (
          <li
            key={transfer.key}
            className="flex flex-col gap-2 rounded-lg border border-line bg-panel p-3"
          >
            <div className="flex items-baseline justify-between gap-3">
              <span
                className="truncate text-sm text-fg-primary"
                title={transfer.name}
              >
                {transfer.name}
              </span>
              <div className="flex shrink-0 items-center gap-2">
                <span className="font-mono text-xs text-fg-muted">
                  {formatBytes(transfer.sizeBytes)}
                </span>
                {CANCELLABLE.has(state.phase) && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onCancel(transfer.key)}
                  >
                    Cancel
                  </Button>
                )}
              </div>
            </div>

            <Progress
              value={state.progress}
              label={`${PHASE_LABEL[state.phase]}: ${transfer.name}`}
              step={PHASE_LABEL[state.phase]}
              tone={tone}
            />

            <div className="flex flex-wrap items-center gap-2">
              {state.resumed && (
                <Badge tone="positive" label="transfer state">
                  resumed
                </Badge>
              )}
              {state.message !== undefined && (
                // The engine's own sentence. The UI never rewrites a failure
                // the engine already named, and never shows a traceback.
                <p
                  role={state.phase === "failed" ? "alert" : undefined}
                  className="text-xs text-danger"
                >
                  {state.message}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
