import { Badge } from "@/components/primitives/Badge";

export interface PrivacyDisclosureProps {
  /**
   * The current analysis job's `step`, verbatim. Pass `null` when there is no
   * running analysis job for this clip, or its step names something else.
   */
  readonly step: string | null;
}

/**
 * `analysis/pipeline.py`'s exact wording for the moment it calls Gemini,
 * one scene at a time: `"sending scene {index + 1} of {total} to Gemini for
 * analysis"`. This is the disclosure hook — that string appearing in the job
 * stream **is** the moment frames are being sent, so parsing it out is how
 * the UI knows when to show the banner rather than guessing from job status.
 */
const SENDING_STEP_PATTERN = /^sending scene (\d+) of (\d+) to Gemini for analysis$/;

export interface SendingFrame {
  readonly index: number;
  readonly total: number;
}

/** Exported for the test that pins the engine's exact step wording. */
export function parseSendingStep(step: string | null): SendingFrame | null {
  if (step === null) return null;
  const match = SENDING_STEP_PATTERN.exec(step);
  if (match === null) return null;
  return { index: Number(match[1]), total: Number(match[2]) };
}

/**
 * P4's disclosure, live: shown at the exact moment a sampled frame is sent to
 * Gemini, not in a settings page and not as a one-time notice at the top of
 * the session (`.claude/rules/frontend-and-licensing.md`).
 *
 * Renders nothing outside that moment — it disappears the instant the job
 * moves to its next scene, then reappears for that scene's own send, which is
 * what makes "one frame per scene" legible rather than an unverifiable claim
 * in copy somewhere.
 */
export function PrivacyDisclosure({ step }: PrivacyDisclosureProps) {
  const sending = parseSendingStep(step);
  if (sending === null) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-3 rounded-xl border border-line bg-accent-surface p-3"
    >
      <Badge tone="ai" label="privacy disclosure">
        Sending to Gemini
      </Badge>
      <div className="flex flex-col gap-1">
        <p className="text-sm text-fg-primary">
          Sending frame {sending.index} of {sending.total} to Gemini for analysis.
        </p>
        <p className="text-xs text-fg-secondary">
          One still frame per scene, nothing else — your footage stays on this
          machine.
        </p>
      </div>
    </div>
  );
}
