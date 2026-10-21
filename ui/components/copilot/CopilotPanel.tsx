"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { getCopilot, getHistory, sendCopilotMessage, undoChange } from "@/lib/api/client";
import type { CopilotAction, CopilotMessage, HistoryEntry } from "@/lib/api/schemas";
import { cx } from "@/lib/cx";

export interface CopilotPanelProps {
  readonly projectId: string;
  /** Bumped by the workspace whenever any panel changes the edit. */
  readonly revision: number;
  /** Called after a change through the copilot (or an undo), so the panels refetch. */
  readonly onChanged: () => void;
}

type Tab = "chat" | "history";
const MAX_CHARS = 500;

/**
 * The copilot: say what you want, and it runs the same operations the panels'
 * controls run. Chat for intent, the panels for precision.
 *
 * Every change it makes is a card in the conversation with its own undo, and
 * the History tab is the one undo stack both doors share - so the chat is an
 * edit log. With no key, no free budget left or no network, the panel says why
 * and stays out of the way; nothing else depends on it (P5). What is sent is
 * disclosed where it is typed (P4): the words and a short text summary of the
 * edit, never footage or frames.
 */
export function CopilotPanel({ projectId, revision, onChanged }: CopilotPanelProps) {
  const [tab, setTab] = useState<Tab>("chat");
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [available, setAvailable] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    void getCopilot(projectId).then((result) => {
      setLoaded(true);
      if (result.ok) {
        setMessages(result.data.messages);
        setAvailable(result.data.available);
        setReason(result.data.reason);
      } else {
        setAvailable(false);
        setReason(result.message);
      }
    });
    void getHistory(projectId).then((result) => {
      if (result.ok) setEntries(result.data.entries);
    });
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh, revision]);

  const send = (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (text === "" || busy || !available) return;
    setBusy(true);
    setError(null);
    const pending: CopilotMessage = {
      id: `pending-${messages.length}`,
      role: "user",
      text,
      outcome: null,
      actions: [],
    };
    setMessages((current) => [...current, pending]);
    setDraft("");
    void sendCopilotMessage(projectId, text).then((result) => {
      setBusy(false);
      if (!result.ok) {
        setError(result.message);
        refresh();
        return;
      }
      setAvailable(result.data.available);
      setReason(result.data.reason);
      setMessages((current) => [...current, result.data.reply]);
      if (result.data.reply.actions.some((action) => action.changed)) onChanged();
      refresh();
    });
  };

  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  const undo = (entryId: string) => {
    setBusy(true);
    void undoChange(projectId, entryId).then((result) => {
      setBusy(false);
      setError(result.ok ? null : result.message);
      if (result.ok) onChanged();
      refresh();
    });
  };

  if (!loaded) return null;

  return (
    <Panel
      title="Copilot"
      action={
        <div role="tablist" aria-label="Copilot views" className="flex gap-1">
          {(["chat", "history"] as const).map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              onClick={() => {
                setTab(name);
                refresh();
              }}
              className={cx(
                "rounded-lg px-2 py-1 text-xs transition-colors duration-micro ease-micro",
                tab === name ? "bg-raised text-fg-primary" : "text-fg-muted hover:text-fg-primary",
              )}
            >
              {name === "chat" ? "Chat" : "History"}
            </button>
          ))}
        </div>
      }
    >
      {tab === "chat" ? (
        <div className="flex flex-col gap-3 p-3">
          {!available && (
            <p role="status" className="rounded-lg border border-line px-3 py-2 text-sm text-fg-secondary">
              {reason ?? "The copilot is off."}
            </p>
          )}
          <ol aria-label="Conversation" className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto">
            {messages.length === 0 && (
              <li className="text-sm text-fg-muted">
                Try “moodier colours and 20 seconds” or “lock the first cut”.
              </li>
            )}
            {messages.map((message) => (
              <li
                key={message.id}
                className={cx(
                  "rounded-lg px-3 py-2 text-sm",
                  message.role === "user"
                    ? "self-end bg-raised text-fg-primary"
                    : "self-start border border-line text-fg-secondary",
                )}
              >
                <p>{message.text}</p>
                {message.actions.length > 0 && (
                  <ul aria-label="Changes" className="mt-2 flex flex-col gap-1">
                    {message.actions.map((action, index) => (
                      <ChangeCard
                        key={`${message.id}-${index}`}
                        action={action}
                        disabled={busy}
                        onUndo={undo}
                      />
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
          {error !== null && (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          )}
          <form onSubmit={send} className="flex flex-col gap-2">
            <label htmlFor="copilot-input" className="sr-only">
              Tell the copilot what to change
            </label>
            <textarea
              id="copilot-input"
              value={draft}
              maxLength={MAX_CHARS}
              rows={2}
              disabled={!available || busy}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKey}
              placeholder={available ? "What should change?" : "The copilot is off"}
              className="resize-none rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg-primary placeholder:text-fg-muted disabled:opacity-60"
            />
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-fg-muted">
                Sends your words and a short text summary of this edit to Gemini. No footage, no frames.
              </p>
              <Button size="sm" type="submit" disabled={!available || busy || draft.trim() === ""}>
                Send
              </Button>
            </div>
          </form>
        </div>
      ) : (
        <ol aria-label="Change history" className="flex max-h-[34rem] flex-col gap-1 overflow-y-auto p-3">
          {entries.length === 0 && <li className="text-sm text-fg-muted">No changes yet.</li>}
          {entries.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-2 text-sm">
              <span className={cx(entry.undone ? "text-fg-muted line-through" : "text-fg-secondary")}>
                <Badge>{entry.source === "copilot" ? "Chat" : "Control"}</Badge> {entry.summary}
              </span>
              {!entry.undone && (
                <Button size="sm" disabled={busy} onClick={() => undo(entry.id)}>
                  Undo
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

function ChangeCard({
  action,
  disabled,
  onUndo,
}: {
  readonly action: CopilotAction;
  readonly disabled: boolean;
  readonly onUndo: (entryId: string) => void;
}) {
  const entry = action.entry_id;
  return (
    <li className="flex items-center justify-between gap-2 rounded-lg bg-surface px-2 py-1">
      <span className={cx("text-xs", action.undone ? "text-fg-muted line-through" : "text-fg-primary")}>
        {action.summary}
      </span>
      {action.undoable && entry !== null ? (
        <Button size="sm" disabled={disabled} onClick={() => onUndo(entry)}>
          Undo
        </Button>
      ) : (
        action.undone && <span className="text-xs text-fg-muted">Undone</span>
      )}
    </li>
  );
}
