#!/usr/bin/env python3
"""Copilot CLI Session Viewer — retro terminal-style Flask app with AI TLDRs.

Run:
    cd ~/copilot-session-viewer && source .venv/bin/activate && python app.py
"""

import os
import sys
import sqlite3
import json
import asyncio
import threading
import urllib.request
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

VERSION = "1.0.0"
REPO = "chonchiog/copilot-session-viewer"
MODEL = os.environ.get("TLDR_MODEL", "claude-sonnet-4.5")

# Auto-detect Copilot data dir across platforms
if os.name == "nt":
    _base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "copilot")
    if not os.path.isdir(_base):
        _base = os.path.expanduser("~\\.copilot")
else:
    _base = os.path.expanduser("~/.copilot")

DB_PATH = os.environ.get("COPILOT_DB", os.path.join(_base, "session-store.db"))
TLDR_CACHE_PATH = os.path.join(_base, "session-tldrs.json")

if not os.path.exists(DB_PATH):
    print(f"⚠️  Database not found: {DB_PATH}")
    print(f"   Set COPILOT_DB env var to override.")
    raise SystemExit(1)

# ── Copilot SDK Setup ──────────────────────────────────────────────────────

from copilot import CopilotClient, PermissionHandler

_copilot_client = None
_loop = asyncio.new_event_loop()


def _run_loop():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


threading.Thread(target=_run_loop, daemon=True).start()


async def _init_copilot():
    global _copilot_client
    _copilot_client = CopilotClient()
    await _copilot_client.start()


asyncio.run_coroutine_threadsafe(_init_copilot(), _loop).result(timeout=30)


def _run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=120)


print(f"🤖 Copilot SDK ready (model: {MODEL})")

# ── TLDR Cache ─────────────────────────────────────────────────────────────


