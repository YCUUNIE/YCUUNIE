# AI Agent World

A **fully local, extensible 2D AI-agent simulation platform**. It looks and
feels like a top-down indie game, but the characters are genuinely autonomous
AI agents that walk around, perceive their world, retrieve memories, make
LLM-backed decisions, talk to each other, use tools, run workflows, write
knowledge into an Obsidian vault, switch between many AI providers, and
recover from failures on their own.

It runs with **zero configuration and zero API keys** — every agent ships with
a real rule-based decision policy (the `local` provider), so the whole world is
alive the moment you start it. Add cloud or local LLMs whenever you want.

```
╔══════════════════════════════════════════════════════╗
║  AI WORLD                              Day 1  06:12   ║
╠═══════════════════════════════════════════╦══════════╣
║        🌲        🏚 Strange Structure      ║ AGENTS   ║
║             🧭 Alex                        ║ ● Alex   ║
║                                           ║ ● Nova   ║
║   🌳    🏠  Village   🛠 Workshop 🔨 Nova   ║ ● Echo   ║
║         🪵 Resources     👁 Echo           ║          ║
╠═══════════════════════════════════════════╩══════════╣
║ EVENT FEED · CHAT                                    ║
╚══════════════════════════════════════════════════════╝
```

---

## Architecture

```
              ┌──────────────────────┐
              │  Phaser 3 Game Client │  TypeScript · Vite (no React)
              └──────────┬───────────┘
                     WebSocket + REST
                         │
              ┌──────────▼───────────┐
              │  FastAPI  (asyncio)   │
              └──────────┬───────────┘
   ┌────────────┬────────┼─────────┬────────────┬───────────┐
   ▼            ▼        ▼         ▼            ▼           ▼
Agent Runtime  World   Event Bus  Workflows   Tools    Recovery /
   │           Engine                                  Self-Healing
   ├── AI Providers (OpenAI, Anthropic, Gemini, OpenRouter,
   │                 Ollama, LM Studio, vLLM, LocalAI, custom, local)
   └── Memory ── Obsidian vault (Markdown + YAML)   SQLite (app state)
```

**Separation of concerns** — each subsystem lives in its own package and only
talks to the others through narrow interfaces:

| Layer | Location |
|-------|----------|
| Game / rendering | `frontend/src/game` |
| UI panels | `frontend/src/ui` |
| Networking | `frontend/src/networking` |
| Agent runtime & decision loop | `backend/app/agents` |
| AI provider abstraction | `backend/app/ai` |
| Memory (Obsidian) | `backend/app/memory` |
| Tools (allowlisted actions) | `backend/app/tools` |
| World engine | `backend/app/world` |
| Workflows & tasks | `backend/app/workflows` |
| Event bus | `backend/app/events` |
| Self-healing / recovery | `backend/app/recovery` |
| Persistence | `backend/app/database` |
| HTTP + WebSocket API | `backend/app/api` |

**Design note — why the split the way it is:** the Phaser renderer never
contains AI logic and the AI runtime never renders. They communicate purely
through events over the WebSocket. This keeps the game loop at 60 FPS while LLM
calls happen asynchronously and event-driven (never per frame).

---

## Quick start

### The one-command way

```bash
cd ai-agent-world
npm install            # installs `concurrently` for the combined dev script
npm run setup          # creates venv, installs backend + frontend deps
npm run dev            # runs backend (:8000) and frontend (:5173) together
```

Then open **http://localhost:5173**.

> No API keys are required — the world runs fully offline on the built-in
> `local` provider.

### macOS / Linux (scripted)

```bash
./scripts/dev.sh
```

### Windows

```bat
scripts\dev.bat
```

### Manual (two terminals)

**Backend**
```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
pip install -r backend/requirements.txt
python -m backend.app.main            # http://127.0.0.1:8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev                           # http://127.0.0.1:5173 (proxies /api and /ws)
```

### Production / single server

```bash
cd frontend && npm run build          # emits frontend/dist
cd .. && python -m backend.app.main   # backend serves the built app at :8000
```

### Docker (optional)

```bash
docker compose up --build             # open http://localhost:8000
```

---

## Using AI providers

