// The whole management UI (framework-free). Builds the layout, all panels, and
// the modal inspectors. Every button here calls a real backend endpoint — no
// dead controls.
import { api, socket } from "../networking/socket";
import { store, AgentDTO } from "../state/store";
import { clear, el, fmtTime, openModal, closeModal } from "./dom";

const EVENT_FILTERS = ["all", "agent", "chat", "task", "memory", "workflow", "system", "error", "recovery"];

export class UI {
  gameHost!: HTMLElement;
  private feedFilter = "all";
  private chatTarget = "All Agents";

  build(root: HTMLElement): HTMLElement {
    const topbar = this.buildTopbar();
    this.gameHost = el("div", { id: "game-host" });
    const sidebar = this.buildSidebar();
    const bottom = this.buildBottom();

    const layout = el("div", { id: "layout" }, [
      topbar,
      el("div", { id: "middle" }, [
        el("div", { id: "stage" }, [this.gameHost]),
        sidebar,
      ]),
      bottom,
    ]);
    root.append(layout);

    // reactive wiring
    store.on("sim", () => this.renderTopbar());
    store.on("agents", () => this.renderSidebar());
    store.on("agent:*", () => this.renderSidebar());
    store.on("*", () => {});
    store.on("agents", () => this.renderChatTargets());
    store.on("events", () => this.renderFeed());
    store.on("chat", () => this.renderChat());
    socket.onStatus((c) => this.setConn(c));
    setInterval(() => this.renderTopbar(), 1000);
    setInterval(() => this.pollSidebarTabs(), 4000);
    return layout;
  }

  // --- top bar -------------------------------------------------------------
  private topbarEl!: HTMLElement;
  private connEl!: HTMLElement;
  private buildTopbar(): HTMLElement {
    this.connEl = el("span", { class: "conn dot-off", title: "WebSocket" }, ["● offline"]);
    this.topbarEl = el("div", { id: "clock" });
    const btn = (label: string, cls: string, fn: () => void, title = "") =>
      el("button", { class: "ctrl " + cls, onclick: fn, title }, [label]);

    const controls = el("div", { id: "sim-controls" }, [
      btn("▶", "", () => api.post("/api/sim/resume"), "Play"),
      btn("⏸", "", () => api.post("/api/sim/pause"), "Pause"),
      btn("x1", "", () => this.setSpeed(1)),
      btn("x2", "", () => this.setSpeed(2)),
      btn("x5", "", () => this.setSpeed(5)),
      btn("x10", "", () => this.setSpeed(10)),
      btn("⏭ Step", "step", () => api.post("/api/sim/step"), "Single step"),
      btn("⏹", "", () => api.post("/api/sim/stop"), "Stop"),
    ]);

    return el("div", { id: "topbar" }, [
      el("div", { class: "brand" }, [el("span", { class: "logo" }, ["◆"]), "AI WORLD"]),
      controls,
      el("div", { class: "top-right" }, [this.topbarEl, this.connEl]),
    ]);
  }

  private setSpeed(s: number) { api.post("/api/sim/speed", { speed: s }); }
  private setConn(c: boolean) {
    this.connEl.className = "conn " + (c ? "dot-on" : "dot-off");
    this.connEl.textContent = c ? "● connected" : "● offline";
  }
  private renderTopbar() {
    this.topbarEl.textContent = `Day ${store.sim.day}  ·  ${this.simClock()}  ·  ${store.sim.paused ? "⏸ paused" : "▶ x" + store.sim.speed}`;
  }
  private simClock(): string {
    const total = Math.floor((store.sim.time % 240) / 240 * 24 * 60);
    const h = String(Math.floor(total / 60)).padStart(2, "0");
    const m = String(total % 60).padStart(2, "0");
    return `${h}:${m}`;
  }

