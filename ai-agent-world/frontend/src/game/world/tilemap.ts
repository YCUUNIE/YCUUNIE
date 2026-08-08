// Procedural world rendering. No external/copyrighted assets — the whole map
// is drawn from primitives into a RenderTexture, so it stays tiny and offline.
import Phaser from "phaser";
import type { WorldDTO } from "../../state/store";

const COLORS = {
  grass: 0x6ab04c,
  grassAlt: 0x5fa343,
  grassDark: 0x55963c,
  water: 0x3aa7d8,
  waterDeep: 0x2b8fbd,
  path: 0xc9a56a,
  pathEdge: 0xb8945a,
  trunk: 0x7a5230,
  leaf: 0x2f8f3e,
  leafLight: 0x3fa54e,
  rock: 0x9098a1,
  rockDark: 0x767d86,
  house: 0xd98a5b,
  houseRoof: 0x9c4f2f,
  workshop: 0x8a7bbd,
  resource: 0xe0b040,
  structure: 0x6d5ea8,
};

export function buildWorldTexture(scene: Phaser.Scene, world: WorldDTO): void {
  const { width, height } = world;
  const g = scene.add.graphics();

  // base grass with subtle checker/noise
  g.fillStyle(COLORS.grass, 1);
  g.fillRect(0, 0, width, height);
  for (let x = 0; x < width; x += 40) {
    for (let y = 0; y < height; y += 40) {
      const r = (x * 7 + y * 13) % 5;
      if (r === 0) { g.fillStyle(COLORS.grassAlt, 1); g.fillRect(x, y, 40, 40); }
      else if (r === 1) { g.fillStyle(COLORS.grassDark, 0.5); g.fillRect(x, y, 40, 40); }
    }
  }

  // location areas (forest floor, water, resource clearing)
  for (const loc of world.locations) {
    if (loc.kind === "water") {
      g.fillStyle(COLORS.waterDeep, 1); g.fillCircle(loc.x, loc.y, loc.radius);
      g.fillStyle(COLORS.water, 1); g.fillCircle(loc.x, loc.y, loc.radius - 10);
    } else if (loc.kind === "forest") {
      g.fillStyle(0x4b8b3b, 0.5); g.fillCircle(loc.x, loc.y, loc.radius);
    } else if (loc.kind === "resource") {
      g.fillStyle(0x7a6a2f, 0.35); g.fillCircle(loc.x, loc.y, loc.radius);
    } else if (loc.kind === "settlement") {
      g.fillStyle(COLORS.path, 0.25); g.fillCircle(loc.x, loc.y, loc.radius);
    }
  }

  // paths between the village and key locations
  const village = world.locations.find((l) => l.kind === "settlement");
  if (village) {
    g.lineStyle(18, COLORS.path, 1);
    for (const loc of world.locations) {
      if (loc === village) continue;
      g.lineBetween(village.x, village.y, loc.x, loc.y);
    }
    g.lineStyle(8, COLORS.pathEdge, 0.5);
    for (const loc of world.locations) {
      if (loc === village) continue;
      g.lineBetween(village.x, village.y, loc.x, loc.y);
    }
  }

  g.generateTexture("world", width, height);
  g.destroy();
}

export function drawObject(scene: Phaser.Scene, obj: any): Phaser.GameObjects.GameObject | null {
  const c = scene.add.container(obj.x, obj.y);
  const g = scene.add.graphics();
  switch (obj.kind) {
    case "tree":
      g.fillStyle(0x000000, 0.15); g.fillEllipse(0, 14, 30, 10);
      g.fillStyle(COLORS.trunk, 1); g.fillRect(-4, 0, 8, 16);
      g.fillStyle(COLORS.leaf, 1); g.fillCircle(0, -8, 20);
      g.fillStyle(COLORS.leafLight, 1); g.fillCircle(-6, -12, 10);
      break;
    case "rock":
      g.fillStyle(0x000000, 0.15); g.fillEllipse(0, 8, 26, 8);
      g.fillStyle(COLORS.rock, 1); g.fillCircle(0, 0, 13);
      g.fillStyle(COLORS.rockDark, 1); g.fillCircle(5, 4, 7);
      break;
    case "building":
      g.fillStyle(0x000000, 0.15); g.fillEllipse(0, 22, 60, 12);
      g.fillStyle(COLORS.house, 1); g.fillRect(-24, -6, 48, 30);
      g.fillStyle(COLORS.houseRoof, 1); g.fillTriangle(-30, -6, 30, -6, 0, -32);
      g.fillStyle(0x5a3a22, 1); g.fillRect(-6, 8, 12, 16);
      break;
    case "workstation":
      g.fillStyle(0x000000, 0.15); g.fillEllipse(0, 20, 60, 12);
      g.fillStyle(COLORS.workshop, 1); g.fillRect(-26, -8, 52, 30);
      g.fillStyle(0x6d5ea8, 1); g.fillTriangle(-30, -8, 30, -8, 0, -30);
      g.fillStyle(0xffd166, 1); g.fillRect(-16, 2, 10, 10);
      g.fillStyle(0xffd166, 1); g.fillRect(6, 2, 10, 10);
      break;
    case "resource":
      g.fillStyle(0x000000, 0.15); g.fillEllipse(0, 12, 28, 8);
      if (obj.properties?.resource === "wood") {
        g.fillStyle(COLORS.trunk, 1);
        g.fillRect(-14, -4, 28, 8); g.fillRect(-14, 4, 28, 8);
      } else {
        g.fillStyle(COLORS.rock, 1); g.fillCircle(-6, 0, 9); g.fillCircle(7, 2, 8);
      }
      break;
    case "structure":
      g.fillStyle(0x000000, 0.2); g.fillEllipse(0, 26, 66, 14);
      g.fillStyle(COLORS.structure, 1); g.fillRect(-26, -30, 52, 56);
      g.lineStyle(3, 0xb9a7ff, 0.9); g.strokeRect(-26, -30, 52, 56);
      g.fillStyle(0x2a2145, 1); g.fillCircle(0, -2, 12);
      g.fillStyle(0xb9a7ff, 0.8); g.fillCircle(0, -2, 5);
      break;
    default:
      g.destroy();
      c.destroy();
      return null;
  }
  c.add(g);
  c.setDepth(obj.y);
  // label for notable structures
  if (obj.kind === "structure" || obj.kind === "workstation") {
    const label = scene.add.text(0, -46, obj.label, {
      fontFamily: "monospace", fontSize: "11px", color: "#ffffff",
      backgroundColor: "#00000066", padding: { x: 4, y: 2 },
    }).setOrigin(0.5);
    c.add(label);
  }
  return c;
}
