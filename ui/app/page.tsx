import Link from "next/link";
import { NewProject } from "@/components/projects/NewProject";
import { EngineDown } from "@/components/shell/EngineDown";
import { listProjectsOnServer } from "@/lib/api/server";
import type { Project } from "@/lib/api/schemas";

/**
 * The project list is live state in another process, so this page is never
 * prerendered — `next build` would otherwise bake in whatever the engine
 * answered on the build machine, which is usually "nothing, I am not running".
 */
export const dynamic = "force-dynamic";

/**
 * Dates are formatted on the server against a fixed locale and an explicit
 * time zone. `toLocaleString()` with neither reads the *server's* locale during
 * SSR and the browser's on hydration, which React reports as a hydration
 * mismatch on any machine where the two differ.
 */
const dateFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

function formatCreated(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : `${dateFormat.format(at)} UTC`;
}

function ProjectRow({ project }: { readonly project: Project }) {
  return (
    <li>
      <Link
        href={`/projects/${project.id}`}
        className="flex items-baseline justify-between gap-4 rounded-xl border border-line bg-panel px-4 py-3 transition-colors duration-micro ease-micro hover:border-line-strong"
      >
        <span className="truncate text-sm text-fg-primary">{project.name}</span>
        <span className="shrink-0 font-mono text-xs text-fg-muted">
          {formatCreated(project.created_at)}
        </span>
      </Link>
    </li>
  );
}

export default async function HomePage() {
  const result = await listProjectsOnServer();

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-8">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-2xl font-semibold tracking-tight text-fg-primary">
          Repcut
        </h1>
        <p className="text-sm text-fg-secondary">
          Local-first AI video editor for gym footage. Your footage stays on this
          machine.
        </p>
      </header>

      {result.ok ? (
        <>
          <NewProject />

          {result.data.length === 0 ? (
            <p className="text-sm text-fg-muted">
              No projects yet. Create one, then drop your clips into it.
            </p>
          ) : (
            <section aria-label="Projects" className="flex flex-col gap-2">
              <h2 className="text-sm text-fg-secondary">Projects</h2>
              <ul className="flex flex-col gap-2">
                {result.data.map((project) => (
                  <ProjectRow key={project.id} project={project} />
                ))}
              </ul>
            </section>
          )}
        </>
      ) : (
        <EngineDown message={result.message} />
      )}

      <div className="flex flex-wrap items-center gap-4">
        <Link
          href="/prompts"
          className="w-fit text-sm text-fg-secondary underline underline-offset-4 transition-colors duration-micro ease-micro hover:text-fg-primary"
        >
          Prompt completion dashboard
        </Link>
        <Link
          href="/status"
          className="w-fit text-sm text-fg-secondary underline underline-offset-4 transition-colors duration-micro ease-micro hover:text-fg-primary"
        >
          Engine status
        </Link>
      </div>
    </main>
  );
}
