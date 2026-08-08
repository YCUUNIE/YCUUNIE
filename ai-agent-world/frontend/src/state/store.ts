// Central client-side state store. Deliberately tiny and framework-free.
// Panels and the Phaser scene subscribe to slices they care about.

export interface AgentDTO {
  id: string; name: string; role: string; color: string;
  x: number; y: number; facing: string; state: string; activity: string;
  personality: string[]; goals: string[]; background: string;
  provider: any; allowed_tools: string[]; relationships: Record<string, any>;
  last_action: any; last_summary: string; last_error: string | null;
  last_provider_used: string; last_latency: number; last_tokens: any;
  memories_retrieved: number; recovery_state: string; energy: number;
  current_task_id: string | null;
}

export interface WorldDTO {
  width: number; height: number;
  locations: any[]; objects: any[]; notes: Record<string, any>;
}

export interface EventDTO { type: string; data: any; ts: number; category: string; }

type Listener = () => void;

class Store {
  agents: Record<string, AgentDTO> = {};
  world: WorldDTO | null = null;
  sim = { running: false, paused: false, speed: 1, day: 1, time: 0 };
  events: EventDTO[] = [];
  chat: { sender: string; target?: string; message: string; ts: number }[] = [];
  config: any = null;
  providers: any = null;
  health: any = null;
  focusedAgentId: string | null = null;

  private listeners: Record<string, Listener[]> = {};

  on(topic: string, fn: Listener) {
    (this.listeners[topic] ||= []).push(fn);
    return () => {
      this.listeners[topic] = (this.listeners[topic] || []).filter((f) => f !== fn);
    };
  }

  emit(topic: string) {
    (this.listeners[topic] || []).forEach((f) => f());
    (this.listeners["*"] || []).forEach((f) => f());
  }

  loadSnapshot(state: any) {
    this.world = state.world;
    this.sim = state.sim;
    this.agents = {};
    for (const a of state.agents) this.agents[a.id] = a;
    this.emit("world");
    this.emit("agents");
    this.emit("sim");
  }

  applyEvent(e: EventDTO) {
    // keep a rolling feed
    this.events.push(e);
    if (this.events.length > 400) this.events = this.events.slice(-400);

    const d = e.data || {};
    switch (e.type) {
      case "agent.moved":
      case "agent.state": {
        const a = this.agents[d.id];
        if (a) {
          if (d.x !== undefined) a.x = d.x;
          if (d.y !== undefined) a.y = d.y;
          if (d.facing) a.facing = d.facing;
          if (d.state) a.state = d.state;
          if (d.activity) a.activity = d.activity;
          if (d.recovery_state) a.recovery_state = d.recovery_state;
          this.emit("agent:" + a.id);
        }
        break;
      }
      case "agent.created": {
        if (d.agent) { this.agents[d.agent.id] = d.agent; this.emit("agents"); }
        break;
      }
      case "agent.talked": {
        this.chat.push({ sender: d.agent_name || d.agent_id, target: d.target, message: d.message, ts: e.ts });
        if (this.chat.length > 200) this.chat = this.chat.slice(-200);
        this.emit("chat");
        this.emit("speech:" + d.agent_id);
        break;
      }
      case "chat.user": {
        this.chat.push({ sender: "You", target: d.target, message: d.message, ts: e.ts });
        this.emit("chat");
        break;
      }
      case "world.changed": {
        this.emit("worldchange");
        break;
      }
      case "ui.focus": {
        this.focusedAgentId = d.agent_id;
        this.emit("focus");
        break;
      }
    }
    this.emit("events");
  }

  get agentList(): AgentDTO[] {
    return Object.values(this.agents);
  }
}

export const store = new Store();