  // --- sidebar -------------------------------------------------------------
  private sidebarBody!: HTMLElement;
  private tab: "agents" | "providers" | "health" = "agents";
  private buildSidebar(): HTMLElement {
    this.sidebarBody = el("div", { id: "sidebar-body" });
    const tabBtn = (id: any, label: string) =>
      el("button", { class: "tab" + (this.tab === id ? " active" : ""), onclick: () => { this.tab = id; this.renderTabs(); this.renderSidebar(); } }, [label]);
    const tabs = el("div", { id: "sidebar-tabs" }, [
      tabBtn("agents", "AGENTS"), tabBtn("providers", "PROVIDERS"), tabBtn("health", "HEALTH"),
    ]);
    this.tabsEl = tabs;
    const actions = el("div", { id: "sidebar-actions" }, [
      el("button", { class: "act", onclick: () => this.openAgentEditor() }, ["+ New Agent"]),
      el("button", { class: "act", onclick: () => this.openFailureDemo() }, ["⚡ Failure Demo"]),
      el("button", { class: "act", onclick: () => this.openMemoryLocation() }, ["📂 Memory"]),
      el("button", { class: "act", onclick: () => this.openDebug() }, ["🐞 Debug"]),
    ]);
    this.renderSidebar();
    return el("div", { id: "sidebar" }, [tabs, this.sidebarBody, actions]);
  }
  private tabsEl!: HTMLElement;
  private renderTabs() {
    Array.from(this.tabsEl.children).forEach((c) => c.classList.remove("active"));
    const idx = { agents: 0, providers: 1, health: 2 }[this.tab];
    this.tabsEl.children[idx]?.classList.add("active");
  }
  private pollSidebarTabs() {
    if (this.tab === "providers" || this.tab === "health") this.renderSidebar();
  }
  private renderSidebar() {
    if (this.tab === "agents") this.renderAgentsTab();
    else if (this.tab === "providers") this.renderProvidersTab();
    else this.renderHealthTab();
  }

  private renderAgentsTab() {
    clear(this.sidebarBody);
    for (const a of store.agentList) {
      const badge = el("span", { class: "status-dot " + this.recoveryClass(a.recovery_state) }, ["●"]);
      const card = el("div", { class: "agent-card", onclick: () => this.openAgentInspector(a.id) }, [
        el("div", { class: "agent-head" }, [
          el("span", { class: "swatch", style: `background:${a.color}` }),
          el("strong", {}, [a.name]),
          badge,
        ]),
        el("div", { class: "agent-sub" }, [`${a.role} · ${a.state}`]),
        el("div", { class: "agent-act" }, [a.activity || "…"]),
        el("div", { class: "agent-prov" }, [`⚙ ${a.provider?.provider || "local"} (${a.last_provider_used || "—"})`]),
      ]);
      this.sidebarBody.append(card);
    }
  }
  private recoveryClass(r: string) {
    return r === "healthy" ? "ok" : r === "recovering" ? "warn" : "err";
  }

  private async renderProvidersTab() {
    clear(this.sidebarBody);
    this.sidebarBody.append(el("div", { class: "loading" }, ["loading providers…"]));
    const data = await api.get("/api/providers");
    clear(this.sidebarBody);
    for (const [name, st] of Object.entries<any>(data.status)) {
      const stats = data.stats[name] || {};
      const dotClass = st.status === "healthy" || st.status === "connected" || st.status === "configured" || st.status === "running"
        ? "ok" : st.status === "offline" || st.status === "no_key" ? "err" : "warn";
      const row = el("div", { class: "provider-row" }, [
        el("div", {}, [
          el("span", { class: "status-dot " + dotClass }, ["●"]),
          el("strong", {}, [name]),
          el("span", { class: "muted" }, [` ${st.status}`]),
        ]),
        el("div", { class: "muted small" }, [`calls ${stats.calls || 0} · err ${stats.errors || 0} · ${stats.avg_latency ? stats.avg_latency + "s" : "—"}`]),
        el("button", { class: "mini", onclick: async (e: any) => {
          e.stopPropagation();
          e.target.textContent = "…";
          const r = await api.post(`/api/providers/${name}/test`);
          e.target.textContent = "test";
          alert(`${name}: ${JSON.stringify(r)}`);
        } }, ["test"]),
      ]);
      this.sidebarBody.append(row);
    }
    this.sidebarBody.append(el("div", { class: "note" }, ["Keys stay server-side. Configure in .env / config.yaml."]));
  }

