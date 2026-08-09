import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { AiSuggested } from "@/components/primitives/AiSuggested";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Modal } from "@/components/primitives/Modal";
import { Panel } from "@/components/primitives/Panel";
import { Progress } from "@/components/primitives/Progress";
import { Slider } from "@/components/primitives/Slider";

/**
 * Runs axe against a rendered container and returns its violations.
 *
 * `color-contrast` is switched off here and only here. axe measures contrast
 * from *computed* styles, and jsdom loads no stylesheet — every element is
 * black-on-transparent, so the rule either reports nonsense or reports
 * "incomplete" on everything. Contrast is genuinely checked, from the token
 * values themselves, in `tokens.test.ts`. Turning a rule off because it cannot
 * run is only defensible when something else covers it, and something does.
 */
async function violations(container: HTMLElement): Promise<axe.Result[]> {
  const results = await axe.run(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  return results.violations;
}

describe("Button", () => {
  it("defaults to type=button so it cannot submit a form it merely sits in", () => {
    render(<Button>Export</Button>);
    expect(screen.getByRole("button", { name: "Export" })).toHaveAttribute(
      "type",
      "button",
    );
  });

  it("does not fire while disabled", async () => {
    const user = userEvent.setup();
    let clicks = 0;
    render(
      <Button disabled onClick={() => (clicks += 1)}>
        Export
      </Button>,
    );
    await user.click(screen.getByRole("button", { name: "Export" }));
    expect(clicks).toBe(0);
  });

  it("has no accessibility violations in any variant", async () => {
    const { container } = render(
      <>
        <Button variant="primary">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="danger">Danger</Button>
      </>,
    );
    expect(await violations(container)).toEqual([]);
  });
});

describe("Badge", () => {
  it("spells out an abbreviation for assistive tech", () => {
    render(<Badge label="variable frame rate">VFR</Badge>);
    // The visible text stays short; the accessible name carries the meaning.
    expect(screen.getByText("variable frame rate:")).toBeInTheDocument();
  });
});

describe("Panel", () => {
  it("is a named landmark when titled, and anonymous when not", () => {
    const { rerender } = render(<Panel title="Media library">clips</Panel>);
    expect(
      screen.getByRole("region", { name: "Media library" }),
    ).toBeInTheDocument();

    rerender(<Panel>clips</Panel>);
    // A landmark with no name is noise in a screen reader's landmark list.
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});

describe("Progress", () => {
  it("reports its value through ARIA, not just visually", () => {
    render(<Progress value={0.42} label="Ingesting clip" />);
    const bar = screen.getByRole("progressbar", { name: "Ingesting clip" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("clamps out-of-range and non-finite values", () => {
    // A job reporting 1.02 is a bar overflowing its track; NaN is a blank one.
    const { rerender } = render(<Progress value={1.02} label="Ingesting" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "100",
    );

    rerender(<Progress value={Number.NaN} label="Ingesting" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "0",
    );

    rerender(<Progress value={-3} label="Ingesting" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
  });

  it("shows the step name and the number, never a bare bar", () => {
    render(
      <Progress value={0.4} label="Ingesting" step="encoding the preview proxy" />,
    );
    expect(screen.getByText("encoding the preview proxy")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
  });
});

describe("Slider", () => {
  function Harness() {
    const [value, setValue] = useState(5);
    return (
      <Slider
        label="Speed"
        value={value}
        min={0}
        max={10}
        onChange={setValue}
        display={`${value}x`}
      />
    );
  }

  /*
    What is deliberately NOT asserted here: that ArrowRight increments the
    value. That is the browser's behaviour for `<input type="range">`, and jsdom
    does not implement it — an assertion on it would be measuring the simulator
    rather than the product, and would pass or fail for reasons unrelated to
    this code.

    This repo has already paid for that mistake once: `warn_if_data_dir_synced`
    was correct, tested, and wired to a path nothing executed, so the gate
    printed PASS for a check that never ran (docs/reports/prompt-02.md). The
    honest assertion is the one below — that this really is the native control,
    which is what delegates the keyboard behaviour to the platform in the first
    place. Real arrow-key scrubbing is checked by a human against criterion 16.
  */
  it("is the native range control, with its bounds exposed", () => {
    render(<Harness />);
    const slider = screen.getByRole("slider", { name: "Speed" });

    expect(slider.tagName).toBe("INPUT");
    expect(slider).toHaveAttribute("type", "range");
    expect(slider).toHaveAttribute("min", "0");
    expect(slider).toHaveAttribute("max", "10");
    expect(slider).toHaveValue("5");
  });

  it("hands the caller a number, not the input's string", async () => {
    render(<Harness />);
    const slider = screen.getByRole("slider", { name: "Speed" });

    // `fireEvent.change` is the one way to drive a range input in jsdom. It
    // exercises the onChange wiring, which is this component's own code.
    fireEvent.change(slider, { target: { value: "8" } });

    expect(slider).toHaveValue("8");
    expect(screen.getByText("8x")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Harness />);
    expect(await violations(container)).toEqual([]);
  });
});

describe("Modal", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>Open</Button>
        <Modal
          open={open}
          title="Delete project"
          description="This cannot be undone."
          onClose={() => setOpen(false)}
          footer={<Button variant="danger">Delete</Button>}
        >
          body
        </Modal>
      </>
    );
  }

  it("moves focus in, traps Tab at both edges, and restores focus on close", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Delete project" });
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    const remove = within(dialog).getByRole("button", { name: "Delete" });

    // 1. focus moved in
    expect(cancel).toHaveFocus();

    // 2. Tab from the last control wraps to the first rather than escaping to
    //    the page underneath, which is where an untrapped dialog leaks.
    await user.tab();
    expect(remove).toHaveFocus();
    await user.tab();
    expect(cancel).toHaveFocus();

    // …and backwards from the first.
    await user.tab({ shift: true });
    expect(remove).toHaveFocus();

    // 3. focus returns to the opener
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("is described by its description, not just labelled by its title", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription("This cannot be undone.");
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("has no accessibility violations", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(await violations(container)).toEqual([]);
  });
});

describe("AiSuggested", () => {
  function Harness({ initial = "Gritty" }: { initial?: string }) {
    const suggestion = "Gritty";
    const [value, setValue] = useState(initial);
    return (
      <AiSuggested
        label="Theme"
        isSuggestion={value === suggestion}
        suggestion={suggestion}
        onReset={() => setValue(suggestion)}
        rationale="Chosen from the scene's motion energy."
      >
        <Button onClick={() => setValue("Clean")}>Change theme</Button>
      </AiSuggested>
    );
  }

  it("says which state it is in, in words as well as colour", () => {
    render(<Harness />);
    // Colour alone would be invisible to a colour-blind user and to greyscale.
    expect(screen.getByText("AI suggested")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reset/ })).not.toBeInTheDocument();
  });

  it("offers reset once overridden, and the reset names what it restores", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "Change theme" }));
    expect(screen.getByText("Your choice")).toBeInTheDocument();

    // "Reset" alone does not say what you get back — P2's reset has to be
    // legible without first undoing something to find out.
    const reset = screen.getByRole("button", {
      name: "Reset Theme to the AI suggestion, Gritty",
    });
    await user.click(reset);

    expect(screen.getByText("AI suggested")).toBeInTheDocument();
  });

  it("groups its controls under the value's name", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Theme" })).toBeInTheDocument();
  });

  it("has no accessibility violations in either state", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness />);
    expect(await violations(container)).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Change theme" }));
    expect(await violations(container)).toEqual([]);
  });
});
