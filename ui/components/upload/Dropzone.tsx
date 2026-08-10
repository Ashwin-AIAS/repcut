"use client";

import { useCallback, useRef, useState } from "react";
import { Button } from "@/components/primitives/Button";
import { cx } from "@/lib/cx";

export interface DropzoneProps {
  readonly onFiles: (files: readonly File[]) => void;
  readonly disabled?: boolean;
}

/**
 * Drag-and-drop plus a real file input.
 *
 * The input is the accessible path and is not decoration: drag-and-drop cannot
 * be performed with a keyboard at all, so a dropzone without a button is a
 * feature that simply does not exist for some users. The button is what gets
 * focus and a label; the drop target is an enhancement on top.
 *
 * `dragCounter` rather than a boolean: `dragenter`/`dragleave` fire for every
 * descendant the pointer crosses, so a naive flag flickers off the moment the
 * cursor moves over a child element inside the zone.
 */
export function Dropzone({ onFiles, disabled = false }: DropzoneProps) {
  const [dragging, setDragging] = useState(false);
  const dragCounter = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = useCallback(
    (list: FileList | null) => {
      if (list === null || disabled) return;
      const files = Array.from(list);
      if (files.length > 0) onFiles(files);
    },
    [disabled, onFiles],
  );

  return (
    <div
      onDragEnter={(event) => {
        event.preventDefault();
        dragCounter.current += 1;
        setDragging(true);
      }}
      onDragLeave={(event) => {
        event.preventDefault();
        dragCounter.current -= 1;
        if (dragCounter.current <= 0) setDragging(false);
      }}
      onDragOver={(event) => {
        // Without this the browser navigates to the dropped file instead.
        event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        dragCounter.current = 0;
        setDragging(false);
        accept(event.dataTransfer.files);
      }}
      className={cx(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-6",
        "transition-colors duration-micro ease-micro",
        dragging ? "border-line-strong bg-raised" : "border-line bg-panel",
      )}
    >
      <p className="text-sm text-fg-secondary">
        Drop clips here, or choose them.
      </p>
      <p className="text-xs text-fg-muted">
        Your footage stays on this machine.
      </p>

      <Button
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        variant="secondary"
      >
        Choose clips
      </Button>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept="video/*"
        // Labelled and visually hidden rather than `display: none` — a hidden
        // input is still the control that owns the file list, and `sr-only`
        // keeps it reachable by assistive tech instead of removing it.
        className="sr-only"
        aria-label="Choose video clips to upload"
        disabled={disabled}
        onChange={(event) => {
          accept(event.target.files);
          // Reset so choosing the same file twice in a row still fires change —
          // which is exactly what a user does after a failed upload.
          event.target.value = "";
        }}
      />
    </div>
  );
}