The frontend **never** calls an AI API. All requests go through the backend,
where keys stay server-side. Each agent independently chooses its provider,
model, temperature, decision interval and a fallback chain.

### Environment variables (secrets only)

Copy `.env.example` to `.env` and fill in what you need:

```
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
OPENROUTER_API_KEY=...
```

Keys are **never** logged, sent over the WebSocket, or exposed to the browser.

### Per-provider setup

| Provider | Setup |
|----------|-------|
| **OpenAI** | `OPENAI_API_KEY` in `.env`; set an agent's `provider: openai`, `model: <model-id>`. |
| **Anthropic** | `ANTHROPIC_API_KEY`; `provider: anthropic`, `model: <model-id>`. |
| **Gemini** | `GOOGLE_API_KEY`; `provider: gemini`, `model: <model-id>`. |
| **OpenRouter** | `OPENROUTER_API_KEY`; `provider: openrouter`, `model: <vendor/model>`. |
| **Ollama** | Run `ollama serve`; `provider: ollama`, `model: <pulled-model>`. Uses the OpenAI-compatible endpoint at `http://localhost:11434/v1`. No key. |
| **LM Studio** | Start its local server; `provider: lmstudio` (`http://localhost:1234/v1`). No key. |
| **vLLM** | Serve an OpenAI-compatible endpoint; `provider: vllm`. |
| **LocalAI** | `provider: localai` (`http://localhost:8080/v1`). |
| **Custom** | Any OpenAI-compatible endpoint; `provider: custom`, set `CUSTOM_BASE_URL` / `CUSTOM_API_KEY`. |
| **local** | Built-in rule-based policy. No network, no key. Always available and used as the ultimate fallback. |

Endpoints are configured in `config/config.yaml` and can be overridden per
provider with `"<PROVIDER>_BASE_URL"` env vars (e.g. `OLLAMA_BASE_URL`).

### Multiple providers at once

Different agents can use different providers simultaneously. Edit
`config/agents.json`:

```json
{
  "name": "Alex",
  "provider": { "provider": "anthropic", "model": "<model>", "fallbacks": ["openrouter", "local"] }
}
```

### Fallback

Each agent has a `fallbacks` list. If its primary provider times out, errors,
is rate-limited, or is missing a key, the request retries down the chain and
finally lands on `local` so the agent never freezes. Fallback activations are
shown in the Event Feed (Recovery filter) and the Debug panel.

---

## Obsidian integration

Set your vault path in `config/config.yaml` (`obsidian.vault_path`) or via
`OBSIDIAN_VAULT_PATH`. If empty, the bundled `./obsidian` folder is used as a
demo vault. Access is **sandboxed** to that vault — agents can never read or
write outside it.

Structure created and maintained by the agents:

```
AI-Agents/
  Agents/         Alex.md, Nova.md, Echo.md      (profiles)
  Memories/       <Agent>/*.md                    (long-term memories)
  Conversations/  Tasks/  Workflows/  World/  Research/  Logs/
  System/Repairs/ *.md                            (self-healing reports)
```

Each note uses YAML frontmatter, e.g.:

```markdown
---
agent: Alex
type: memory
importance: high
created: 2026-08-08 06:12
---
Alex discovered a strange structure near the northern forest.
```

Retrieval is **targeted** (keyword + importance + recency scoring), never
"dump the whole vault into the prompt". Open the vault in Obsidian, or click
**📂 Memory** in the app to browse the live files.

---

## Interacting with the world

- **Chat** to `All Agents`, an individual agent, `System`, or the `Repair Agent`.
  Agents answer from their real state, memory, location and relationships.
- **Natural-language commands**, e.g. *"tell Alex to investigate the northern
  forest"*, *"pause the simulation"*, *"speed up to 5"*.
- **Slash commands**: `/help /agents /status /pause /resume /speed N /select NAME
  /focus NAME /memory NAME /tasks /workflows /system`.
- **Simulation controls**: play, pause, x1/x2/x5/x10, single-step, stop.
- **Click an agent** to open its inspector (personality, goals, provider,
  relationships, last action) with buttons for Memory, Workflow, Edit, Chat, Focus.
