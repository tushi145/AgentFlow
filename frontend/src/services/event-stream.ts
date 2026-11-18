/* Resilient client for GET /tasks/{id}/events (Server-Sent Events).

   Backend contract (backend/app/api/routes/tasks.py):
   - `event: agentflow` with `id:` = persisted event id; data = ExecutionEvent JSON.
   - History after `Last-Event-ID` (header) or `?after=` (query) is replayed, then live events stream.
   - `task_finished` is the terminal event; `event: end` is sent when the task is already terminal
     and nothing is left to replay. `event: ping` is a keep-alive.

   Reconnection has two layers:
   1. Transient drops: the browser's EventSource reconnects by itself and sends `Last-Event-ID`.
   2. Hard failures (HTTP error, proxy 5xx, server down): EventSource closes for good, so we open a
      new one with `?after=<last seen id>` using exponential backoff.
   3. Silent stalls: if the server dies behind a proxy, the proxy can keep the browser's connection
      open with no data and no error (observed with the Vite dev proxy). The backend sends a ping
      every 15 s, so a stream with no frame at all for `stallTimeoutMs` is treated as dead and
      reopened the same way as (2).
   Events are de-duplicated by id, so a replay can never show an event twice. */

import type { ExecutionEvent } from "@/types/api";

export const TERMINAL_EVENT = "task_finished";

export type StreamState =
  | "connecting"    // first connection attempt
  | "open"          // connected; replaying history and/or streaming live
  | "reconnecting"  // connection lost; retrying (native or manual)
  | "ended"         // task reached a terminal state; stream closed on purpose
  | "failed";       // gave up after repeated failures; `retry()` starts again

export interface StreamSnapshot {
  state: StreamState;
  lastEventId: number;
  /** Consecutive failed attempts in the current outage (0 while healthy). */
  attempt: number;
  /** Delay before the next manual reconnect, when one is scheduled. */
  nextRetryMs: number | null;
}

/** The subset of EventSource this client uses (lets tests inject a double). */
export interface ESLike {
  readonly readyState: number;
  close(): void;
  addEventListener(type: string, listener: (e: MessageEvent<string>) => void): void;
  onopen: ((ev: Event) => unknown) | null;
  onerror: ((ev: Event) => unknown) | null;
}
export type EventSourceFactory = (url: string) => ESLike;

export interface StreamOptions {
  onEvents: (events: ExecutionEvent[]) => void;
  onSnapshot?: (s: StreamSnapshot) => void;
  /** Called once when the stream ends because the task is terminal. */
  onEnd?: () => void;
  factory?: EventSourceFactory;
  baseDelayMs?: number;
  maxDelayMs?: number;
  /** Consecutive failed attempts before reporting `failed`. */
  maxAttempts?: number;
  /** Events are delivered in small batches so a long replay renders once, not per event. */
  flushMs?: number;
  /** No frame (event, ping, end) for this long ⇒ the connection is considered dead. Must exceed the
   *  server ping interval (15 s) with margin. */
  stallTimeoutMs?: number;
}

const CLOSED = 2;

export class TaskEventStream {
  private es: ESLike | null = null;
  private snap: StreamSnapshot = { state: "connecting", lastEventId: 0, attempt: 0, nextRetryMs: null };
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private stallTimer: ReturnType<typeof setTimeout> | null = null;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private buffer: ExecutionEvent[] = [];
  private stopped = false;
  private readonly opts: Required<Omit<StreamOptions, "onSnapshot" | "onEnd">> & Pick<StreamOptions, "onSnapshot" | "onEnd">;

  private readonly url: string;

  constructor(url: string, opts: StreamOptions) {
    this.url = url;
    this.opts = {
      ...opts,
      // `??` (not spread defaults): callers may pass `undefined` explicitly.
      factory: opts.factory ?? ((u) => new EventSource(u)),
      baseDelayMs: opts.baseDelayMs ?? 1000,
      maxDelayMs: opts.maxDelayMs ?? 15_000,
      maxAttempts: opts.maxAttempts ?? 8,
      flushMs: opts.flushMs ?? 40,
      stallTimeoutMs: opts.stallTimeoutMs ?? 35_000,
    };
  }

  get snapshot(): StreamSnapshot {
    return this.snap;
  }

  start(): void {
    this.stopped = false;
    if (typeof window !== "undefined") window.addEventListener("online", this.onOnline);
    this.connect();
  }

