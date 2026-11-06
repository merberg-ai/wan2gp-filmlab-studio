// Cockpit event stream: reconnects with after_event_id and tells callers to
// resync whenever continuity cannot be guaranteed (reconnect, overflow).

import { useEffect, useRef, useState } from "react";

export interface FilmLabEvent {
  schema: "filmlab.event.v1";
  event_id: number | null;
  type: string;
  project_id: string | null;
  timestamp?: string;
  payload: Record<string, unknown>;
}

export type StreamState = "connecting" | "live" | "offline";

export function useEventStream(onEvent: (e: FilmLabEvent) => void, onResync: () => void): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  const handlers = useRef({ onEvent, onResync });
  handlers.current = { onEvent, onResync };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let lastId = 0;
    let retry = 1000;
    let timer: number | undefined;
    let closed = false;
    const seen = new Set<number>();

    const connect = () => {
      setState("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws${lastId ? `?after_event_id=${lastId}` : ""}`);
      ws.onmessage = (msg) => {
        const ev = JSON.parse(msg.data) as FilmLabEvent;
        if (ev.type === "heartbeat") return;
        if (ev.type === "stream.ready") {
          setState("live");
          retry = 1000;
          lastId = Math.max(lastId, Number(ev.payload.last_event_id) || 0);
          handlers.current.onResync();
          return;
        }
        if (ev.type === "stream.overflow") { handlers.current.onResync(); return; }
        if (ev.event_id !== null) {
          if (seen.has(ev.event_id)) return; // replay + live can overlap
          seen.add(ev.event_id);
          lastId = Math.max(lastId, ev.event_id);
        }
        handlers.current.onEvent(ev);
      };
      ws.onclose = () => {
        if (closed) return;
        setState("offline");
        timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
    };
    connect();
    return () => { closed = true; window.clearTimeout(timer); ws?.close(); };
  }, []);

  return state;
}
