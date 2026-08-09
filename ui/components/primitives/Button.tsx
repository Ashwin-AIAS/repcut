import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/cx";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

/**
 * `variant` and `size` are the whole styling surface. There is no `className`
 * escape hatch on purpose — the moment one exists, the tokens stop being the
 * only source of style and every later prompt inherits a component whose look
 * depends on its call site.
 */
export interface ButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
  readonly children: ReactNode;
}

/*
  No variant uses the accent hue. It means exactly one thing in this UI — "the
  AI chose this" (P2) — and a primary button borrowing it for prominence would
  make the signal ambiguous everywhere it genuinely appears.

  `primary` inverts instead: near-white on the dark canvas, 17.8:1, and visually
  louder than anything the accent could do.
*/
const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-fg-primary text-surface hover:bg-fg-secondary disabled:hover:bg-fg-primary",
  secondary:
    "bg-raised text-fg-primary border border-line hover:border-line-strong",
  ghost: "bg-transparent text-fg-secondary hover:text-fg-primary hover:bg-raised",
  danger:
    "bg-transparent text-danger border border-danger hover:bg-raised",
};

/*
  Padding, not a fixed height. The token scale is 4/8/12/16/24/32px, and a
  `h-10` (2.5rem) is not on it — it would silently resolve to Tailwind's own
  default scale, which is exactly the "second source of style" the tokens exist
  to prevent. Height falls out of padding plus the type ramp's line height.
*/
const SIZES: Record<ButtonSize, string> = {
  sm: "px-3 py-1 text-sm",
  md: "px-4 py-2 text-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      // An unspecified `type` inside a form defaults to `submit`, which makes
      // an unrelated button navigate. Defaulted here rather than remembered.
      type={type}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium",
        "transition-colors duration-micro ease-micro",
        // `disabled:` rather than an `aria-disabled` style: a disabled button is
        // removed from the tab order by the platform, which is the behaviour we
        // want, and the cursor tells a mouse user before they click.
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