- **Panels**: Agents / Providers / Health tabs, Event Feed with category
  filters, Debug panel, Agent editor, Failure demo, Memory browser.

---

## Self-healing

`SelfHealingManager` runs the pipeline:

```
detect → classify → diagnose → checkpoint → repair → verify (tests + health)
      → success ? resume : rollback → log (SQLite + Obsidian)
```

The internal **Repair Agent** has a **restricted** toolset (inspect state, run
tests/health checks, checkpoint/rollback, restart agents/services, and
*propose* code patches). It has **no shell access and never executes arbitrary
code**. Source-code repair is confined to the project workspace, forbids
`.env`/keys/OS files, and requires explicit approval (assisted mode).

Try it live: **⚡ Failure Demo** → simulate a provider failure, agent error,
WebSocket disconnect, invalid tool call, or workflow failure, and watch the
recovery play out in the Event Feed.

---

## Extending

### Add an agent

Add an object to `config/agents.json` (or use the in-app **+ New Agent**
editor, which supports JSON import/export):

```json
{ "name": "Mira", "role": "Merchant",
  "personality": ["friendly", "calculating"],
  "goals": ["trade resources", "build relationships"],
  "provider": { "provider": "local" } }
```

It appears in the world automatically on restart (or immediately via the editor).

### Add a provider

Subclass `AIProvider` in `backend/app/ai/base.py`, implement `generate()` and
`health()`, then register it in `backend/app/ai/manager.py`. Nothing in the
agent system needs to change.

```python
class MyProvider(AIProvider):
    name = "myprovider"
    async def generate(self, messages, model, temperature, max_tokens, timeout, want_json=True):
        ...  # return (text, tokens)
```

### Add a tool

Add a Pydantic action model in `backend/app/schemas.py`, then an executor in
`backend/app/tools/registry.py` and register it in `TOOL_CATALOG` /
`_executors`. Tools are allowlisted per agent and validated before execution.

---

## Memory system

`MemoryProvider` (`remember / recall / search / summarize / forget / all_for`)
is the interface; `ObsidianMemoryProvider` is the reference implementation.
Memories carry an importance (`low/medium/high/critical`) — trivial events stay
transient, important ones persist to the vault. Swap in a vector DB later
(Chroma, Qdrant, …) by implementing the same interface.

---

## Testing

```bash
npm run test           # or: python -m pytest backend/tests -q
```

Covers agents, providers & fallback, memory & sandbox, tools & validation,
workflows & cycles, self-healing & rollback, and a full API + WebSocket
end-to-end integration test.

Type-check / build the frontend:

```bash
cd frontend && npm run build
```

---

## Debugging & troubleshooting

- **Debug panel** (🐞) shows each agent's provider, model, last action, memory
  retrieved, LLM latency, tokens, last error, recovery state and WebSocket status.
- **Health**: `GET /api/health` and `GET /api/health/detailed`, mirrored in the
  Health tab.
- **Nothing moves / offline dot** — the backend isn't reachable; start it and
  the frontend auto-reconnects.
- **A provider shows offline** — that's expected if the service isn't running or
  the key is unset; agents fall back to `local`.
- **Ollama in Docker** — set `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`.
- **Port in use** — change the port in `backend/app/main.py` (`main()`).

---

## Security model

Agents only ever act through the explicit, validated tool allowlist. They
**cannot** run shell commands or arbitrary code, read files outside the
configured Obsidian vault, touch `.env`/keys/OS files, or reach the network
except via the backend's provider layer. API keys never leave the server.

---

## Project layout

```
ai-agent-world/
├── frontend/          Phaser 3 + Vite + TypeScript client
├── backend/           FastAPI app + tests
├── config/            config.yaml, agents.json
├── obsidian/          bundled demo vault
├── scripts/           dev.sh, dev.bat
├── data/              SQLite (generated)
├── .env.example
├── docker-compose.yml
└── package.json       one-command dev runner
```

---

## Roadmap (architected for, not yet built)

Larger / procedural worlds, multiplayer, voice, vision models, image
generation, factions & economy, crafting, quests, agent learning, and vector
databases — the interfaces (providers, memory, tools, world, workflows) are
designed to make these additive rather than invasive.