def load_tldr_cache():
    if os.path.exists(TLDR_CACHE_PATH):
        with open(TLDR_CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_tldr_cache(cache):
    with open(TLDR_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


_tldr_cache = load_tldr_cache()

# ── Helpers ────────────────────────────────────────────────────────────────


def get_db(readonly=True):
    uri = f"file:{DB_PATH}" + ("?mode=ro" if readonly else "")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── Cleanup SDK-generated junk sessions on startup ─────────────────────────

def _cleanup_sdk_sessions():
    try:
        db = get_db(readonly=False)
        # Find SDK-generated 1-turn junk and completely empty sessions
        junk = db.execute("""
            SELECT s.id FROM sessions s
            WHERE (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) = 0
               OR ((SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) = 1
                   AND ((SELECT t.user_message FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0)
                        LIKE 'Summarize this conversation%'
                     OR (SELECT t.user_message FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0)
                        LIKE 'Give this conversation%'
                     OR (SELECT t.user_message FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0)
                        LIKE 'Summarize this session%'))
        """).fetchall()
        if junk:
            for row in junk:
                sid = row["id"]
                db.execute("DELETE FROM turns WHERE session_id = ?", (sid,))
                db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            db.commit()
            print(f"🧹 Cleaned {len(junk)} junk sessions")
        db.close()
    except Exception:
        pass

_cleanup_sdk_sessions()


def time_ago(iso_str):
    if not iso_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return iso_str


def generate_tldr(session_id):
    """Use Copilot SDK to generate a TLDR for a session."""
    db = get_db()
    rows = db.execute(
        "SELECT user_message, assistant_response FROM turns "
        "WHERE session_id = ? ORDER BY turn_index LIMIT 10",
        (session_id,),
    ).fetchall()
    db.close()

    if not rows:
        return "Empty session (no conversation)"

    transcript = ""
    for r in rows:
        if r["user_message"]:
            transcript += f"User: {r['user_message'][:200]}\n"
        if r["assistant_response"]:
            transcript += f"Assistant: {r['assistant_response'][:200]}\n"

    system = (
        "You generate short titles for conversations, like a chat history sidebar. "
        "Respond with ONLY the title — 3 to 8 words max. "
        "Be specific, not generic. Use title case. No quotes, no prefix."
    )
    prompt = f"Give this conversation a short title:\n\n{transcript}"

    async def _call():
        session = await _copilot_client.create_session(
            {
                "model": MODEL,
                "system_message": {"mode": "replace", "content": system},
                "available_tools": [],
                "on_permission_request": PermissionHandler.approve_all,
            }
        )
        async with session:
            response = await session.send_and_wait(
                {"prompt": prompt}, timeout=60
            )
            return response.data.content if response else None

    return _run_async(_call())


# ── HTML ───────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>copilot.local</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{
  --bg:#0a0e14;--surface:#111820;--surface2:#182028;--border:#1e2a35;
  --text:#c8d6e0;--text-dim:#5a7080;--text-faint:#3a4a55;
  --green:#00e676;--green-dim:rgba(0,230,118,.10);--green-glow:rgba(0,230,118,.25);
  --cyan:#00e5ff;--cyan-dim:rgba(0,229,255,.10);
  --red:#ff5252;--red-dim:rgba(255,82,82,.10);
  --yellow:#ffd740;--yellow-dim:rgba(255,215,64,.10);
  --orange:#ff9100;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'JetBrains Mono',monospace;background:var(--bg);color:var(--text);
  line-height:1.6;min-height:100vh}

/* ── Header ── */
.header{text-align:center;padding:28px 20px 16px;border-bottom:1px solid var(--border)}
.header h1{font-size:28px;font-weight:700;letter-spacing:6px;text-transform:uppercase;
  color:var(--green);text-shadow:0 0 20px var(--green-glow)}
.header .sub{font-size:11px;color:var(--text-dim);margin-top:4px;letter-spacing:2px}
.update-banner{font-size:11px;margin-top:8px;padding:6px 14px;background:var(--yellow-dim);
  border:1px solid rgba(255,215,64,.3);border-radius:3px;color:var(--yellow);
  display:inline-block}
.update-banner a{color:var(--yellow);text-decoration:underline}
.dismiss-btn{background:none;border:none;color:var(--yellow);cursor:pointer;font-size:13px;
  margin-left:10px;opacity:.6;font-family:inherit}
.dismiss-btn:hover{opacity:1}

/* ── Toolbar ── */
.toolbar{display:flex;gap:8px;padding:12px 24px;border-bottom:1px solid var(--border);
  align-items:center;flex-wrap:wrap}
.search-input{flex:1;min-width:200px;padding:8px 14px;background:var(--surface);
  border:1px solid var(--border);border-radius:4px;color:var(--green);
  font-family:inherit;font-size:12px;outline:none}
.search-input:focus{border-color:var(--green);box-shadow:0 0 8px var(--green-glow)}
.search-input::placeholder{color:var(--text-faint)}
.chip{font-size:11px;padding:4px 12px;border:1px solid var(--border);border-radius:3px;
  background:transparent;color:var(--text-dim);cursor:pointer;font-family:inherit;transition:.15s}
.chip:hover,.chip.active{border-color:var(--green);color:var(--green);background:var(--green-dim)}
.sort-select{font-size:11px;padding:4px 8px;border:1px solid var(--border);border-radius:3px;
  background:var(--surface);color:var(--text-dim);font-family:inherit;outline:none;cursor:pointer}
.new-sesh-btn{font-size:12px;padding:6px 16px;border:1px solid var(--green);border-radius:3px;
  background:var(--green-dim);color:var(--green);font-family:inherit;cursor:pointer;
  font-weight:600;letter-spacing:1px;transition:.15s;margin-left:auto}
.new-sesh-btn:hover{background:rgba(0,230,118,.25);box-shadow:0 0 12px var(--green-glow);
  text-shadow:0 0 8px var(--green-glow)}
.stats{font-size:11px;color:var(--text-faint);margin-left:auto}

/* ── Session List (full width) ── */
.session-list{padding:12px 24px}
.session-row{display:flex;align-items:center;gap:14px;padding:14px 18px;
  border:1px solid var(--border);border-radius:4px;margin-bottom:8px;
  cursor:pointer;transition:.15s;background:var(--surface)}
.session-row:hover{border-color:var(--green);background:var(--surface2)}
.session-row .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot-ai{background:var(--green);box-shadow:0 0 6px var(--green-glow)}
.dot-pending{background:var(--text-faint)}
.session-row .title{flex:1;font-size:13px;color:var(--text);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.session-row .title.dim{color:var(--text-dim);font-style:italic}
.edit-inline{opacity:0;font-size:11px;cursor:pointer;transition:.15s;margin-left:4px}
.session-row:hover .edit-inline{opacity:.5}
.edit-inline:hover{opacity:1!important}
.session-row .meta{display:flex;gap:10px;align-items:center;flex-shrink:0}
.tag{font-size:10px;padding:2px 8px;border-radius:3px;font-weight:500}
.tag-turns{background:var(--cyan-dim);color:var(--cyan);border:1px solid rgba(0,229,255,.2)}
.tag-empty{background:rgba(90,112,128,.1);color:var(--text-faint);border:1px solid var(--border)}
.tag-time{color:var(--text-faint);font-size:10px}
.tag-date{color:var(--text-dim);font-size:10px}
.row-actions{display:flex;gap:4px;align-items:center}
.gen-btn{padding:5px 14px;border:1px solid rgba(0,230,118,.4);border-radius:3px;
  background:var(--green-dim);color:var(--green);font-size:11px;cursor:pointer;
  font-family:inherit;transition:.15s;font-weight:600;letter-spacing:.5px}
.gen-btn:hover{background:rgba(0,230,118,.2);box-shadow:0 0 10px var(--green-glow);
  border-color:var(--green)}
.gen-btn:disabled{opacity:.3;cursor:not-allowed;box-shadow:none}
.gen-btn.done{border-color:var(--text-faint);background:transparent;color:var(--text-dim);font-weight:400}
.gen-btn.done:hover{border-color:var(--green);color:var(--green);background:var(--green-dim)}
.row-btn{padding:3px 8px;border:1px solid var(--border);border-radius:3px;
  background:transparent;color:var(--text-dim);font-size:10px;cursor:pointer;
  font-family:inherit;transition:.15s}
.row-btn:hover{border-color:var(--green);color:var(--green)}
.row-btn:disabled{opacity:.3;cursor:not-allowed}
.row-btn.danger:hover{border-color:var(--red);color:var(--red)}
.empty-state{text-align:center;padding:60px;color:var(--text-faint);font-size:13px}

/* ── Detail View (replaces list) ── */
.detail-view{padding:0 24px 24px}
.detail-back{display:inline-flex;align-items:center;gap:6px;padding:12px 0;
  color:var(--green);font-size:12px;cursor:pointer;border:none;background:none;
  font-family:inherit}
.detail-back:hover{text-decoration:underline}
.detail-card{border:1px solid var(--border);border-radius:4px;background:var(--surface);overflow:hidden}
.detail-header{padding:16px 20px;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.detail-title{font-size:16px;font-weight:600;color:var(--green);cursor:pointer;
  display:flex;align-items:center;gap:8px}
.detail-title:hover .edit-icon{opacity:1}
.edit-icon{opacity:.3;font-size:12px;transition:.15s}
.title-input{font-size:16px;font-weight:600;color:var(--green);background:var(--bg);
  border:1px solid var(--green);border-radius:3px;padding:4px 10px;width:100%;
  font-family:inherit;outline:none;box-shadow:0 0 8px var(--green-glow)}
.detail-info{display:flex;gap:12px;font-size:11px;color:var(--text-dim);margin-top:6px;flex-wrap:wrap}
.detail-info code{background:var(--surface2);padding:1px 6px;border-radius:3px;
  border:1px solid var(--border);font-size:10px}
.detail-actions{display:flex;gap:6px;flex-shrink:0}

/* ── Conversation ── */
.conversation{max-height:70vh;overflow-y:auto;padding:8px 0}
.turn{border-bottom:1px solid var(--border);padding:6px 0}
.turn:last-child{border-bottom:none}
.turn-header{font-size:11px;padding:10px 24px 4px;font-weight:600;letter-spacing:.5px}
.turn-header.user{color:var(--cyan)}
.turn-header.assistant{color:var(--green)}
.turn-body{padding:4px 24px 16px;font-size:12px;color:var(--text);line-height:1.8;
  word-break:break-word}
.turn-body.assistant{color:var(--text-dim)}
.turn-body p{margin-bottom:8px}
.turn-body p:last-child{margin-bottom:0}
.turn-body pre{background:var(--bg);border:1px solid var(--border);border-radius:4px;
  padding:10px;overflow-x:auto;margin:8px 0;font-size:11px}
.turn-body code{font-size:11px;font-family:'JetBrains Mono',monospace}
.turn-body code:not([class]){background:rgba(0,230,118,.06);padding:1px 5px;border-radius:2px;
  color:var(--green)}
.turn-body ul,.turn-body ol{margin-left:18px;margin-bottom:8px}
.turn-body h1,.turn-body h2,.turn-body h3{margin:10px 0 6px;color:var(--green)}
.turn-body h1{font-size:15px} .turn-body h2{font-size:14px} .turn-body h3{font-size:13px}
.turn-body a{color:var(--cyan)}
.turn-body blockquote{border-left:2px solid var(--green);padding-left:10px;color:var(--text-faint);margin:8px 0}
.turn-body table{border-collapse:collapse;margin:8px 0;font-size:11px}
.turn-body th,.turn-body td{border:1px solid var(--border);padding:4px 8px}
.turn-body strong{color:var(--text)}
.show-more-btn{font-size:11px;color:var(--cyan);cursor:pointer;background:none;
  border:none;padding:2px 0;font-family:inherit}
.show-more-btn:hover{text-decoration:underline}

/* ── Files ── */
.files-bar{padding:12px 20px;border-bottom:1px solid var(--border);background:var(--surface2)}
.files-bar .files-label{font-size:11px;color:var(--green);margin-right:8px;font-weight:600;letter-spacing:.5px}
.file-tag{font-size:11px;display:inline-block;padding:4px 10px;margin:3px;
  background:var(--green-dim);border:1px solid rgba(0,230,118,.2);border-radius:3px;
  color:var(--green);cursor:pointer;transition:.15s}
.file-tag:hover{background:rgba(0,230,118,.2);border-color:var(--green);box-shadow:0 0 6px var(--green-glow)}
.file-tag .file-icon{margin-right:4px}
.file-row{padding:8px 10px;border-bottom:1px solid var(--border);cursor:pointer;
  display:flex;align-items:center;gap:8px;transition:.15s;border-radius:3px}
.file-row:last-child{border-bottom:none}
.file-row:hover{background:var(--green-dim)}
.file-icon-label{color:var(--green);font-size:12px;flex-shrink:0}
.file-name{font-size:12px;color:var(--text);font-weight:500}
.file-path{font-size:10px;color:var(--text-faint);margin-left:auto;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;max-width:300px}

/* ── Modal ── */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;
  align-items:center;justify-content:center;z-index:100}
.modal-bg.active{display:flex}
.modal{background:var(--surface);border:1px solid var(--green);border-radius:4px;
  padding:24px;max-width:420px;width:90%;box-shadow:0 0 30px var(--green-glow)}
.modal h3{color:var(--green);margin-bottom:8px;font-size:14px}
.modal p{color:var(--text-dim);font-size:12px;margin-bottom:16px}
.modal .actions{display:flex;justify-content:flex-end;gap:8px}

/* ── Toast ── */
.toast{position:fixed;bottom:24px;right:24px;background:var(--surface);
  border:1px solid var(--green);color:var(--green);padding:10px 18px;border-radius:4px;
  font-size:12px;font-family:inherit;z-index:200;display:none;
  box-shadow:0 0 20px var(--green-glow)}
.toast.error{border-color:var(--red);color:var(--red);box-shadow:0 0 20px var(--red-dim)}

/* ── Summary Popup ── */
.summary-popup{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;
  align-items:center;justify-content:center;z-index:100}
.summary-card{background:var(--surface);border:1px solid var(--green);border-radius:4px;
  padding:24px;max-width:600px;width:90%;max-height:70vh;overflow-y:auto;
  box-shadow:0 0 30px var(--green-glow)}
.summary-card h3{color:var(--green);font-size:14px;margin-bottom:12px}
.summary-card .summary-text{font-size:12px;line-height:1.8;color:var(--text)}
.summary-card .summary-text p{margin-bottom:8px}
.summary-card .actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px;
  border-top:1px solid var(--border);padding-top:12px}

/* ── Buttons ── */
.btn{padding:5px 12px;border:1px solid var(--border);border-radius:3px;
  background:transparent;color:var(--text-dim);font-size:11px;cursor:pointer;
  font-family:inherit;transition:.15s;display:inline-flex;align-items:center;gap:4px}
.btn:hover{border-color:var(--green);color:var(--green)}
.btn:disabled{opacity:.3;cursor:not-allowed}
.btn-green{color:var(--green);border-color:rgba(0,230,118,.3)}
.btn-green:hover{background:var(--green-dim);box-shadow:0 0 8px var(--green-glow)}
.btn-danger{color:var(--red)}
.btn-danger:hover{border-color:var(--red);background:var(--red-dim)}

/* ── Spinner ── */
.spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--border);
  border-top-color:var(--green);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div class="header">
  <h1>COPILOT  SESSIONS</h1>
  <div class="sub" id="stats">Loading sessions…</div>
  <div class="update-banner" id="updateBanner" style="display:none"></div>
