/**
 * Join class names, dropping anything falsy.
 *
 * Deliberately not `clsx` or `tailwind-merge`: this is eight lines, and a merge
 * library would invite the pattern it exists to rescue — components that accept
 * arbitrary overriding classes. The design system's position is that a variant
 * is a named prop, not a class a caller passes in.
 */
export function cx(...parts: readonly (string | false | null | undefined)[]): string {
  return parts.filter((part): part is string => Boolean(part)).join(" ");
}
