import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

/**
 * Flat config, required by ESLint 9 — which Next 16 requires in turn.
 *
 * `eslint-config-next@16` exports a real flat-config array, so this imports it
 * directly. The `@eslint/eslintrc` `FlatCompat` shim that the migration guides
 * still show is for the 14/15-era preset and throws a circular-JSON error
 * against this one; it is not needed here.
 *
 * `core-web-vitals` already includes `next` and `next/typescript`, so listing
 * those separately would load the same rules twice.
 */
// Named rather than exported inline: `import/no-anonymous-default-export` is
// one of the rules this file turns on, and it applies to this file too.
const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "coverage/**"],
  },
  ...nextCoreWebVitals,
];

export default config;