</div>

<div class="toolbar">
  <input class="search-input" id="search" placeholder="$ grep sessions…" autocomplete="off">
  <button class="chip active" data-filter="all">ALL</button>
  <button class="chip" data-filter="today">TODAY</button>
  <button class="chip" data-filter="week">WEEK</button>
  <select class="sort-select" id="sortSelect">
    <option value="newest">NEWEST</option>
    <option value="oldest">OLDEST</option>
    <option value="most-msgs">MOST MSGS</option>
  </select>
  <button class="new-sesh-btn" onclick="newSession()" title="Start a new Copilot session">🚀 NEW SESH</button>
</div>

<div id="content">
  <div class="session-list" id="sessionList"></div>
</div>

<!-- Delete Modal -->
<div class="modal-bg" id="deleteModal">
  <div class="modal">
    <h3>▸ CONFIRM DELETE</h3>
    <p id="deleteInfo">This will permanently remove this session.</p>
    <div class="actions">
      <button class="btn" onclick="closeModal()">CANCEL</button>
      <button class="btn btn-danger" id="confirmDelete">DELETE</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ── State ──
let sessions = [];
let currentPage = 1;
let totalSessions = 0;
let hasMore = false;
let currentView = 'list';
let deleteId = null;
let currentFilter = 'all';
let currentSort = 'newest';

