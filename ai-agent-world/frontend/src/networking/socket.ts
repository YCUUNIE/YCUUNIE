// WebSocket client with automatic reconnection. All AI/world updates arrive
// here as events and are fed into the store; no AI API is ever called from the
// browser.
import { store } from "../state/store";

type StatusListener = (connected: boolean) => void;

class GameSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectDelay = 1000;
  private pingTimer: number | null = null;
  private statusListeners: StatusListener[] = [];
  connected = false;

  constructor() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = `${proto}://${location.host}/ws`;
  }

  onStatus(fn: StatusListener) {
    this.statusListeners.push(fn);
  }

  private setStatus(c: boolean) {
    this.connected = c;
    this.statusListeners.forEach((f) => f(c));
  }

  connect() {
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.setStatus(true);
      this.reconnectDelay = 1000;
      this.pingTimer = window.setInterval(() => this.send({ type: "ping" }), 15000);
    };
    this.ws.onmessage = (ev) => {
      let msg: any;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "init") {
        store.config = msg.data.config;
        store.loadSnapshot(msg.data.state);
        (msg.data.events || []).forEach((e: any) => store.events.push(e));
        (msg.data.chat || []).forEach((c: any) =>
          store.chat.push({ sender: c.sender, target: c.target, message: c.message, ts: c.ts }));
        store.emit("events");
        store.emit("chat");
      } else if (msg.type === "event") {
        store.applyEvent(msg.event);
      }
    };
    this.ws.onclose = () => {
      this.setStatus(false);
      if (this.pingTimer) { clearInterval(this.pingTimer); this.pingTimer = null; }
      this.scheduleReconnect();
    };
    this.ws.onerror = () => { this.ws?.close(); };
  }

  private scheduleReconnect() {
    setTimeout(() => this.connect(), this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 1.6, 8000);
  }

  send(obj: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }
}

export const socket = new GameSocket();

// Thin REST helpers.
export const api = {
  async get(path: string) {
    const r = await fetch(path);
    return r.json();
  },
  async post(path: string, body?: any) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return r.json();
  },
  async put(path: string, body: any) {
    const r = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  },
};
