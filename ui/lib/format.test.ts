import { describe, expect, it } from "vitest";
import {
  UNKNOWN,
  formatBytes,
  formatDuration,
  formatFps,
  formatResolution,
  formatTimecode,
  frameRateMode,
} from "@/lib/format";

describe("formatDuration", () => {
  it.each([
    [0, "0:00"],
    [5, "0:05"],
    [65, "1:05"],
    [3600, "1:00:00"],
    [3661, "1:01:01"],
    [5.01, "0:05"],
  ])("%s seconds renders as %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it.each([null, Number.NaN, Number.POSITIVE_INFINITY, -1])(
    "renders %s as unknown rather than as a number",
    (value) => {
      expect(formatDuration(value)).toBe(UNKNOWN);
    },
  );
});

describe("formatTimecode", () => {
  it("includes the frame, because a whole second is 30 frames of ambiguity", () => {
    expect(formatTimecode(5.5, 30)).toBe("0:05.15");
  });

  it("never emits a frame number equal to the rate", () => {
    // Floating-point seconds can land a hair under the next second and floor to
    // exactly `fps`, producing "0:00.30" on a 30fps clip — a frame that does
    // not exist.
    expect(formatTimecode(0.9999999, 30)).toBe("0:00.29");
  });

  it("falls back to the plain duration when the rate is unknown", () => {
    expect(formatTimecode(5.5, null)).toBe("0:05");
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1024, "1.0 KB"],
    [1536, "1.5 KB"],
    [1024 * 1024, "1.0 MB"],
    [2 * 1024 * 1024 * 1024, "2.0 GB"],
  ])("%s bytes renders as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });

  it("drops the decimal once three digits are showing", () => {
    expect(formatBytes(200 * 1024 * 1024)).toBe("200 MB");
  });

  it("renders null as unknown", () => {
    expect(formatBytes(null)).toBe(UNKNOWN);
  });
});

describe("formatFps", () => {
  it("keeps 29.97 apart from 30", () => {
    // Rounding these together is the entire drift problem in one number.
    expect(formatFps(29.97)).toBe("29.97");
    expect(formatFps(30)).toBe("30");
  });

  it("renders null and nonsense as unknown", () => {
    expect(formatFps(null)).toBe(UNKNOWN);
    expect(formatFps(0)).toBe(UNKNOWN);
  });
});

describe("formatResolution", () => {
  it("renders whatever the engine resolved as the display size", () => {
    // A portrait phone clip is landscape pixels plus a rotation tag; the engine
    // has already applied it, so 720x1280 is the correct thing to show.
    expect(formatResolution(720, 1280)).toBe("720x1280");
  });

  it("is unknown unless both dimensions are known", () => {
    expect(formatResolution(1920, null)).toBe(UNKNOWN);
    expect(formatResolution(null, 1080)).toBe(UNKNOWN);
  });
});

describe("frameRateMode", () => {
  it("keeps three values, because null is not 'constant'", () => {
    expect(frameRateMode(true)).toBe("variable");
    expect(frameRateMode(false)).toBe("constant");
    // The container could not answer. Rendering this as "constant" is the
    // silent-desync bug the column exists to catch.
    expect(frameRateMode(null)).toBe("unknown");
  });
});