marked.setOptions({
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, {language: lang}).value;
    return hljs.highlightAuto(code).value;
  },
  breaks: true, gfm: true
});

function toast(msg, isError) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = 'toast' + (isError ? ' error' : '');
  el.style.display = 'block'; setTimeout(() => el.style.display = 'none', 3000);
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

// ── Load ──
let _searchTimer = null;

async function load(page = 1, append = false) {
  const q = document.getElementById('search').value.trim();
  const params = new URLSearchParams({
    page, per_page: 5, filter: currentFilter, sort: currentSort
  });
  if (q) params.set('q', q);
  const res = await fetch('/api/sessions?' + params);
  const data = await res.json();
  if (append) {
    sessions = sessions.concat(data.sessions);
  } else {
    sessions = data.sessions;
  }
  currentPage = data.page;
  totalSessions = data.total;
  hasMore = data.has_more;
  updateStats();
  if (currentView === 'list') renderList();
}

function updateStats() {
  document.getElementById('stats').textContent = `${totalSessions} sessions`;
}

// ── Render List ──
function renderList() {
  currentView = 'list';
  const el = document.getElementById('content');
  if (!sessions.length) {
    el.innerHTML = '<div class="empty-state">No sessions found.</div>';
    return;
  }

  el.innerHTML = '<div class="session-list">' + sessions.map(s => `
    <div class="session-row" onclick="openSession('${s.id}')">
      <div class="dot ${s.has_ai_tldr ? 'dot-ai' : 'dot-pending'}"></div>
      <div class="title ${s.has_ai_tldr ? '' : 'dim'}">${esc(s.tldr)} <span class="edit-inline" onclick="event.stopPropagation();renameFromList('${s.id}')" title="Rename">✏️</span></div>
      <div class="meta">
        <span class="tag ${s.turn_count ? 'tag-turns' : 'tag-empty'}">${s.turn_count} msgs</span>
        <span class="tag-time">${esc(s.time_ago)}</span>
        <span class="tag-date">${esc(s.created_at_short)}</span>
        <div class="row-actions" onclick="event.stopPropagation()">
          <button class="gen-btn ${s.has_ai_tldr?'done':''}" onclick="genTldr('${s.id}',this)" ${s.turn_count===0?'disabled':''}
            title="${s.has_ai_tldr?'Regenerate':'Generate'} TLDR">✨ ${s.has_ai_tldr?'REGEN':'GENERATE TLDR'}</button>
          <button class="btn btn-green" onclick="resumeSession('${s.id}')" title="Resume in terminal">▸ RESUME</button>
        </div>
      </div>
    </div>
  `).join('') +
  (hasMore ? `<div style="text-align:center;padding:16px">
    <button class="btn btn-green" onclick="loadMore()">▾ LOAD MORE</button>
  </div>` : '') +
  '</div>';
}

function loadMore() {
  load(currentPage + 1, true);
}

