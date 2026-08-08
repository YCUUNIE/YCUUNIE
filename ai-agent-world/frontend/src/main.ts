import Phaser from "phaser";
import "./style.css";
import { socket } from "./networking/socket";
import { store } from "./state/store";
import { WorldScene } from "./game/scenes/WorldScene";
import { UI } from "./ui/ui";

const root = document.getElementById("app")!;
const ui = new UI();
ui.build(root);

// Boot Phaser inside the game host once the layout exists.
const scene = new WorldScene();
const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: ui.gameHost,
  backgroundColor: "#3f6b34",
  scale: {
    mode: Phaser.Scale.RESIZE,
    autoCenter: Phaser.Scale.CENTER_BOTH,
  },
  render: { pixelArt: false, antialias: true },
  scene,
});

// Pass a selection callback into the scene once it's ready.
game.scene.start("world", {
  onSelect: (id: string) => ui.openAgentInspector(id),
});

// Kick off the realtime connection.
socket.connect();

// Fallback: if the socket is slow, hydrate initial state over REST.
setTimeout(async () => {
  if (!store.world) {
    try {
      const state = await (await fetch("/api/state")).json();
      store.config = await (await fetch("/api/config")).json();
      store.loadSnapshot(state);
    } catch {
      /* backend not up yet; the socket will retry */
    }
  }
}, 1500);
