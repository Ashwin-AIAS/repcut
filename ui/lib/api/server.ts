import "server-only";
import { requestFrom } from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/client";
import { mediaFileSchema, projectSchema } from "@/lib/api/schemas";
import type { MediaFile, Project } from "@/lib/api/schemas";
import { ENGINE_URL } from "@/lib/env";

/**
 * Engine reads made by the Next server, for the first paint.
 *
 * `server-only` is the guard: importing this from a Client Component is a build
 * error rather than a runtime surprise. It matters because this module reads
 * `ENGINE_URL`, which has no `NEXT_PUBLIC_` prefix precisely so it never
 * reaches the browser bundle.
 *
 * Only reads live here. Everything that mutates — creating a project,
 * uploading, reingesting — is a browser call in `client.ts`, because those need
 * to stream, cancel and report progress, none of which survives a round trip
 * through a server action.
 */

/** Never cached: the library changes as jobs finish, and a stale grid reads as a bug. */
const FRESH: RequestInit = { cache: "no-store" };

export function listProjectsOnServer(): Promise<ApiResult<Project[]>> {
  return requestFrom(ENGINE_URL, "/projects", projectSchema.array(), FRESH);
}

export function getProjectOnServer(id: string): Promise<ApiResult<Project>> {
  return requestFrom(
    ENGINE_URL,
    `/projects/${encodeURIComponent(id)}`,
    projectSchema,
    FRESH,
  );
}

export function listMediaOnServer(
  projectId: string,
): Promise<ApiResult<MediaFile[]>> {
  return requestFrom(
    ENGINE_URL,
    `/projects/${encodeURIComponent(projectId)}/media`,
    mediaFileSchema.array(),
    FRESH,
  );
}
