"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/primitives/Button";
import { createProject } from "@/lib/api/client";

/** Matches the engine's `max_length` on the field, so the error arrives here. */
const MAX_NAME = 120;

/**
 * Create a project and go to it.
 *
 * A browser call rather than a server action: the mutation is the browser's
 * either way, and routing it through the Next server would put a second origin
 * between the user and the only process that can answer — which is also the
 * process whose being down is the failure this form has to report.
 *
 * Not optimistic. Optimism is for reversible things
 * (`.claude/rules/frontend-and-licensing.md`), and this navigates: showing a
 * project that does not exist and then landing on a 404 is worse than a
 * half-second wait.
 */
export function NewProject() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = name.trim();

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (trimmed.length === 0 || busy) return;

    setBusy(true);
    setError(null);
    const result = await createProject(trimmed);
    if (!result.ok) {
      setError(result.message);
      setBusy(false);
      return;
    }

    // Deliberately left busy: the row is created and navigation is in flight,
    // so re-enabling the button here would offer a second identical project.
    router.push(`/projects/${result.data.id}`);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <label htmlFor="new-project-name" className="text-sm text-fg-secondary">
        New project
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <input
          id="new-project-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={MAX_NAME}
          disabled={busy}
          placeholder="Push day, 12 Aug"
          autoComplete="off"
          className="min-w-0 flex-1 rounded-lg border border-line bg-raised px-3 py-2 text-sm text-fg-primary placeholder:text-fg-muted transition-colors duration-micro ease-micro hover:border-line-strong disabled:opacity-50"
        />
        <Button type="submit" variant="primary" disabled={busy || trimmed.length === 0}>
          {busy ? "Creating…" : "Create"}
        </Button>
      </div>
      {error !== null && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  );
}