  private async renderHealthTab() {
    clear(this.sidebarBody);
    this.sidebarBody.append(el("div", { class: "loading" }, ["checking health…"]));
    const h = await api.get("/api/health/detailed");
    clear(this.sidebarBody);
    this.sidebarBody.append(el("div", { class: "health-overall " + h.status }, [`SYSTEM: ${h.status.toUpperCase()}`]));
    for (const [name, sub] of Object.entries<any>(h.subsystems)) {
      if (name === "providers") continue;
      const s = sub.status || "unknown";
      const dot = ["healthy", "connected", "running"].includes(s) ? "ok" : ["error", "offline", "stopped"].includes(s) ? "err" : "warn";
      this.sidebarBody.append(el("div", { class: "health-row" }, [
        el("span", { class: "status-dot " + dot }, ["●"]),
        el("span", { class: "hname" }, [name]),
        el("span", { class: "muted" }, [s]),
      ]));
    }
    // agents health
    const ar = h.subsystems.agent_runtime?.agents || {};
    this.sidebarBody.append(el("div", { class: "health-sep" }, ["AGENTS"]));
    for (const info of Object.values<any>(ar)) {
      const dot = info.status === "healthy" ? "ok" : info.status === "recovering" ? "warn" : "err";
      this.sidebarBody.append(el("div", { class: "health-row" }, [
        el("span", { class: "status-dot " + dot }, ["●"]),
        el("span", { class: "hname" }, [info.name]),
        el("span", { class: "muted" }, [info.status]),
      ]));
    }
  }

  // --- bottom: feed + chat -------------------------------------------------
  private feedEl!: HTMLElement;
  private chatEl!: HTMLElement;
  private chatTargetSel!: HTMLSelectElement;
  private chatInput!: HTMLInputElement;

  private buildBottom(): HTMLElement {
    // event feed
    const filters = el("div", { class: "feed-filters" },
      EVENT_FILTERS.map((f) => el("button", {
        class: "filter" + (f === this.feedFilter ? " active" : ""),
        onclick: () => { this.feedFilter = f; this.syncFilterButtons(filters); this.renderFeed(); },
      }, [f])));
    this.filtersEl = filters;
    this.feedEl = el("div", { class: "feed-list" });
    const feed = el("div", { id: "feed" }, [
      el("div", { class: "panel-title" }, ["EVENT FEED"]),
      filters, this.feedEl,
    ]);

    // chat
    this.chatEl = el("div", { class: "chat-list" });
    this.chatTargetSel = el("select", { class: "chat-target", onchange: (e: any) => { this.chatTarget = e.target.value; } }) as HTMLSelectElement;
    this.renderChatTargets();
    this.chatInput = el("input", { class: "chat-input", placeholder: "Message… (try /help or 'tell Alex to explore the northern forest')",
      onkeydown: (e: any) => { if (e.key === "Enter") this.sendChat(); } }) as HTMLInputElement;
    const chat = el("div", { id: "chat" }, [
      el("div", { class: "panel-title" }, ["CHAT"]),
      this.chatEl,
      el("div", { class: "chat-bar" }, [
        el("span", { class: "chat-with" }, ["To:"]),
        this.chatTargetSel,
        this.chatInput,
        el("button", { class: "send", onclick: () => this.sendChat() }, ["Send"]),
      ]),
    ]);
    this.renderChat();
    this.renderFeed();
    return el("div", { id: "bottom" }, [feed, chat]);
  }
  private filtersEl!: HTMLElement;
  private syncFilterButtons(container: HTMLElement) {
    Array.from(container.children).forEach((c) =>
      c.classList.toggle("active", c.textContent === this.feedFilter));
  }