  /** Permanently stop (component unmount). */
  close(): void {
    this.stopped = true;
    if (typeof window !== "undefined") window.removeEventListener("online", this.onOnline);
    this.clearRetry();
    this.clearStall();
    this.flush();
    this.es?.close();
    this.es = null;
  }

  /** Manual retry after `failed` (or to skip the backoff wait). */
  retry(): void {
    if (this.snap.state === "ended") return;
    this.clearRetry();
    this.update({ attempt: 0 });
    this.connect();
  }

  private readonly onOnline = () => {
    if (this.snap.state === "reconnecting" || this.snap.state === "failed") this.retry();
  };

  private update(p: Partial<StreamSnapshot>) {
    this.snap = { ...this.snap, ...p };
    this.opts.onSnapshot?.(this.snap);
  }

  private connect() {
    if (this.stopped) return;
    this.es?.close();
    const last = this.snap.lastEventId;
    const url = last > 0 ? `${this.url}?after=${last}` : this.url;
    this.update({ state: this.snap.state === "connecting" ? "connecting" : "reconnecting", nextRetryMs: null });
    const es = this.opts.factory(url);
    this.es = es;

    this.armStall(es);

    es.onopen = () => {
      if (this.es !== es) return;
      this.armStall(es);
      this.update({ state: "open", attempt: 0, nextRetryMs: null });
    };
    es.addEventListener("ping", () => {
      if (this.es === es) this.armStall(es);
    });
    es.addEventListener("agentflow", (e) => {
      if (this.es !== es) return;
      this.armStall(es);
      let ev: ExecutionEvent;
      try {
        ev = JSON.parse(e.data) as ExecutionEvent;
      } catch {
        return; // malformed frame: ignore rather than break the stream
      }
      if (typeof ev.id !== "number" || ev.id <= this.snap.lastEventId) return; // de-duplicate replays
      this.snap = { ...this.snap, lastEventId: ev.id };
      this.push(ev);
      if (ev.type === TERMINAL_EVENT) this.finish();
    });
    es.addEventListener("end", () => {
      if (this.es === es) this.finish();
    });
    es.onerror = () => {
      if (this.es !== es || this.snap.state === "ended" || this.stopped) return;
      this.onFailure(es, es.readyState === CLOSED);
    };
  }

  /** `hard`: the connection is unusable and must be replaced (vs. the browser retrying natively). */
  private onFailure(es: ESLike, hard: boolean) {
    const attempt = this.snap.attempt + 1;
    if (attempt >= this.opts.maxAttempts) {
      this.clearStall();
      es.close();
      this.update({ state: "failed", attempt, nextRetryMs: null });
      return;
    }
    if (!hard) {
      // Transient drop: the browser reconnects by itself and sends Last-Event-ID.
      this.update({ state: "reconnecting", attempt, nextRetryMs: null });
      return;
    }
    // Hard failure: reopen with ?after=<last id> after a backoff.
    this.clearStall();
    es.close();
    this.schedule(attempt);
  }

  private armStall(es: ESLike) {
    this.clearStall();
    this.stallTimer = setTimeout(() => {
      this.stallTimer = null;
      if (this.es !== es || this.stopped || this.snap.state === "ended") return;
      this.onFailure(es, true); // silent stall: no frame, no error — treat the connection as dead
    }, this.opts.stallTimeoutMs);
  }

  private clearStall() {
    if (this.stallTimer !== null) {
      clearTimeout(this.stallTimer);
      this.stallTimer = null;
    }
  }

  private schedule(attempt: number) {
    const delay = Math.min(this.opts.maxDelayMs, this.opts.baseDelayMs * 2 ** (attempt - 1));
    this.update({ state: "reconnecting", attempt, nextRetryMs: delay });
    this.clearRetry();
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, delay);
  }

  private finish() {
    this.flush();
    this.clearRetry();
    this.clearStall();
    this.es?.close();
    this.es = null;
    this.update({ state: "ended", attempt: 0, nextRetryMs: null });
    this.opts.onEnd?.();
  }

  private push(ev: ExecutionEvent) {
    this.buffer.push(ev);
    if (this.flushTimer === null) this.flushTimer = setTimeout(() => this.flush(), this.opts.flushMs);
  }

  private flush() {
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    if (!this.buffer.length) return;
    const batch = this.buffer;
    this.buffer = [];
    this.opts.onEvents(batch);
  }

  private clearRetry() {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }
}
