import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { Dropzone } from "@/components/upload/Dropzone";
import { UploadQueue, type QueuedTransfer } from "@/components/upload/UploadQueue";

function file(name = "clip.mp4"): File {
  return new File([new Uint8Array(8)], name, { type: "video/mp4" });
}

function transfer(overrides: Partial<QueuedTransfer> = {}): QueuedTransfer {
  return {
    key: "1",
    name: "clip.mp4",
    sizeBytes: 2_147_483_648,
    state: { phase: "uploading", progress: 0.5, resumed: false },
    ...overrides,
  };
}

describe("Dropzone", () => {
  /**
   * Drag-and-drop cannot be performed with a keyboard at all, so the file input
   * is not a fallback — without it the feature does not exist for some users.
   */
  it("is operable by keyboard through a labelled file input", async () => {
    const onFiles = vi.fn();
    render(<Dropzone onFiles={onFiles} />);

    const input = screen.getByLabelText("Choose video clips to upload");
    await userEvent.upload(input, file());

    expect(onFiles).toHaveBeenCalledWith([expect.objectContaining({ name: "clip.mp4" })]);
  });

  it("lets the same file be chosen twice, which is what a retry is", async () => {
    const onFiles = vi.fn();
    render(<Dropzone onFiles={onFiles} />);

    const input = screen.getByLabelText("Choose video clips to upload");
    await userEvent.upload(input, file());
    await userEvent.upload(input, file());

    expect(onFiles).toHaveBeenCalledTimes(2);
  });

  it("accepts a drop, and does not let the browser navigate to the file", () => {
    const onFiles = vi.fn();
    const { container } = render(<Dropzone onFiles={onFiles} />);
    const zone = container.firstElementChild;
    if (zone === null) throw new Error("nothing rendered");

    // jsdom implements no `DataTransfer`, so the drop carries a stand-in with
    // the one property the handler reads.
    const notCancelled = fireEvent.drop(zone, {
      dataTransfer: { files: [file("drop.mp4")] },
    });

    expect(onFiles).toHaveBeenCalledWith([expect.objectContaining({ name: "drop.mp4" })]);
    // `dispatchEvent` returns false when the handler called preventDefault —
    // without which the browser navigates away to the dropped file.
    expect(notCancelled).toBe(false);
  });

  it("sends nothing while disabled", async () => {
    const onFiles = vi.fn();
    render(<Dropzone onFiles={onFiles} disabled />);

    await userEvent.click(screen.getByRole("button", { name: "Choose clips" }));

    expect(onFiles).not.toHaveBeenCalled();
  });

  it("discloses that footage stays local, at the point it is chosen", () => {
    render(<Dropzone onFiles={() => {}} />);

    expect(screen.getByText("Your footage stays on this machine.")).toBeInTheDocument();
  });
});

describe("UploadQueue", () => {
  /**
   * Checksumming a 2GB clip takes ten seconds before a byte is sent. A bar at
   * 0% with no explanation for ten seconds is where a user decides the app is
   * broken, so every phase is named.
   */
  it.each([
    ["hashing", "Checksumming"],
    ["uploading", "Transferring"],
    ["finalizing", "Verifying and storing"],
  ] as const)("names the %s phase", (phase, label) => {
    render(
      <UploadQueue
        transfers={[transfer({ state: { phase, progress: 0.1, resumed: false } })]}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("says when a transfer resumed, which is the only visible proof it did", () => {
    render(
      <UploadQueue
        transfers={[transfer({ state: { phase: "uploading", progress: 0.7, resumed: true } })]}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText("resumed")).toBeInTheDocument();
  });

  it("shows a failure as the engine worded it, and announces it", () => {
    render(
      <UploadQueue
        transfers={[
          transfer({
            state: {
              phase: "failed",
              progress: 0,
              resumed: false,
              message: "that file is not a video the engine can read",
            },
          }),
        ]}
        onCancel={() => {}}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("that file is not a video the engine can read");
  });

  it("offers cancel while the bytes are still ours to stop sending", async () => {
    const onCancel = vi.fn();
    render(<UploadQueue transfers={[transfer()]} onCancel={onCancel} />);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledWith("1");
  });

  /**
   * Not during finalize: the engine is hashing and moving the assembled file,
   * and aborting the request would not un-run it — it would only cost the UI
   * the answer.
   */
  it.each(["finalizing", "succeeded", "failed"] as const)(
    "offers no cancel once %s",
    (phase) => {
      render(
        <UploadQueue
          transfers={[transfer({ state: { phase, progress: 1, resumed: false } })]}
          onCancel={() => {}}
        />,
      );

      expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    },
  );

  it("has no accessibility violations", async () => {
    const { container } = render(
      <UploadQueue transfers={[transfer()]} onCancel={() => {}} />,
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
