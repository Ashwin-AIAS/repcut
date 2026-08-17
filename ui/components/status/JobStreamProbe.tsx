"use client";

import { Field, StatusPill, type Tone } from "@/components/status/StatusField";
import { useJobStream } from "@/lib/jobs/useJobStream";

export type ProbeVerdict = "checking" | "reachable" | "blocked";

/**
 * Whether *this browser* can open the engine's job stream.
 *
 * The engine's own `jobs_socket_ready` cannot answer this. The socket was
 * refused by the page's Content-Security-Policy, before a handshake was ever
 * sent — so the engine was healthy, reported healthy, and the product did not
 * work. Anything sitting between the two, a policy, a wrong origin, a stale
 * `NEXT_PUBLIC_ENGINE_URL`, is visible only from here.
 *
 * It uses `useJobStream`, the same hook the editor uses, deliberately: a probe
 * that opened its own socket its own way would be testing itself.
 */
export function useJobStreamProbe(): ProbeVerdict {
  const { status, attempts } = useJobStream();

  // A pure function of what the hook reports, with no state of its own. The
  // reconnect backoff makes `status` oscillate "closed" → "connecting" →
  // "closed", so a row reading `status` alone would flicker between a red
  // verdict and no verdict; `attempts` is what separates the first connection
  // (still checking) from a retry after a failure (already blocked).
  if (status === "open") return "reachable";
  if (status === "closed") return "blocked";
  return attempts > 0 ? "blocked" : "checking";
}

const LABEL: Record<ProbeVerdict, string> = {
  checking: "Checking…",
  reachable: "Yes",
  blocked: "No — jobs will not report progress",
};

const TONE: Record<ProbeVerdict, Tone> = {
  checking: "neutral",
  reachable: "positive",
  blocked: "danger",
};

const HINT: Record<ProbeVerdict, string> = {
  checking: "Opening the job stream from this page.",
  reachable: "Job progress will stream live into the editor.",
  blocked:
    "The browser could not open the engine's job stream, so long jobs will look frozen. " +
    "Check that the engine is running, and that NEXT_PUBLIC_ENGINE_URL names the port it is on.",
};

/**
 * The `/status` row for the browser's own half of the answer.
 *
 * Renders a whole `Field` rather than handing the page a value: the page is a
 * Server Component and cannot pass this one a function, so the boundary is
 * drawn here, at a component with no props.
 */
export function JobStreamProbe() {
  const verdict = useJobStreamProbe();

  return (
    <Field label="This browser can reach it" hint={HINT[verdict]}>
      <StatusPill tone={TONE[verdict]} label={LABEL[verdict]} />
    </Field>
  );
}