// ── Open Session Detail ──
async function openSession(id) {
  currentView = 'detail';
  history.pushState({view:'detail', id}, '', '#' + id);
  const s = sessions.find(x => x.id === id);
  const el = document.getElementById('content');

  el.innerHTML = `
    <div class="detail-view">
      <button class="detail-back" onclick="backToList()">◂ BACK TO SESSIONS</button>
      <div class="detail-card">
        <div class="detail-header">
          <div>
            <div class="detail-title" onclick="editTitle('${s.id}')" id="detailTitle" title="Click to rename">${esc(s.tldr)} <span class="edit-icon">✏️</span></div>
            <div class="detail-info">
              <span>🕐 ${esc(s.created_at_short)}</span>
              <span>${esc(s.time_ago)}</span>
              ${s.cwd ? '<span>📁 <code>'+esc(s.cwd)+'</code></span>' : ''}
              <span class="tag tag-turns">${s.turn_count} msgs</span>
            </div>
          </div>
          <div class="detail-actions">
            <button class="btn btn-green" onclick="resumeSession('${s.id}')">▸ RESUME</button>
            <button class="btn btn-green" onclick="genTldr('${s.id}')" ${s.turn_count===0?'disabled':''} id="genBtn">
              ✨ ${s.has_ai_tldr?'REGEN':'GEN'} TLDR
            </button>
            <button class="btn btn-green" onclick="genSummary('${s.id}')" ${s.turn_count===0?'disabled':''} id="sumBtn">📝 SUMMARY</button>
            <button class="btn btn-green" id="filesBtn" style="display:none" onclick="showFilesPanel()">📂 FILES TOUCHED</button>
            <button class="btn" onclick="exportMd('${s.id}')">📄 EXPORT</button>
            <button class="btn" onclick="copyTldr('${s.id}')">📋 COPY</button>
            <button class="btn btn-danger" onclick="askDelete('${s.id}','${esc(s.tldr)}')">✕ DELETE</button>
          </div>
        </div>
        <div id="filesPanel"></div>
        <div class="conversation" id="conversation">
          <div style="text-align:center;padding:40px;color:var(--text-faint)">
            <span class="spinner"></span> Loading…
          </div>
        </div>
      </div>
    </div>`;

  const [turnsRes, filesRes] = await Promise.all([
    fetch('/api/sessions/' + id + '/turns'),
    fetch('/api/sessions/' + id + '/files')
  ]);
  const turns = await turnsRes.json();
  const files = await filesRes.json();

  const conv = document.getElementById('conversation');
  if (!turns.length) {
    conv.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-faint)">No conversation data.</div>';
  } else {
    conv.innerHTML = turns.map((t, i) => {
      let html = '';
      if (t.user_message) {
        html += `<div class="turn">
          <div class="turn-header user">▸ YOU</div>
          <div class="turn-body">${esc(t.user_message)}</div>
        </div>`;
      }
      if (t.assistant_response) {
        const full = t.assistant_response;
        const isLong = full.length > 3000;
        const display = isLong ? full.slice(0, 3000) : full;
        html += `<div class="turn">
          <div class="turn-header assistant">▸ COPILOT</div>
          <div class="turn-body assistant">
            <div class="turn-content">${renderMd(display)}</div>
            ${isLong ? `<button class="show-more-btn" onclick="expandTurn(this,'${btoa(encodeURIComponent(full))}')">▸ Show full response…</button>` : ''}
          </div>
        </div>`;
      }
      return html;
    }).join('');
    conv.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b));
  }

  if (files.length) {
    window._currentFiles = files;
    const btn = document.getElementById('filesBtn');
    if (btn) {
      btn.style.display = '';
      btn.textContent = '📂 FILES TOUCHED (' + files.length + ')';
    }
    // Auto-show files panel at top
    showFilesPanel();
  }
}

function renderMd(text) {
  if (!text) return '';
  try { return marked.parse(text); } catch(e) { return esc(text); }
}

function expandTurn(btn, b64) {
  const full = decodeURIComponent(atob(b64));
  const container = btn.previousElementSibling;
  container.innerHTML = renderMd(full);
  container.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b));
  btn.remove();
}

function backToList() {
  currentView = 'list';
  history.pushState({view:'list'}, '', '#');
  load();
  window.scrollTo(0, 0);
}

// ── Rename Title ──
function editTitle(id) {
  const s = sessions.find(x => x.id === id);
  const dt = document.getElementById('detailTitle');
  dt.innerHTML = `<input class="title-input" id="titleInput" value="${esc(s.tldr)}"
    onkeydown="if(event.key==='Enter')saveTitle('${id}');if(event.key==='Escape')cancelEdit('${id}')"
    onblur="saveTitle('${id}')">`;
  const inp = document.getElementById('titleInput');
  inp.focus();
  inp.select();
}

function renameFromList(id) {
  const s = sessions.find(x => x.id === id);
  const newTitle = prompt('Rename session:', s.tldr);
  if (newTitle !== null && newTitle.trim()) {
    saveCustomTitle(id, newTitle.trim());
  }
}

async function saveCustomTitle(id, title) {
  const s = sessions.find(x => x.id === id);
  if (!s) return;
  s.tldr = title;
  s.has_ai_tldr = true;
  await fetch('/api/sessions/' + id + '/tldr', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tldr: title})
  });
  updateStats();
  if (currentView === 'list') renderList();
  toast('✏️ Title saved');
}

async function saveTitle(id) {
  const inp = document.getElementById('titleInput');
  if (!inp) return;
  const newTitle = inp.value.trim();
  const s = sessions.find(x => x.id === id);
  if (!newTitle || newTitle === s.tldr) { cancelEdit(id); return; }
  await saveCustomTitle(id, newTitle);
  const dt = document.getElementById('detailTitle');
  if (dt) { dt.innerHTML = `${esc(newTitle)} <span class="edit-icon">✏️</span>`; dt.onclick = () => editTitle(id); }
}

function cancelEdit(id) {
  const s = sessions.find(x => x.id === id);
  const dt = document.getElementById('detailTitle');
  if (dt) dt.innerHTML = `${esc(s.tldr)} <span class="edit-icon">✏️</span>`;
  dt.onclick = () => editTitle(id);
}

// ── TLDR Generation ──
async function genTldr(id, rowBtn) {
  const detailBtn = document.getElementById('genBtn');
  const btn = rowBtn || detailBtn;
  if (btn) { btn.disabled = true; const orig = btn.innerHTML; btn.innerHTML = '<span class="spinner"></span>'; }
  try {
    const res = await fetch('/api/sessions/' + id + '/tldr', { method: 'POST' });
    const data = await res.json();
    if (data.ok && data.tldr) {
      const s = sessions.find(x => x.id === id);
      if (s) { s.tldr = data.tldr; s.has_ai_tldr = true; }
      updateStats();
      if (currentView === 'list') renderList();
      else {
        const dt = document.getElementById('detailTitle');
        if (dt) { dt.innerHTML = `${esc(data.tldr)} <span class="edit-icon">✏️</span>`; dt.onclick = () => editTitle(id); }
        if (detailBtn) { detailBtn.disabled = false; detailBtn.innerHTML = '✨ REGEN TLDR'; }
      }
      toast('✨ TLDR generated');
    } else { toast(data.error || 'Failed', true); }
  } catch(e) { toast('Error: ' + e.message, true); }
}

