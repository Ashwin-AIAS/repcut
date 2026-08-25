import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { EngineDown } from "@/components/shell/EngineDown";

describe("EngineDown", () => {
  /**
   * The engine is a process the user starts themselves, so "not running" is a
   * normal state rather than an exceptional one — and the useful response is
   * the command that fixes it, not an apology.
   */
  it("gives the command that fixes it, not just the failure", () => {
    render(<EngineDown message="The engine is not responding." />);

    expect(screen.getByText("make dev")).toBeInTheDocument();
    expect(screen.getByText("The engine is not responding.")).toBeInTheDocument();
  });

  it("is a named region, so it is findable rather than an anonymous red box", () => {
    render(<EngineDown message="The engine is not responding." />);

    expect(
      screen.getByRole("region", { name: "Engine not reachable" }),
    ).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<EngineDown message="The engine is not responding." />);

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
