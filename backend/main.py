import asyncio
import fcntl
import json
import os
import pty
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import threading
import uuid
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# ── Models ────────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    name: str
    command: Optional[str] = None
    cwd: Optional[str] = None


class SessionInfo(BaseModel):
    id: str
    name: str
    command: str
    pid: int
    cwd: str
    status: str
    last_activity_at: Optional[str] = None


class SessionArchiveItem(BaseModel):
    id: str
    name: str
    command: str
    pid: int
    cwd: str
    status: str
    last_activity_at: Optional[str] = None
    created_at: str
    updated_at: str
    ticket_ids: list[str] = []
    ticket_titles: list[str] = []
    ticket_count: int = 0


class ProjectCreate(BaseModel):
    name: str
    path: str
    project_goal: Optional[str] = ""
    constraints: Optional[str] = ""
    architecture_notes: Optional[str] = ""
    links: Optional[str] = ""
    reference_docs: Optional[str] = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    project_goal: Optional[str] = None
    constraints: Optional[str] = None
    architecture_notes: Optional[str] = None
    links: Optional[str] = None
    reference_docs: Optional[str] = None


class ProjectItem(BaseModel):
    id: str
    name: str
    path: str
    project_goal: str
    constraints: str
    architecture_notes: str
    links: str
    reference_docs: str


class TicketCreate(BaseModel):
    project_id: str
    title: str
    description: str = ""
    success_criteria: str = ""
    branch_name: Optional[str] = None
    worktree_path: Optional[str] = None


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    success_criteria: Optional[str] = None
    status: Optional[str] = None      # pending | in_progress | done
    session_id: Optional[str] = None
    branch_name: Optional[str] = None
    worktree_path: Optional[str] = None


class TicketItem(BaseModel):
    id: str
    project_id: str
    title: str
    description: str
    success_criteria: str
    status: str                        # pending | in_progress | done
    branch_name: Optional[str] = None
    worktree_path: Optional[str] = None
    session_id: Optional[str] = None


class ProjectMemoryCreate(BaseModel):
    project_id: str
    type: str
    content: str


class ProjectMemoryUpdate(BaseModel):
    type: Optional[str] = None
    content: Optional[str] = None


class ProjectMemoryItem(BaseModel):
    id: str
    project_id: str
    type: str
    content: str


class CopilotQuery(BaseModel):
    project_id: str
    thread_id: Optional[str] = None
    input: str


class ChatThreadItem(BaseModel):
    id: str
    project_id: str
    title: str


class ChatMessageItem(BaseModel):
    id: str
    thread_id: str
    role: str
    content: str
    created_at: str


# ── DB ───────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent / "swefoundry.db"
LOG_PATH = Path(__file__).resolve().parent / "swefoundry.log"
DB_LOCK = threading.Lock()
DB_CONN: Optional[sqlite3.Connection] = None

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("swefoundry")
if not logger.handlers:
    logger.setLevel(LOG_LEVEL)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    global DB_CONN
    if DB_CONN is None:
        DB_CONN = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        DB_CONN.row_factory = sqlite3.Row
    return DB_CONN


