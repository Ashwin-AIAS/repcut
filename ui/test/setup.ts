import "@testing-library/jest-dom/vitest";

/**
 * jsdom implements no layout engine, so a handful of browser APIs that the
 * design system's components legitimately call are simply absent. Each shim
 * below exists because a component needs it, not to silence a warning.
 */

// `matchMedia` backs the `prefers-reduced-motion` handling. jsdom has no
// implementation at all, so a component that asks reduced-motion questions
// throws rather than returning an answer. Default to "no preference"; the
// reduced-motion test overrides this per-case.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

// The player calls `scrollIntoView` when a keyboard selection moves off screen.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

/**
 * jsdom ships no media pipeline: `play()` is undefined and `currentTime` never
 * advances. The player's keyboard handling is pure arithmetic over
 * `currentTime`, which is exactly what the tests assert, so the element needs
 * to behave like a seekable stub rather than like a real decoder.
 */
if (!HTMLMediaElement.prototype.play) {
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: function play(this: HTMLMediaElement): Promise<void> {
      this.dispatchEvent(new Event("play"));
      return Promise.resolve();
    },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: function pause(this: HTMLMediaElement): void {
      this.dispatchEvent(new Event("pause"));
    },
  });
}