  private renderChatTargets() {
    if (!this.chatTargetSel) return;
    const current = this.chatTarget;
    clear(this.chatTargetSel);
    const opts = ["All Agents", ...store.agentList.map((a) => a.name), "System", "Repair Agent"];
    for (const o of opts) this.chatTargetSel.append(el("option", { value: o, selected: o === current }, [o]));
  }

  private eventLabel(e: any): string {
    const d = e.data || {};
    const who = d.agent_name || d.agent_id || "";
    switch (e.type) {
      case "agent.talked": return `${who} → ${d.target}: "${d.message}"`;
      case "agent.thought": return d.summary || `${who} is thinking…`;
      case "agent.memory.created": return `${who} remembered (${d.importance}): ${d.content}`;
      case "agent.memory.recalled": return `${who} recalled ${d.count} memories`;
      case "agent.task.created": return `Task created: ${d.task?.name} → ${d.agent_id || "?"}`;
      case "agent.task.completed": return `Task completed: ${d.task?.name}`;
      case "agent.tool.called": return `${who} used tool: ${d.tool}`;
      case "agent.error": return `⚠ ${who || d.scope}: ${d.message}`;
      case "agent.recovered": return `✔ ${who} recovered ${d.reason ? "(" + d.reason + ")" : ""}`;
      case "world.changed": return `World: ${d.change}${d.object ? " " + d.object.label : ""}${d.agent_name ? " by " + d.agent_name : ""}`;
      case "workflow.started": return `Workflow started: ${d.workflow?.name}`;
      case "workflow.completed": return `Workflow completed: ${d.workflow?.name}`;
      case "system.recovery": return `🛠 ${d.message}`;
      case "system.error": return `⚠ system: ${d.message}`;
      case "chat.user": return `You → ${d.target}: ${d.message}`;
      default: return `${e.type} ${JSON.stringify(d).slice(0, 60)}`;
    }
  }

  private renderFeed() {
    if (!this.feedEl) return;
    const items = store.events.filter((e) => this.feedFilter === "all" || e.category === this.feedFilter).slice(-120);
    clear(this.feedEl);
    for (const e of items) {
      this.feedEl.append(el("div", { class: "feed-item cat-" + e.category }, [
        el("span", { class: "feed-time" }, [fmtTime(e.ts)]),
        el("span", { class: "feed-text" }, [this.eventLabel(e)]),
      ]));
    }
    this.feedEl.scrollTop = this.feedEl.scrollHeight;
  }

  private renderChat() {
    if (!this.chatEl) return;
    clear(this.chatEl);
    for (const m of store.chat.slice(-60)) {
      const mine = m.sender === "You";
      this.chatEl.append(el("div", { class: "chat-msg " + (mine ? "mine" : "them") }, [
        el("span", { class: "chat-sender" }, [m.sender + (m.target && mine ? " → " + m.target : "") + ":"]),
        el("span", { class: "chat-body" }, [" " + m.message]),
      ]));
    }
    this.chatEl.scrollTop = this.chatEl.scrollHeight;
  }

  private async sendChat() {
    const text = this.chatInput.value.trim();
    if (!text) return;
    this.chatInput.value = "";
    const res = await api.post("/api/chat", { target: this.chatTarget, message: text });
    // system replies aren't broadcast as agent.talked, so show them inline
    if (res.type === "system") {
      for (const r of res.responses || []) {
        store.chat.push({ sender: r.agent, message: r.reply, ts: Date.now() / 1000 });
      }
      store.emit("chat");
    }
    if (res.note) {
      store.chat.push({ sender: "System", message: res.note, ts: Date.now() / 1000 });
      store.emit("chat");
    }
  }

