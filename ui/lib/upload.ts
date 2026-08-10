import { createSHA256 } from "hash-wasm";
import {
  finalizeUpload,
  findInProgressUpload,
  getUpload,
  openUpload,
  putChunk,
} from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/client";
import type { Upload, UploadFinalize } from "@/lib/api/schemas";

/**
 * One clip, from a dropped `File` to a row in the media library.
 *
 * The constraint that shapes everything here: **never load a whole file into
 * memory**. A gym session is multi-gigabyte, and the engine is measured at
 * under 500MB RSS while receiving one (criterion 13). The browser side has to
 * hold to the same discipline or the tab is what dies instead.
 *
 * So the file is only ever touched through `File.slice()`, which returns a lazy
 * view backed by the file on disk — never `file.arrayBuffer()`, which would
 * read the whole thing.
 */

/** Amendment 004: 8MB. Matches the engine's default and the guide's constraint. */
export const CHUNK_BYTES = 8 * 1024 * 1024;

export type UploadPhase =
  | "queued"
  | "hashing"
  | "uploading"
  | "finalizing"
  | "succeeded"
  | "failed";

export interface TransferState {
  readonly phase: UploadPhase;
  /** 0–1 within the current phase. Hashing and sending are both real work. */
  readonly progress: number;
  /** True when this transfer picked up an interrupted one. */
  readonly resumed: boolean;
  /** Set on `failed`: the engine's sentence, or ours. Never a stack trace. */
  readonly message?: string;
}

export type OnTransferState = (state: TransferState) => void;

/**
 * SHA-256 of a file, computed over 8MB slices.
 *
 * `crypto.subtle.digest` cannot do this: it is one-shot and takes the entire
 * buffer, so hashing a 2GB clip with it means holding 2GB in the tab. `hash-wasm`
 * exposes an incremental hasher, which is the whole reason it is a dependency.
 *
 * This digest is not an optimisation. It is the key the resume lookup uses —
 * the engine's partial unique index is on `(project_id, declared_sha256)` and
 * treats NULLs as distinct, so a transfer that declares no hash cannot be found
 * again by a tab that lost its session id (amendment 004 §7).
 */
export async function hashFile(
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  const hasher = await createSHA256();
  hasher.init();

  for (let offset = 0; offset < file.size; offset += CHUNK_BYTES) {
    if (signal?.aborted === true) throw new DOMException("aborted", "AbortError");
    const slice = file.slice(offset, Math.min(offset + CHUNK_BYTES, file.size));
    hasher.update(new Uint8Array(await slice.arrayBuffer()));
    onProgress?.(Math.min(offset + CHUNK_BYTES, file.size) / Math.max(1, file.size));
  }

  return hasher.digest("hex");
}

/**
 * Find the transfer this file already belongs to, or start one.
 *
 * **This is the resume-on-mount obligation** (amendment 004 §7). A refreshed
 * tab, a crashed tab and a reopened browser all arrive holding no session id,
 * and the naive response — start a new transfer — now fails loudly against the
 * partial unique index instead of quietly orphaning a `.part` file. The lookup
 * is what turns that from an error into a resume.
 *
 * `POST /uploads` also resolves the race by returning the open session rather
 * than raising, so both paths converge; the lookup is still done first because
 * it is the one that reports `bytes_received` without touching anything.
 */
async function openOrResume(
  projectId: string,
  file: File,
  sha256: string,
): Promise<ApiResult<Upload>> {
  const existing = await findInProgressUpload(projectId, sha256);
  if (existing.ok) return existing;

  // Only a genuine miss means "nothing to resume, so start one". Anything else
  // — unreachable, a bad project id — is a real failure, and starting a fresh
  // transfer on top of it would be the orphaned-`.part` bug all over again.
  if (existing.code !== "upload_not_found") return existing;

  return openUpload(projectId, {
    display_name: file.name,
    size_bytes: file.size,
    chunk_size_bytes: CHUNK_BYTES,
    sha256,
  });
}

/**
 * How many times to re-ask the engine where it is before giving up.
 *
 * A mismatch is not patched over by seeking — the engine's offset is
 * authoritative and accepting a chunk at the wrong place writes a hole that
 * only surfaces as a hash mismatch at the end of a multi-gigabyte transfer.
 * Bounded because an unbounded resync loop against a stuck engine is an
 * infinite one.
 */
const MAX_RESYNCS = 3;

export async function transferFile(
  projectId: string,
  file: File,
  onState: OnTransferState,
  signal?: AbortSignal,
): Promise<ApiResult<UploadFinalize>> {
  const fail = (message: string): void =>
    onState({ phase: "failed", progress: 0, resumed: false, message });

  let sha256: string;
  try {
    onState({ phase: "hashing", progress: 0, resumed: false });
    sha256 = await hashFile(
      file,
      (fraction) => onState({ phase: "hashing", progress: fraction, resumed: false }),
      signal,
    );
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") {
      fail("Cancelled before the transfer started.");
      return { ok: false, code: "aborted", message: "Cancelled." };
    }
    fail("This file could not be read. It may have been moved or renamed.");
    return { ok: false, code: "unreadable_file", message: "Could not read the file." };
  }

  const opened = await openOrResume(projectId, file, sha256);
  if (!opened.ok) {
    fail(opened.message);
    return opened;
  }

  const upload = opened.data;
  const resumed = upload.resumed && upload.bytes_received > 0;
  let offset = upload.bytes_received;
  let resyncs = 0;

  const report = (): void =>
    onState({
      phase: "uploading",
      progress: file.size === 0 ? 1 : offset / file.size,
      resumed,
    });

  report();

  while (offset < file.size) {
    if (signal?.aborted === true) {
      fail("Transfer cancelled. It can be resumed from here.");
      return { ok: false, code: "aborted", message: "Cancelled." };
    }

    const end = Math.min(offset + upload.chunk_size_bytes, file.size);
    const sent = await putChunk(upload.id, offset, file.slice(offset, end), signal);

    if (!sent.ok) {
      if (sent.code === "chunk_offset_mismatch" && resyncs < MAX_RESYNCS) {
        // The engine committed a different amount than we assumed — usually a
        // chunk that landed while the response was lost. Ask where it is.
        resyncs += 1;
        const where = await getUpload(upload.id);
        if (where.ok) {
          offset = where.data.bytes_received;
          report();
          continue;
        }
      }
      fail(sent.message);
      return sent;
    }

    offset = sent.data.bytes_received;
    report();
  }

  onState({ phase: "finalizing", progress: 1, resumed });
  const finalized = await finalizeUpload(upload.id);
  if (!finalized.ok) {
    fail(finalized.message);
    return finalized;
  }

  onState({ phase: "succeeded", progress: 1, resumed });
  return finalized;
}
