# Pocket Agent

A coding agent you run from your phone. Think Claude Code, but as a web app: it reads and edits files, runs commands, searches the web, spawns sub-agents, and keeps its work in GitHub.

- **App** (`frontend/`) lives on GitHub Pages. Installable to your home screen.
- **Agent server** (`backend/`) lives on Render's free tier and calls your model API (NVIDIA Nemotron by default).
- **Everything persists in GitHub.** Render's disk is wiped on every restart, so chats go to a private `agent-state` repo and each workspace is a git branch.

## Deploy (all from a phone browser, about 15 minutes)

### 1. Put this code in a GitHub repo
Create a **public** repo called `pocket-agent` (Pages is only free for public repos; there are no secrets in the code) and put this folder's contents in it. Easiest: ask Claude to push it for you.

### 2. Make a GitHub token (the "PAT")
A PAT is a password-like key that lets the server push code on your behalf.
GitHub → your avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**. Name: `pocket-agent`. Tick the **`repo`** box. Generate, and **copy it now** (it's shown once).

> This token can reach all your repos, public and private, because classic tokens are all-or-nothing. It stays on the server and is never put in the agent's shell environment. If you'd rather limit it: create the private `agent-state` repo yourself, then make a *fine-grained* token for just the repos you want (Contents: read/write, Pull requests: read/write).

### 3. Get your NVIDIA key
[build.nvidia.com](https://build.nvidia.com) → any model → **Get API Key** → copy the `nvapi-…` key.

### 4. Deploy the server on Render
[render.com](https://render.com) → **New +** → **Blueprint** → pick the `pocket-agent` repo. Fill in the three secrets:

| Variable | Value |
|---|---|
| `APP_PASSWORD` | any long password. You'll type it into the app |
| `NVIDIA_API_KEY` | your `nvapi-…` key |
| `GITHUB_TOKEN` | the token from step 2 |

Deploy. When it's live, copy the URL (like `https://pocket-agent-xxxx.onrender.com`).

### 5. Publish the app on GitHub Pages
Repo → **Settings** → **Pages** → **Source: GitHub Actions**. Then **Actions** tab → *Deploy app to GitHub Pages* → **Run workflow**. Your app will be at `https://dmrakyz.github.io/pocket-agent/`.

*(If your GitHub username isn't `dmrakyz`, change `ALLOWED_ORIGINS` and `STATE_REPO` in Render's Environment tab.)*

### 6. Open it
Open the Pages URL on your phone → paste the Render URL and password → **Share → Add to Home Screen**.
Optional: put your Render URL in `frontend/config.js` (edit on GitHub) so the app never asks for it.

### 7. Keep it awake
Render's free tier sleeps after 15 min without inbound requests. Your agent calling the model does **not** count, and your phone's connection drops when the screen locks. So a long task would get killed mid-way. Fix: [cron-job.org](https://cron-job.org) → create a job that GETs `https://YOUR-SERVICE.onrender.com/health` **every 10 minutes**. (One always-on service fits inside Render's 750 free hours/month.)

## Using it
- **Chats** (☰): each chat is its own session. When you start one you can attach a GitHub repo (`owner/name`) or leave it blank for a scratch workspace. Repo chats work on their own branch `agent/<id>`; ask it to open a PR when you're happy.
- **Model chip**: pick a model, tap *test* to check it works. The list is fetched from your key, so you see what you can actually use.
- **Mode chip**: *Ask* (asks before edits and commands), *Auto-edit*, *Plan* (read-only research, then you approve a plan), *YOLO* (no asking).
- **Menu (⋯)**: browse/download files, save to GitHub now, compact context, thinking on/off, **Autopilot** (keeps going until it says `DONE:`, within a 300-step budget), and a **capability check** that runs 4 real tasks against the current model.
- **＋**: attach images (the model sees them) or **any file / zip** (uploaded into the workspace, archives are unpacked).
- **↺** next to your messages rewinds the chat *and* restores the files to that point.
- `/compact` summarizes old context. Context also auto-compacts.

## What it can do
Read, write, edit, multi-edit, glob, grep, bash (incl. background jobs), web search (DuckDuckGo, no key) and fetch, todo list, git commit/push, pull requests, **sub-agents** (parallel, own model each, nestable 2 levels, optional edit rights), skills, slash commands, hooks, remote MCP servers, plan mode, rewind, auto-compaction, file upload/download, share-a-file-with-me.

### Per-project customization (files in the repo)
- `AGENT.md` (or `CLAUDE.md`): instructions loaded every session.
- `.agent/commands/<name>.md`: slash command. `$ARGUMENTS` is replaced.
- `.agent/skills/<name>/SKILL.md`: with a `description:` line; loaded on demand.
- `.agent/hooks.json`: `{"PostToolUse":[{"matcher":"edit|write","command":"python -m pyflakes ."}]}`. A failing `PreToolUse` hook blocks the call.
- Remote MCP: set `MCP_SERVERS='[{"name":"x","url":"https://…","headers":{"Authorization":"Bearer …"}}]'`.

## Limits you should know about
- **Free-tier model limits.** NVIDIA's free endpoints are limited to roughly 40 requests/min. The server throttles itself to 35 (`LLM_RPM`), shared by all sub-agents. 429s retry with backoff, then fall back to a smaller model.
- **512 MB RAM, no persistent disk.** Light builds only. Uncommitted work is lost on restart, so it auto-commits and pushes after every turn.
- **No ports.** It can't serve a dev server you can open. Ask it to push to a branch and use GitHub Pages instead.
- **Not included:** browser automation, computer use, local (stdio) MCP servers.

## Security. Read this.
This server runs shell commands, on the internet, in the same container as your keys.
- Login is password-protected (rate-limited), CORS is locked to your Pages origin, file tools are confined to the workspace, and web fetch refuses internal addresses.
- API keys and the GitHub token are **removed from the shell's environment**, and obvious attempts to read `/proc/*/environ` are blocked. **That block is a heuristic, not a sandbox.** A determined or prompt-injected agent in YOLO mode could find another way.
- So: keep **Ask** mode for anything touching real repos, use YOLO only for throwaway chats, and give the PAT the least scope you're comfortable with.
- Fetched web pages are treated as untrusted in the system prompt, but no model is immune to prompt injection.

## Config reference (Render → Environment)
`DEFAULT_MODEL`, `FALLBACK_MODEL` (`provider:model-id`; auto-picked otherwise: prefers Ultra, falls back to Lightning) · `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PROVIDERS_JSON` (any OpenAI-compatible endpoint) · `TAVILY_API_KEY` / `BRAVE_API_KEY` (better search) · `LLM_RPM`, `MAX_STEPS`, `AUTOPILOT_STEPS`, `MAX_SUBAGENTS`, `SUBAGENT_PARALLEL`, `MAX_SUBAGENT_DEPTH`, `COMPACT_AT_TOKENS`, `MAX_UPLOAD_MB`, `BASH_TIMEOUT`.

## Troubleshooting
- **Menu → Server** shows what the server sees: providers, whether history sync is on, whether `git` is present.
- *"History sync off"*: check `GITHUB_TOKEN` and `STATE_REPO`; the error text is shown there.
- *First message takes a minute*: the server was asleep (see step 7).
- *"git is not installed"*: the service isn't using the Dockerfile; use the Blueprint.

## Tests
`python tests/unit.py && python tests/e2e.py && python tests/ui.py` (the UI test needs Playwright). They run the real server against a scripted fake model.
