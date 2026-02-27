# SWEfoundry

SWEfoundry is a web UI to run and coordinate multiple terminal coding agents (`codex`, `claude`, and shell) per project.

Tagline: **Your personal software factory**.

The goal is to give you one place to plan, run agent sessions, map work to branches/tickets, watch diffs, and steer execution like an engineering manager for a team of software agents.

![SWEfoundry screenshot](./screenshot.png)

## What it does

- Manages multiple coding-agent terminal sessions in one workspace.
- Organizes work as `Projects -> Tickets -> Sessions`.
- Links tickets to branch/worktree intent (branch/worktree creation/switching is provided to sessions as context/instructions).
- Injects ticket context into assigned sessions so agents can start with task details and success criteria.
- Shows project files and read-only git insights (status, branches, diff, log).
- Provides a Notion-like Overview doc for high-level goals/specs/plans.
- Includes a right-side AI copilot + dedicated chat tab for ticket/project operations.
- Tracks activity and chat history in SQLite.
- Shows active sessions plus a paginated archive (closed/stale/all).

## Product model

Hierarchy:

- `Project`
- `Ticket` (spec + success criteria + branch/worktree intent)
- `Session` (Codex/Claude/Shell linked to ticket context)

Execution model:

- You create projects and define high-level goals in the Overview doc.
- You create tickets manually or with copilot help.
- You launch sessions from Sessions tab or directly from ticket cards.
- You assign tickets to sessions; backend injects ticket context after CLI startup.
- You track progress via activity log, git views, and session output.
- You can map multiple tickets to one session when needed.

## Architecture

- Frontend: React + Vite + `xterm` (`frontend/`)
- Backend: FastAPI + WebSocket + PTY process runner (`backend/`)
- DB: SQLite (`backend/swefoundry.db`)
- Logs: rotating backend log (`backend/swefoundry.log`)

The backend serves the built frontend from `frontend/dist` when present.

## Requirements

- Linux/WSL/macOS environment with `/bin/bash` and PTY support.
- Node.js + npm.
- Python 3.10+ (recommended).
- Optional: `codex` and/or `claude` binaries available on `PATH`.

## Quick start

### 1) Build frontend

```bash
cd /mnt/c/hrishi/orchestrator/frontend
npm install
npm run build
```

### 2) Start backend

```bash
cd /mnt/c/hrishi/orchestrator/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

Open:

- `http://127.0.0.1:8001`

## Environment variables

- `OPENAI_API_KEY`: required for copilot endpoint (`/api/copilot/query`).
- `OPENAI_MODEL`: optional model override (default: `gpt-4.1-mini`).
- `LOG_LEVEL`: backend log level (`INFO` default, `DEBUG` for deep session traces).

Example:

```bash
cd /mnt/c/hrishi/orchestrator/backend
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4.1-mini LOG_LEVEL=DEBUG uvicorn main:app --reload --port 8001
```

## Terminal session model

- Sessions are launched as local OS processes using PTY via `/bin/bash -lc <command>`.
- Backend streams PTY output over WebSocket (`/api/ws/{session_id}`).
- Terminal supports:
  - interactive typing
  - paste to terminal
  - copy selected output
  - `Ctrl+C` and reset controls
  - scrollback/history in xterm viewport

Ticket assignment behavior:

- `POST /api/tickets/{ticket_id}/assign/{session_id}` sets ticket status and injects branch/worktree + description context into the session after startup delay.

Worktree/branch behavior:

- Tickets store branch and worktree intent.
- Agent sessions receive branch/worktree instructions as context.
- Branch/worktree creation is intentionally performed by the session (agent/user), not auto-executed by backend.

## Key API routes

- Sessions:
  - `POST /api/sessions`
  - `GET /api/sessions`
  - `GET /api/sessions/archive`
  - `DELETE /api/sessions/{session_id}`
- Projects:
  - `POST /api/projects`
  - `GET /api/projects`
  - `PATCH /api/projects/{project_id}`
  - `DELETE /api/projects/{project_id}`
  - `GET /api/projects/{project_id}/files`
- Tickets:
  - `POST /api/tickets`
  - `GET /api/tickets`
  - `PATCH /api/tickets/{ticket_id}`
  - `DELETE /api/tickets/{ticket_id}`
  - `POST /api/tickets/{ticket_id}/assign/{session_id}`
- Git (read-only):
  - `GET /api/projects/{project_id}/git/status`
  - `GET /api/projects/{project_id}/git/branches`
  - `GET /api/projects/{project_id}/git/diff`
  - `GET /api/projects/{project_id}/git/log`
- Chat/Copilot:
  - `GET /api/chat/threads`
  - `POST /api/chat/threads`
  - `GET /api/chat/messages`
  - `POST /api/copilot/query`
- Activity:
  - `GET /api/activity`

## Troubleshooting

### Codex/Claude says terminal is `dumb`

SWEfoundry sets terminal env to `TERM=xterm-256color` for launched sessions. Create a fresh session after restart. If warning persists, verify the agent binary itself is not overriding `TERM`.

### Session output behaves unexpectedly

Run backend with:

```bash
LOG_LEVEL=DEBUG uvicorn main:app --reload --port 8001
```

Then inspect:

```bash
tail -f /mnt/c/hrishi/orchestrator/backend/swefoundry.log
```

### No frontend updates visible

Rebuild frontend and restart backend:

```bash
cd /mnt/c/hrishi/orchestrator/frontend && npm run build
cd /mnt/c/hrishi/orchestrator/backend && pkill -f 'uvicorn main:app --reload --port 8001' || true
cd /mnt/c/hrishi/orchestrator/backend && uvicorn main:app --reload --port 8001
```

## Security note

No authentication or multi-user isolation is implemented. Run this only on trusted local/dev networks.

## Roadmap

Current:

- Human-in-the-loop copilot for project/ticket/session management.
- Session orchestration with context injection and visibility tooling.

Future:

- Stronger **agent orchestrator** layer (automation between agents, handoffs, output interception and delegation).
- Deeper planning integration (for example Notion/project-plan synchronization).
- Richer token/usage analytics and proactive prompt suggestions from project and cross-agent context.
