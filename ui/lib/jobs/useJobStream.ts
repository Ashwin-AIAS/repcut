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
  /**
   * Failed connection attempts since the last successful open; 0 before the
   * first one resolves.
   *
   * Published because `status` alone cannot tell a first connection from a
   * retry: both read "connecting". `/status` needs that difference — "checking"
   * and "the browser cannot reach this" are the same status one second apart,
   * and a page that showed them the same way would flicker between a red
   * verdict and no verdict while the backoff ran.
   */
  readonly attempts: number;
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
  // The ref is the source — backoff has to read it synchronously inside the
  // close handler — and this is the published copy, written from the same line
  // so the two cannot drift.
  const [attempts, setAttempts] = useState(0);

  // Held in refs so the effect can tear down cleanly without re-running on
  // every state change — a reconnect loop that restarts on its own output is
  // the classic way this hook becomes a request storm.
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    let disposed = false;

    /**
     * Record a failed connection and try again later.
     *
     * One place, reached from three: a socket that closed, a socket that
     * errored without ever closing, and a socket that was never created.
     */
    function scheduleReconnect(): void {
      if (disposed) return;
      setStatus("closed");
      const attempt = attemptRef.current;
      attemptRef.current = attempt + 1;
      setAttempts(attemptRef.current);
      const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
      // Jitter so several tabs reconnecting after an engine restart do not
      // arrive in lockstep.
      timerRef.current = setTimeout(connect, base + Math.random() * 500);
    }

    function connect(): void {
      if (disposed) return;
      setStatus("connecting");

      let socket: WebSocket;
      try {
        socket = new WebSocket(engineWebSocketUrl("/ws/jobs"));
      } catch {
        // A malformed URL — a hand-edited `NEXT_PUBLIC_ENGINE_URL` — throws
        // here rather than failing on the wire. Unhandled it would kill the
        // effect, taking the retry with it.
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      // A connection is only ever failed once, however many events say so.
      // A normal drop fires `error` then `close`; without this guard that would
      // count as two failures and skip a step of the backoff.
      let failed = false;
      const fail = (): void => {
        if (failed) return;
        failed = true;
        scheduleReconnect();
      };

      socket.onopen = () => {
        if (disposed) return;
        attemptRef.current = 0;
        setAttempts(0);
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

      socket.onclose = fail;

      // `onerror` was assumed to be always followed by `onclose`, so this used
      // to be `socket.close()` and nothing else. It is not always followed.
      // Measured in Chrome: a socket refused by the page's
      // Content-Security-Policy is *constructed*, fires `error`, and lands in
      // `readyState === CLOSED` without ever firing `close` — so `close()` on it
      // is a no-op, the reconnect never ran, and the panel read "Connecting to
      // the engine…" for as long as the tab was open while the engine sat idle
      // and logged nothing, because nothing had reached it.
      //
      // Failing from here as well is what turns that into a reported failure:
      // `status` becomes "closed" and `attempts` advances, so the editor can say
      // the stream dropped and `/status` can show its browser row red.
      socket.onerror = () => {
        socket.close();
        fail();
      };
    }

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

  return { jobs, status, attempts };
}
