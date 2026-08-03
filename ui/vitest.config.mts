import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Unit tests only, Node environment — nothing here renders React, so no JSX
 * transform plugin and no DOM shim are pulled in. `vitest run` (see the `test`
 * script) is single-shot: watch mode would hang CI.
 *
 * Vitest does not read `tsconfig.json` path mappings, so the `@/` alias used by
 * the app is re-declared here.
 */
const projectRoot = fileURLToPath(new URL(".", import.meta.url)).replace(
  /[\\/]$/,
  "",
);

export default defineConfig({
  resolve: {
    alias: { "@": projectRoot },
  },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
