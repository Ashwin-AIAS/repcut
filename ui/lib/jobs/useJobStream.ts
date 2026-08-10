"use client";

import { useEffect, useRef, useState } from "react";
import { engineWebSocketUrl } from "@/lib/api/engine";
import { jobEventSchema } from "@/lib/api/schemas";
import type { JobEvent } from "@/lib/api/schemas";

export type StreamStatus = "connecting" | "open" | "closed";

export interface JobStream {
  /** Jobs seen on this connection, newest activity first. */
  readonly jobs: readonly JobEvent[];
  readonly status: StreamStatus;
}

/** Backoff between reconnects: 1s, 2s, 4s, 8s, then hold. Jittered at the call site. */
const BACKOFF_MS = [1000, 2000, 4000, 8000] as const;

/**
 * Live job state from the engine's `/ws/jobs`.
 *
 * Long jobs must surface `queued → running (percent + step) → succeeded |
 * failed (cause)`, never a bare spinner
 * (`.claude/rules/frontend-and-licensing.md`). This hook is where that stream
 * arrives; rendering it is the components' job.
 *
 * Events are keyed by job id and merged, not appended: the engine sends the
 * whole job on every change, so a list built by appending would show the same
 * job five times as it progressed.
 *
 * Reconnects with backoff. A dropped socket is normal here — the engine is
 * restarted by hand during development, and criterion 4 kills it deliberately —
 * so a stream that gives up after one failure would leave a permanently frozen
 * progress bar with no indication it had stopped listening.
 */
export function useJobStream(enabled = true): JobStream {
  const [jobs, setJobs] = useState<readonly JobEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");

  // Held in refs so the effect can tear down cleanly without re-running on
  // every state change — a reconnect loop that restarts on its own output is
  // the classic way this hook becomes a request storm.
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    let disposed = false;

    const connect = (): void => {
      if (disposed) return;
      setStatus("connecting");

      const socket = new WebSocket(engineWebSocketUrl("/ws/jobs"));
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) return;
        attemptRef.current = 0;
        setStatus("open");
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        let payload: unknown;
        try {
          payload = JSON.parse(event.data);
        } catch {
          // A frame that is not JSON is not something the UI can act on, and
          // dropping it is better than tearing down a working stream.
          return;
        }

        // A frame that is not a job event is the engine's keepalive, which is
        // sent through the quiet so a half-open socket is noticed.
        const parsed = jobEventSchema.safeParse(payload);
        if (!parsed.success) return;
        const job = parsed.data;

        setJobs((current) => {
          const without = current.filter(
            (existing) => existing.job_id !== job.job_id,
          );
          return [job, ...without];
        });
      };

      socket.onclose = () => {
        if (disposed) return;
        setStatus("closed");
        const attempt = attemptRef.current;
        attemptRef.current = attempt + 1;
        const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        // Jitter so several tabs reconnecting after an engine restart do not
        // arrive in lockstep.
        timerRef.current = setTimeout(connect, base + Math.random() * 500);
      };

      // `onerror` is always followed by `onclose`, so reconnection is handled
      // in one place rather than raced from two.
      socket.onerror = () => socket.close();
    };

    connect();

    return () => {
      disposed = true;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      // Detach before closing: a close handler that fires during teardown would
      // schedule a reconnect for a component that no longer exists.
      const socket = socketRef.current;
      if (socket !== null) {
        socket.onclose = null;
        socket.onerror = null;
        socket.onmessage = null;
        socket.onopen = null;
        socket.close();
      }
    };
  }, [enabled]);

  return { jobs, status };
}
