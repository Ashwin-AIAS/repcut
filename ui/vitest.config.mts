import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Two projects, because the two kinds of test want different worlds and mixing
 * them costs one of them.
 *
 * `lib` stays exactly as it was: Node environment, no JSX transform, no DOM
 * shim. Those tests cover fetch wrappers and parsers, and running them inside
 * jsdom would replace the runtime they actually execute in with a simulation of
 * a different one.
 *
 * `ui` renders React into jsdom. It is where the accessibility baseline is
 * measured (`make verify-02` criterion 12), so it needs a real DOM with real
 * focus behaviour rather than a shallow renderer.
 *
 * `vitest run` (see the `test` script) is single-shot: watch mode would hang CI.
 *
 * Vitest does not read `tsconfig.json` path mappings, so the `@/` alias used by
 * the app is re-declared here — once, and shared by both projects.
 */
const projectRoot = fileURLToPath(new URL(".", import.meta.url)).replace(
  /[\\/]$/,
  "",
);

const alias = { "@": projectRoot };

export default defineConfig({
  test: {
    projects: [
      {
        resolve: { alias },
        test: {
          name: "lib",
          environment: "node",
          include: ["lib/**/*.test.ts"],
        },
      },
      {
        plugins: [react()],
        resolve: { alias },
        test: {
          name: "ui",
          environment: "jsdom",
          include: ["components/**/*.test.tsx", "app/**/*.test.tsx"],
          setupFiles: ["./test/setup.ts"],
          // Testing Library's cleanup runs between tests; without this each
          // render would stack in the same document and `getByRole` would start
          // finding the previous test's markup.
          globals: true,
        },
      },
    ],
  },
});
