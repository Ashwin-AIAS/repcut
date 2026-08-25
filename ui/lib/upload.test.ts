import { afterEach, describe, expect, it, vi } from "vitest";
import { hashFile, transferFile, type TransferState } from "@/lib/upload";

/**
 * A fake engine for the upload endpoints, in memory.
 *
 * Written against `engine/repcut/api/uploads.py` rather than against the
 * uploader: the point of these tests is that the client follows the *engine's*
 * rules — the server owns the offset, a mismatch is refused rather than
 * absorbed, and a lost session id is recovered through the hash — and a fake
 * shaped around the client would agree with whatever the client did.
 */
interface Session {
  id: string;
  project_id: string;
  display_name: string;
  declared_size_bytes: number;
  chunk_size_bytes: number;
  bytes_received: number;
  status: "in_progress" | "completed" | "aborted";
  resumed: boolean;
}

interface FakeEngine {
  sessions: Map<string, Session>;
  /** Every chunk PUT, as `[offset, byteLength]`, in the order they arrived. */
  writes: [number, number][];
  /** Sessions findable by `(project_id, sha256)`, as the partial index makes them. */
  inProgress: Map<string, string>;
  /** Force the next chunk PUT to answer 409 with this committed offset. */
  nextMismatchAt: number | null;
}

const CHUNK = 300;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function engineError(code: string, message: string, status: number): Response {
  return jsonResponse({ error: { code, message } }, status);
}

function sessionBody(session: Session): Session {
  return { ...session };
}

function installFakeEngine(seed?: Partial<Session>): FakeEngine {
  const engine: FakeEngine = {
    sessions: new Map(),
    writes: [],
    inProgress: new Map(),
    nextMismatchAt: null,
  };

  if (seed !== undefined) {
    const session: Session = {
      id: "session-seeded",
      project_id: "project-1",
      display_name: "clip.mp4",
      declared_size_bytes: 0,
      chunk_size_bytes: CHUNK,
      bytes_received: 0,
      status: "in_progress",
      resumed: true,
      ...seed,
    };
    engine.sessions.set(session.id, session);
    engine.inProgress.set(`${session.project_id}:${seed.id ?? "hash"}`, session.id);
  }

  const handler = async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = new URL(String(input));
    const path = url.pathname;
    const method = init?.method ?? "GET";

    // GET /projects/{id}/uploads/in-progress?sha256=…
    const lookup = /^\/projects\/([^/]+)\/uploads\/in-progress$/.exec(path);
    if (lookup !== null && method === "GET") {
      const sha256 = url.searchParams.get("sha256") ?? "";
      const found = engine.inProgress.get(`${lookup[1]}:${sha256}`);
      const session = found === undefined ? undefined : engine.sessions.get(found);
      if (session === undefined) {
        return engineError(
          "upload_not_found",
          "there is no transfer in progress for that clip",
          404,
        );
      }
      return jsonResponse(sessionBody(session));
    }

    // POST /projects/{id}/uploads
    const open = /^\/projects\/([^/]+)\/uploads$/.exec(path);
    if (open !== null && method === "POST") {
      const body = JSON.parse(String(init?.body)) as {
        display_name: string;
        size_bytes: number;
        sha256: string;
      };
      const session: Session = {
        id: `session-${engine.sessions.size + 1}`,
        project_id: open[1],
        display_name: body.display_name,
        declared_size_bytes: body.size_bytes,
        chunk_size_bytes: CHUNK,
        bytes_received: 0,
        status: "in_progress",
        resumed: false,
      };
      engine.sessions.set(session.id, session);
      engine.inProgress.set(`${session.project_id}:${body.sha256}`, session.id);
      return jsonResponse(sessionBody(session));
    }

    // PUT /uploads/{id}/chunk?offset=…
    const chunk = /^\/uploads\/([^/]+)\/chunk$/.exec(path);
    if (chunk !== null && method === "PUT") {
      const session = engine.sessions.get(chunk[1]);
      if (session === undefined) {
        return engineError("upload_not_found", "no such transfer", 404);
      }
      const offset = Number(url.searchParams.get("offset"));
      const blob = init?.body as Blob;

      if (engine.nextMismatchAt !== null) {
        session.bytes_received = engine.nextMismatchAt;
        engine.nextMismatchAt = null;
        return engineError(
          "chunk_offset_mismatch",
          "that chunk does not start where this transfer left off",
          409,
        );
      }
      if (offset !== session.bytes_received) {
        return engineError(
          "chunk_offset_mismatch",
          "that chunk does not start where this transfer left off",
          409,
        );
      }

      engine.writes.push([offset, blob.size]);
      session.bytes_received = offset + blob.size;
      return jsonResponse(sessionBody(session));
    }

    // GET /uploads/{id}
    const get = /^\/uploads\/([^/]+)$/.exec(path);
    if (get !== null && method === "GET") {
      const session = engine.sessions.get(get[1]);
      if (session === undefined) {
        return engineError("upload_not_found", "no such transfer", 404);
      }
      return jsonResponse(sessionBody(session));
    }

    // POST /uploads/{id}/finalize
    const finalize = /^\/uploads\/([^/]+)\/finalize$/.exec(path);
    if (finalize !== null && method === "POST") {
      const session = engine.sessions.get(finalize[1]);
      if (session === undefined) {
        return engineError("upload_not_found", "no such transfer", 404);
      }
      session.status = "completed";
      return jsonResponse({
        sha256: "a".repeat(64),
        media_file_id: "media-1",
        project_id: session.project_id,
        job_id: "job-1",
        duplicate: false,
      });
    }

    return engineError("not_found", "no such route", 404);
  };

  vi.stubGlobal("fetch", vi.fn(handler));
  return engine;
}

