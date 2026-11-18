/* Test double for the browser EventSource (jsdom has none). Lets tests drive SSE frames, opens and
   failures deterministically. Test-only — never shipped. */

import type { ExecutionEvent } from "@/types/api";

type Listener = (e: MessageEvent<string>) => void;

export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static reset() {
    FakeEventSource.instances = [];
  }
  static get last(): FakeEventSource {
    const es = FakeEventSource.instances.at(-1);
    if (!es) throw new Error("No EventSource was opened");
    return es;
  }

  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readyState = 0;
  closed = false;
  onopen: ((e: Event) => unknown) | null = null;
  onerror: ((e: Event) => unknown) | null = null;
  private listeners = new Map<string, Listener[]>();

  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  removeEventListener() {}

  close() {
    this.readyState = 2;
    this.closed = true;
  }

  // ---- test drivers
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  emit(...events: ExecutionEvent[]) {
    for (const ev of events) {
      for (const fn of this.listeners.get("agentflow") ?? []) fn(new MessageEvent("agentflow", { data: JSON.stringify(ev), lastEventId: String(ev.id) }));
    }
  }

  send(type: string, data = "{}") {
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data }));
  }

  /** `permanent`: the browser gave up (HTTP error) → CLOSED. Otherwise it retries natively. */
  fail(permanent = true) {
    this.readyState = permanent ? 2 : 0;
    this.onerror?.(new Event("error"));
  }
}

let nextId = 1;
export function ev(type: string, payload: Record<string, unknown> = {}, message = type, id?: number): ExecutionEvent {
  const eid = id ?? nextId++;
  return { id: eid, task_id: "t-1", type, message, payload, created_at: new Date(Date.UTC(2026, 10, 18, 10, 0, eid)).toISOString() };
}
export function resetEventIds() {
  nextId = 1;
}
