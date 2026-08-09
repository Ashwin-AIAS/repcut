import { cx } from "@/lib/cx";

export interface SkeletonProps {
  /** Tailwind sizing utilities only — shape is the caller's, style is not. */
  readonly className?: string;
  /**
   * What is loading. Rendered for assistive tech, which otherwise hears nothing
   * at all while the screen is full of placeholder blocks.
   */
  readonly label?: string;
}

/**
 * A loading placeholder.
 *
 * The pulse is a CSS animation, so the global `prefers-reduced-motion` rule in
 * `globals.css` stops it without this component knowing — that rule sets
 * `animation-duration: 0.01ms` on everything, which leaves the block visible
 * and still rather than removing it.
 *
 * This is the one component that takes a `className`, and only for width and
 * height: a skeleton has to match the shape of whatever it stands in for, and
 * that shape lives at the call site. No colour may be passed.
 */
export function Skeleton({ className, label }: SkeletonProps) {
  return (
    <div
      role="status"
      aria-label={label ?? "Loading"}
      className={cx("animate-pulse rounded-lg bg-raised", className)}
    />
  );
}
