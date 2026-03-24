# Copilot Session Viewer

A retro terminal-style web app to browse, search, and manage your GitHub Copilot CLI conversation history — with AI-powered titles and summaries.

![Python](https://img.shields.io/badge/Python-3.11+-green) ![Flask](https://img.shields.io/badge/Flask-3.x-blue) ![Copilot SDK](https://img.shields.io/badge/Copilot_SDK-✨-purple)

## Quick Start

### 1. Install uv

**Mac/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Add uv to PATH (required after first install — or restart PowerShell)
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
```

**Or with pip (any OS):**
```bash
pip install uv
```

### 2. Clone and run

**Mac/Linux:**
```bash
git clone https://github.com/chonchiog/copilot-session-viewer.git
cd copilot-session-viewer
uv run --native-tls python app.py
# Open http://localhost:5051
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/chonchiog/copilot-session-viewer.git
cd copilot-session-viewer
uv run python app.py
# Open http://localhost:5051
```

### 3. (Optional) Custom hostname
```bash
# Mac/Linux
echo "127.0.0.1 copilot.local" | sudo tee -a /etc/hosts

# Windows (run PowerShell as Admin)
Add-Content C:\Windows\System32\drivers\etc\hosts "127.0.0.1 copilot.local"

# Then visit http://copilot.local:5051
```

## Prerequisites

- **Python 3.11+**
- **[GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)** installed and authenticated
  - Requires a [GitHub Copilot subscription](https://github.com/features/copilot) (Individual, Business, or Enterprise)
  - Install: `npm install -g @githubnext/github-copilot-cli` or `brew install gh && gh extension install github/gh-copilot`
  - Authenticate: `gh auth login` then run `copilot` at least once to create your first session

## Features

- **Session list** — browse all Copilot CLI conversations with search, filters, and pagination
- **🚀 New session** — launch a fresh Copilot CLI session from the toolbar
- **✨ AI titles** — click to generate short titles using Copilot SDK
- **📝 AI summaries** — generate a quick summary popup of any conversation
- **✏️ Rename** — click the pencil to rename any session
- **▸ Resume** — open a terminal and resume the conversation with `copilot --resume`
- **📂 Files touched** — inline panel showing all files created/edited, click to open in Finder/Explorer
- **Conversation viewer** — full chat history with markdown rendering and syntax highlighting
- **📄 Export** — download any session as a Markdown file
- **🗑 Delete** — remove sessions you no longer need
- **Search & filter** — full-text server-side search, date filters (Today/Week), sort by date/messages
- **Pagination** — loads 5 sessions at a time with "Load more"
- **Browser history** — back/forward navigation works between list and detail views
- **Cross-platform** — auto-detects Copilot data on Mac, Linux, and Windows
- **🧹 Auto-cleanup** — on startup, removes empty sessions and junk sessions created by the AI title generator (see below)
- **Update notifications** — checks GitHub for new versions and shows a dismissible banner

## Auto-Cleanup

Every time the app starts, it automatically cleans up:

- **Empty sessions** — sessions with no messages (e.g. from accidentally opening and closing Copilot CLI)
- **SDK junk sessions** — 1-turn sessions created as a side effect of AI title/summary generation

This keeps your session list clean. Your real conversations and AI-generated titles are never affected — titles are stored separately in `session-tldrs.json`.

## Configuration

| Env Variable | Default | Description |
|---|---|---|
| `COPILOT_DB` | Auto-detected | Path to `session-store.db` |
| `TLDR_MODEL` | `claude-sonnet-4.5` | Model for AI title/summary generation |
| `PORT` | `5051` | HTTP port to listen on |

The app auto-detects your Copilot data directory:
- **Mac/Linux**: `~/.copilot/session-store.db`
- **Windows**: `%LOCALAPPDATA%\copilot\session-store.db`

## Tech Stack

Single-file Flask app (`app.py`) with:
- [GitHub Copilot SDK](https://pypi.org/project/github-copilot-sdk/) for AI features
- [marked.js](https://marked.js.org/) + [highlight.js](https://highlightjs.org/) for markdown rendering
- Retro terminal UI with JetBrains Mono font and green glow theme

## Uninstall

Remove everything — the app, uv, and any host entries.

**Mac/Linux:**
```bash
# Remove the app
rm -rf ~/copilot-session-viewer

# Remove TLDR cache (keeps your Copilot sessions intact)
rm -f ~/.copilot/session-tldrs.json

# Remove copilot.local from /etc/hosts (if added)
sudo sed -i '' '/copilot.local/d' /etc/hosts

# Uninstall uv (if you don't need it for other projects)
rm -rf ~/.local/bin/uv ~/.local/bin/uvx ~/.cargo/bin/uv
```

**Windows (PowerShell as Admin):**
```powershell
# Remove the app
Remove-Item -Recurse -Force ~\copilot-session-viewer

# Remove TLDR cache
Remove-Item -Force "$env:LOCALAPPDATA\copilot\session-tldrs.json" -ErrorAction SilentlyContinue

# Remove copilot.local from hosts (if added)
$hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
(Get-Content $hosts) | Where-Object { $_ -notmatch 'copilot\.local' } | Set-Content $hosts

# Uninstall uv
Remove-Item -Recurse -Force "$env:USERPROFILE\.local\bin\uv.exe",
  "$env:USERPROFILE\.local\bin\uvx.exe",
  "$env:USERPROFILE\.local\bin\uvw.exe" -ErrorAction SilentlyContinue

# Remove uv from PATH (current session)
$env:Path = ($env:Path -split ';' | Where-Object { $_ -notmatch '\.local\\bin' }) -join ';'
```