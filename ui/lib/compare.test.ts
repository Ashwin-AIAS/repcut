import { describe, expect, it } from "vitest";
import { FRAME_CENTRE, frameAt, frameOf, sideSeconds } from "@/lib/compare";

// The same vectors as engine/tests/test_review.py: a scene starting at 1.2345 s
// opens on proxy frame 38 at 30 fps.
describe("before/after frame mapping", () => {
  it("points both sides at the middle of the same frame", () => {
    expect(sideSeconds("after", 0, 38, 30)).toBeCloseTo(FRAME_CENTRE / 30, 6);
    expect(sideSeconds("before", 0, 38, 30)).toBeCloseTo((38 + FRAME_CENTRE) / 30, 6);
    for (const frame of [3, 40, 75]) {
      const before = sideSeconds("before", frame, 38, 30);
      const after = sideSeconds("after", frame, 38, 30);
      expect(Math.floor(before * 30)).toBe(38 + frame);
      expect(Math.floor(after * 30)).toBe(frame);
    }
  });

  it("maps a scrub position and a playing time to a frame inside the scene", () => {
    expect(frameAt(0, 80)).toBe(0);
    expect(frameAt(1, 80)).toBe(79);
    expect(frameAt(0.5, 80)).toBe(40);
    expect(frameAt(-1, 80)).toBe(0);
    expect(frameOf(sideSeconds("after", 12, 38, 30), 30, 80)).toBe(12);
    expect(frameOf(99, 30, 80)).toBe(79);
  });
});
