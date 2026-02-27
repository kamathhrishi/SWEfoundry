import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";

const API = "";

type Theme = "dark" | "light";
type Tab = "overview" | "tickets" | "sessions" | "files" | "git" | "chat" | "activity";

type Project = {
  id: string;
  name: string;
  path: string;
  project_goal: string;
  constraints: string;
  architecture_notes: string;
  links: string;
  reference_docs: string;
};

type SessionInfo = {
  id: string;
  name: string;
  command: string;
  pid: number;
  cwd: string;
  status: string;
  last_activity_at?: string | null;
};

type Ticket = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  success_criteria: string;
  status: "pending" | "in_progress" | "done";
  branch_name?: string | null;
  worktree_path?: string | null;
  session_id: string | null;
};

type FileEntry = { name: string; type: "file" | "dir"; path: string };

type GitResult = { ok: boolean; stdout: string; stderr: string; code: number };

type ChatThread = { id: string; project_id: string; title: string };
type ChatMessage = { id: string; thread_id: string; role: string; content: string; created_at: string };

type CreateKind = "codex" | "claude" | "shell";

function SessionTerminal({
  session,
  onClose,
  theme,
}: {
  session: SessionInfo;
  onClose: (id: string) => void;
  theme: Theme;
}) {
  const [wsStatus, setWsStatus] = useState("Disconnected");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const shouldFollowRef = useRef(true);

  const darkTheme = {
    background: "#0f1216", foreground: "#e6edf3",
    cursor: "#f8c66c", selectionBackground: "#2a2f3a",
  };
  const lightTheme = {
    background: "#f8fafc", foreground: "#0f172a",
    cursor: "#1d53d1", selectionBackground: "#dbeafe",
  };

  useEffect(() => {
    if (!hostRef.current) return;
    if (!termRef.current) {
      const term = new Terminal({
        cursorBlink: true,
        fontFamily: '"JetBrains Mono", Menlo, monospace',
        fontSize: 13,
        scrollback: 5000,
        convertEol: false,
        scrollOnOutput: true,
        theme: theme === "light" ? lightTheme : darkTheme,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();
      termRef.current = term;
      fitRef.current = fit;
    }
    const onResize = () => {
      if (!fitRef.current) return;
      requestAnimationFrame(() => fitRef.current?.fit());
    };
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    const term = termRef.current;
    if (!term) return;
    requestAnimationFrame(() => fitRef.current?.fit());
    term.reset();
    term.write(`\r\n\x1b[38;5;244m[connected to ${session.name} — ${session.cwd}]\x1b[0m\r\n`);

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/ws/${session.id}`);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      setWsStatus("Connected");
      fitRef.current?.fit();
      if (term.cols && term.rows) ws.send(`__RESIZE__ ${term.cols} ${term.rows}`);
      term.focus();
    };
    ws.onclose = () => setWsStatus("Disconnected");
    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) term.write(new Uint8Array(e.data));
      else term.write(e.data);
      if (shouldFollowRef.current) term.scrollToBottom();
    };

    const d1 = term.onData((data) => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
    const d2 = term.onResize(({ cols, rows }) => {
      if (rows > 200) return;
      if (ws.readyState === WebSocket.OPEN) ws.send(`__RESIZE__ ${cols} ${rows}`);
    });
    const d3 = term.onScroll(() => {
      const buf = term.buffer.active;
      const atBottom = buf.viewportY >= buf.baseY;
      shouldFollowRef.current = atBottom;
    });
    wsRef.current = ws;
    return () => { d1.dispose(); d2.dispose(); d3.dispose(); ws.close(); };
  }, [session.id, session.name]);

  useEffect(() => {
    const term = termRef.current as any;
    if (!term) return;
    const t = theme === "light" ? lightTheme : darkTheme;
    if (term.setOption) term.setOption("theme", t);
    else if (term.options) term.options.theme = t;
  }, [theme]);

  const handlePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    const text = e.clipboardData?.getData("text");
    if (!text) return;
    e.preventDefault();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(text);
    }
  };

  const sendControl = (seq: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(seq);
    }
  };

  const copySelection = async () => {
    const term = termRef.current;
    if (!term) return;
    const text = term.getSelection();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback: do nothing if clipboard blocked
    }
  };

  return (
    <div className="terminal-card">
      <div className="terminal-header">
        <div>
          <div className="terminal-title">{session.name}</div>
          <div className="terminal-sub">{session.cwd}</div>
        </div>
        <div className={`ws-status ${wsStatus.toLowerCase()}`}>{wsStatus}</div>
        <button className="ghost sm" onClick={() => sendControl("\x03")}>Ctrl+C</button>
        <button className="ghost sm" onClick={() => sendControl("\x1bc")}>Reset</button>
        <button className="ghost sm" onClick={copySelection}>Copy</button>
        <button className="icon-btn" onClick={() => onClose(session.id)} aria-label="Close">×</button>
      </div>
      <div className="terminal-host" ref={hostRef} onPaste={handlePaste} />
    </div>
  );
}

type FsEntry = { name: string; type: "file" | "dir"; path: string };
type FsResponse = { path: string; parent: string | null; entries: FsEntry[] };

function FolderPicker({
  onSelect,
  onClose,
}: {
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<FsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const navigate = async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/fs?path=${encodeURIComponent(path)}`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(d.detail ?? "Failed to open folder.");
        return;
      }
      setData(await res.json());
    } catch {
      setError("Network error.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { navigate("~"); }, []);

  const breadcrumbs = data
    ? data.path.split("/").filter(Boolean).map((seg, i, arr) => ({
        label: seg || "/",
        path: "/" + arr.slice(0, i + 1).join("/"),
      }))
    : [];

  return (
    <div className="picker-backdrop" onClick={onClose}>
      <div className="picker-modal" onClick={(e) => e.stopPropagation()}>
        <div className="picker-header">
          <span className="picker-title">Select Project Folder</span>
          <button className="icon-btn" onClick={onClose}>×</button>
        </div>

        <div className="picker-breadcrumbs">
          <button className="crumb" onClick={() => navigate("/")}>
            /
          </button>
          {breadcrumbs.map((crumb, i) => (
            <React.Fragment key={crumb.path}>
              <span className="crumb-sep">/</span>
              <button
                className={`crumb ${i === breadcrumbs.length - 1 ? "active" : ""}`}
                onClick={() => navigate(crumb.path)}
              >
                {crumb.label}
              </button>
            </React.Fragment>
          ))}
        </div>

        {data && (
          <div className="picker-current">{data.path}</div>
        )}

        <div className="picker-list">
          {loading && <div className="picker-loading">Loading…</div>}
          {error && <div className="picker-error">{error}</div>}

          {!loading && data?.parent && (
            <div className="picker-entry dir" onClick={() => navigate(data.parent!)}>
              <span className="picker-icon">↑</span>
              <span className="picker-name">..</span>
            </div>
          )}

          {!loading && data?.entries.map((e) => (
            <div
              key={e.path}
              className={`picker-entry ${e.type} ${e.type === "file" ? "disabled" : ""}`}
              onClick={() => e.type === "dir" && navigate(e.path)}
            >
              <span className="picker-icon">{e.type === "dir" ? "▸" : "·"}</span>
              <span className="picker-name">{e.name}</span>
            </div>
          ))}

          {!loading && data?.entries.filter((e) => e.type === "dir").length === 0 && (
            <div className="picker-empty">No subfolders here.</div>
          )}
        </div>

        <div className="picker-footer">
          <span className="picker-selected">{data?.path ?? ""}</span>
          <button onClick={() => data && onSelect(data.path)} disabled={!data}>
            Select This Folder
          </button>
        </div>
      </div>
    </div>
  );
}

function FileTree({ projectId }: { projectId: string }) {
  const [tree, setTree] = useState<Record<string, FileEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));
  const [loading, setLoading] = useState<Set<string>>(new Set());

  const load = useCallback(async (subpath: string) => {
    if (tree[subpath] !== undefined) return;
    setLoading((p) => new Set(p).add(subpath));
    try {
      const res = await fetch(`${API}/api/projects/${projectId}/files?subpath=${encodeURIComponent(subpath)}`);
      if (res.ok) {
        const data = await res.json();
        setTree((p) => ({ ...p, [subpath]: data }));
      }
    } finally {
      setLoading((p) => { const s = new Set(p); s.delete(subpath); return s; });
    }
  }, [projectId, tree]);

  useEffect(() => {
    setTree({});
    setExpanded(new Set([""]));
    load("");
  }, [projectId]);

  const toggle = async (entry: FileEntry) => {
    if (entry.type !== "dir") return;
    const next = new Set(expanded);
    if (next.has(entry.path)) {
      next.delete(entry.path);
    } else {
      next.add(entry.path);
      await load(entry.path);
    }
    setExpanded(next);
  };

  const renderDir = (subpath: string, depth: number): React.ReactNode => {
    const entries = tree[subpath];
    if (!entries) return null;
    return entries.map((e) => (
      <div key={e.path}>
        <div
          className={`file-entry ${e.type}`}
          style={{ paddingLeft: `${12 + depth * 16}px` }}
          onClick={() => toggle(e)}
        >
          <span className="file-icon">{e.type === "dir" ? (expanded.has(e.path) ? "▾" : "▸") : "·"}</span>
          <span className="file-name">{e.name}</span>
        </div>
        {e.type === "dir" && expanded.has(e.path) && (
          loading.has(e.path)
            ? <div className="file-loading" style={{ paddingLeft: `${28 + depth * 16}px` }}>loading…</div>
            : renderDir(e.path, depth + 1)
        )}
      </div>
    ));
  };

  if (loading.has("") && !tree[""]) {
    return <div className="file-loading">loading…</div>;
  }

  return <div className="file-tree">{renderDir("", 0)}</div>;
}

function OverviewPanel({
  project,
  onSave,
}: {
  project: Project;
  onSave: (patch: Partial<Project>) => void;
}) {
  const docRef = useRef<HTMLDivElement | null>(null);
  const template = [
    "<h1>Project Overview</h1>",
    "<h2>Goals</h2>",
    "<p></p>",
    "<h2>Constraints</h2>",
    "<p></p>",
    "<h2>Architecture Notes</h2>",
    "<p></p>",
    "<h2>Links</h2>",
    "<p></p>",
    "<h2>Reference Docs</h2>",
    "<p></p>",
  ].join("");

  useEffect(() => {
    if (!docRef.current) return;
    docRef.current.innerHTML = project.project_goal?.trim() ? project.project_goal : template;
  }, [project.id]);

  const insertHtml = (html: string) => {
    const el = docRef.current;
    if (!el) return;
    el.focus();
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      el.insertAdjacentHTML("beforeend", html);
      return;
    }
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const frag = range.createContextualFragment(html);
    range.insertNode(frag);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
  };

  const saveDoc = () => {
    const html = docRef.current?.innerHTML?.trim() ? docRef.current.innerHTML : template;
    onSave({ project_goal: html });
  };

  return (
    <div className="overview-panel">
      <div className="overview-doc">
        <div className="doc-header">
          <div className="card-title">Overview Doc</div>
          <div className="doc-actions">
            <button onClick={saveDoc}>Save</button>
          </div>
        </div>
        <div className="doc-toolbar">
          <button className="ghost sm" onClick={() => insertHtml("<h1>Heading</h1>")}>H1</button>
          <button className="ghost sm" onClick={() => insertHtml("<h2>Heading</h2>")}>H2</button>
          <button className="ghost sm" onClick={() => insertHtml("<ul><li>Checklist item</li></ul>")}>Checklist</button>
          <button className="ghost sm" onClick={() => insertHtml("<pre><code>code</code></pre>")}>Code</button>
          </div>
        <div
          ref={docRef}
          className="field doc-editor rich-doc"
          contentEditable
          suppressContentEditableWarning
        />
      </div>
    </div>
  );
}

function TicketsPanel({
  project,
  sessions,
  tickets,
  onUpdate,
  onDelete,
  onAssign,
  onCreate,
  onCreateSessionForTicket,
}: {
  project: Project;
  sessions: SessionInfo[];
  tickets: Ticket[];
  onUpdate: (id: string, patch: Partial<Ticket>) => void;
  onDelete: (id: string) => void;
  onAssign: (ticketId: string, sessionId: string) => void;
  onCreate: (title: string, description: string, success: string) => void;
  onCreateSessionForTicket: (ticket: Ticket, kind: CreateKind) => void;
}) {
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newSuccess, setNewSuccess] = useState("");
  const [showForm, setShowForm] = useState(false);

  const submit = () => {
    if (!newTitle.trim()) return;
    onCreate(newTitle.trim(), newDesc.trim(), newSuccess.trim());
    setNewTitle("");
    setNewDesc("");
    setNewSuccess("");
    setShowForm(false);
  };

  const statusLabel = (s: Ticket["status"]) =>
    s === "pending" ? "Pending" : s === "in_progress" ? "In Progress" : "Done";

  const projectTickets = tickets.filter((t) => t.project_id === project.id);

  return (
    <div className="tickets-panel">
      <div className="panel-toolbar">
        <span className="panel-count">{projectTickets.length} ticket{projectTickets.length !== 1 ? "s" : ""}</span>
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "+ New Ticket"}
        </button>
      </div>

      {showForm && (
        <div className="ticket-form">
          <input
            className="field"
            placeholder="Title"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            autoFocus
          />
          <textarea
            className="field"
            placeholder="Description (this gets sent to the agent)"
            rows={4}
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <textarea
            className="field"
            placeholder="Success criteria"
            rows={3}
            value={newSuccess}
            onChange={(e) => setNewSuccess(e.target.value)}
          />
          <div className="form-actions">
            <button onClick={submit} disabled={!newTitle.trim()}>Create Ticket</button>
          </div>
        </div>
      )}

      {projectTickets.length === 0 && !showForm && (
        <div className="empty-state">No tickets yet. Create one to get started.</div>
      )}

      <div className="tickets-list">
        {projectTickets.map((t) => (
          <div key={t.id} className={`ticket-card status-${t.status}`}>
            <div className="ticket-top">
              <span className={`ticket-badge ${t.status}`}>{statusLabel(t.status)}</span>
              <div className="ticket-actions">
                {t.status !== "done" && (
                  <button
                    className="ghost sm"
                    onClick={() => onUpdate(t.id, { status: "done" })}
                  >
                    Mark done
                  </button>
                )}
                {t.status === "done" && (
                  <button
                    className="ghost sm"
                    onClick={() => onUpdate(t.id, { status: "pending", session_id: null })}
                  >
                    Reopen
                  </button>
                )}
                <button className="ghost sm danger" onClick={() => onDelete(t.id)}>Delete</button>
              </div>
            </div>

            <div className="ticket-title">{t.title}</div>
            {t.description && <div className="ticket-desc">{t.description}</div>}
            {t.success_criteria && <div className="ticket-criteria">Success: {t.success_criteria}</div>}

            <div className="ticket-meta">
              <span>Branch: {t.branch_name || "—"}</span>
              <span>Worktree: {t.worktree_path || "—"}</span>
            </div>

            {t.status !== "done" && (
              <div className="ticket-assign">
                {sessions.length === 0 ? (
                  <span className="muted-text">No active sessions — create one for this ticket</span>
                ) : (
                  <>
                    <select
                      className="assign-select"
                      value={t.session_id ?? ""}
                      onChange={(e) => {
                        if (e.target.value) onAssign(t.id, e.target.value);
                      }}
                    >
                      <option value="">Assign to session…</option>
                      {sessions.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name} — {s.cwd.split("/").pop()} ({s.status})
                        </option>
                      ))}
                    </select>
                    {t.session_id && (
                      <span className="assigned-label">
                        Assigned: {sessions.find((s) => s.id === t.session_id)?.name ?? "unknown"}
                      </span>
                    )}
                  </>
                )}
                <div className="ticket-create-session">
                  <span className="muted-text">Create session for this ticket:</span>
                  <div className="launch-row">
                    <button className="sm" onClick={() => onCreateSessionForTicket(t, "claude")}>Claude</button>
                    <button className="sm" onClick={() => onCreateSessionForTicket(t, "codex")}>Codex</button>
                    <button className="sm ghost" onClick={() => onCreateSessionForTicket(t, "shell")}>Shell</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SessionsWorkspace({
  sessions,
  activeSession,
  onClose,
  onSelect,
  onLaunch,
  launching,
  launchError,
  theme,
}: {
  sessions: SessionInfo[];
  activeSession: SessionInfo | null;
  onClose: (id: string) => void;
  onSelect: (id: string) => void;
  onLaunch: (kind: CreateKind) => void;
  launching: boolean;
  launchError: string | null;
  theme: Theme;
}) {
  const [showNew, setShowNew] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [archiveStatus, setArchiveStatus] = useState("closed");
  const [archiveItems, setArchiveItems] = useState<any[]>([]);
  const [archiveTotal, setArchiveTotal] = useState(0);
  const [archivePage, setArchivePage] = useState(1);
  const pageSize = 10;

  const loadArchive = async (page: number, status: string) => {
    const offset = (page - 1) * pageSize;
    const res = await fetch(`${API}/api/sessions/archive?status=${status}&limit=${pageSize}&offset=${offset}`);
    if (res.ok) {
      const data = await res.json();
      setArchiveItems(data.items || []);
      setArchiveTotal(data.total || 0);
    }
  };

  useEffect(() => {
    if (!showArchive) return;
    loadArchive(archivePage, archiveStatus);
  }, [showArchive, archivePage, archiveStatus]);

  useEffect(() => {
    if (!activeSession && sessions.length > 0) {
      onSelect(sessions[0].id);
    }
  }, [activeSession, sessions, onSelect]);

  return (
    <div className="sessions-workspace">
      <div className="session-tabs">
        {sessions.map((s) => (
          <button
            key={s.id}
            className={`session-tab ${activeSession?.id === s.id ? "active" : ""}`}
            onClick={() => onSelect(s.id)}
            title={s.cwd}
          >
            <span className="tab-label">{s.name}</span>
            <span className={`tab-status ${s.status}`}>{s.status}</span>
            <span
              className="tab-close"
              onClick={(e) => { e.stopPropagation(); onClose(s.id); }}
            >×</span>
          </button>
        ))}
        <div className="session-tab new">
          <button className="sm ghost" onClick={() => setShowNew((v) => !v)} disabled={launching}>+</button>
          {showNew && (
            <div className="session-new-menu">
              <button className="sm" onClick={() => { onLaunch("claude"); setShowNew(false); }} disabled={launching}>Claude</button>
              <button className="sm" onClick={() => { onLaunch("codex"); setShowNew(false); }} disabled={launching}>Codex</button>
              <button className="sm ghost" onClick={() => { onLaunch("shell"); setShowNew(false); }} disabled={launching}>Shell</button>
            </div>
          )}
        </div>
        <button className="ghost sm" onClick={() => setShowArchive(true)}>Archive</button>
      </div>
      {launchError && <div className="inline-error">{launchError}</div>}
      <div className="sessions-terminal-panel">
        {activeSession ? (
          <SessionTerminal
            key={activeSession.id}
            session={activeSession}
            onClose={onClose}
            theme={theme}
          />
        ) : (
          <div className="empty-state">No sessions yet. Create one with +.</div>
        )}
      </div>

      {showArchive && (
        <div className="modal-backdrop" onClick={() => setShowArchive(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="card-title">Session Archive</div>
              <button className="icon-btn" onClick={() => setShowArchive(false)}>×</button>
            </div>
            <div className="modal-filters">
              <select
                className="assign-select"
                value={archiveStatus}
                onChange={(e) => { setArchiveStatus(e.target.value); setArchivePage(1); }}
              >
                <option value="closed">Closed</option>
                <option value="stale">Stale</option>
                <option value="all">All</option>
              </select>
            </div>
            <div className="archive-list">
              {archiveItems.map((s) => (
                <div key={s.id} className="archive-item">
                  <div className="archive-title">{s.name}</div>
                  <div className="archive-meta">
                    <span>{s.status}</span>
                    <span>{s.updated_at}</span>
                    <span>{s.ticket_count} ticket(s)</span>
                  </div>
                  {s.ticket_titles?.length > 0 && (
                    <div className="archive-tickets">{s.ticket_titles.join(", ")}</div>
                  )}
                </div>
              ))}
              {archiveItems.length === 0 && <div className="empty-state">No archived sessions.</div>}
            </div>
            <div className="modal-footer">
              <button
                className="ghost sm"
                onClick={() => setArchivePage((p) => Math.max(1, p - 1))}
                disabled={archivePage === 1}
              >
                Prev
              </button>
              <span className="muted-text">Page {archivePage}</span>
              <button
                className="ghost sm"
                onClick={() => setArchivePage((p) => p + 1)}
                disabled={archivePage * pageSize >= archiveTotal}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function GitPanel({ project }: { project: Project }) {
  const [status, setStatus] = useState<GitResult | null>(null);
  const [branches, setBranches] = useState<GitResult | null>(null);
  const [diff, setDiff] = useState<GitResult | null>(null);
  const [log, setLog] = useState<GitResult | null>(null);

  const load = async (kind: "status" | "branches" | "diff" | "log") => {
    const res = await fetch(`${API}/api/projects/${project.id}/git/${kind}`);
    const data = await res.json();
    if (kind === "status") setStatus(data);
    if (kind === "branches") setBranches(data);
    if (kind === "diff") setDiff(data);
    if (kind === "log") setLog(data);
  };

  return (
    <div className="git-panel">
      <div className="git-actions">
        <button onClick={() => load("status")}>Refresh Status</button>
        <button onClick={() => load("branches")} className="ghost">Branches</button>
        <button onClick={() => load("diff")} className="ghost">Diff</button>
        <button onClick={() => load("log")} className="ghost">Log</button>
      </div>
      <div className="git-grid">
        <div className="git-card">
          <div className="card-title">Status</div>
          <pre>{status?.stdout || status?.stderr || ""}</pre>
        </div>
        <div className="git-card">
          <div className="card-title">Branches</div>
          <pre>{branches?.stdout || branches?.stderr || ""}</pre>
        </div>
        <div className="git-card">
          <div className="card-title">Diff</div>
          <pre>{diff?.stdout || diff?.stderr || ""}</pre>
        </div>
        <div className="git-card">
          <div className="card-title">Log</div>
          <pre>{log?.stdout || log?.stderr || ""}</pre>
        </div>
      </div>
    </div>
  );
}

function ChatPanel({ projectId }: { projectId: string }) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const loadThreads = async () => {
    const res = await fetch(`${API}/api/chat/threads?project_id=${projectId}`);
    if (res.ok) {
      const data = await res.json();
      setThreads(data);
      if (!activeThreadId && data.length > 0) setActiveThreadId(data[0].id);
    }
  };

  const loadMessages = async (threadId: string) => {
    const res = await fetch(`${API}/api/chat/messages?thread_id=${threadId}`);
    if (res.ok) setMessages(await res.json());
  };

  useEffect(() => { loadThreads(); }, [projectId]);
  useEffect(() => { if (activeThreadId) loadMessages(activeThreadId); }, [activeThreadId]);

  const send = async () => {
    if (!input.trim()) return;
    setBusy(true);
    const res = await fetch(`${API}/api/copilot/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, thread_id: activeThreadId, input: input.trim() }),
    });
    if (res.ok) {
      const data = await res.json();
      setActiveThreadId(data.thread_id);
      setInput("");
      await loadThreads();
      await loadMessages(data.thread_id);
    }
    setBusy(false);
  };

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <div className="card-title">Copilot</div>
        <div className="chat-thread">
          <span className="muted-text">Thread</span>
          <select
            className="assign-select"
            value={activeThreadId ?? ""}
            onChange={(e) => setActiveThreadId(e.target.value)}
          >
            {threads.map((t) => (
              <option key={t.id} value={t.id}>{t.title}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="chat-body">
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}`}>
            <div className="chat-bubble">
              <div className="chat-meta">{m.role}</div>
              <div className="chat-text">{m.content}</div>
            </div>
          </div>
        ))}
        {messages.length === 0 && <div className="empty-state">No messages yet.</div>}
      </div>
      <div className="chat-input">
        <textarea
          className="field"
          rows={3}
          placeholder="Ask the copilot…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button onClick={send} disabled={busy}>Send</button>
      </div>
    </div>
  );
}

function ActivityPanel({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<any[]>([]);

  const load = async () => {
    const res = await fetch(`${API}/api/activity?project_id=${projectId}`);
    if (res.ok) setItems(await res.json());
  };

  useEffect(() => { load(); }, [projectId]);

  return (
    <div className="activity-panel">
      <div className="panel-toolbar">
        <span className="panel-count">{items.length} events</span>
        <button className="ghost sm" onClick={load}>Refresh</button>
      </div>
      <div className="activity-list">
        {items.map((i) => (
          <div key={i.id} className="activity-item">
            <span className="activity-action">{i.action}</span>
            <span className="activity-entity">{i.entity_type}</span>
            <span className="activity-time">{i.created_at}</span>
          </div>
        ))}
        {items.length === 0 && <div className="empty-state">No activity yet.</div>}
      </div>
    </div>
  );
}

export default function App() {
  const theme: Theme = "dark";
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [showAddProject, setShowAddProject] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectPath, setNewProjectPath] = useState("");
  const [projectError, setProjectError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const activeProject = projects.find((p) => p.id === activeProjectId) ?? null;
  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? null;

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/projects`).then((r) => r.json()),
      fetch(`${API}/api/sessions`).then((r) => r.json()),
      fetch(`${API}/api/tickets`).then((r) => r.json()),
    ]).then(([p, s, t]) => {
      setProjects(p);
      setSessions(s);
      setTickets(t);
      if (p.length > 0) setActiveProjectId(p[0].id);
      if (s.length > 0) setActiveSessionId(s[0].id);
    });
  }, []);

  const addProject = async () => {
    if (!newProjectPath.trim()) return;
    setProjectError(null);
    const res = await fetch(`${API}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newProjectName.trim(), path: newProjectPath.trim() }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setProjectError(d.detail ?? "Failed to add project.");
      return;
    }
    const p: Project = await res.json();
    setProjects((prev) => [...prev, p]);
    setActiveProjectId(p.id);
    setNewProjectName("");
    setNewProjectPath("");
    setShowAddProject(false);
  };

  const removeProject = async (id: string) => {
    await fetch(`${API}/api/projects/${id}`, { method: "DELETE" });
    setProjects((prev) => prev.filter((p) => p.id !== id));
    if (activeProjectId === id) setActiveProjectId(projects.find((p) => p.id !== id)?.id ?? null);
  };

  const saveProject = async (patch: Partial<Project>) => {
    if (!activeProjectId) return;
    const res = await fetch(`${API}/api/projects/${activeProjectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (res.ok) {
      const updated = await res.json();
      setProjects((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    }
  };

  const launchSession = async (kind: CreateKind) => {
    if (launching) return;
    setLaunching(true);
    setLaunchError(null);
    const cmdMap: Record<CreateKind, string> = { codex: "codex", claude: "claude", shell: "/bin/bash" };
    const nameMap: Record<CreateKind, string> = { codex: "Codex", claude: "Claude", shell: "Shell" };
    const res = await fetch(`${API}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: nameMap[kind],
        command: cmdMap[kind],
        cwd: activeProject?.path ?? null,
      }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setLaunchError(d.detail ?? "Failed to launch.");
      setLaunching(false);
      return;
    }
    const s: SessionInfo = await res.json();
    setSessions((prev) => [s, ...prev]);
    setActiveSessionId(s.id);
    setActiveTab("sessions");
    setLaunching(false);
  };

  const closeSession = async (id: string) => {
    await fetch(`${API}/api/sessions/${id}`, { method: "DELETE" });
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeSessionId === id) {
      const remaining = sessions.filter((s) => s.id !== id);
      setActiveSessionId(remaining[0]?.id ?? null);
    }
  };

  const createTicket = async (title: string, description: string, success: string) => {
    if (!activeProjectId) return;
    const res = await fetch(`${API}/api/tickets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: activeProjectId, title, description, success_criteria: success }),
    });
    if (res.ok) {
      const t: Ticket = await res.json();
      setTickets((prev) => [...prev, t]);
    }
  };

  const updateTicket = async (id: string, patch: Partial<Ticket>) => {
    const res = await fetch(`${API}/api/tickets/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (res.ok) {
      const t: Ticket = await res.json();
      setTickets((prev) => prev.map((x) => (x.id === id ? t : x)));
    }
  };

  const deleteTicket = async (id: string) => {
    await fetch(`${API}/api/tickets/${id}`, { method: "DELETE" });
    setTickets((prev) => prev.filter((t) => t.id !== id));
  };

  const assignTicket = async (ticketId: string, sessionId: string) => {
    const res = await fetch(`${API}/api/tickets/${ticketId}/assign/${sessionId}`, {
      method: "POST",
    });
    if (res.ok) {
      const t: Ticket = await res.json();
      setTickets((prev) => prev.map((x) => (x.id === ticketId ? t : x)));
      setActiveSessionId(sessionId);
      setActiveTab("sessions");
    }
  };

  const createSessionForTicket = async (ticket: Ticket, kind: CreateKind) => {
    if (!activeProject) return;
    const cmdMap: Record<CreateKind, string> = { codex: "codex", claude: "claude", shell: "/bin/bash" };
    const nameMap: Record<CreateKind, string> = { codex: "Codex", claude: "Claude", shell: "Shell" };
    const shortTitle = ticket.title.slice(0, 32);
    const name = `${nameMap[kind]} — ${shortTitle}`;
    const res = await fetch(`${API}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        command: cmdMap[kind],
        cwd: activeProject.path,
      }),
    });
    if (!res.ok) return;
    const s: SessionInfo = await res.json();
    setSessions((prev) => [s, ...prev]);
    await assignTicket(ticket.id, s.id);
  };

  const tabs: Tab[] = ["overview", "tickets", "sessions", "files", "git", "chat", "activity"];

  const openTickets = useMemo(
    () => tickets.filter((t) => t.project_id === activeProjectId && t.status !== "done").length,
    [tickets, activeProjectId]
  );

  return (
    <div className="app layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">&lt;/&gt;</div>
          <div>
            <div className="brand-title">SWEfoundry</div>
            <div className="brand-sub">Agent factory</div>
          </div>
        </div>

        <div className="sidebar-section">
          <div className="section-header">
            <span className="section-label">Sections</span>
          </div>
          <div className="section-list">
            {tabs.map((tab) => (
              <button
                key={tab}
                className={`section-btn ${activeTab === tab ? "active" : ""}`}
                onClick={() => setActiveTab(tab)}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
                {tab === "tickets" && activeProject && (
                  <span className="section-badge">{openTickets || ""}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="project-switcher">
            <label className="muted-text">Project</label>
            <select
              className="assign-select"
              value={activeProjectId ?? ""}
              onChange={(e) => setActiveProjectId(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <button className="ghost sm" onClick={() => setShowAddProject((v) => !v)}>
              {showAddProject ? "Close" : "+ New"}
            </button>
          </div>
          <div className="topbar-actions" />
        </header>

        {showAddProject && (
          <div className="project-add-card">
            <input
              className="field"
              placeholder="Name (optional)"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
            />
            <div className="path-row">
              <input
                className="field"
                placeholder="/path/to/project"
                value={newProjectPath}
                onChange={(e) => setNewProjectPath(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addProject()}
              />
              <button className="ghost sm" onClick={() => setShowPicker(true)} title="Browse">
                Browse
              </button>
            </div>
            {projectError && <div className="inline-error">{projectError}</div>}
            <button onClick={addProject} disabled={!newProjectPath.trim()}>Add</button>
          </div>
        )}

        <div className={`main-surface ${activeTab === "sessions" ? "no-scroll" : ""}`}>
          {activeTab === "overview" && (
            activeProject ? (
              <OverviewPanel
                project={activeProject}
                onSave={saveProject}
              />
            ) : (
              <div className="empty-state">Select a project to view details.</div>
            )
          )}

          {activeTab === "tickets" && (
            activeProject ? (
              <TicketsPanel
                project={activeProject}
                sessions={sessions}
                tickets={tickets}
                onUpdate={updateTicket}
                onDelete={deleteTicket}
                onAssign={assignTicket}
                onCreate={createTicket}
                onCreateSessionForTicket={createSessionForTicket}
              />
            ) : (
              <div className="empty-state">Select a project to manage tickets.</div>
            )
          )}

          {activeTab === "sessions" && (
            <SessionsWorkspace
              sessions={sessions.filter((s) => s.status !== "closed")}
              activeSession={activeSession}
              onClose={closeSession}
              onSelect={(id) => setActiveSessionId(id)}
              onLaunch={launchSession}
              launching={launching}
              launchError={launchError}
              theme={theme}
            />
          )}

          {activeTab === "files" && (
            activeProject ? (
              <div className="files-panel">
                <div className="files-header">
                  <span className="files-root">{activeProject.path}</span>
                </div>
                <FileTree projectId={activeProject.id} />
              </div>
            ) : (
              <div className="empty-state">Select a project to browse files.</div>
            )
          )}

          {activeTab === "git" && (
            activeProject ? (
              <GitPanel project={activeProject} />
            ) : (
              <div className="empty-state">Select a project to view git status.</div>
            )
          )}

          {activeTab === "chat" && (
            activeProject ? (
              <ChatPanel projectId={activeProject.id} />
            ) : (
              <div className="empty-state">Select a project to chat.</div>
            )
          )}

          {activeTab === "activity" && (
            activeProject ? (
              <ActivityPanel projectId={activeProject.id} />
            ) : (
              <div className="empty-state">Select a project to view activity.</div>
            )
          )}
        </div>
      </main>

      <aside className="rightbar">
        {activeProject ? (
          <ChatPanel projectId={activeProject.id} />
        ) : (
          <div className="empty-state">Select a project to chat.</div>
        )}
      </aside>

      {showPicker && (
        <FolderPicker
          onSelect={(path) => {
            setNewProjectPath(path);
            if (!newProjectName.trim()) {
              setNewProjectName(path.split("/").filter(Boolean).pop() ?? "");
            }
            setShowPicker(false);
          }}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}