// ── Summary ──
async function genSummary(id) {
  const btn = document.getElementById('sumBtn');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Generating…'; }
  try {
    const res = await fetch('/api/sessions/' + id + '/summary', { method: 'POST' });
    const data = await res.json();
    if (data.ok && data.summary) {
      const popup = document.createElement('div');
      popup.className = 'summary-popup';
      popup.onclick = (e) => { if (e.target === popup) popup.remove(); };
      popup.innerHTML = `<div class="summary-card">
        <h3>📝 SESSION SUMMARY</h3>
        <div class="summary-text">${marked.parse(data.summary)}</div>
        <div class="actions">
          <button class="btn" onclick="navigator.clipboard.writeText(\`${data.summary.replace(/`/g,'\\`')}\`);toast('📋 Copied')">📋 COPY</button>
          <button class="btn btn-green" onclick="this.closest('.summary-popup').remove()">CLOSE</button>
        </div>
      </div>`;
      document.body.appendChild(popup);
    } else { toast(data.error || 'Failed', true); }
  } catch(e) { toast('Error: ' + e.message, true); }
  if (btn) { btn.disabled = false; btn.innerHTML = '📝 SUMMARY'; }
}

// ── Files Panel ──
let _filesPage = 0;
const FILES_PER_PAGE = 5;

function showFilesPanel() {
  _filesPage = 0;
  renderFilesPanel();
}

function renderFilesPanel() {
  const files = window._currentFiles || [];
  const start = _filesPage * FILES_PER_PAGE;
  const pageFiles = files.slice(start, start + FILES_PER_PAGE);
  const totalPages = Math.ceil(files.length / FILES_PER_PAGE);

  const panel = document.getElementById('filesPanel');
  if (!panel) return;

  panel.innerHTML = '<div class="files-bar">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<span class="files-label">📂 FILES TOUCHED (' + files.length + ')</span>' +
    '<div style="display:flex;gap:6px;align-items:center">' +
    (_filesPage > 0 ? '<button class="btn" onclick="_filesPage--;renderFilesPanel()" style="padding:2px 8px">◂ PREV</button>' : '') +
    (totalPages > 1 ? '<span style="font-size:10px;color:var(--text-faint)">' + (_filesPage+1) + '/' + totalPages + '</span>' : '') +
    (_filesPage < totalPages-1 ? '<button class="btn" onclick="_filesPage++;renderFilesPanel()" style="padding:2px 8px">NEXT ▸</button>' : '') +
    '</div>' +
    '</div>' +
    pageFiles.map((f, idx) => {
      const icon = f.tool_name === 'create' ? '✚' : '✎';
      const parts = f.file_path.replace(/[\\\\]/g, '/').split('/');
      const name = esc(parts.pop());
      const path = esc(f.file_path);
      return '<div class="file-row" data-file-idx="' + (start + idx) + '" onclick="openFileByIdx(this.dataset.fileIdx)">' +
        '<span class="file-icon-label">' + icon + '</span> ' +
        '<span class="file-name">' + name + '</span>' +
        '<span class="file-path">' + path + '</span>' +
        '</div>';
    }).join('') +
    '</div>';
}

// ── Open File ──
function openFileByIdx(idx) {
  const files = window._currentFiles || [];
  const f = files[parseInt(idx)];
  if (f) openFile(f.file_path);
}

async function openFile(path) {
  try {
    const res = await fetch('/api/open-file', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path})
    });
    const data = await res.json();
    if (data.ok) toast('📂 Opened in file manager');
    else toast(data.error || 'File not found', true);
  } catch(e) { toast('Error: ' + e.message, true); }
}

// ── New Session ──
async function newSession() {
  try {
    const res = await fetch('/api/new-session', { method: 'POST' });
    const data = await res.json();
    if (data.ok) toast('🚀 New session launched!');
    else toast(data.error || 'Failed to launch', true);
  } catch(e) { toast('Error: ' + e.message, true); }
}

// ── Resume ──
async function resumeSession(id) {
  try {
    const res = await fetch('/api/sessions/' + id + '/resume', { method: 'POST' });
    const data = await res.json();
    if (data.ok) toast('▸ Terminal opened at ' + data.cwd);
    else toast('Failed to open terminal', true);
  } catch(e) { toast('Error: ' + e.message, true); }
}

// ── Export ──
async function exportMd(id) {
  const s = sessions.find(x => x.id === id);
  const res = await fetch('/api/sessions/' + id + '/turns');
  const turns = await res.json();
  let md = `# ${s.tldr}\n\n`;
  md += `- **Date**: ${s.created_at_short}\n- **CWD**: ${s.cwd||'N/A'}\n- **Turns**: ${s.turn_count}\n\n---\n\n`;
  turns.forEach((t, i) => {
    if (t.user_message) md += `## User (Turn ${i})\n\n${t.user_message}\n\n`;
    if (t.assistant_response) md += `## Copilot\n\n${t.assistant_response}\n\n---\n\n`;
  });
  const blob = new Blob([md], {type:'text/markdown'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = `session-${id.slice(0,8)}.md`;
  a.click(); toast('📄 Exported');
}

function copyTldr(id) {
  const s = sessions.find(x => x.id === id);
  if (s) { navigator.clipboard.writeText(s.tldr); toast('📋 Copied'); }
}

// ── Delete ──
function askDelete(id, tldr) {
  deleteId = id;
  document.getElementById('deleteInfo').textContent = 'Session: "' + tldr + '"';
  document.getElementById('deleteModal').classList.add('active');
}
function closeModal() {
  document.getElementById('deleteModal').classList.remove('active');
  deleteId = null;
}
document.getElementById('confirmDelete').onclick = async () => {
  if (!deleteId) return;
  await fetch('/api/sessions/' + deleteId, { method: 'DELETE' });
  sessions = sessions.filter(s => s.id !== deleteId);
  closeModal();
  toast('✕ Session deleted');
  if (currentView === 'detail') backToList();
  else load();
};

// ── Events ──
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    currentFilter = chip.dataset.filter;
    load();
  });
});
document.getElementById('sortSelect').addEventListener('change', e => {
  currentSort = e.target.value; load();
});
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => load(), 300);
});

