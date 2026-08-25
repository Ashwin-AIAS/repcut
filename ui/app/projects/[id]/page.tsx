import Link from "next/link";
import { EngineDown } from "@/components/shell/EngineDown";
import { Workspace } from "@/components/workspace/Workspace";
import { getProjectOnServer, listMediaOnServer } from "@/lib/api/server";

/** The project and its library are live engine state; never prerender them. */
export const dynamic = "force-dynamic";

/**
 * The editor.
 *
 * Both reads happen on the server so the first paint already has the library —
 * a grid that appears a beat after the page is the "loading shell" pattern this
 * design system does not use.
 *
 * They are awaited together: they are independent queries against the same
 * local process, and sequencing them would double the wait for no ordering
 * benefit.
 */
export default async function ProjectPage({
  params,
}: {
  // A Promise since Next 15 — the route can start rendering before the segment
  // is resolved.
  readonly params: Promise<{ readonly id: string }>;
}) {
  const { id } = await params;
  const [project, media] = await Promise.all([
    getProjectOnServer(id),
    listMediaOnServer(id),
  ]);

  if (!project.ok) {
    return (
      <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-8">
        <Link
          href="/"
          className="w-fit text-sm text-fg-secondary underline underline-offset-4 transition-colors duration-micro ease-micro hover:text-fg-primary"
        >
          Repcut
        </Link>
        {project.code === "project_not_found" ? (
          <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
            <h1 className="font-display text-base font-semibold text-fg-primary">
              No such project
            </h1>
            {/* The engine's sentence, not ours. */}
            <p className="text-sm text-fg-secondary">{project.message}</p>
          </section>
        ) : (
          <EngineDown message={project.message} />
        )}
      </main>
    );
  }

  return (
    <Workspace
      project={project.data}
      // A library that failed to load starts empty rather than blocking the
      // editor: the workspace refetches on mount, so a transient failure heals
      // itself, and an upload can start meanwhile.
      initialClips={media.ok ? media.data : []}
    />
  );
}
