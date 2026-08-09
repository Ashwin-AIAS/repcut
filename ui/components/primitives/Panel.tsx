import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

export interface PanelProps {
  /** Rendered as the panel's heading and used as its accessible name. */
  readonly title?: string;
  /** Sits opposite the title — a count, a control, a status. */
  readonly action?: ReactNode;
  readonly children: ReactNode;
  /** Panels that fill a shell region scroll internally rather than growing. */
  readonly scroll?: boolean;
}

/**
 * A titled region. The editor shell is made of these.
 *
 * When a title is given the element is a `<section>` with an accessible name,
 * so the panels show up as navigable landmarks rather than as anonymous divs —
 * which is most of what makes a dense editor screen usable with a screen
 * reader. Without a title it stays a plain container, because a nameless
 * landmark is noise.
 */
export function Panel({ title, action, children, scroll = false }: PanelProps) {
  const body = (
    <div className={cx("min-h-0", scroll && "flex-1 overflow-y-auto")}>
      {children}
    </div>
  );

  if (title === undefined) {
    return (
      <div className="flex min-h-0 flex-col rounded-xl border border-line bg-panel">
        {body}
      </div>
    );
  }

  return (
    <section
      aria-label={title}
      className="flex min-h-0 flex-col rounded-xl border border-line bg-panel"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="font-display text-sm font-semibold tracking-tight text-fg-primary">
          {title}
        </h2>
        {action}
      </header>
      {body}
    </section>
  );
}
