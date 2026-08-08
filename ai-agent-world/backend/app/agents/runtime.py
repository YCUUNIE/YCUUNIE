"""Agent runtime — the beating heart of the simulation.

Responsibilities:
  * own the world tick loop (movement, decision scheduling) — decoupled from
    LLM calls, which are event-driven and async so the world never blocks;
  * for each agent, run perceive → recall → decide (LLM) → validate → act →
    remember → emit, on the agent's own decision interval;
  * provide a real, offline *rule-based policy* used both when an agent's
    provider is ``local`` and as the ultimate fallback (graceful degradation);
  * route agent-to-agent messages and user chat through the same live state;
  * detect and recover from stuck agents, invalid actions and repeated errors.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..ai import LLMRequest, get_ai_manager
from ..database import get_database
from ..events import event_bus
from ..memory import Memory, get_memory_provider
from ..schemas import ChatReply, Decision
from ..tools import ToolContext, get_tool_registry
from ..workflows import get_workflow_engine
from ..world import get_world
from .agent import Agent, AgentState, ProviderConfig
from .defaults import default_agents


class AgentRuntime:
    def __init__(self):
        self.world = get_world()
        self.memory = get_memory_provider()
        self.workflows = get_workflow_engine()
        self.tools = get_tool_registry()
        self.ai = get_ai_manager()
        self.db = get_database()

        self.agents: Dict[str, Agent] = {}
        self.running = False
        self.paused = False
        self.speed = 1.0
        self.sim_time = 0.0
        self.day = 1
        self._task: Optional[asyncio.Task] = None
        self._tick_rate = 10
        self._move_emit_accum = 0.0
        self._decision_locks: Dict[str, bool] = {}

    # --- lifecycle ---------------------------------------------------------
    def load_defaults(self) -> None:
        for agent in default_agents():
            self.agents[agent.id] = agent
        # spread the three agents around the village at start
        starts = {"alex": (720, 560), "nova": (940, 700), "echo": (760, 720)}
        for aid, (x, y) in starts.items():
            if aid in self.agents:
                self.agents[aid].x, self.agents[aid].y = x, y

    def by_name(self) -> Dict[str, Agent]:
        return {a.name: a for a in self.agents.values()}

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.paused = False
        for agent in self.agents.values():
            agent.next_decision_at = self.sim_time + random.uniform(0.5, 3.0)
            await event_bus.emit("agent.created", agent=agent.to_dict())
        self._task = asyncio.create_task(self._loop())
        await event_bus.emit("system.recovery", scope="sim", message="Simulation started")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.1, min(10.0, speed))

    # --- main loop ---------------------------------------------------------
    async def _loop(self) -> None:
        interval = 1.0 / self._tick_rate
        while self.running:
            await asyncio.sleep(interval)
            if self.paused:
                continue
            sim_dt = interval * self.speed
            self.sim_time += sim_dt
            # day/time bookkeeping (1 in-game day == 240 sim-seconds)
            self.day = 1 + int(self.sim_time // 240)

            moved_any = False
            for agent in self.agents.values():
                if agent.step_movement(sim_dt):
                    moved_any = True

            # throttle movement broadcasts to ~5/sec
            self._move_emit_accum += interval
            if moved_any and self._move_emit_accum >= 0.18:
                self._move_emit_accum = 0.0
                for agent in self.agents.values():
                    if agent.is_moving() or agent.state == AgentState.WALKING:
                        await event_bus.emit("agent.moved", **agent.public_view())

            # schedule decisions
            for agent in self.agents.values():
                if self.sim_time >= agent.next_decision_at and not self._decision_locks.get(agent.id):
                    self._decision_locks[agent.id] = True
                    asyncio.create_task(self._decision_cycle(agent))

    async def step_once(self) -> None:
        """Advance a single decision cycle for every agent (debug single-step)."""
        for agent in self.agents.values():
            if not self._decision_locks.get(agent.id):
                self._decision_locks[agent.id] = True
                await self._decision_cycle(agent)

    # --- perception --------------------------------------------------------
    def _perception(self, agent: Agent) -> Dict[str, Any]:
        loc = self.world.location_at(agent.x, agent.y) or self.world.nearest_location(agent.x, agent.y)
        nearby_objects = [
            {"id": o.id, "kind": o.kind, "label": o.label, "x": round(o.x), "y": round(o.y)}
            for o in self.world.objects_near(agent.x, agent.y, 200)
        ]
        nearby_agents = []
        for other in self.agents.values():
            if other.id == agent.id:
                continue
            d = ((other.x - agent.x) ** 2 + (other.y - agent.y) ** 2) ** 0.5
            if d < 260:
                nearby_agents.append({"name": other.name, "activity": other.activity, "distance": round(d)})
        # sense (but do not reveal) hidden things nearby
        hint = None
        for o in self.world.objects_near(agent.x, agent.y, 320, include_hidden=True):
            if not o.discovered:
                hint = "You sense something unusual nearby that you haven't examined."
                break
        task = self.workflows.active_task_for(agent.name)
        wf = self.workflows.workflow_for(agent.name)
        return {
            "location": loc,
            "position": {"x": round(agent.x), "y": round(agent.y)},
            "objects_nearby": nearby_objects,
            "agents_nearby": nearby_agents,
            "unusual_hint": hint,
            "inbox": list(agent.inbox),
            "current_task": task.to_dict() if task else None,
            "workflow": wf.to_dict() if wf else None,
            "known_locations": list(self.world.locations.keys()),
        }

    # --- decision cycle ----------------------------------------------------
    async def _decision_cycle(self, agent: Agent) -> None:
        try:
            agent.thinking = True
            prev_state = agent.state
            if agent.state not in (AgentState.ERROR, AgentState.RECOVERING):
                agent.state = AgentState.THINKING
            perception = self._perception(agent)

            # memory retrieval based on the current situation
            query = perception["location"] or ""
            if perception["inbox"]:
                query += " " + " ".join(m["message"] for m in perception["inbox"])
            if perception["unusual_hint"]:
                query += " unusual structure discovery"
            memories = await self.memory.recall(agent.name, query or agent.role, limit=4)
            agent.memories_retrieved = len(memories)
            if memories:
                await event_bus.emit("agent.memory.recalled", agent_id=agent.id, count=len(memories))

            await event_bus.emit("agent.thought", agent_id=agent.id, agent_name=agent.name,
                                 summary=self._thinking_summary(agent, perception))

            decision = await self._decide(agent, perception, memories)

            # guard against infinite action loops (spec: "infinite action loop")
            decision = await self._loop_guard(agent, decision)

            # act
            await self._apply_decision(agent, decision, perception)

            agent.consecutive_errors = 0
            if agent.recovery_state != "healthy":
                agent.recovery_state = "healthy"
                await event_bus.emit("agent.recovered", agent_id=agent.id, agent_name=agent.name)

        except Exception as exc:  # noqa: BLE001
            await self._handle_agent_error(agent, str(exc))
        finally:
            agent.thinking = False
            # stuck detection: walking but not progressing
            self._stuck_check(agent)
            # schedule next decision (personality can vary cadence slightly)
            jitter = random.uniform(-1.0, 1.5)
            agent.next_decision_at = self.sim_time + max(3.0, agent.provider.decision_interval + jitter)
            self._decision_locks[agent.id] = False
            await event_bus.emit("agent.state", **agent.public_view())

    def _thinking_summary(self, agent: Agent, perception: Dict[str, Any]) -> str:
        # Safe summary only — never raw chain-of-thought.
        if perception["inbox"]:
            return f"{agent.name} is considering a message from {perception['inbox'][0]['from']}..."
        if perception["unusual_hint"]:
            return f"{agent.name} is evaluating something unusual nearby..."
        return f"{agent.name} is deciding what to do next..."

    async def _decide(self, agent: Agent, perception: Dict[str, Any], memories: List[Memory]) -> Decision:
        """Ask the agent's provider for a structured decision; repair/fallback as needed."""
        def local_policy() -> str:
            return self._heuristic_decision(agent, perception).model_dump_json()

        if agent.provider.provider == "local":
            return self._heuristic_decision(agent, perception)

        messages = self._build_prompt(agent, perception, memories)
        request = LLMRequest(
            messages=messages, provider=agent.provider.provider, model=agent.provider.model,
            temperature=agent.provider.temperature, max_tokens=agent.provider.max_tokens,
            fallbacks=agent.provider.fallbacks, local_policy=local_policy, agent_id=agent.id,
        )
        result = await self.ai.complete(request)
        agent.last_provider_used = result.provider_used
        agent.last_latency = result.latency
        agent.last_tokens = result.tokens
        if result.used_fallback:
            agent.recovery_state = "recovering"
            await event_bus.emit("agent.state", **agent.public_view())

        # parse + validate; repair once, then fall back to heuristic
        try:
            return Decision.model_validate_json(result.text)
        except Exception:
            try:
                data = json.loads(result.text)
                return Decision.model_validate(data)
            except Exception:
                await event_bus.emit("agent.error", agent_id=agent.id, scope="parse",
                                     message="Invalid LLM output; normalized via local policy")
                return self._heuristic_decision(agent, perception)

    def _build_prompt(self, agent: Agent, perception: Dict[str, Any], memories: List[Memory]) -> List[Dict[str, str]]:
        mem_text = "\n".join(f"- {m.content}" for m in memories) or "(no relevant memories)"
        system = (
            f"You are {agent.name}, a {agent.role} in a living 2D world. "
            f"Personality: {', '.join(agent.personality)}. "
            f"Background: {agent.background}. "
            f"Goals: {'; '.join(agent.goals)}.\n"
            "Your personality MUST shape your choices. Respond ONLY with a compact JSON object of the form:\n"
            '{"summary": str, "intent": str, "speech": str|null, '
            '"action": {"type": one of '
            '[move_to, move_to_location, talk_to_agent, observe_area, inspect_object, work, create_task, complete_task, idle], ...}, '
            '"memory": {"should_store": bool, "content": str, "importance": "low|medium|high|critical"}}\n'
            "For move_to include x,y. For move_to_location include location. "
            "For talk_to_agent include target,message. Do not output anything except JSON. "
            "The summary must be a safe one-line intention, never hidden reasoning."
        )
        user = (
            f"Current situation:\n"
            f"- Location: {perception['location']}\n"
            f"- Position: {perception['position']}\n"
            f"- Objects nearby: {perception['objects_nearby']}\n"
            f"- Agents nearby: {perception['agents_nearby']}\n"
            f"- Known locations: {perception['known_locations']}\n"
            f"- Current task: {perception['current_task']}\n"
            f"- Messages to you: {perception['inbox']}\n"
            f"- Note: {perception['unusual_hint'] or 'nothing unusual'}\n"
            f"Relevant memories:\n{mem_text}\n\n"
            "Decide your next single action as JSON."
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # --- the offline rule-based brain -------------------------------------
    def _traits(self, agent: Agent) -> Dict[str, bool]:
        p = {t.lower() for t in agent.personality}
        return {
            "curious": "curious" in p, "risk": "risk-taking" in p or "risk" in p,
            "cautious": "cautious" in p, "social": "social" in p,
            "analytical": "analytical" in p, "observant": "observant" in p,
            "practical": "practical" in p, "patient" : "patient" in p,
        }

    def _heuristic_decision(self, agent: Agent, perception: Dict[str, Any]) -> Decision:
        """A genuine (if simple) decision policy. Personality changes behaviour."""
        traits = self._traits(agent)
        inbox = perception["inbox"]

        # 1) Always react to messages first (social agents more warmly).
        if inbox:
            msg = inbox[0]
            agent.inbox = agent.inbox[1:]
            sender = msg["from"]
            text = msg["message"].lower()
            sig = f"{sender}:{text}"
            # Ignore a message identical to the last one we handled (kills echo loops).
            if agent.flags.get("last_inbox_sig") == sig:
                return Decision(summary=f"{agent.name} already handled {sender}'s message",
                                intent="idle", speech=None,
                                action={"type": "idle", "reason": "already responded"})
            agent.flags["last_inbox_sig"] = sig
            # A discovery report: go investigate (once per report).
            discovery = any(w in text for w in ("found", "structure", "discover", "strange", "unusual", "investigate"))
            if discovery and not agent.flags.get("investigated_" + sender):
                agent.flags["investigated_" + sender] = True
                loc = "Unknown Structure" if agent.role != "Explorer" else "Northern Forest"
                reply = ("On my way to take a look." if traits["practical"] or traits["risk"]
                         else "Interesting — I'll record this and come observe.")
                return Decision(
                    summary=f"Responding to {sender} about a discovery",
                    intent="investigate", speech=f"{sender}, {reply}",
                    action={"type": "move_to_location", "location": loc},
                    memory={"should_store": True, "importance": "medium",
                            "content": f"{sender} told me: {msg['message']}"},
                )
            # Any other message: note it internally. Do NOT reply back — replying
            # creates a new inbound message and can ping-pong forever.
            return Decision(summary=f"{agent.name} notes {sender}'s message", intent="acknowledge",
                            speech=None, action={"type": "idle", "reason": f"heard {sender}"},
                            memory={"should_store": True, "importance": "low",
                                    "content": f"{sender} said: {msg['message']}"})

        # 2) If something unusual is sensed, curious/risk-taking agents investigate.
        if perception["unusual_hint"]:
            if traits["risk"] or traits["curious"] or agent.role == "Explorer":
                # find the hidden object and observe to reveal it
                return Decision(
                    summary=f"{agent.name} moves to examine something unusual",
                    intent="investigate", speech="There's something odd here — let me look.",
                    action={"type": "observe_area", "radius": 320},
                    memory={"should_store": False, "content": "", "importance": "low"},
                )
            if traits["cautious"] or traits["analytical"]:
                return Decision(summary=f"{agent.name} cautiously observes before approaching",
                                intent="assess", speech="I should assess this carefully first.",
                                action={"type": "observe_area", "radius": 220})

        # 3) Role-driven default behaviour.
        if agent.role == "Explorer":
            structure = self.world.objects.get("structure_1")
            discovered_by_me = bool(structure and structure.discovered and structure.discovered_by == agent.name)
            # Announce the discovery to Nova exactly ONCE.
            if discovered_by_me and not agent.flags.get("shared_structure"):
                agent.flags["shared_structure"] = True
                return Decision(
                    summary="Alex shares the discovery with Nova",
                    intent="share", speech="I found a strange structure near the northern forest!",
                    action={"type": "talk_to_agent", "target": "Nova",
                            "message": "I found a strange structure near the northern forest. Can you investigate?"},
                    memory={"should_store": True, "importance": "high",
                            "content": "I discovered a strange structure near the northern forest and told Nova."},
                )
            # Otherwise keep exploring somewhere new (avoid repeating the last spot).
            options = ["Northern Forest", "River", "Resource Area", "Village", "Workshop"]
            if discovered_by_me:
                options = [o for o in options if o != "Unknown Structure"]
            last = agent.flags.get("last_explore_target")
            options = [o for o in options if o != last] or options
            target = random.choice(options)
            agent.flags["last_explore_target"] = target
            if random.random() < 0.35:
                return Decision(summary=f"Alex scans the area around {perception['location']}",
                                intent="observe", speech=None,
                                action={"type": "observe_area", "radius": 240})
            return Decision(summary=f"Alex explores toward {target}", intent="explore",
                            speech=None, action={"type": "move_to_location", "location": target},
                            memory={"should_store": False, "content": ""})

        if agent.role == "Engineer":
            loc = perception["location"]
            if loc != "Workshop" and random.random() < 0.5:
                return Decision(summary="Nova heads to the workshop", intent="build", speech=None,
                                action={"type": "move_to_location", "location": "Workshop"})
            if loc in ("Resource Area",) or random.random() < 0.3:
                return Decision(summary="Nova collects resources", intent="gather",
                                action={"type": "move_to_location", "location": "Resource Area"})
            return Decision(summary="Nova works on the settlement", intent="build",
                            speech=None, action={"type": "work", "target": "workshop", "note": "improving the workshop"},
                            memory={"should_store": True, "importance": "low",
                                    "content": "Worked on improving the workshop."})

        if agent.role == "Researcher":
            # observe & record; follow interesting agents
            if random.random() < 0.5:
                return Decision(summary="Echo observes and records", intent="record",
                                action={"type": "observe_area", "radius": 240},
                                memory={"should_store": True, "importance": "medium",
                                        "content": f"Observed activity near {perception['location']}."})
            movers = [a for a in perception["agents_nearby"]]
            if movers:
                who = movers[0]["name"]
                return Decision(summary=f"Echo studies {who}", intent="study",
                                action={"type": "move_to_location", "location": "Village"},
                                memory={"should_store": True, "importance": "low",
                                        "content": f"Studying {who}'s behaviour."})
            return Decision(summary="Echo patrols the village", intent="observe",
                            action={"type": "move_to_location", "location": "Northern Forest"})

        # fallback
        return Decision(summary=f"{agent.name} waits", intent="idle",
                        action={"type": "idle", "reason": "no pressing goal"})

    # --- loop detection & break-out ---------------------------------------
    async def _loop_guard(self, agent: Agent, decision: Decision) -> Decision:
        """If an agent chooses the same action+speech three cycles running, treat
        it as a stuck loop, self-heal, and force a different action."""
        sig = (decision.action.type + "|" + (decision.speech or "")[:50] + "|"
               + json.dumps(decision.action.model_dump(), sort_keys=True, default=str)[:120])
        # idle is a legitimate resting state; don't count it as a loop.
        if decision.action.type == "idle":
            agent.loop_signature = sig
            agent.loop_repeat = 0
            return decision
        if sig == agent.loop_signature:
            agent.loop_repeat += 1
        else:
            agent.loop_signature = sig
            agent.loop_repeat = 0
        if agent.loop_repeat >= 2:  # the 3rd identical action in a row
            agent.loop_repeat = 0
            agent.loop_signature = ""
            await event_bus.emit("agent.error", agent_id=agent.id, agent_name=agent.name,
                                 scope="loop", message="Repeated identical action detected; breaking the loop")
            alt = self._break_loop_action(agent)
            await event_bus.emit("agent.recovered", agent_id=agent.id, agent_name=agent.name,
                                 reason="broke out of an action loop")
            return alt
        return decision

    def _break_loop_action(self, agent: Agent) -> Decision:
        if agent.role == "Explorer":
            opts = [n for n in self.world.locations if n != agent.flags.get("last_explore_target")]
            target = random.choice(opts) if opts else "Village"
            agent.flags["last_explore_target"] = target
            return Decision(summary=f"{agent.name} changes course to {target}", intent="explore",
                            action={"type": "move_to_location", "location": target})
        if agent.role == "Engineer":
            return Decision(summary=f"{agent.name} gets back to building", intent="build",
                            action={"type": "work", "target": "workshop", "note": "back to building"})
        return Decision(summary=f"{agent.name} pauses to observe", intent="observe",
                        action={"type": "observe_area", "radius": 200})

    # --- apply decision ----------------------------------------------------
    async def _apply_decision(self, agent: Agent, decision: Decision, perception: Dict[str, Any]) -> None:
        agent.last_summary = decision.summary
        agent.last_action = decision.action.model_dump()

        if decision.speech:
            agent.state = AgentState.TALKING
            await event_bus.emit("agent.talked", agent_id=agent.id, agent_name=agent.name,
                                 target="world", message=decision.speech)
            await self.db.log_message("world", agent.name, None, decision.speech)

        ctx = ToolContext(agent=agent, world=self.world, memory=self.memory,
                          workflows=self.workflows, agents_by_name=self.by_name(),
                          deliver_message=self._deliver_message)
        observation = await self.tools.execute(ctx, decision.action)

        # discovery bookkeeping for explorer's structure find
        if isinstance(observation, dict) and observation.get("revealed"):
            for obj in observation["revealed"]:
                await self.memory.remember(Memory.new(
                    agent.name, f"I discovered {obj['label']} near {perception['location']}.",
                    importance="high", kind="observation", source="world"))
                await event_bus.emit("agent.memory.created", agent_id=agent.id,
                                     importance="high", content=f"Discovered {obj['label']}")

        # store requested memory (skip identical consecutive writes to avoid spam)
        if (decision.memory.should_store and decision.memory.content
                and decision.memory.content != agent.last_memory_content):
            agent.last_memory_content = decision.memory.content
            mem = await self.memory.remember(Memory.new(
                agent.name, decision.memory.content, importance=decision.memory.importance.value,
                kind="memory", source="world"))
            await event_bus.emit("agent.memory.created", agent_id=agent.id,
                                 importance=mem.importance, content=mem.content)

        await self.db.upsert("agents", agent.id, agent.to_dict())

    # --- messaging ---------------------------------------------------------
    async def _deliver_message(self, target_name: str, from_name: str, message: str) -> None:
        target = self.by_name().get(target_name)
        if not target:
            return
        target.inbox.append({"from": from_name, "message": message, "ts": time.time()})
        # make the recipient react soon
        target.next_decision_at = min(target.next_decision_at, self.sim_time + 1.0)
        await self.db.log_message("agent", from_name, target_name, message)

    # --- user chat ---------------------------------------------------------
    async def chat_with_agent(self, agent_name: str, message: str) -> Dict[str, Any]:
        agent = self.by_name().get(agent_name)
        if not agent:
            return {"agent": agent_name, "reply": f"There is no agent named {agent_name}."}
        prev_state = agent.state
        agent.state = AgentState.THINKING
        agent.thinking = True
        await event_bus.emit("agent.thought", agent_id=agent.id, agent_name=agent.name,
                             summary=f"{agent.name} is considering your message...")
        perception = self._perception(agent)
        memories = await self.memory.recall(agent.name, message, limit=4)

        reply = await self._chat_reply(agent, message, perception, memories)

        agent.state = AgentState.TALKING
        agent.thinking = False
        await event_bus.emit("agent.talked", agent_id=agent.id, agent_name=agent.name,
                             target="You", message=reply.reply)
        await self.db.log_message("chat", agent.name, "You", reply.reply)

        # optionally act on the user's request
        if reply.action is not None:
            ctx = ToolContext(agent=agent, world=self.world, memory=self.memory,
                              workflows=self.workflows, agents_by_name=self.by_name(),
                              deliver_message=self._deliver_message)
            await self.tools.execute(ctx, reply.action)
        if reply.memory.should_store and reply.memory.content:
            await self.memory.remember(Memory.new(agent.name, reply.memory.content,
                                                   importance=reply.memory.importance.value,
                                                   kind="conversation", source="conversation"))
        agent.next_decision_at = self.sim_time + 4.0
        return {"agent": agent.name, "reply": reply.reply}

    async def _chat_reply(self, agent: Agent, message: str, perception: Dict[str, Any],
                          memories: List[Memory]) -> ChatReply:
        def local_policy() -> str:
            return self._heuristic_chat(agent, message, perception, memories).model_dump_json()

        if agent.provider.provider == "local":
            return self._heuristic_chat(agent, message, perception, memories)

        mem_text = "\n".join(f"- {m.content}" for m in memories) or "(none)"
        system = (
            f"You are {agent.name}, a {agent.role}. Personality: {', '.join(agent.personality)}. "
            f"Background: {agent.background}. Speak in-character, briefly. "
            "You are inside a live 2D world and must answer from your real current state. "
            'Respond ONLY as JSON: {"reply": str, "action": {"type": ...}|null, '
            '"memory": {"should_store": bool, "content": str, "importance": "low|medium|high|critical"}}.'
        )
        user = (
            f"User says: {message}\n"
            f"Your location: {perception['location']}. Nearby agents: {perception['agents_nearby']}. "
            f"Current task: {perception['current_task']}. Memories:\n{mem_text}\n"
            "If the user asks you to go somewhere or do something, include an action."
        )
        request = LLMRequest(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            provider=agent.provider.provider, model=agent.provider.model,
            temperature=agent.provider.temperature, max_tokens=agent.provider.max_tokens,
            fallbacks=agent.provider.fallbacks, local_policy=local_policy, agent_id=agent.id,
        )
        result = await self.ai.complete(request)
        agent.last_provider_used = result.provider_used
        try:
            return ChatReply.model_validate_json(result.text)
        except Exception:
            try:
                return ChatReply.model_validate(json.loads(result.text))
            except Exception:
                return self._heuristic_chat(agent, message, perception, memories)

    def _heuristic_chat(self, agent: Agent, message: str, perception: Dict[str, Any],
                        memories: List[Memory]) -> ChatReply:
        text = message.lower()
        loc = perception["location"]
        # world-aware answers
        if "where" in text and "you" in text:
            return ChatReply(reply=f"I'm currently near the {loc}.")
        for other in self.agents.values():
            if other.id != agent.id and other.name.lower() in text and "where" in text:
                oloc = self.world.location_at(other.x, other.y) or self.world.nearest_location(other.x, other.y)
                return ChatReply(reply=f"{other.name} is over near the {oloc}.")
        if "what" in text and ("doing" in text or "up to" in text):
            return ChatReply(reply=f"I'm {agent.activity}. My goal right now is to {agent.goals[0] if agent.goals else 'help out'}.")
        if "remember" in text or "found" in text or "discover" in text:
            if memories:
                return ChatReply(reply="Here's what stands out: " + memories[0].content)
            return ChatReply(reply="Nothing major to report yet, but I'm keeping watch.")
        if ("go" in text or "investigate" in text or "explore" in text):
            # map any known location mentioned
            for name in self.world.locations:
                if name.lower() in text:
                    return ChatReply(reply=f"Alright, heading to the {name} now.",
                                     action={"type": "move_to_location", "location": name},
                                     memory={"should_store": True, "importance": "medium",
                                             "content": f"User asked me to go to {name}."})
            return ChatReply(reply="Sure — where would you like me to go?")
        if "build" in text and agent.role == "Engineer":
            return ChatReply(reply="I'll get to the workshop and start building.",
                             action={"type": "move_to_location", "location": "Workshop"})
        # personality-flavoured default
        flavour = {
            "Explorer": "Always something new to find out there!",
            "Engineer": "Let me know if you need something built.",
            "Researcher": "I'm documenting everything as it happens.",
        }.get(agent.role, "How can I help?")
        return ChatReply(reply=f"{flavour}")

    # --- recovery ----------------------------------------------------------
    def _stuck_check(self, agent: Agent) -> None:
        if agent.state == AgentState.WALKING and agent.is_moving():
            agent.stuck_counter += 1
        else:
            agent.stuck_counter = 0

    async def recover_agent(self, agent: Agent, reason: str) -> Dict[str, Any]:
        agent.recovery_state = "recovering"
        agent.state = AgentState.RECOVERING
        await event_bus.emit("agent.state", **agent.public_view())
        # safe reset of transient state
        agent.target_x = agent.target_y = None
        agent.inbox = agent.inbox[-3:]
        agent.stuck_counter = 0
        agent.consecutive_errors = 0
        agent.next_decision_at = self.sim_time + 1.0
        agent.state = AgentState.IDLE
        agent.recovery_state = "healthy"
        agent.last_error = None
        await event_bus.emit("agent.recovered", agent_id=agent.id, agent_name=agent.name, reason=reason)
        return {"agent": agent.id, "recovered": True, "reason": reason}

    async def _handle_agent_error(self, agent: Agent, error: str) -> None:
        agent.consecutive_errors += 1
        agent.last_error = error
        agent.recovery_state = "error"
        agent.state = AgentState.ERROR
        await event_bus.emit("agent.error", agent_id=agent.id, agent_name=agent.name,
                             scope="runtime", message=error)
        # automatic recovery after repeated failures
        if agent.consecutive_errors >= 2:
            await self.recover_agent(agent, f"auto-recovery after error: {error[:80]}")

    # --- snapshots ---------------------------------------------------------
    def agents_snapshot(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self.agents.values()]

    def full_snapshot(self) -> Dict[str, Any]:
        return {
            "world": self.world.snapshot(),
            "agents": self.agents_snapshot(),
            "workflows": self.workflows.snapshot(),
            "sim": {"running": self.running, "paused": self.paused, "speed": self.speed,
                    "day": self.day, "time": round(self.sim_time, 1)},
        }

    # --- agent CRUD --------------------------------------------------------
    async def add_agent(self, data: Dict[str, Any]) -> Agent:
        agent = Agent.from_dict(data)
        if not agent.allowed_tools:
            from ..tools.registry import DEFAULT_ALLOWED
            agent.allowed_tools = list(DEFAULT_ALLOWED)
        # place near village
        agent.x, agent.y = 820, 660
        self.agents[agent.id] = agent
        agent.next_decision_at = self.sim_time + 1.0
        await self.db.upsert("agents", agent.id, agent.to_dict())
        await event_bus.emit("agent.created", agent=agent.to_dict())
        return agent

    async def update_agent(self, agent_id: str, data: Dict[str, Any]) -> Optional[Agent]:
        agent = self.agents.get(agent_id)
        if not agent:
            return None
        for field_name in ("name", "role", "personality", "background", "goals", "allowed_tools", "color"):
            if field_name in data:
                setattr(agent, field_name, data[field_name])
        if "provider" in data:
            p = data["provider"]
            agent.provider = ProviderConfig(
                provider=p.get("provider", agent.provider.provider),
                model=p.get("model", agent.provider.model),
                temperature=p.get("temperature", agent.provider.temperature),
                max_tokens=p.get("max_tokens", agent.provider.max_tokens),
                decision_interval=p.get("decision_interval", agent.provider.decision_interval),
                fallbacks=p.get("fallbacks", agent.provider.fallbacks),
            )
        await self.db.upsert("agents", agent.id, agent.to_dict())
        await event_bus.emit("agent.created", agent=agent.to_dict())
        return agent


_runtime: Optional[AgentRuntime] = None


def get_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
    return _runtime