// ── Browser back/forward ──
window.addEventListener('popstate', (e) => {
  if (e.state && e.state.view === 'detail' && e.state.id) {
    openSession(e.state.id);
  } else {
    currentView = 'list';
    renderList();
    window.scrollTo(0, 0);
  }
});

async function init() {
  await load();
  const hash = location.hash.slice(1);
  if (hash && sessions.find(s => s.id === hash)) {
    openSession(hash);
  }
  // Check for updates
  try {
    const res = await fetch('/api/version');
    const v = await res.json();
    if (v.update_available) {
      const el = document.getElementById('updateBanner');
      el.innerHTML = '⚠ Update available: v' + v.latest + ' (you have v' + v.current + ') — run <code>git pull</code> to update <button class="dismiss-btn" id="dismissBtn">✕</button>';
      el.style.display = 'inline-block';
      document.getElementById('dismissBtn').onclick = () => el.style.display = 'none';
    }
  } catch(e) {}
}
init();
</script>
</body>
</html>
"""


# ── API Routes ─────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return HTML


@app.route("/api/version")
def api_version():
    """Check for updates against GitHub releases."""
    result = {"current": VERSION, "latest": VERSION, "update_available": False}
    try:
        import ssl
        url = f"https://raw.githubusercontent.com/{REPO}/main/pyproject.toml"
        req = urllib.request.Request(url, headers={"User-Agent": "copilot-session-viewer"})
        # Try default SSL first, fall back to unverified for corporate proxies
        ctx = None
        data = None
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode()
        except Exception:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                data = resp.read().decode()
        for line in data.splitlines():
            if line.startswith("version"):
                latest = line.split('"')[1]
                result["latest"] = latest
                result["update_available"] = latest != VERSION
                break
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/sessions")
def api_sessions():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    filt = request.args.get("filter", "all")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    offset = (page - 1) * per_page

    # Build WHERE clause
    conditions = [
        """((SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) > 1
           OR (SELECT t.user_message FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0)
              NOT LIKE 'Summarize this conversation%'
           AND (SELECT t.user_message FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0)
              NOT LIKE 'Give this conversation%')"""
    ]
    params = []

    # Date filters
    if filt == "today":
        conditions.append("s.created_at >= datetime('now', '-1 day')")
    elif filt == "week":
        conditions.append("s.created_at >= datetime('now', '-7 days')")
    elif filt == "month":
        conditions.append("s.created_at >= datetime('now', '-30 days')")

    # Search
    if q:
        conditions.append(
            "(s.summary LIKE ? OR "
            "(SELECT t.user_message FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0) LIKE ?)"
        )
        params.extend([f"%{q}%", f"%{q}%"])

    # Sort
    order = "s.created_at DESC"
    if sort == "oldest":
        order = "s.created_at ASC"
    elif sort == "most-msgs":
        order = "(SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) DESC"
    elif sort == "name":
        order = "COALESCE(s.summary, '') ASC"

    where = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM sessions s WHERE {where}"
    total = get_db().execute(count_sql, params).fetchone()[0]

    sql = f"""
        SELECT s.id, s.summary, s.cwd, s.repository, s.branch,
               s.created_at, s.updated_at,
               (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) as turn_count,
               (SELECT substr(t.user_message, 1, 300)
                FROM turns t WHERE t.session_id = s.id AND t.turn_index = 0) as first_message
        FROM sessions s
        WHERE {where}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    db = get_db()
    rows = db.execute(sql, params + [per_page, offset]).fetchall()
    db.close()

    # Also search TLDR cache for query matches
    result = []
    for r in rows:
        d = dict(r)
        has_ai = d["id"] in _tldr_cache
        if has_ai:
            d["tldr"] = _tldr_cache[d["id"]]
        elif d["first_message"]:
            first_line = d["first_message"].split("\n")[0].strip()
            d["tldr"] = (first_line[:60] + "…") if len(first_line) > 60 else first_line
        elif d["summary"]:
            d["tldr"] = (d["summary"][:60] + "…") if len(d["summary"]) > 60 else d["summary"]
        else:
            d["tldr"] = "Untitled session"
        # Filter by search in TLDR cache too
        if q and has_ai and q.lower() not in d["tldr"].lower():
            if not d.get("first_message") or q.lower() not in d["first_message"].lower():
                continue
        d["has_ai_tldr"] = has_ai
        d["time_ago"] = time_ago(d["created_at"])
        try:
            dt = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
            d["created_at_short"] = dt.strftime("%b %d, %H:%M")
        except Exception:
            d["created_at_short"] = d["created_at"] or ""
        result.append(d)

    return jsonify({
        "sessions": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_more": offset + per_page < total,
    })


