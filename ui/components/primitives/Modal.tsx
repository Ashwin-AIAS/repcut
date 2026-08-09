"use client";

import { useCallback, useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { Button } from "@/components/primitives/Button";

export interface ModalProps {
  readonly open: boolean;
  readonly title: string;
  /** Optional supporting sentence, announced with the title. */
  readonly description?: string;
  readonly onClose: () => void;
  readonly children: ReactNode;
  /** The confirming action. Cancel is always present and always closes. */
  readonly footer?: ReactNode;
}

/** Everything focusable, in document order. `:not([disabled])` matters: a disabled control is not a tab stop. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal dialog with a real focus trap.
 *
 * Built on `<dialog>`'s semantics by hand rather than on the element itself:
 * `showModal()` gives a trap and an inert background for free, but jsdom does
 * not implement it, which would make the accessibility criterion untestable —
 * and an untestable accessibility guarantee is the kind that quietly stops
 * being true.
 *
 * Three things a modal has to get right, all of them things sighted mouse users
 * never notice and keyboard users cannot work around:
 *
 * 1. **Focus moves in** when it opens, so the next Tab is inside the dialog.
 * 2. **Focus cannot leave** while it is open — Tab from the last control wraps
 *    to the first, Shift+Tab from the first wraps to the last.
 * 3. **Focus returns** to whatever opened it on close, so the user is put back
 *    where they were rather than at the top of the document.
 */
export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  footer,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  // Captured on open, restored on close. Reading it during the close pass would
  // be too late — focus has usually moved by then.
  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    return () => {
      openerRef.current?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (panel === null) return;
    const first = panel.querySelector<HTMLElement>(FOCUSABLE);
    // Falls back to the panel itself (tabIndex -1) when a dialog has no
    // controls at all, so focus never stays behind on the page underneath.
    (first ?? panel).focus();
  }, [open]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const panel = panelRef.current;
      if (panel === null) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      // Only the two edges are handled; every Tab in between is the browser's,
      // which already knows the document order better than a re-implementation.
      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      }
    },
    [onClose],
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4"
      // The backdrop closes on click, but only when the click started and
      // ended on the backdrop itself — otherwise a drag that begins inside the
      // dialog and releases outside it dismisses the user's own work.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description === undefined ? undefined : descriptionId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="flex w-full max-w-lg flex-col gap-4 rounded-xl border border-line bg-panel p-6"
      >
        <div className="flex flex-col gap-2">
          <h2
            id={titleId}
            className="font-display text-lg font-semibold tracking-tight text-fg-primary"
          >
            {title}
          </h2>
          {description !== undefined && (
            <p id={descriptionId} className="text-sm text-fg-secondary">
              {description}
            </p>
          )}
        </div>

        <div className="text-sm text-fg-secondary">{children}</div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {footer}
        </div>
      </div>
    </div>
  );
}