def init_db() -> None:
    conn = db()
    with DB_LOCK:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                project_goal TEXT DEFAULT '',
                constraints TEXT DEFAULT '',
                architecture_notes TEXT DEFAULT '',
                links TEXT DEFAULT '',
                reference_docs TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                success_criteria TEXT DEFAULT '',
                status TEXT NOT NULL,
                branch_name TEXT,
                worktree_path TEXT,
                session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                command TEXT NOT NULL,
                cwd TEXT NOT NULL,
                pid INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_activity_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_threads (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_memory (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_session ON tickets(session_id);
            CREATE INDEX IF NOT EXISTS idx_threads_project ON chat_threads(project_id);
            CREATE INDEX IF NOT EXISTS idx_messages_thread ON chat_messages(thread_id);
            CREATE INDEX IF NOT EXISTS idx_activity_project ON activity_log(project_id);
            CREATE INDEX IF NOT EXISTS idx_memory_project ON project_memory(project_id);
            """
        )
        conn.commit()


def db_execute(query: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = db()
    with DB_LOCK:
        cur = conn.execute(query, params)
        conn.commit()
        return cur


def db_fetchone(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    conn = db()
    with DB_LOCK:
        cur = conn.execute(query, params)
        return cur.fetchone()


def db_fetchall(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    conn = db()
    with DB_LOCK:
        cur = conn.execute(query, params)
        return cur.fetchall()


def log_activity(project_id: str, entity_type: str, entity_id: str, action: str, details: Optional[dict] = None) -> None:
    db_execute(
        "INSERT INTO activity_log (id, project_id, entity_type, entity_id, action, details_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            project_id,
            entity_type,
            entity_id,
            action,
            json.dumps(details or {}),
            now_utc(),
        ),
    )


def project_row_to_item(row: sqlite3.Row) -> ProjectItem:
    return ProjectItem(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        project_goal=row["project_goal"] or "",
        constraints=row["constraints"] or "",
        architecture_notes=row["architecture_notes"] or "",
        links=row["links"] or "",
        reference_docs=row["reference_docs"] or "",
    )


def update_project_record(project_id: str, payload: ProjectUpdate) -> ProjectItem:
    row = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Project not found.")

    updates = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip() or row["name"]
    if payload.path is not None:
        path = Path(payload.path).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory not found: {payload.path}")
        updates["path"] = str(path)
    for key in ("project_goal", "constraints", "architecture_notes", "links", "reference_docs"):
        val = getattr(payload, key)
        if val is not None:
            updates[key] = val

    if updates:
        updates["updated_at"] = now_utc()
        set_clause = ", ".join([f"{k}=?" for k in updates])
        db_execute(f"UPDATE projects SET {set_clause} WHERE id=?", (*updates.values(), project_id))
        log_activity(project_id, "project", project_id, "update", {"fields": list(updates.keys())})

    row = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    return project_row_to_item(row)


def ticket_row_to_item(row: sqlite3.Row) -> TicketItem:
    return TicketItem(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        description=row["description"] or "",
        success_criteria=row["success_criteria"] or "",
        status=row["status"],
        branch_name=row["branch_name"],
        worktree_path=row["worktree_path"],
        session_id=row["session_id"],
    )


def session_row_to_item(row: sqlite3.Row) -> SessionInfo:
    return SessionInfo(
        id=row["id"],
        name=row["name"],
        command=row["command"],
        pid=row["pid"],
        cwd=row["cwd"],
        status=row["status"],
        last_activity_at=row["last_activity_at"],
    )


def memory_row_to_item(row: sqlite3.Row) -> ProjectMemoryItem:
    return ProjectMemoryItem(
        id=row["id"],
        project_id=row["project_id"],
        type=row["type"],
        content=row["content"],
    )


def create_memory_record(payload: ProjectMemoryCreate) -> ProjectMemoryItem:
    project = db_fetchone("SELECT * FROM projects WHERE id=?", (payload.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    now = now_utc()
    mem_id = str(uuid.uuid4())
    db_execute(
        "INSERT INTO project_memory (id, project_id, type, content, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (mem_id, payload.project_id, payload.type, payload.content, now, now),
    )
    log_activity(payload.project_id, "memory", mem_id, "create", {"type": payload.type})
    row = db_fetchone("SELECT * FROM project_memory WHERE id=?", (mem_id,))
    return memory_row_to_item(row)


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def slugify(value: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40] or "ticket"


def create_ticket_record(payload: TicketCreate) -> TicketItem:
    project = db_fetchone("SELECT * FROM projects WHERE id=?", (payload.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty.")
    ticket_id = str(uuid.uuid4())
    slug = slugify(title)
    branch = payload.branch_name or f"ticket-{ticket_id.split('-')[0]}-{slug}"
    worktree = payload.worktree_path or str(Path(project["path"]) / ".worktrees" / branch)
    now = now_utc()
    db_execute(
        """
        INSERT INTO tickets (id, project_id, title, description, success_criteria, status, branch_name, worktree_path, session_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticket_id,
            payload.project_id,
            title,
            payload.description,
            payload.success_criteria,
            "pending",
            branch,
            worktree,
            None,
            now,
            now,
        ),
    )
    log_activity(payload.project_id, "ticket", ticket_id, "create", {"title": title})
    row = db_fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    return ticket_row_to_item(row)


def update_ticket_record(ticket_id: str, payload: TicketUpdate) -> TicketItem:
    row = db_fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    updates: dict = {}
    if payload.title is not None:
        t = payload.title.strip()
        if not t:
            raise HTTPException(status_code=400, detail="Title cannot be empty.")
        updates["title"] = t
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.success_criteria is not None:
        updates["success_criteria"] = payload.success_criteria
    if payload.status is not None:
        if payload.status not in ("pending", "in_progress", "done"):
            raise HTTPException(status_code=400, detail="Invalid status.")
        updates["status"] = payload.status
    if payload.session_id is not None:
        updates["session_id"] = payload.session_id
    if payload.branch_name is not None:
        updates["branch_name"] = payload.branch_name
    if payload.worktree_path is not None:
        updates["worktree_path"] = payload.worktree_path

    if updates:
        updates["updated_at"] = now_utc()
        set_clause = ", ".join([f"{k}=?" for k in updates])
        db_execute(f"UPDATE tickets SET {set_clause} WHERE id=?", (*updates.values(), ticket_id))
        log_activity(row["project_id"], "ticket", ticket_id, "update", {"fields": list(updates.keys())})
    row = db_fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    return ticket_row_to_item(row)


def delete_ticket_record(ticket_id: str) -> None:
    row = db_fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    db_execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    log_activity(row["project_id"], "ticket", ticket_id, "delete", {})


def assign_ticket_record(ticket_id: str, session_id: str) -> TicketItem:
    row = db_fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    session = manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    branch = row["branch_name"] or ""
    worktree = row["worktree_path"] or ""
    instruction = ""
    if branch and worktree:
        instruction = (
            f"# Ticket assignment\n"
            f"# Please create/checkout branch: {branch}\n"
            f"# Suggested worktree path: {worktree}\n"
        )

    text = (instruction + (row["description"] or "")).strip()
    if text:
        payload = (text + "\n").encode()
        # Delay injection to let interactive CLIs finish booting
        def _send():
            session.write(payload)
        threading.Timer(1.0, _send).start()

    db_execute(
        "UPDATE tickets SET status=?, session_id=?, updated_at=? WHERE id=?",
        ("in_progress", session_id, now_utc(), ticket_id),
    )
    log_activity(row["project_id"], "ticket", ticket_id, "assign", {"session_id": session_id})
    updated = db_fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    return ticket_row_to_item(updated)



# ── Terminal session ──────────────────────────────────────────────────────────

event_loop: Optional[asyncio.AbstractEventLoop] = None


class TerminalSession:
    def __init__(self, name: str, command: str, cwd: Optional[str] = None, on_activity=None):
        self.id = str(uuid.uuid4())
        self.name = name
        self.command = command
        self.cwd = cwd or os.getcwd()
        self.pid = -1
        self.master_fd = -1
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.history: Deque[bytes] = deque()
        self.history_max_bytes = 2_000_000
        self.history_bytes = 0
        self._reader_thread: Optional[threading.Thread] = None
        self._closed = threading.Event()
        self._on_activity = on_activity
        self._start_process()

    def _mark_activity(self) -> None:
        if self._on_activity:
            try:
                self._on_activity(self.id)
            except Exception:
                pass

    def _start_process(self) -> None:
        master_fd, slave_fd = pty.openpty()
        pid = os.fork()
        if pid == 0:
            os.setsid()
            try:
                os.chdir(self.cwd)
            except OSError:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(master_fd)
            os.close(slave_fd)
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")
            os.execvpe("/bin/bash", ["/bin/bash", "-lc", self.command], env)
        else:
            os.close(slave_fd)
            self.master_fd = master_fd
            self.pid = pid
            logger.info("session started id=%s pid=%s name=%s cwd=%s cmd=%s", self.id, self.pid, self.name, self.cwd, self.command)
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                data = os.read(self.master_fd, 4096)
                if not data:
                    break
                self._append_history(data)
                self._mark_activity()
                logger.debug("session output id=%s bytes=%s", self.id, len(data))
                if event_loop:
                    event_loop.call_soon_threadsafe(self.queue.put_nowait, data)
            except OSError:
                break
        self._closed.set()
        logger.info("session reader closed id=%s", self.id)

    def _append_history(self, data: bytes) -> None:
        self.history.append(data)
        self.history_bytes += len(data)
        while self.history_bytes > self.history_max_bytes and self.history:
            dropped = self.history.popleft()
            self.history_bytes -= len(dropped)

    def write(self, data: bytes) -> None:
        if self._closed.is_set():
            return
        try:
            os.write(self.master_fd, data)
            self._mark_activity()
            logger.debug("session input id=%s bytes=%s", self.id, len(data))
        except OSError:
            self._closed.set()

    def resize(self, cols: int, rows: int) -> None:
        if self._closed.is_set():
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, 0x5414, winsize)
            logger.debug("session resize id=%s cols=%s rows=%s", self.id, cols, rows)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        logger.info("session closed id=%s", self.id)


class SessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def create(self, name: str, command: str, cwd: Optional[str] = None, on_activity=None) -> TerminalSession:
        session = TerminalSession(name=name, command=command, cwd=cwd, on_activity=on_activity)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Optional[TerminalSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> Dict[str, TerminalSession]:
        with self._lock:
            return dict(self._sessions)

    def delete(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            session.close()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI()
manager = SessionManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.on_event("startup")
async def _startup():
    global event_loop
    event_loop = asyncio.get_running_loop()
    init_db()
    db_execute("UPDATE sessions SET status='stale' WHERE status='running'")


def touch_session_activity(session_id: str) -> None:
    db_execute(
        "UPDATE sessions SET last_activity_at=?, updated_at=? WHERE id=?",
        (now_utc(), now_utc(), session_id),
    )


# ── Session endpoints ─────────────────────────────────────────────────────────

@app.post("/api/sessions", response_model=SessionInfo)
async def create_session(payload: SessionCreate):
    command = payload.command or "/bin/bash"
    if command not in ("/bin/bash", "bash"):
        try:
            argv = shlex.split(command)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid command string.")
        if argv:
            exe = argv[0]
            if not exe.startswith("/") and shutil.which(exe) is None:
                raise HTTPException(status_code=400, detail=f"Command not found on PATH: {exe}")

    cwd = payload.cwd
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        if not cwd_path.exists() or not cwd_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Working directory not found: {cwd}")
        cwd = str(cwd_path)

    # Harden terminal behavior to avoid input/output translation quirks
    safe_command = (
        "export TERM=xterm-256color; export COLORTERM=truecolor; "
        "stty -ixon -icrnl -inlcr; "
        f"{command}"
    )
    session = manager.create(name=payload.name, command=safe_command, cwd=cwd, on_activity=touch_session_activity)
    now = now_utc()
    db_execute(
        "INSERT INTO sessions (id, name, command, cwd, pid, status, last_activity_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (session.id, session.name, session.command, session.cwd, session.pid, "running", now, now, now),
    )
    return SessionInfo(
        id=session.id,
        name=session.name,
        command=session.command,
        pid=session.pid,
        cwd=session.cwd,
        status="running",
        last_activity_at=now,
    )


@app.get("/api/sessions", response_model=list[SessionInfo])
async def list_sessions():
    rows = db_fetchall("SELECT * FROM sessions ORDER BY created_at DESC")
    items = []
    for row in rows:
        status = row["status"]
        if status == "running" and not is_pid_alive(row["pid"]):
            status = "stale"
            db_execute("UPDATE sessions SET status=? WHERE id=?", ("stale", row["id"]))
        items.append(
            SessionInfo(
                id=row["id"],
                name=row["name"],
                command=row["command"],
                pid=row["pid"],
                cwd=row["cwd"],
                status=status,
                last_activity_at=row["last_activity_at"],
            )
        )
    return items


@app.get("/api/sessions/archive")
async def list_session_archive(status: str = "closed", limit: int = 50, offset: int = 0):
    status_filter = status.lower()
    params: list = []
    where = ""
    if status_filter != "all":
        where = "WHERE s.status=?"
        params.append(status_filter)
    params.extend([limit, offset])

    rows = db_fetchall(
        f"""
        SELECT s.*,
               GROUP_CONCAT(t.id) AS ticket_ids,
               GROUP_CONCAT(t.title) AS ticket_titles,
               COUNT(t.id) AS ticket_count
        FROM sessions s
        LEFT JOIN tickets t ON t.session_id = s.id
        {where}
        GROUP BY s.id
        ORDER BY s.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    total_row = db_fetchone(
        f"SELECT COUNT(*) AS cnt FROM sessions s {where}",
        tuple(params[:-2]),
    )
    items = []
    for r in rows:
        ids = (r["ticket_ids"] or "").split(",") if r["ticket_ids"] else []
        titles = (r["ticket_titles"] or "").split(",") if r["ticket_titles"] else []
        items.append({
            "id": r["id"],
            "name": r["name"],
            "command": r["command"],
            "pid": r["pid"],
            "cwd": r["cwd"],
            "status": r["status"],
            "last_activity_at": r["last_activity_at"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "ticket_ids": ids,
            "ticket_titles": titles,
            "ticket_count": r["ticket_count"],
        })
    return {"items": items, "total": total_row["cnt"] if total_row else 0}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    manager.delete(session_id)
    db_execute("UPDATE sessions SET status=?, updated_at=? WHERE id=?", ("closed", now_utc(), session_id))
    return {"ok": True}


# ── Filesystem browser ───────────────────────────────────────────────────────

@app.get("/api/fs")
async def browse_fs(path: str = "~"):
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Path not found or not a directory.")
    try:
        entries = sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        items = []
        for e in entries:
            if e.name.startswith("."):
                continue
            items.append({
                "name": e.name,
                "type": "dir" if e.is_dir() else "file",
                "path": str(e),
            })
        return {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "entries": items,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied.")


# ── Project endpoints ─────────────────────────────────────────────────────────

@app.post("/api/projects", response_model=ProjectItem)
async def create_project(payload: ProjectCreate):
    path = Path(payload.path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {payload.path}")
    name = payload.name.strip() or path.name
    now = now_utc()
    project_id = str(uuid.uuid4())
    db_execute(
        """
        INSERT INTO projects (id, name, path, project_goal, constraints, architecture_notes, links, reference_docs, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            project_id,
            name,
            str(path),
            payload.project_goal or "",
            payload.constraints or "",
            payload.architecture_notes or "",
            payload.links or "",
            payload.reference_docs or "",
            now,
            now,
        ),
    )
    log_activity(project_id, "project", project_id, "create", {"name": name})
    row = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    return project_row_to_item(row)


@app.get("/api/projects", response_model=list[ProjectItem])
async def list_projects():
    rows = db_fetchall("SELECT * FROM projects ORDER BY created_at DESC")
    return [project_row_to_item(r) for r in rows]


@app.patch("/api/projects/{project_id}", response_model=ProjectItem)
async def update_project(project_id: str, payload: ProjectUpdate):
    return update_project_record(project_id, payload)


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    row = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Project not found.")
    db_execute("DELETE FROM projects WHERE id=?", (project_id,))
    log_activity(project_id, "project", project_id, "delete", {})
    return {"ok": True}


@app.get("/api/projects/{project_id}/files")
async def list_project_files(project_id: str, subpath: str = ""):
    project = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    root = Path(project["path"]).resolve()
    target = (root / subpath).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside project root.")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory.")

    try:
        items = sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        return [
            {"name": item.name, "type": "dir" if item.is_dir() else "file",
             "path": str(item.relative_to(root))}
            for item in items
            if not item.name.startswith(".")
        ]
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied.")


# ── Project memory ────────────────────────────────────────────────────────────

@app.post("/api/project-memory", response_model=ProjectMemoryItem)
async def create_memory(payload: ProjectMemoryCreate):
    return create_memory_record(payload)


@app.get("/api/project-memory", response_model=list[ProjectMemoryItem])
async def list_memory(project_id: str):
    rows = db_fetchall("SELECT * FROM project_memory WHERE project_id=? ORDER BY created_at DESC", (project_id,))
    return [memory_row_to_item(r) for r in rows]


@app.patch("/api/project-memory/{memory_id}", response_model=ProjectMemoryItem)
async def update_memory(memory_id: str, payload: ProjectMemoryUpdate):
    row = db_fetchone("SELECT * FROM project_memory WHERE id=?", (memory_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Memory item not found.")
    updates = {}
    if payload.type is not None:
        updates["type"] = payload.type
    if payload.content is not None:
        updates["content"] = payload.content
    if updates:
        updates["updated_at"] = now_utc()
        set_clause = ", ".join([f"{k}=?" for k in updates])
        db_execute(f"UPDATE project_memory SET {set_clause} WHERE id=?", (*updates.values(), memory_id))
        log_activity(row["project_id"], "memory", memory_id, "update", {"fields": list(updates.keys())})
    row = db_fetchone("SELECT * FROM project_memory WHERE id=?", (memory_id,))
    return memory_row_to_item(row)


@app.delete("/api/project-memory/{memory_id}")
async def delete_memory(memory_id: str):
    row = db_fetchone("SELECT * FROM project_memory WHERE id=?", (memory_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Memory item not found.")
    db_execute("DELETE FROM project_memory WHERE id=?", (memory_id,))
    log_activity(row["project_id"], "memory", memory_id, "delete", {})
    return {"ok": True}


# ── Ticket endpoints ──────────────────────────────────────────────────────────

@app.post("/api/tickets", response_model=TicketItem)
async def create_ticket(payload: TicketCreate):
    return create_ticket_record(payload)


@app.get("/api/tickets", response_model=list[TicketItem])
async def list_tickets(project_id: Optional[str] = None):
    if project_id:
        rows = db_fetchall("SELECT * FROM tickets WHERE project_id=? ORDER BY created_at DESC", (project_id,))
    else:
        rows = db_fetchall("SELECT * FROM tickets ORDER BY created_at DESC")
    return [ticket_row_to_item(r) for r in rows]


@app.patch("/api/tickets/{ticket_id}", response_model=TicketItem)
async def update_ticket(ticket_id: str, payload: TicketUpdate):
    return update_ticket_record(ticket_id, payload)


@app.delete("/api/tickets/{ticket_id}")
async def delete_ticket(ticket_id: str):
    delete_ticket_record(ticket_id)
    return {"ok": True}


@app.post("/api/tickets/{ticket_id}/assign/{session_id}", response_model=TicketItem)
async def assign_ticket(ticket_id: str, session_id: str):
    return assign_ticket_record(ticket_id, session_id)


# ── Activity log ──────────────────────────────────────────────────────────────

@app.get("/api/activity")
async def list_activity(project_id: str):
    rows = db_fetchall(
        "SELECT * FROM activity_log WHERE project_id=? ORDER BY created_at DESC LIMIT 200",
        (project_id,),
    )
    return [dict(r) for r in rows]


# ── Git (read-only) ──────────────────────────────────────────────────────────

def run_git(path: str, args: List[str]) -> dict:
    cmd = ["git", "-C", path] + args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "code": proc.returncode,
    }


def require_project(project_id: str) -> sqlite3.Row:
    row = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Project not found.")
    return row


@app.get("/api/projects/{project_id}/git/status")
async def git_status(project_id: str):
    project = require_project(project_id)
    return run_git(project["path"], ["status", "--porcelain", "-b"])


@app.get("/api/projects/{project_id}/git/branches")
async def git_branches(project_id: str):
    project = require_project(project_id)
    return run_git(project["path"], ["branch", "--all"])


@app.get("/api/projects/{project_id}/git/diff")
async def git_diff(project_id: str, ref: str = "HEAD"):
    project = require_project(project_id)
    return run_git(project["path"], ["diff", ref])


@app.get("/api/projects/{project_id}/git/log")
async def git_log(project_id: str, ref: str = "HEAD", limit: int = 50):
    project = require_project(project_id)
    return run_git(project["path"], ["log", f"-n{limit}", "--oneline", ref])


# ── Chat / Copilot ────────────────────────────────────────────────────────────

COPILOT_SYSTEM = (
    "You are SWEfoundry Copilot. You help manage projects, tickets, and sessions. "
    "When you can perform actions, respond with JSON: {\"reply\": string, \"actions\": [..]} . "
    "Each action is {\"type\": string, \"payload\": object}. Supported types: "
    "create_ticket, update_ticket, delete_ticket, assign_ticket, add_project_memory, update_project."
)


def extract_text_from_response(resp: dict) -> str:
    output = resp.get("output", [])
    texts: List[str] = []
    for item in output:
        if item.get("type") == "message" and item.get("role") == "assistant":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
    return "\n".join([t for t in texts if t]).strip()


def parse_copilot_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"reply": text, "actions": []}


def execute_actions(project_id: str, actions: List[dict]) -> List[dict]:
    results = []
    for action in actions:
        atype = action.get("type")
        payload = action.get("payload") or {}
        try:
            if atype == "create_ticket":
                item = create_ticket_record(TicketCreate(project_id=project_id, **payload))
                results.append({"type": atype, "ok": True, "id": item.id})
            elif atype == "update_ticket":
                ticket_id = payload.get("id")
                if not ticket_id:
                    raise ValueError("missing id")
                update_payload = payload.copy()
                update_payload.pop("id", None)
                item = update_ticket_record(ticket_id, TicketUpdate(**update_payload))
                results.append({"type": atype, "ok": True, "id": item.id})
            elif atype == "delete_ticket":
                ticket_id = payload.get("id")
                if not ticket_id:
                    raise ValueError("missing id")
                delete_ticket_record(ticket_id)
                results.append({"type": atype, "ok": True, "id": ticket_id})
            elif atype == "assign_ticket":
                ticket_id = payload.get("ticket_id")
                session_id = payload.get("session_id")
                if not ticket_id or not session_id:
                    raise ValueError("missing ticket_id or session_id")
                item = assign_ticket_record(ticket_id, session_id)
                results.append({"type": atype, "ok": True, "id": item.id})
            elif atype == "add_project_memory":
                mem = create_memory_record(ProjectMemoryCreate(project_id=project_id, **payload))
                results.append({"type": atype, "ok": True, "id": mem.id})
            elif atype == "update_project":
                proj = update_project_record(project_id, ProjectUpdate(**payload))
                results.append({"type": atype, "ok": True, "id": proj.id})
            else:
                results.append({"type": atype, "ok": False, "error": "unsupported action"})
        except Exception as exc:
            results.append({"type": atype, "ok": False, "error": str(exc)})
    return results


@app.get("/api/chat/threads", response_model=list[ChatThreadItem])
async def list_threads(project_id: str):
    rows = db_fetchall("SELECT * FROM chat_threads WHERE project_id=? ORDER BY updated_at DESC", (project_id,))
    return [ChatThreadItem(id=r["id"], project_id=r["project_id"], title=r["title"]) for r in rows]


@app.post("/api/chat/threads", response_model=ChatThreadItem)
async def create_thread(project_id: str, title: str = "Copilot"):
    project = db_fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    now = now_utc()
    thread_id = str(uuid.uuid4())
    db_execute(
        "INSERT INTO chat_threads (id, project_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
        (thread_id, project_id, title, now, now),
    )
    return ChatThreadItem(id=thread_id, project_id=project_id, title=title)


@app.get("/api/chat/messages", response_model=list[ChatMessageItem])
async def list_messages(thread_id: str):
    rows = db_fetchall("SELECT * FROM chat_messages WHERE thread_id=? ORDER BY created_at ASC", (thread_id,))
    return [ChatMessageItem(id=r["id"], thread_id=r["thread_id"], role=r["role"], content=r["content"], created_at=r["created_at"]) for r in rows]


@app.post("/api/copilot/query")
async def copilot_query(payload: CopilotQuery):
    project = db_fetchone("SELECT * FROM projects WHERE id=?", (payload.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    thread_id = payload.thread_id
    if not thread_id:
        now = now_utc()
        thread_id = str(uuid.uuid4())
        db_execute(
            "INSERT INTO chat_threads (id, project_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (thread_id, payload.project_id, "Copilot", now, now),
        )

    user_msg_id = str(uuid.uuid4())
    db_execute(
        "INSERT INTO chat_messages (id, thread_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (user_msg_id, thread_id, "user", payload.input, now_utc()),
    )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not set on the server.")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    messages = db_fetchall(
        "SELECT role, content FROM chat_messages WHERE thread_id=? ORDER BY created_at ASC LIMIT 20",
        (thread_id,),
    )
    input_items = [{"role": "system", "content": COPILOT_SYSTEM}]
    input_items += [{"role": r["role"], "content": r["content"]} for r in messages]

    req = {
        "model": model,
        "input": input_items,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=req,
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    text = extract_text_from_response(data)
    parsed = parse_copilot_json(text)
    reply_text = parsed.get("reply") or ""
    actions = parsed.get("actions") or []

    action_results = execute_actions(payload.project_id, actions)

    assistant_id = str(uuid.uuid4())
    db_execute(
        "INSERT INTO chat_messages (id, thread_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (assistant_id, thread_id, "assistant", reply_text, now_utc()),
    )
    db_execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (now_utc(), thread_id))

    return {
        "thread_id": thread_id,
        "reply": reply_text,
        "actions": actions,
        "action_results": action_results,
    }


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/api/ws/{session_id}")
async def terminal_ws(ws: WebSocket, session_id: str):
    session = manager.get(session_id)
    if not session:
        await ws.close(code=1008)
        return
    await ws.accept()
    logger.info("ws connect session_id=%s", session_id)
    for chunk in list(session.history):
        await ws.send_bytes(chunk)

    async def sender():
        while True:
            data = await session.queue.get()
            await ws.send_bytes(data)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                session.write(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                text = msg["text"]
                if text.startswith("__RESIZE__"):
                    try:
                        _, cols_str, rows_str = text.strip().split()
                        session.resize(int(cols_str), int(rows_str))
                    except ValueError:
                        pass
                else:
                    session.write(text.encode())
    except WebSocketDisconnect:
        logger.info("ws disconnect session_id=%s", session_id)
    finally:
        sender_task.cancel()


# ── Static files / SPA ────────────────────────────────────────────────────────

if FRONTEND_DIST.exists():
    @app.get("/", response_class=Response)
    async def serve_index():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{path:path}", response_class=Response)
    async def serve_spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = FRONTEND_DIST / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
