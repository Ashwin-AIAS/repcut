import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NewProject } from "@/components/projects/NewProject";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

function engineAnswers(body: unknown, status = 201): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
}

beforeEach(() => {
  push.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NewProject", () => {
  it("labels its field, rather than relying on the placeholder", () => {
    render(<NewProject />);

    expect(screen.getByLabelText("New project")).toBeInTheDocument();
  });

  it("cannot be submitted empty, or with only spaces", async () => {
    render(<NewProject />);
    const create = screen.getByRole("button", { name: "Create" });

    expect(create).toBeDisabled();

    await userEvent.type(screen.getByLabelText("New project"), "   ");
    expect(create).toBeDisabled();
  });

  it("creates the project and goes to it", async () => {
    engineAnswers({
      id: "project-9",
      name: "Push day",
      created_at: "2026-08-10T09:00:00Z",
      updated_at: "2026-08-10T09:00:00Z",
    });
    render(<NewProject />);

    await userEvent.type(screen.getByLabelText("New project"), "Push day");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/projects/project-9");
    });
  });

  /**
   * Not optimistic, deliberately: this navigates, and showing a project that
   * does not exist and then landing on a 404 is worse than a half-second wait.
   */
  it("shows the engine's own sentence when creation fails, and stays put", async () => {
    engineAnswers(
      { error: { code: "unreachable", message: "The engine is not responding." } },
      503,
    );
    render(<NewProject />);

    await userEvent.type(screen.getByLabelText("New project"), "Push day");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The engine is not responding.",
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<NewProject />);

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
