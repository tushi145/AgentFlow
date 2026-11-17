import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

// Lazy-loaded route chunks can take >1s to import on a cold jsdom run.
configure({ asyncUtilTimeout: 5000 });

// jsdom lacks these browser APIs used by Radix / theme code.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({ matches: query.includes("dark"), media: query, onchange: null, addEventListener() {}, removeEventListener() {},
       addListener() {}, removeListener() {}, dispatchEvent: () => false }) as unknown as MediaQueryList;
}
class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver ??= RO;
Element.prototype.scrollIntoView ??= () => {};
Element.prototype.hasPointerCapture ??= () => false;

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
});
afterAll(() => server.close());
