// Visual representation of an agent: procedural character, name/role label, a
// state indicator, plus speech and thought bubbles. Kept independent from the
// AI system — it only reflects state pushed from the store.
import Phaser from "phaser";
import type { AgentDTO } from "../../state/store";

const STATE_ICON: Record<string, string> = {
  IDLE: "💤", WALKING: "🚶", THINKING: "💭", TALKING: "💬", WORKING: "🔨",
  EXPLORING: "🧭", WAITING: "⏳", SLEEPING: "😴", ERROR: "⚠️", RECOVERING: "🛠️",
};

export class AgentSprite {
  scene: Phaser.Scene;
  agent: AgentDTO;
  container: Phaser.GameObjects.Container;
  private body: Phaser.GameObjects.Graphics;
  private nameLabel: Phaser.GameObjects.Text;
  private stateIcon: Phaser.GameObjects.Text;
  private bubble?: Phaser.GameObjects.Container;
  private bubbleTimer?: Phaser.Time.TimerEvent;
  private bobT = 0;
  targetX: number;
  targetY: number;

  constructor(scene: Phaser.Scene, agent: AgentDTO, onClick: (id: string) => void) {
    this.scene = scene;
    this.agent = agent;
    this.targetX = agent.x;
    this.targetY = agent.y;

    this.container = scene.add.container(agent.x, agent.y);
    this.body = scene.add.graphics();
    this.drawBody();

    this.nameLabel = scene.add.text(0, -34, `${agent.name}`, {
      fontFamily: "monospace", fontSize: "12px", color: "#ffffff",
      backgroundColor: "#00000088", padding: { x: 5, y: 2 },
    }).setOrigin(0.5);

    this.stateIcon = scene.add.text(16, -30, STATE_ICON[agent.state] || "", {
      fontSize: "14px",
    }).setOrigin(0.5);

    this.container.add([this.body, this.nameLabel, this.stateIcon]);
    this.container.setSize(40, 48);
    this.container.setInteractive(new Phaser.Geom.Rectangle(-20, -40, 40, 56), Phaser.Geom.Rectangle.Contains);
    this.container.on("pointerdown", () => onClick(agent.id));
    this.container.on("pointerover", () => this.nameLabel.setColor("#ffe08a"));
    this.container.on("pointerout", () => this.nameLabel.setColor("#ffffff"));
  }

  private colorNum(): number {
    return parseInt((this.agent.color || "#5aa9e6").replace("#", "0x"));
  }

  private drawBody() {
    const g = this.body;
    g.clear();
    const col = this.colorNum();
    const recovering = this.agent.recovery_state !== "healthy";
    // shadow
    g.fillStyle(0x000000, 0.2); g.fillEllipse(0, 16, 26, 8);
    // legs
    g.fillStyle(0x33404d, 1); g.fillRect(-6, 6, 4, 10); g.fillRect(2, 6, 4, 10);
    // body
    g.fillStyle(recovering ? 0xd9773d : col, 1);
    g.fillRoundedRect(-9, -8, 18, 18, 5);
    // head
    g.fillStyle(0xf1d0a9, 1); g.fillCircle(0, -14, 8);
    // hair cap tinted with agent color
    g.fillStyle(col, 1); g.fillEllipse(0, -18, 16, 8);
    // facing eyes
    g.fillStyle(0x222222, 1);
    const f = this.agent.facing;
    if (f === "left") { g.fillCircle(-4, -14, 1.6); }
    else if (f === "right") { g.fillCircle(4, -14, 1.6); }
    else if (f === "up") { /* back of head, no eyes */ }
    else { g.fillCircle(-3, -13, 1.6); g.fillCircle(3, -13, 1.6); }
    if (this.agent.state === "ERROR") {
      g.lineStyle(2, 0xff5252, 1); g.strokeCircle(0, -4, 18);
    }
  }

  update(dt: number) {
    // smooth interpolate toward target position
    const c = this.container;
    c.x += (this.targetX - c.x) * Math.min(1, dt * 6);
    c.y += (this.targetY - c.y) * Math.min(1, dt * 6);
    c.setDepth(c.y);
    // walking bob
    const moving = Math.abs(this.targetX - c.x) > 1 || Math.abs(this.targetY - c.y) > 1;
    if (moving) {
      this.bobT += dt * 10;
      this.body.y = Math.sin(this.bobT) * 1.6;
    } else {
      this.body.y = 0;
    }
    if (this.bubble) {
      this.bubble.x = c.x;
      this.bubble.y = c.y - 52;
    }
  }

  applyDTO(agent: AgentDTO) {
    const stateChanged = agent.state !== this.agent.state ||
      agent.facing !== this.agent.facing || agent.recovery_state !== this.agent.recovery_state;
    this.agent = agent;
    this.targetX = agent.x;
    this.targetY = agent.y;
    if (stateChanged) {
      this.stateIcon.setText(STATE_ICON[agent.state] || "");
      this.drawBody();
    }
  }

  showSpeech(text: string, kind: "say" | "think" = "say") {
    if (this.bubble) { this.bubble.destroy(); this.bubbleTimer?.remove(); }
    const scene = this.scene;
    const maxLen = 60;
    const shown = text.length > maxLen ? text.slice(0, maxLen) + "…" : text;
    const label = scene.add.text(0, 0, (kind === "think" ? "💭 " : "") + shown, {
      fontFamily: "monospace", fontSize: "12px", color: "#1b1b2b",
      backgroundColor: kind === "think" ? "#dfe6ff" : "#ffffff",
      padding: { x: 8, y: 5 }, wordWrap: { width: 200 }, align: "center",
    }).setOrigin(0.5);
    const tail = scene.add.graphics();
    tail.fillStyle(kind === "think" ? 0xdfe6ff : 0xffffff, 1);
    tail.fillTriangle(-6, label.height / 2, 6, label.height / 2, 0, label.height / 2 + 8);
    this.bubble = scene.add.container(this.container.x, this.container.y - 52, [label, tail]);
    this.bubble.setDepth(100000);
    this.bubbleTimer = scene.time.delayedCall(Math.min(6000, 2200 + shown.length * 45), () => {
      this.bubble?.destroy();
      this.bubble = undefined;
    });
  }

  destroy() {
    this.bubble?.destroy();
    this.container.destroy();
  }
}