  // --- modals: inspector ---------------------------------------------------
  async openAgentInspector(id: string) {
    const a = store.agents[id];
    if (!a) return;
    const body = el("div", { class: "inspector" });
    const rel = Object.entries(a.relationships || {}).map(([k, v]: any) =>
      el("div", { class: "rel" }, [`${k}: trust ${(v.trust ?? 0).toFixed(2)} · friend ${(v.friendship ?? 0).toFixed(2)} · respect ${(v.respect ?? 0).toFixed(2)}`]));
    body.append(
      el("div", { class: "insp-grid" }, [
        this.kv("Role", a.role), this.kv("State", a.state),
        this.kv("Location", `(${Math.round(a.x)}, ${Math.round(a.y)})`),
        this.kv("Provider", `${a.provider?.provider} / ${a.provider?.model}`),
        this.kv("Energy", (a.energy ?? 1).toFixed(2)),
        this.kv("Recovery", a.recovery_state),
      ]),
      el("h4", {}, ["Personality"]),
      el("div", { class: "chips" }, a.personality.map((p) => el("span", { class: "chip" }, [p]))),
      el("h4", {}, ["Goals"]),
      el("ul", {}, a.goals.map((g) => el("li", {}, [g]))),
      el("h4", {}, ["Last action"]),
      el("pre", { class: "code" }, [JSON.stringify(a.last_action, null, 2) || "—"]),
      el("h4", {}, ["Relationships"]),
      ...(rel.length ? rel : [el("div", { class: "muted" }, ["none yet"])]),
      el("div", { class: "insp-actions" }, [
        el("button", { onclick: () => { closeModal(); this.openMemoryInspector(id); } }, ["Inspect Memory"]),
        el("button", { onclick: () => { closeModal(); this.openWorkflowInspector(a.name); } }, ["Workflow"]),
        el("button", { onclick: () => { closeModal(); this.openAgentEditor(a); } }, ["Edit"]),
        el("button", { onclick: () => { this.chatTarget = a.name; this.renderChatTargets(); closeModal(); this.chatInput.focus(); } }, ["Chat"]),
        el("button", { onclick: () => { socket.send({}); store.focusedAgentId = id; store.emit("focus"); } }, ["Focus"]),
      ]),
    );
    openModal(`${a.name} — Agent Inspector`, body);
  }
  private kv(k: string, v: string) {
    return el("div", { class: "kv" }, [el("span", { class: "k" }, [k]), el("span", { class: "v" }, [v])]);
  }

  async openMemoryInspector(id: string) {
    const data = await api.get(`/api/agents/${id}/memory`);
    const body = el("div", { class: "memlist" });
    const groups: Record<string, any[]> = { critical: [], high: [], medium: [], low: [] };
    for (const m of data.memories) (groups[m.importance] || groups.low).push(m);
    for (const imp of ["critical", "high", "medium", "low"]) {
      if (!groups[imp].length) continue;
      body.append(el("h4", { class: "imp-" + imp }, [imp.toUpperCase() + " importance"]));
      for (const m of groups[imp]) {
        body.append(el("div", { class: "mem-item" }, [
          el("div", { class: "mem-content" }, [m.content]),
          el("div", { class: "mem-meta muted" }, [`${m.kind} · from ${m.source} · ${fmtTime(m.created)}`]),
        ]));
      }
    }
    if (!data.memories.length) body.append(el("div", { class: "muted" }, ["No memories yet."]));
    openModal(`${data.agent} — Memory`, body);
  }

  async openWorkflowInspector(agentName: string) {
    const wfs = await api.get("/api/workflows");
    const tasks = await api.get("/api/tasks");
    const body = el("div", {});
    const mine = wfs.filter((w: any) => w.assignee === agentName);
    if (mine.length) {
      for (const w of mine) {
        body.append(el("h4", {}, [w.name + ` (${Math.round(w.progress * 100)}%)`]));
        const ul = el("ul", { class: "wf-steps" });
        for (const s of w.steps) ul.append(el("li", { class: s.done ? "done" : "pending" }, [(s.done ? "✓ " : "○ ") + s.label]));
        body.append(ul);
      }
    }
    body.append(el("h4", {}, ["Tasks"]));
    const mineTasks = tasks.filter((t: any) => t.assignee === agentName);
    if (mineTasks.length) {
      for (const t of mineTasks) body.append(el("div", { class: "task-row" }, [`${t.name} — ${t.status} (p${t.priority})`]));
    } else body.append(el("div", { class: "muted" }, ["No tasks."]));
    openModal(`${agentName} — Workflows`, body);
  }

