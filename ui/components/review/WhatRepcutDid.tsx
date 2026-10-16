"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { getSummary } from "@/lib/api/client";
import type { Claim, JobEvent } from "@/lib/api/schemas";

export interface WhatRepcutDidProps {
  readonly projectId: string;
  readonly jobs: readonly JobEvent[];
  /** Bumped by the workspace when the edit changes, so the summary is re-read. */
  readonly revision?: number;
}

/**
 * What Repcut did to this edit, in plain words.
 *
 * Every line comes from the engine, which builds it from the edit's own record
 * and refuses to send a line the record does not back. Nothing here is a
 * template: if the edit did not do it, it is not listed.
 */
export function WhatRepcutDid({ projectId, jobs, revision = 0 }: WhatRepcutDidProps) {
  const [claims, setClaims] = useState<readonly Claim[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(0);
  const finished = jobs
    .filter((job) => job.status === "succeeded")
    .map((job) => job.job_id)
    .sort()
    .join(",");

  useEffect(() => {
    let stale = false;
    void getSummary(projectId).then((result) => {
      if (stale) return;
      if (result.ok) {
        setClaims(result.data.claims);
        setError(null);
      } else {
        setError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [projectId, finished, revision, asked]);

  return (
    <Panel
      title="What Repcut did"
      action={
        <Button size="sm" variant="ghost" onClick={() => setAsked((value) => value + 1)}>
          Refresh
        </Button>
      }
    >
      <div className="flex flex-col gap-2 p-3">
        {error !== null && <p className="text-sm text-fg-muted">{error}</p>}
        {claims !== null && claims.length === 0 && (
          <p className="text-sm text-fg-muted">Nothing yet: this edit has no changes to report.</p>
        )}
        {claims !== null && claims.length > 0 && (
          <ul aria-label="Processing summary" className="flex flex-col gap-1">
            {claims.map((claim) => (
              <li key={claim.id} data-claim={claim.id} className="text-sm text-fg-primary">
                {claim.text}
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-fg-muted">
          Each line is read from this edit&apos;s own plan and grades, and checked against them
          before it is shown.
        </p>
      </div>
    </Panel>
  );
}
