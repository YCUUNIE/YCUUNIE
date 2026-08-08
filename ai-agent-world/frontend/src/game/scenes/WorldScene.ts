// The Phaser scene. Owns rendering only; it reflects world/agent state from the
// store and forwards agent-click selection to the UI. No AI logic lives here.
import Phaser from "phaser";
import { store } from "../../state/store";
import { AgentSprite } from "../entities/AgentSprite";
import { buildWorldTexture, drawObject } from "../world/tilemap";

export class WorldScene extends Phaser.Scene {
  private sprites: Record<string, AgentSprite> = {};
  private built = false;
  private onSelect: (id: string) => void = () => {};
  private lastTime = 0;

  constructor() {
    super("world");
  }

  init(data: { onSelect?: (id: string) => void }) {
    if (data?.onSelect) this.onSelect = data.onSelect;
  }

  create() {
    this.cameras.main.setBackgroundColor("#3f6b34");
    // Try to build immediately if world already loaded, else wait.
    if (store.world) this.buildWorld();
    store.on("world", () => this.buildWorld());
    store.on("agents", () => this.syncAgents());
    store.on("speech:", () => {});
    // subscribe to speech events for every agent
    store.on("chat", () => this.handleSpeech());
    store.on("focus", () => {
      const id = store.focusedAgentId;
      if (id && this.sprites[id]) {
        this.cameras.main.pan(this.sprites[id].container.x, this.sprites[id].container.y, 400, "Sine.easeInOut");
      }
    });

    // camera controls: drag to pan, wheel to zoom
    this.input.on("pointermove", (p: Phaser.Input.Pointer) => {
      if (p.isDown && !p.event.defaultPrevented) {
        this.cameras.main.scrollX -= (p.x - p.prevPosition.x) / this.cameras.main.zoom;
        this.cameras.main.scrollY -= (p.y - p.prevPosition.y) / this.cameras.main.zoom;
      }
    });
    this.input.on("wheel", (_p: any, _o: any, _dx: number, dy: number) => {
      const cam = this.cameras.main;
      cam.zoom = Phaser.Math.Clamp(cam.zoom - dy * 0.0012, 0.5, 2.0);
    });
  }

  private buildWorld() {
    if (this.built || !store.world) return;
    this.built = true;
    const world = store.world;
    buildWorldTexture(this, world);
    this.add.image(0, 0, "world").setOrigin(0, 0).setDepth(-10000);
    for (const obj of world.objects) drawObject(this, obj);
    this.cameras.main.setBounds(0, 0, world.width, world.height);
    this.cameras.main.centerOn(world.width / 2, world.height / 2 - 60);
    this.cameras.main.setZoom(0.85);
    this.syncAgents();
    // re-draw newly discovered objects
    store.on("worldchange", () => this.refreshObjects());
  }

  private drawnObjects = new Set<string>();

  private refreshObjects() {
    if (!store.world) return;
    for (const obj of store.world.objects) {
      if (!this.drawnObjects.has(obj.id)) {
        drawObject(this, obj);
        this.drawnObjects.add(obj.id);
      }
    }
    // Also request a fresh world snapshot to pick up new discoveries.
    fetch("/api/state").then((r) => r.json()).then((s) => {
      if (s.world) {
        for (const obj of s.world.objects) {
          if (!this.drawnObjects.has(obj.id) && obj.kind === "structure") {
            drawObject(this, obj);
            this.drawnObjects.add(obj.id);
          }
        }
      }
    }).catch(() => {});
  }

  private syncAgents() {
    for (const agent of store.agentList) {
      if (!this.sprites[agent.id]) {
        this.sprites[agent.id] = new AgentSprite(this, agent, this.onSelect);
        this.drawnObjects.add(agent.id);
        store.on("agent:" + agent.id, () => {
          const a = store.agents[agent.id];
          if (a) this.sprites[agent.id]?.applyDTO(a);
        });
      } else {
        this.sprites[agent.id].applyDTO(agent);
      }
    }
  }

  private lastSpeechTs = 0;
  private handleSpeech() {
    // find the most recent agent utterance and show a bubble
    const msgs = store.chat;
    for (let i = msgs.length - 1; i >= 0 && i > msgs.length - 4; i--) {
      const m = msgs[i];
      if (m.ts <= this.lastSpeechTs) continue;
      if (m.sender === "You") continue;
      const agent = store.agentList.find((a) => a.name === m.sender);
      if (agent && this.sprites[agent.id]) {
        this.sprites[agent.id].showSpeech(m.message, "say");
        this.lastSpeechTs = Math.max(this.lastSpeechTs, m.ts);
      }
    }
  }

  update(time: number) {
    const dt = this.lastTime ? (time - this.lastTime) / 1000 : 0.016;
    this.lastTime = time;
    for (const id in this.sprites) this.sprites[id].update(dt);
  }
}