  // --- agent editor --------------------------------------------------------
  openAgentEditor(existing?: AgentDTO) {
    const val = existing || ({ name: "", role: "", personality: [], goals: [], background: "",
      provider: { provider: "local", model: "rule-policy", temperature: 0.7, decision_interval: 10, fallbacks: ["local"] },
      color: "#c77dff" } as any);
    const f = (id: string, v: any) => el("input", { id, value: v ?? "", class: "editor-input" }) as HTMLInputElement;
    const nameI = f("ed-name", val.name), roleI = f("ed-role", val.role);
    const persI = f("ed-pers", (val.personality || []).join(", "));
    const goalsI = f("ed-goals", (val.goals || []).join(", "));
    const bgI = el("textarea", { class: "editor-input", rows: 2 }, [val.background || ""]) as HTMLTextAreaElement;
    const provI = f("ed-prov", val.provider?.provider || "local");
    const modelI = f("ed-model", val.provider?.model || "");
    const tempI = f("ed-temp", val.provider?.temperature ?? 0.7);
    const intI = f("ed-int", val.provider?.decision_interval ?? 10);
    const fbI = f("ed-fb", (val.provider?.fallbacks || ["local"]).join(", "));
    const colorI = el("input", { type: "color", value: val.color || "#c77dff" }) as HTMLInputElement;

    const jsonOut = el("textarea", { class: "editor-input mono", rows: 6 }) as HTMLTextAreaElement;
    const collect = () => ({
      id: existing?.id,
      name: nameI.value, role: roleI.value,
      personality: persI.value.split(",").map((s) => s.trim()).filter(Boolean),
      goals: goalsI.value.split(",").map((s) => s.trim()).filter(Boolean),
      background: bgI.value, color: colorI.value,
      provider: {
        provider: provI.value, model: modelI.value,
        temperature: parseFloat(tempI.value) || 0.7,
        decision_interval: parseFloat(intI.value) || 10,
        fallbacks: fbI.value.split(",").map((s) => s.trim()).filter(Boolean),
      },
    });

    const body = el("div", { class: "editor" }, [
      this.field("Name", nameI), this.field("Role", roleI),
      this.field("Personality (comma-sep)", persI),
      this.field("Goals (comma-sep)", goalsI),
      this.field("Background", bgI),
      el("div", { class: "editor-2col" }, [
        this.field("Provider", provI), this.field("Model", modelI),
        this.field("Temperature", tempI), this.field("Decision interval (s)", intI),
        this.field("Fallbacks (comma-sep)", fbI), this.field("Color", colorI),
      ]),
      el("div", { class: "editor-actions" }, [
        el("button", { class: "act", onclick: () => { jsonOut.value = JSON.stringify(collect(), null, 2); } }, ["Export JSON"]),
        el("button", { class: "act", onclick: () => {
          try {
            const data = JSON.parse(jsonOut.value);
            nameI.value = data.name || ""; roleI.value = data.role || "";
            persI.value = (data.personality || []).join(", "); goalsI.value = (data.goals || []).join(", ");
            bgI.value = data.background || ""; provI.value = data.provider?.provider || "local";
            modelI.value = data.provider?.model || "";
          } catch { alert("Invalid JSON"); }
        } }, ["Import JSON"]),
        el("button", { class: "act primary", onclick: async () => {
          const data = collect();
          if (existing) await api.put(`/api/agents/${existing.id}`, { data });
          else await api.post("/api/agents", { data });
          closeModal();
        } }, [existing ? "Save" : "Create"]),
      ]),
      el("label", { class: "muted small" }, ["JSON import/export"]),
      jsonOut,
    ]);
    openModal(existing ? `Edit ${existing.name}` : "New Agent", body, true);
  }
  private field(label: string, input: HTMLElement) {
    return el("label", { class: "field" }, [el("span", {}, [label]), input]);
  }

