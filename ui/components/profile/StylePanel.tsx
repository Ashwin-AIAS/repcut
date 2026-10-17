"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { getProfile, resetProfile, setProfileEnabled } from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/client";
import type { Profile, ProfileItemName } from "@/lib/api/schemas";

export interface StylePanelProps {
  /** Called after any change, so the edit and grade re-plan with (or without) the profile. */
  readonly onChanged?: () => void;
}

const LABELS: Record<ProfileItemName, string> = {
  theme: "Theme",
  intensity: "Grade strength",
  pacing: "Pacing",
  caption_style: "Caption style",
  music_energy: "Music energy",
};

/**
 * "Your style": what Repcut learned from your changes, and why.
 *
 * P2 applies to the profile itself: every learned item shows the evidence it
 * came from in words, can be forgotten on its own, and the whole profile can
 * be switched off. It only nudges suggestions; no option ever disappears.
 */
export function StylePanel({ onChanged }: StylePanelProps) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stale = false;
    void getProfile().then((result) => {
      if (stale) return;
      if (result.ok) setProfile(result.data);
      else setError(result.message);
    });
    return () => {
      stale = true;
    };
  }, []);

  const settle = useCallback(
    (promise: Promise<ApiResult<Profile>>) => {
      setBusy(true);
      void promise.then((result) => {
        setBusy(false);
        if (result.ok) {
          setProfile(result.data);
          setError(null);
          onChanged?.();
        } else {
          setError(result.message);
        }
      });
    },
    [onChanged],
  );

  if (profile === null) {
    return error === null ? null : (
      <Panel title="Your style">
        <p className="p-3 text-sm text-fg-muted">{error}</p>
      </Panel>
    );
  }

  const learned = profile.items.filter((item) => item.events > 0);

  return (
    <Panel
      title="Your style"
      action={
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          aria-pressed={profile.enabled}
          onClick={() => settle(setProfileEnabled(!profile.enabled))}
        >
          {profile.enabled ? "On" : "Off"}
        </Button>
      }
    >
      <div className="flex flex-col gap-3 p-3" aria-busy={busy}>
        {error !== null && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <p className="text-xs text-fg-muted">
          Learned from the changes you make, on this machine. It nudges suggestions; it never
          removes an option.
        </p>
        {learned.length === 0 ? (
          <p className="text-sm text-fg-secondary">
            Nothing learned yet. Change a theme, the pacing or a caption style a few times and it
            will show here.
          </p>
        ) : (
          <ul aria-label="What your style profile learned" className="flex flex-col gap-2">
            {learned.map((item) => (
              <li key={item.item} data-item={item.item} className="flex items-start gap-3">
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="flex items-center gap-2 text-sm text-fg-primary">
                    {LABELS[item.item]}
                    <Badge tone={item.active && profile.enabled ? "ai" : "neutral"}>
                      {item.active ? (profile.enabled ? "in use" : "paused") : "still learning"}
                    </Badge>
                  </span>
                  <span className="text-xs text-fg-secondary">{item.why}</span>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  aria-label={`Forget ${LABELS[item.item]}`}
                  onClick={() => settle(resetProfile(item.item))}
                >
                  Forget
                </Button>
              </li>
            ))}
          </ul>
        )}
        {learned.length > 0 && (
          <Button
            size="sm"
            variant="danger"
            disabled={busy}
            onClick={() => settle(resetProfile("all"))}
          >
            Forget everything
          </Button>
        )}
      </div>
    </Panel>
  );
}
