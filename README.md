<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent ☤

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/NousResearch/hermes-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://nousresearch.com"><img src="https://img.shields.io/badge/Built%20by-Nous%20Research-blueviolet?style=for-the-badge" alt="Built by Nous Research"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
</p>

**The self-improving AI agent built by [Nous Research](https://nousresearch.com).** It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — [Nous Portal](https://portal.nousresearch.com), [OpenRouter](https://openrouter.ai) (200+ models), [NovitaAI](https://novita.ai) (AI-native cloud for Model API, Agent Sandbox, and GPU Cloud), [NVIDIA NIM](https://build.nvidia.com) (Nemotron), [Xiaomi MiMo](https://platform.xiaomimimo.com), [z.ai/GLM](https://z.ai), [Kimi/Moonshot](https://platform.moonshot.ai), [MiniMax](https://www.minimax.io), [Hugging Face](https://huggingface.co), OpenAI, or your own endpoint. Switch with `hermes model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Seven terminal backends — local, Docker, SSH, Singularity, Modal, Daytona, and Vercel Sandbox. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Quick Install

> **Substrate Edition fork (`ggrace519/hermes-agent`):** this fork ships an
> additional PostgreSQL-backed cognitive substrate (Phases A–C) on top of
> upstream Hermes, and replaces SQLite with PostgreSQL for all state. Defaults
> match the upstream installer (`HERMES_HOME=~/.hermes`, CLI shim `hermes`)
> and add a `docker compose` PostgreSQL service on port 5432 with database
> `hermes`. If you already have an upstream Hermes install on the same machine
> and want to coexist with it, pass
> `--cli-name hermes-substrate --hermes-home ~/.hermes-substrate`.
>
> The substrate-edition installer one-liner is:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/ggrace519/hermes-agent/main/scripts/install.sh | bash
> ```

### Linux, macOS, WSL2, Termux

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

### Windows (native, PowerShell) — Early Beta

> **Heads up:** Native Windows support is **early beta**. It installs and runs, but hasn't been road-tested as broadly as our Linux/macOS/WSL2 paths. Please [file issues](https://github.com/NousResearch/hermes-agent/issues) when you hit rough edges. For the most battle-tested Windows setup today, run the Linux/macOS one-liner above inside **WSL2**.

Run this in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)
```

The installer handles everything: uv, Python 3.11, Node.js, ripgrep, ffmpeg, **and a portable Git Bash** (MinGit, unpacked to `%LOCALAPPDATA%\hermes\git` — no admin required, completely isolated from any system Git install).  Hermes uses this bundled Git Bash to run shell commands.

If you already have Git installed, the installer detects it and uses that instead.  Otherwise a ~45MB MinGit download is all you need — it won't touch or interfere with any system Git.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://hermes-agent.nousresearch.com/docs/getting-started/termux). On Termux, Hermes installs a curated `.[termux]` extra because the full `.[all]` extra currently pulls Android-incompatible voice dependencies.
>
> **Windows:** Native Windows is supported as an **early beta** — the PowerShell one-liner above installs everything, but expect rough edges and please file issues when you hit them. If you'd rather use WSL2 (our most battle-tested Windows path), the Linux command works there too. Native Windows install lives under `%LOCALAPPDATA%\hermes`; WSL2 installs under `~/.hermes` as on Linux.  The only Hermes feature that currently needs WSL2 specifically is the browser-based dashboard chat pane (it uses a POSIX PTY — classic CLI and gateway both run natively).

After installation:

```bash
source ~/.bashrc    # reload shell (or: source ~/.zshrc)
hermes              # start chatting!
```

---

## Database setup (Phase 0+)

This fork uses PostgreSQL 17 (with the `vector` and `pg_trgm` extensions)
as the single source of truth for session transcripts, kanban state, and
the substrate's perception slices. No SQLite anywhere — `state.db` and
the kanban SQLite DB from upstream are gone. The substrate-edition
installer brings up a `docker compose` PG service automatically; the
section below covers the manual / production paths.

**For local development:**

```bash
docker compose up -d postgres
export HERMES_PG_DSN=postgresql://hermes:hermes@localhost:5432/hermes
uv run alembic -c migrations/alembic.ini upgrade head
```

**For production deploys:** point `HERMES_PG_DSN` at any PostgreSQL 17+ instance with the `vector` and `pg_trgm` extensions installed. Run `alembic upgrade head` as part of your deploy.

**Migrating from a SQLite Hermes (upstream):**

```bash
uv run hermes db migrate-from-sqlite --sqlite-path ~/.hermes/state.db
```

(One-shot; safe to re-run with `--dry-run` to preview.)

---

## Cognitive substrate (Phases A–C)

> **Heads up:** the substrate adds a `substrate_*` set of tables to your
> Hermes Postgres database, plus four background asyncio workers (Sentinel,
> Curator, force-reject, partition-maintenance). Hermes continues to behave
> exactly as before — substrate failures are non-fatal and the recall path
> is env-gated off by default — but the schema migration is permanent. Back
> up your DB before the first run if you care.

The substrate is a PostgreSQL-backed **L0 perception sink + recall source**
that runs alongside Hermes. Every user message, assistant response, tool
call/result, sub-agent spawn/return, session-lifecycle event, and cron
dispatch is emitted as a *slice* on a named *stream*
(`hermes.world.user_message.cli`, `hermes.self_action.assistant_response`,
etc.). Slices are stored in `substrate_slices` (RANGE-partitioned monthly on
ingest time) and decided by Sentinel.

### Phase A — perception sink

- New schema via Alembic revision `20260523_0003_substrate_skeleton` —
  three tables (`substrate_streams`, `substrate_slices`,
  `substrate_decay_profiles`), 15 auto-registered streams, monthly partition
  carving.
- Background workers spawned on Hermes startup: Sentinel (200 ms tick,
  pass-through stub in A; real defense Phase B+), force-reject (10 s tick,
  drops pending slices past their decay-profile TTL), partition-maintenance
  (24 h tick, keeps a rolling window of 3 monthly partitions ahead of
  `now()`).
- A `hermes substrate` CLI for poking at substrate state. (When
  installed side-by-side as `hermes-substrate`, substitute the launcher name
  accordingly.)

### Phase B — Curator

- The Curator sub-agent runs a continuous decay + release loop. Slices fade
  per their decay profile's half-life; below the profile's
  `min_salience_to_retain` threshold they release per the tombstone policy
  (`thin` / `full` / `none`). Each decision emits a self-state slice on
  `substrate.self_state` so Phase E's Reflector can develop calibration.
- New inspect subtree: `hermes substrate curator [summary | histogram |
  recent | pressure]`.

### Phase C — recall API + pgvector embeddings

- New tables: `substrate_recall_log` (append-only audit of every `recall()`
  call) + the `embedding vector(1536)` column on `substrate_slices`.
- The Curator backfills semantic embeddings asynchronously using
  `text-embedding-3-small` (1536-d); recall against missing-embedding slices
  falls back to keyword Jaccard.
- A `SubstrateMemoryProvider` is registered into Hermes's `MemoryManager`.
  Gated by `HERMES_SUBSTRATE_RECALL` (default `0`) — when enabled, the
  per-turn `<memory-context>` block is composed from substrate slices using
  a composite score (pgvector similarity + keyword Jaccard + salience +
  recency, ranked under a 1500-token budget by default). The model also gets
  a `substrate_recall_more` tool for explicit deeper-search asks.
- New inspect subtree: `hermes substrate recall [summary | recent |
  sample --session-id <id> | config]`.

### Inspecting substrate state

```bash
hermes substrate            # default summary (streams, slice counts, pending)
hermes substrate streams    # per-stream slice counts
hermes substrate slices --stream hermes.world.user_message.cli --limit 20
hermes substrate pending    # current pending-queue depth + oldest age
hermes substrate profiles   # the 4 seeded decay profiles
hermes substrate curator    # Curator decay/release activity
hermes substrate recall     # recall coverage + recent calls
```

If your DB is on an older Alembic revision when Hermes starts, the substrate
boot raises a `RuntimeError` with the upgrade command to run; set
`HERMES_AUTO_MIGRATE=1` to upgrade automatically on first boot.

Procedural operator docs ship as a bundled skill — load with `/substrate` or
`hermes -s substrate`. Design rationale + future-phase specs live
in the [llm-cognitive-thought](https://github.com/ggrace519/llm-cognitive-thought)
spec repo.

---

## Development testing

The test suite uses `pytest-postgresql` against a **separate** PostgreSQL
container so the developer's real Hermes DB on port 5432 is never touched.

**One-time setup:**

```bash
docker compose --profile test up -d postgres-test
# Starts a dedicated PG on port 5433 with its own volume (hermes_pg_test_data).
```

**Running tests locally:**

```bash
# Per-file isolation runner (matches CI). Picks up the test DB via
# tests/conftest.py:_TEST_PG_PORT which defaults to 5433.
PYTEST_XDIST_WORKER=run_local uv run python scripts/run_tests_parallel.py tests/substrate/

# Or a single file:
PYTEST_XDIST_WORKER=run_local uv run python -m pytest tests/substrate/test_commit.py \
    -o "addopts=" --timeout-method=thread --timeout=120
```

`PYTEST_XDIST_WORKER` must be set when running pytest directly (the parallel
runner sets it per subprocess automatically). The value is just a unique label
— `pytest-postgresql` uses it to derive per-worker DB names so concurrent
subprocesses don't race on the shared template DB.

**To target a different test PG** (e.g. an external test cluster), set
`HERMES_TEST_POSTGRES_PORT` (or `POSTGRES_PORT`) before running pytest. CI
sets `HERMES_TEST_POSTGRES_PORT=5432` to use the ephemeral GitHub Actions
service container.

**Running tests in a Linux container (matches CI exactly):**

Windows hosts hit a class of pytest failures that are host-environment
noise (POSIX file permissions, `/mnt/c` paths in acp_adapter tests,
tilde expansion in cron_workdir tests) rather than real bugs. The
`test-runner` docker-compose service runs the suite inside Debian +
Python 3.11 + the `[all,dev]` extras — mirroring the GH Actions CI
environment exactly — so failures reproduce locally and Windows-host
noise disappears.

```bash
# First-time build (~3 min):
docker compose --profile test up -d postgres-test
docker compose --profile test build test-runner

# Full suite (~15 min):
scripts/run_tests_docker.sh

# Single file:
scripts/run_tests_docker.sh tests/substrate/test_commit.py

# Subset + extra pytest args:
scripts/run_tests_docker.sh tests/substrate/ -- -v -k 'reinforce'
```

Source is bind-mounted so test edits don't trigger an image rebuild;
the venv lives at `/opt/venv` inside the image to survive the bind
mount. The `postgres-test` container is shared with host-side runs.

---

## Getting Started

```bash
hermes              # Interactive CLI — start a conversation
hermes model        # Choose your LLM provider and model
hermes tools        # Configure which tools are enabled
hermes config set   # Set individual config values
hermes gateway      # Start the messaging gateway (Telegram, Discord, etc.)
hermes setup        # Run the full setup wizard (configures everything at once)
hermes claw migrate # Migrate from OpenClaw (if coming from OpenClaw)
hermes update       # Update to the latest version
hermes doctor       # Diagnose any issues
```

📖 **[Full documentation →](https://hermes-agent.nousresearch.com/docs/)**

## CLI vs Messaging Quick Reference

Hermes has two entry points: start the terminal UI with `hermes`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action | CLI | Messaging platforms |
|---------|-----|---------------------|
| Start chatting | `hermes` | Run `hermes gateway setup` + `hermes gateway start`, then send the bot a message |
| Start fresh conversation | `/new` or `/reset` | `/new` or `/reset` |
| Change model | `/model [provider:model]` | `/model [provider:model]` |
| Set a personality | `/personality [name]` | `/personality [name]` |
| Retry or undo the last turn | `/retry`, `/undo` | `/retry`, `/undo` |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]` |
| Browse skills | `/skills` or `/<skill-name>` | `/<skill-name>` |
| Interrupt current work | `Ctrl+C` or send a new message | `/stop` or send a new message |
| Platform-specific status | `/platforms` | `/status`, `/sethome` |

For the full command lists, see the [CLI guide](https://hermes-agent.nousresearch.com/docs/user-guide/cli) and the [Messaging Gateway guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging).

---

## Documentation

All documentation lives at **[hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)**:

| Section | What's Covered |
|---------|---------------|
| [Quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart) | Install → setup → first conversation in 2 minutes |
| [CLI Usage](https://hermes-agent.nousresearch.com/docs/user-guide/cli) | Commands, keybindings, personalities, sessions |
| [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) | Config file, providers, models, all options |
| [Messaging Gateway](https://hermes-agent.nousresearch.com/docs/user-guide/messaging) | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://hermes-agent.nousresearch.com/docs/user-guide/security) | Command approval, DM pairing, container isolation |
| [Tools & Toolsets](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools) | 40+ tools, toolset system, terminal backends |
| [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | Procedural memory, Skills Hub, creating skills |
| [Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) | Persistent memory, user profiles, best practices |
| [MCP Integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) | Connect any MCP server for extended capabilities |
| [Cron Scheduling](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) | Scheduled tasks with platform delivery |
| [Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files) | Project context that shapes every conversation |
| [Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) | Project structure, agent loop, key classes |
| [Contributing](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing) | Development setup, PR process, code style |
| [CLI Reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) | All commands and flags |
| [Environment Variables](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) | Complete env var reference |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Hermes can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`hermes setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
hermes claw migrate              # Interactive migration (full preset)
hermes claw migrate --dry-run    # Preview what would be migrated
hermes claw migrate --preset user-data   # Migrate without secrets
hermes claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:
- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.hermes/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `hermes claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing) for development setup, code style, and PR process.

Quick start for contributors — clone and go with `setup-hermes.sh`:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
./setup-hermes.sh     # installs uv, creates venv, installs .[all], symlinks ~/.local/bin/hermes
./hermes              # auto-detects the venv, no need to `source` first
```

Manual path (equivalent to the above):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

---

## Community

- 💬 [Discord](https://discord.gg/NousResearch)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/NousResearch/hermes-agent/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for Hermes and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.
- 🔌 [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — Community WeChat bridge: Run Hermes Agent and OpenClaw on the same WeChat account.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Nous Research](https://nousresearch.com).
