import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { urls } from "@/services/api";
import { type EventSourceFactory, type StreamSnapshot, TaskEventStream } from "@/services/event-stream";
import type { ExecutionEvent } from "@/types/api";

const INITIAL: StreamSnapshot = { state: "connecting", lastEventId: 0, attempt: 0, nextRetryMs: null };

interface Store { taskId: string | undefined; events: ExecutionEvent[]; snapshot: StreamSnapshot }

/** Streams a task's persisted events (full replay, then live) and exposes connection health. */
export function useTaskEvents(taskId: string | undefined, opts: {
  onEvents?: (batch: ExecutionEvent[]) => void;
  onEnd?: () => void;
  factory?: EventSourceFactory;
} = {}) {
  // State is tagged with its task id, so switching tasks never shows the previous task's events.
  const [store, setStore] = useState<Store>({ taskId, events: [], snapshot: INITIAL });
  const streamRef = useRef<TaskEventStream | null>(null);
  // Latest callbacks without re-opening the stream when they change.
  const cb = useRef(opts);
  useLayoutEffect(() => {
    cb.current = opts;
  });

  useEffect(() => {
    if (!taskId) return;
    const stream = new TaskEventStream(urls.taskEvents(taskId), {
      onEvents: (batch) => {
        setStore((s) => (s.taskId === taskId ? { ...s, events: [...s.events, ...batch] } : { taskId, events: batch, snapshot: s.snapshot }));
        cb.current.onEvents?.(batch);
      },
      onSnapshot: (snapshot) => setStore((s) => (s.taskId === taskId ? { ...s, snapshot } : { taskId, events: [], snapshot })),
      onEnd: () => cb.current.onEnd?.(),
      factory: cb.current.factory,
    });
    streamRef.current = stream;
    stream.start();
    return () => {
      stream.close();
      streamRef.current = null;
    };
  }, [taskId]);

  const retry = useCallback(() => streamRef.current?.retry(), []);
  const current = store.taskId === taskId ? store : { events: [], snapshot: INITIAL };
  return { events: current.events, ...current.snapshot, retry };
}
