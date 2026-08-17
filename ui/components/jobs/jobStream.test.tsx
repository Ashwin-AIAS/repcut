import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useJobStream } from "@/lib/jobs/useJobStream";

/**
 * The hook against a socket that fails without ever closing.
 *
 * This is the shape of the defect that cost Prompt 02 two evenings, and it is
 * not the shape it looked like. Measured in Chrome: a WebSocket refused by the
 * page's Content-Security-Policy is *constructed* — no throw — then fires
 * `error` and settles in `readyState === CLOSED` **without firing `close`**.
 * The hook's `onerror` handler called `socket.close()` on the strength of
 * "error is always followed by close"; on an already-closed socket that is a
 * no-op, so no close event ever arrived, no reconnect was scheduled, and no
 * state changed. The panel read "Connecting to the engine…" for as long as the
 * tab stayed open while the engine sat idle and logged nothing.
 *
 * Lives here rather than in `lib/`: the `lib` vitest project runs in Node with
 * no DOM, and this needs React and a `WebSocket` global to replace.
 */
function StreamReadout() {
  const { status, attempts } = useJobStream();
  return (
    <span data-testid="readout">
      {status}:{attempts}
    </span>
  );
}

const originalWebSocket = globalThis.WebSocket;

afterEach(() => {
  globalThis.WebSocket = originalWebSocket;
});

/** Counts constructions, so a retry can be told from a single attempt. */
interface FakeSocketLog {
  readonly constructed: () => number;
}

/**
 * A socket that behaves the way a CSP-refused one does: constructed, then
 * `error`, then nothing. `close()` is a no-op, exactly as it is on a socket
 * already in `CLOSED`.
 */
function installRefusingWebSocket(): FakeSocketLog {
  let constructed = 0;

  class RefusedSocket {
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onmessage: (() => void) | null = null;
    readyState = 3;

    constructor() {
      constructed += 1;
      setTimeout(() => this.onerror?.(), 0);
    }

    close(): void {
      // A no-op, as on a socket that is already CLOSED. If the hook relies on
      // this producing a close event, it hangs — which is the bug.
    }
  }

  globalThis.WebSocket = RefusedSocket as unknown as typeof WebSocket;
  return { constructed: () => constructed };
}

describe("useJobStream when the socket errors without closing", () => {
  it("reports the failure instead of waiting forever", async () => {
    installRefusingWebSocket();

    render(<StreamReadout />);

    // "closed" is what makes the editor say the stream dropped and `/status`
    // show its browser row red. A non-zero attempt count is what tells a retry
    // apart from a first connection still in flight.
    await waitFor(() =>
      expect(screen.getByTestId("readout")).toHaveTextContent(/^closed:[1-9]/),
    );
  });

  it("keeps retrying rather than giving up after the first refusal", async () => {
    const log = installRefusingWebSocket();

    render(<StreamReadout />);

    // The backoff starts at 1s, so a second construction proves the retry is
    // wired to the error and not only to a close event.
    await waitFor(() => expect(log.constructed()).toBeGreaterThan(1), { timeout: 4000 });
  });
});

describe("useJobStream when the socket cannot be constructed", () => {
  it("reports a throwing constructor rather than killing the effect", async () => {
    globalThis.WebSocket = class {
      constructor() {
        // A malformed URL — a hand-edited NEXT_PUBLIC_ENGINE_URL — throws here.
        throw new DOMException("The URL is invalid.", "SyntaxError");
      }
    } as unknown as typeof WebSocket;

    render(<StreamReadout />);

    await waitFor(() =>
      expect(screen.getByTestId("readout")).toHaveTextContent(/^closed:[1-9]/),
    );
  });
});
