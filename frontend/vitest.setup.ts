import "@testing-library/jest-dom/vitest";
if (!window.matchMedia) {
  window.matchMedia = (q: string) => ({ matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as unknown as MediaQueryList;
}
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
