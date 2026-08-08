"""World engine.

Holds persistent world state — named locations, discoverable objects and
resource nodes — and answers perception queries ("what is near this point?").
The world has memory of its own: what has been discovered, by whom, and what is
known about it. Agent positions live on the agents; the world provides the
static stage and the perception model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import get_config


@dataclass
class WorldObject:
    id: str
    kind: str                     # tree | rock | water | building | workstation | resource | structure | path
    x: float
    y: float
    label: str = ""
    discovered: bool = True       # scenery is known; secrets start hidden
    discovered_by: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "x": self.x, "y": self.y, "label": self.label,
            "discovered": self.discovered, "discovered_by": self.discovered_by,
            "properties": self.properties,
        }


@dataclass
class Location:
    name: str
    x: float
    y: float
    radius: float
    kind: str = "area"
    description: str = ""

    def contains(self, x: float, y: float) -> bool:
        return math.hypot(x - self.x, y - self.y) <= self.radius

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "x": self.x, "y": self.y, "radius": self.radius,
                "kind": self.kind, "description": self.description}


class World:
    def __init__(self):
        cfg = get_config()
        self.width = cfg.get("world", "width", default=1600)
        self.height = cfg.get("world", "height", default=1200)
        self.objects: Dict[str, WorldObject] = {}
        self.locations: Dict[str, Location] = {}
        self.notes: Dict[str, Dict[str, Any]] = {}   # world-memory keyed by location name
        self._build_default_world()

    def _build_default_world(self) -> None:
        # Named locations that agents can reference and travel to.
        locs = [
            Location("Village", 800, 620, 220, "settlement", "The central settlement where agents gather."),
            Location("Workshop", 980, 700, 90, "workstation", "Nova's workshop for building structures."),
            Location("Resource Area", 560, 780, 140, "resource", "A clearing rich in wood and stone."),
            Location("River", 300, 400, 160, "water", "A river running through the western map."),
            Location("Northern Forest", 700, 180, 240, "forest", "A dense forest to the north."),
            Location("Unknown Structure", 720, 140, 60, "structure", "A strange structure at the forest edge."),
        ]
        for loc in locs:
            self.locations[loc.name] = loc

        objs: List[WorldObject] = []
        # scenery trees around the forest
        forest_trees = [(640, 160), (700, 120), (760, 180), (680, 220), (740, 240), (620, 220)]
        for i, (x, y) in enumerate(forest_trees):
            objs.append(WorldObject(f"tree_{i}", "tree", x, y, "Tree"))
        # village trees
        for i, (x, y) in enumerate([(720, 560), (900, 560), (760, 700), (860, 700)]):
            objs.append(WorldObject(f"vtree_{i}", "tree", x, y, "Tree"))
        # rocks
        for i, (x, y) in enumerate([(520, 760), (600, 820), (560, 720)]):
            objs.append(WorldObject(f"rock_{i}", "rock", x, y, "Rock"))
        # buildings & workstation
        objs.append(WorldObject("house_1", "building", 780, 620, "House"))
        objs.append(WorldObject("house_2", "building", 860, 640, "House"))
        objs.append(WorldObject("workshop", "workstation", 980, 700, "Workshop",
                                properties={"buildable": True}))
        # resource nodes
        objs.append(WorldObject("wood_node", "resource", 560, 780, "Wood",
                                properties={"resource": "wood", "amount": 20}))
        objs.append(WorldObject("stone_node", "resource", 600, 820, "Stone",
                                properties={"resource": "stone", "amount": 15}))
        # the mystery — starts undiscovered
        objs.append(WorldObject("structure_1", "structure", 720, 140, "Strange Structure",
                                discovered=False,
                                properties={"danger": "unknown", "contents": "unknown"}))
        for obj in objs:
            self.objects[obj.id] = obj

    # --- perception --------------------------------------------------------
    def location_at(self, x: float, y: float) -> Optional[str]:
        best: Optional[str] = None
        best_d = 1e9
        for loc in self.locations.values():
            d = math.hypot(x - loc.x, y - loc.y)
            if loc.contains(x, y) and d < best_d:
                best, best_d = loc.name, d
        return best

    def nearest_location(self, x: float, y: float) -> str:
        return min(self.locations.values(), key=lambda l: math.hypot(x - l.x, y - l.y)).name

    def objects_near(self, x: float, y: float, radius: float = 180, include_hidden: bool = False) -> List[WorldObject]:
        out = []
        for obj in self.objects.values():
            if not include_hidden and not obj.discovered:
                continue
            if math.hypot(x - obj.x, y - obj.y) <= radius:
                out.append(obj)
        return out

    def discover(self, object_id: str, agent: str) -> Optional[WorldObject]:
        obj = self.objects.get(object_id)
        if obj and not obj.discovered:
            obj.discovered = True
            obj.discovered_by = agent
            return obj
        return obj

    def location(self, name: str) -> Optional[Location]:
        return self.locations.get(name)

    def note_location(self, name: str, **kw: Any) -> None:
        self.notes.setdefault(name, {})
        self.notes[name].update(kw)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "locations": [l.to_dict() for l in self.locations.values()],
            "objects": [o.to_dict() for o in self.objects.values() if o.discovered],
            "notes": self.notes,
        }


_world: Optional[World] = None


def get_world() -> World:
    global _world
    if _world is None:
        _world = World()
    return _world