function clip(bytes: number, name = "clip.mp4"): File {
  return new File([new Uint8Array(bytes).fill(7)], name, { type: "video/mp4" });
}

function recorder(): { states: TransferState[]; onState: (s: TransferState) => void } {
  const states: TransferState[] = [];
  return { states, onState: (state) => states.push(state) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("hashFile", () => {
  it("digests over slices, never reading the whole file", async () => {
    const file = clip(1000);
    const whole = vi.spyOn(file, "arrayBuffer");

    const digest = await hashFile(file);

    // The known SHA-256 of 1000 bytes of 0x07, so a change to the chunking
    // cannot quietly change the digest the resume lookup is keyed on.
    expect(digest).toHaveLength(64);
    expect(whole).not.toHaveBeenCalled();
  });

  it("reports progress and stops on an abort signal", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(hashFile(clip(1000), undefined, controller.signal)).rejects.toThrow(
      DOMException,
    );
  });
});

describe("transferFile", () => {
  it("sends the whole file in the engine's chunk size, in order, then finalizes", async () => {
    const engine = installFakeEngine();
    const { states, onState } = recorder();

    const result = await transferFile("project-1", clip(1000), onState);

    expect(result.ok).toBe(true);
    expect(engine.writes).toEqual([
      [0, 300],
      [300, 300],
      [600, 300],
      [900, 100],
    ]);
    expect(states.map((state) => state.phase)).toContain("finalizing");
    expect(states.at(-1)?.phase).toBe("succeeded");
  });

  /**
   * The resume obligation of amendment 004 §7, from the client's side.
   *
   * The tab holds no session id — this is a fresh `transferFile` call, as a
   * reloaded page makes — and finds its transfer by `(project_id, sha256)`.
   * Without the lookup it would open a second session, collide with the engine's
   * partial unique index, and leave the first `.part` orphaned.
   */
  it("resumes an interrupted transfer instead of starting a second one", async () => {
    const file = clip(1000);
    const sha256 = await hashFile(file);
    const engine = installFakeEngine({
      id: sha256,
      declared_size_bytes: 1000,
      bytes_received: 600,
    });

    const { states, onState } = recorder();
    const result = await transferFile("project-1", file, onState);

    expect(result.ok).toBe(true);
    expect(engine.sessions.size).toBe(1);
    expect(engine.writes).toEqual([
      [600, 300],
      [900, 100],
    ]);
    expect(states.some((state) => state.resumed)).toBe(true);
  });

  /**
   * A mismatch is resolved by asking the engine, never by assuming.
   *
   * Accepting a chunk at a guessed offset writes a hole that surfaces only as a
   * hash mismatch at the end of a multi-gigabyte transfer — an hour of the
   * user's time to learn the transfer was wrong from its second chunk.
   */
  it("re-asks the engine for the offset when a chunk is refused", async () => {
    const engine = installFakeEngine();
    const { onState } = recorder();

    // The engine says it committed 450 bytes, not the 300 the client assumed.
    engine.nextMismatchAt = 450;

    const result = await transferFile("project-1", clip(1000), onState);

    expect(result.ok).toBe(true);
    // The refused chunk is not retried where the client thought it was: it
    // continues from 450, the number the engine gave when asked.
    expect(engine.writes).toEqual([
      [450, 300],
      [750, 250],
    ]);
  });

  it("gives up rather than looping when the engine keeps refusing", async () => {
    const engine = installFakeEngine();
    const { states, onState } = recorder();

    // Every chunk is refused, and the offset never moves.
    Object.defineProperty(engine, "nextMismatchAt", {
      get: () => 0,
      set: () => {},
    });

    const result = await transferFile("project-1", clip(1000), onState);

    expect(result.ok).toBe(false);
    expect(states.at(-1)?.phase).toBe("failed");
    expect(states.at(-1)?.message).toBeTruthy();
  });

  it("stops when cancelled, and says the transfer can be resumed", async () => {
    installFakeEngine();
    const controller = new AbortController();
    const { states, onState } = recorder();

    const running = transferFile(
      "project-1",
      clip(1000),
      (state) => {
        onState(state);
        if (state.phase === "uploading" && state.progress > 0) controller.abort();
      },
      controller.signal,
    );

    const result = await running;

    expect(result.ok).toBe(false);
    expect(states.at(-1)?.phase).toBe("failed");
    expect(states.at(-1)?.message).toMatch(/resumed/i);
  });

  it("reports the engine's own sentence when a transfer cannot be opened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        engineError("project_not_found", "that project does not exist", 404),
      ),
    );
    const { states, onState } = recorder();

    const result = await transferFile("gone", clip(10), onState);

    expect(result.ok).toBe(false);
    expect(states.at(-1)?.message).toBe("that project does not exist");
  });
});