@app.route("/api/sessions/<session_id>/turns")
def api_turns(session_id):
    db = get_db()
    rows = db.execute(
        "SELECT turn_index, user_message, assistant_response, timestamp "
        "FROM turns WHERE session_id = ? ORDER BY turn_index",
        (session_id,),
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sessions/<session_id>/files")
def api_files(session_id):
    db = get_db()
    rows = db.execute(
        "SELECT file_path, tool_name FROM session_files "
        "WHERE session_id = ? ORDER BY first_seen_at",
        (session_id,),
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sessions/<session_id>/tldr", methods=["POST"])
def api_generate_tldr(session_id):
    try:
        tldr = generate_tldr(session_id)
        if tldr:
            _tldr_cache[session_id] = tldr
            save_tldr_cache(_tldr_cache)
            return jsonify({"ok": True, "tldr": tldr})
        return jsonify({"ok": False, "error": "No response from AI"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sessions/<session_id>/tldr", methods=["PUT"])
def api_set_tldr(session_id):
    """Manually set/rename a TLDR."""
    data = request.get_json()
    tldr = data.get("tldr", "").strip()
    if not tldr:
        return jsonify({"ok": False, "error": "Empty title"}), 400
    _tldr_cache[session_id] = tldr
    save_tldr_cache(_tldr_cache)
    return jsonify({"ok": True, "tldr": tldr})


@app.route("/api/sessions/<session_id>/summary", methods=["POST"])
def api_summary(session_id):
    """Generate an AI summary of the full session."""
    db = get_db()
    rows = db.execute(
        "SELECT user_message, assistant_response FROM turns "
        "WHERE session_id = ? ORDER BY turn_index LIMIT 20",
        (session_id,),
    ).fetchall()
    db.close()
    if not rows:
        return jsonify({"ok": False, "error": "No conversation data"}), 400

    transcript = ""
    for r in rows:
        if r["user_message"]:
            transcript += f"User: {r['user_message'][:300]}\n"
        if r["assistant_response"]:
            transcript += f"Assistant: {r['assistant_response'][:300]}\n"

    system = (
        "You summarize conversations concisely. Write a brief summary with: "
        "1) What the user wanted, 2) What was done, 3) Key outcomes. "
        "Use bullet points. Keep it under 150 words. Use markdown."
    )
    prompt = f"Summarize this session:\n\n{transcript}"

    try:
        async def _call():
            session = await _copilot_client.create_session(
                {
                    "model": MODEL,
                    "system_message": {"mode": "replace", "content": system},
                    "available_tools": [],
                    "on_permission_request": PermissionHandler.approve_all,
                }
            )
            async with session:
                response = await session.send_and_wait(
                    {"prompt": prompt}, timeout=90
                )
                return response.data.content if response else None

        summary = _run_async(_call())
        if summary:
            return jsonify({"ok": True, "summary": summary})
        return jsonify({"ok": False, "error": "No response from AI"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/open-file", methods=["POST"])
def api_open_file():
    """Open a file's parent folder in the system file manager."""
    import subprocess
    data = request.get_json()
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "No path provided"}), 400

    # If the file exists, reveal it; otherwise walk up to nearest existing parent
    if os.path.exists(path):
        target = path
    else:
        target = os.path.dirname(path)
        while target and not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
    if not os.path.exists(target):
        return jsonify({"ok": False, "error": f"Path not found: {path}"}), 404

    if sys.platform == "darwin":
        if os.path.isfile(path):
            subprocess.Popen(["open", "-R", path])  # Reveal in Finder
        else:
            subprocess.Popen(["open", target])
    elif os.name == "nt":
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["explorer", target])
    else:
        subprocess.Popen(["xdg-open", target])

    return jsonify({"ok": True})


@app.route("/api/new-session", methods=["POST"])
def api_new_session():
    """Open a terminal with a fresh Copilot session."""
    import subprocess
    cmd = "copilot"
    cwd = os.path.expanduser("~")

    if os.name == "nt":
        subprocess.Popen(
            ["powershell", "-NoExit", "-Command", f"cd '{cwd}'; {cmd}"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif sys.platform == "darwin":
        escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "Terminal" to do script "cd {cwd} && {escaped}"'
        subprocess.Popen(["osascript", "-e", script])
    else:
        for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
            if os.system(f"which {term} >/dev/null 2>&1") == 0:
                subprocess.Popen([term, "--", "bash", "-c", f"cd {cwd} && {cmd}"])
                break

    return jsonify({"ok": True})


@app.route("/api/sessions/<session_id>/resume", methods=["POST"])
def api_resume(session_id):
    """Open a terminal and resume the Copilot session."""
    import subprocess, shlex
    db = get_db()
    row = db.execute(
        "SELECT cwd FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    db.close()
    cwd = row["cwd"] if row and row["cwd"] else os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    resume_cmd = f"cd {shlex.quote(cwd)} && copilot --resume={session_id}"

    if os.name == "nt":
        subprocess.Popen(
            ["powershell", "-NoExit", "-Command",
             f"cd '{cwd}'; copilot --resume={session_id}"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif sys.platform == "darwin":
        escaped = resume_cmd.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "Terminal" to do script "{escaped}"'
        subprocess.Popen(["osascript", "-e", script])
    else:
        for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
            if os.system(f"which {term} >/dev/null 2>&1") == 0:
                subprocess.Popen([term, "--", "bash", "-c", resume_cmd])
                break

    return jsonify({"ok": True, "cwd": cwd})


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete(session_id):
    db = get_db(readonly=False)
    try:
        db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM session_files WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM session_refs WHERE session_id = ?", (session_id,))
        ids = db.execute(
            "SELECT rowid FROM search_index WHERE session_id = ?", (session_id,)
        ).fetchall()
        for row in ids:
            db.execute(
                "DELETE FROM search_index WHERE rowid = ?", (row[0],)
            )
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.commit()
    finally:
        db.close()
    if session_id in _tldr_cache:
        del _tldr_cache[session_id]
        save_tldr_cache(_tldr_cache)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5051))
    print(f"\n  🚀 Copilot Session Viewer")
    print(f"  📂 Database: {DB_PATH}")
    print(f"  🌐 Open http://localhost:{port}\n")
    print(f"  💡 Tip: Add '127.0.0.1 copilot.local' to /etc/hosts")
    print(f"     then visit http://copilot.local:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False)