  // --- failure demo --------------------------------------------------------
  openFailureDemo() {
    const log = el("div", { class: "demo-log" });
    const run = async (what: string, label: string) => {
      log.prepend(el("div", { class: "demo-line" }, [`▶ ${label}…`]));
      const r = await api.post(`/api/repair/simulate/${what}`);
      log.prepend(el("div", { class: "demo-line ok" }, [`✔ ${label}: ${JSON.stringify(r).slice(0, 160)}`]));
    };
    const body = el("div", {}, [
      el("p", { class: "muted" }, ["Trigger a controlled failure and watch the self-healing pipeline detect → diagnose → repair → verify. Results also appear in the Event Feed (Recovery filter)."]),
      el("div", { class: "demo-btns" }, [
        el("button", { class: "act", onclick: () => run("provider_failure", "Provider failure") }, ["Simulate Provider Failure"]),
        el("button", { class: "act", onclick: () => run("agent_error", "Agent error") }, ["Simulate Agent Error"]),
        el("button", { class: "act", onclick: () => run("websocket", "WebSocket disconnect") }, ["Simulate WebSocket Disconnect"]),
        el("button", { class: "act", onclick: () => run("invalid_tool", "Invalid tool call") }, ["Simulate Invalid Tool Call"]),
        el("button", { class: "act", onclick: () => run("workflow_failure", "Workflow failure") }, ["Simulate Workflow Failure"]),
      ]),
      el("h4", {}, ["Result log"]), log,
    ]);
    openModal("⚡ Self-Healing Failure Demo", body, true);
  }

  // --- memory location -----------------------------------------------------
  async openMemoryLocation() {
    const data = await api.get("/api/memory/location");
    const list = el("div", { class: "file-list" });
    for (const f of data.files) {
      list.append(el("div", { class: "file-item", onclick: async () => {
        const note = await api.get(`/api/memory/note?path=${encodeURIComponent(f)}`);
        openModal(f, el("pre", { class: "code" }, [note.content || "(empty)"]), true);
      } }, [f]));
    }
    const body = el("div", {}, [
      el("div", { class: "kv" }, [el("span", { class: "k" }, ["Vault"]), el("span", { class: "v mono" }, [data.path])]),
      el("p", { class: "muted small" }, ["These Markdown files are written live by the agents. Open the folder in Obsidian to browse them."]),
      el("h4", {}, [`Notes (${data.files.length})`]), list,
    ]);
    openModal("📂 Obsidian Memory", body, true);
  }

  // --- debug panel ---------------------------------------------------------
  async openDebug() {
    const body = el("div", { class: "debug" });
    const render = () => {
      clear(body);
      for (const a of store.agentList) {
        body.append(el("div", { class: "debug-agent" }, [
          el("div", { class: "debug-name", style: `color:${a.color}` }, [`${a.name} (${a.role})`]),
          el("div", { class: "debug-grid" }, [
            this.kv("State", a.state), this.kv("Provider", a.last_provider_used || a.provider?.provider),
            this.kv("Model", a.provider?.model), this.kv("Last action", a.last_action?.type || "—"),
            this.kv("Memory retrieved", String(a.memories_retrieved)),
            this.kv("LLM latency", (a.last_latency ?? 0) + "s"),
            this.kv("Tokens", JSON.stringify(a.last_tokens || {})),
            this.kv("Last error", a.last_error || "None"),
            this.kv("Recovery", a.recovery_state),
          ]),
          el("div", { class: "debug-summary muted" }, [a.last_summary || ""]),
        ]));
      }
      body.append(el("div", { class: "debug-foot" }, [`WebSocket: ${socket.connected ? "connected" : "offline"} · Agents: ${store.agentList.length}`]));
    };
    render();
    const unsub = store.on("events", render);
    const overlay = openModal("🐞 Debug Panel", body, true);
    overlay.addEventListener("click", (e: any) => { if (e.target === overlay) unsub(); });
  }
}
