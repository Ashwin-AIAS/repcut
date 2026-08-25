import type { z } from "zod";
import { ENGINE_ORIGIN } from "@/lib/api/engine";
import {
  apiErrorSchema,
  jobSchema,
  mediaFileSchema,
  projectSchema,
  uploadFinalizeSchema,
  uploadSchema,
} from "@/lib/api/schemas";
import type { Job, MediaFile, Project, Upload, UploadFinalize } from "@/lib/api/schemas";

/**
 * The engine client.
 *
 * Follows `lib/health.ts`: every call returns a discriminated result rather
 * than throwing. A dead engine is a card the user can read and act on, not an
 * exception that unmounts a tree — and video work has too many legitimately
 * slow, legitimately failing steps for exceptions to be the normal path.
 */

export type ApiResult<T> =
  | { readonly ok: true; readonly data: T }
  | {
      readonly ok: false;
      /** The engine's own code when it named the failure, else a client-side one. */
      readonly code: string;
      /** One human-readable sentence. Never a stack trace, never a path. */
      readonly message: string;
    };

const REQUEST_TIMEOUT_MS = 15_000;

const UNREACHABLE =
  "The engine is not responding. Start it with `make dev`, then try again.";

/**
 * A failure that is ours rather than the engine's.
 *
 * Kept separate from the engine's vocabulary so the UI never invents a `code`
 * that looks like one the engine could have sent.
 */
function clientError<T>(code: string, message: string): ApiResult<T> {
  return { ok: false, code, message };
}

async function readError(response: Response): Promise<{ code: string; message: string }> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      code: "unreadable_error",
      message: `The engine answered with HTTP ${response.status} and no explanation.`,
    };
  }

  const parsed = apiErrorSchema.safeParse(body);
  if (parsed.success) return parsed.data.error;

  return {
    code: "unreadable_error",
    message: `The engine answered with HTTP ${response.status} in an unexpected shape.`,
  };
}

/**
 * One request against an explicit origin, parsed against `schema`.
 *
 * The origin is a parameter because the browser and the Next server reach the
 * engine by different values — `NEXT_PUBLIC_ENGINE_URL` and `ENGINE_URL`
 * respectively (see `lib/api/engine.ts` for why the public one has to exist).
 * Sharing this function is what keeps their error handling identical; two
 * copies would drift, and the failure would be a page that renders a different
 * message than the one beside it.
 *
 * `signal` lets a caller cancel — used by the uploader, where a component that
 * unmounts mid-transfer must not keep writing.
 */
export async function requestFrom<S extends z.ZodTypeAny>(
  origin: string,
  path: string,
  schema: S,
  init?: RequestInit,
): Promise<ApiResult<z.infer<S>>> {
  let response: Response;
  try {
    response = await fetch(`${origin}${path}`, {
      ...init,
      headers: { accept: "application/json", ...init?.headers },
      signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return clientError("aborted", "That request was cancelled.");
    }
    // Connection refused, DNS failure, timeout — the engine is not there.
    return clientError("unreachable", UNREACHABLE);
  }

  if (!response.ok) {
    const { code, message } = await readError(response);
    return { ok: false, code, message };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return clientError(
      "invalid_response",
      "The engine's reply was not valid JSON.",
    );
  }

  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    const fields = [
      ...new Set(
        parsed.error.issues
          .map((issue) => issue.path.join("."))
          .filter((p) => p.length > 0),
      ),
    ];
    return clientError(
      "invalid_response",
      fields.length > 0
        ? `The engine's reply did not match the expected shape. Problem fields: ${fields.join(", ")}.`
        : "The engine's reply did not match the expected shape.",
    );
  }

  return { ok: true, data: parsed.data };
}

/** A request from the browser, against the engine's public origin. */
export function request<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  init?: RequestInit,
): Promise<ApiResult<z.infer<S>>> {
  return requestFrom(ENGINE_ORIGIN, path, schema, init);
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  };
}

// --- projects ---------------------------------------------------------------

export function listProjects(): Promise<ApiResult<Project[]>> {
  return request("/projects", projectSchema.array());
}

export function getProject(id: string): Promise<ApiResult<Project>> {
  return request(`/projects/${encodeURIComponent(id)}`, projectSchema);
}

export function createProject(name: string): Promise<ApiResult<Project>> {
  return request("/projects", projectSchema, json({ name }));
}

export function listMedia(projectId: string): Promise<ApiResult<MediaFile[]>> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/media`,
    mediaFileSchema.array(),
  );
}

export function reingest(mediaFileId: string): Promise<ApiResult<Job>> {
  return request(
    `/media/${encodeURIComponent(mediaFileId)}/reingest`,
    jobSchema,
    { method: "POST" },
  );
}

/**
 * Stop a job. Idempotent, and a no-op on one that already finished.
 *
 * The response is the row as it stands now — for a running job still
 * `running`, because the handler unwinds on its own and the terminal
 * `cancelled` event arrives over the socket. Callers render the stream, not
 * this reply.
 */
export function cancelJob(jobId: string): Promise<ApiResult<Job>> {
  return request(`/jobs/${encodeURIComponent(jobId)}/cancel`, jobSchema, {
    method: "POST",
  });
}

// --- artifact URLs ----------------------------------------------------------
//
// Plain URLs rather than fetches: a `<video src>` and an `<img src>` are loaded
// by the browser itself, which is what gets us Range requests and progressive
// decode for free. Wrapping them in fetch would buffer a whole proxy in memory
// to hand it back as a blob.

export function proxyUrl(mediaFileId: string): string {
  return `${ENGINE_ORIGIN}/media/${encodeURIComponent(mediaFileId)}/proxy`;
}

export function thumbnailStripUrl(mediaFileId: string): string {
  return `${ENGINE_ORIGIN}/media/${encodeURIComponent(mediaFileId)}/thumbnail-strip`;
}

// --- uploads ----------------------------------------------------------------

export function findInProgressUpload(
  projectId: string,
  sha256: string,
): Promise<ApiResult<Upload>> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/uploads/in-progress?sha256=${encodeURIComponent(sha256)}`,
    uploadSchema,
  );
}

export function openUpload(
  projectId: string,
  body: {
    display_name: string;
    size_bytes: number;
    chunk_size_bytes: number;
    sha256: string;
  },
): Promise<ApiResult<Upload>> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/uploads`,
    uploadSchema,
    json(body),
  );
}

/** Where the engine thinks a transfer stands — the authoritative resume offset. */
export function getUpload(uploadId: string): Promise<ApiResult<Upload>> {
  return request(`/uploads/${encodeURIComponent(uploadId)}`, uploadSchema);
}

export function putChunk(
  uploadId: string,
  offset: number,
  chunk: Blob,
  signal?: AbortSignal,
): Promise<ApiResult<Upload>> {
  return request(
    `/uploads/${encodeURIComponent(uploadId)}/chunk?offset=${offset}`,
    uploadSchema,
    {
      method: "PUT",
      // A Blob body streams from disk; reading it into an ArrayBuffer first
      // would put the whole chunk in JS memory for no reason.
      body: chunk,
      headers: { "content-type": "application/octet-stream" },
      signal,
    },
  );
}

export function finalizeUpload(
  uploadId: string,
): Promise<ApiResult<UploadFinalize>> {
  return request(
    `/uploads/${encodeURIComponent(uploadId)}/finalize`,
    uploadFinalizeSchema,
    { method: "POST" },
  );
}
