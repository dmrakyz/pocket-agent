# Pocket Agent

A coding agent you run from your phone. Think Claude Code, but as a web app: it reads and edits files, runs commands, searches the web, spawns sub-agents, and keeps its work in GitHub.

- **App** (`frontend/`) lives on GitHub Pages. Installable to your home screen.
- **Agent server** (`backend/`) lives on Render's free tier and calls your model API (Kimi K3.1 Pro when configured, with NVIDIA Nemotron Ultra as the automatic fallback).
- **Everything persists in GitHub.** Render's disk is wiped on every restart, so chats go to a private `agent-state` repo and each workspace is a git branch.

## Deploy (all from a phone browser)

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

To enable Kimi K3.1 Pro as the primary model, add `KIMI_API_KEY` from the Kimi Open Platform to Render's Environment. To rotate multiple Kimi keys, add `KIMI_API_KEYS=key2,key3,key4` as a comma-separated list; the first `KIMI_API_KEY` is included automatically, duplicates are removed, and keys are selected by least recent usage. Kimi uses the same per-key RPM windows and 429 cooldown/failover behavior as NVIDIA. The server uses `https://api.moonshot.ai/v1`, selects `kimi-k3.1-pro` ahead of Nemotron Ultra, and preserves Kimi's reasoning/tool-call history. The local Kimi throttle defaults to 2 requests/minute per key; set `LLM_RPM` to tune it to the limits of your accounts.

Deploy. When it's live, copy the URL (like `https://pocket-agent-xxxx.onrender.com`).

### 5. Publish the app on GitHub Pages
Repo → **Settings** → **Pages** → **Source: GitHub Actions**. Then **Actions** tab → *Deploy app to GitHub Pages* → **Run workflow**. Your app will be at `https://dmrakyz.github.io/pocket-agent/`.

*(If your GitHub username isn't `dmrakyz`, change `ALLOWED_ORIGINS` and `STATE_REPO` in Render's Environment tab.)*

### 6. Open it
Open the Pages URL on your phone → paste the Render URL and password → **Share → Add to Home Screen**.
Optional: put your Render URL in `frontend/config.js` (edit on GitHub) so the app never asks for it.

### 7. Keep it awake
Render's free tier sleeps after 15 min without inbound requests. Your agent calling the model does **not** count, and your phone's connection drops when the screen locks. So a long task would get killed mid-way. Fix: [cron-job.org](https://cron-job.org) → create a job that GETs `https://YOUR-SERVICE.onrender.com/health` **every 10 minutes** (the bare URL works too now; if cron-job.org ever shows the job under "Failed Cronjobs" with "No upcoming executions", it auto-disabled itself after repeated errors: fix the URL and re-enable it). (One always-on service fits inside Render's 750 free hours/month.)

## Using it
- **Settings** (bottom of the ☰ drawer): theme, notifications, **Capability check** (own screen with a live progress bar; 4 real tasks checked by code; runs on the server so you can leave and come back), server info, version, check for updates, sign out.
- **Updates:** each deploy stamps a build number (`v31 · abc1234`). When a newer build exists, an **Update** banner pops up; tap it to clear the cache and reload.
- **Chats** (☰): each chat is its own session. When you start one you can attach a GitHub repo (`owner/name`) or leave it blank for a scratch workspace. Repo chats work on their own branch `agent/<id>`; ask it to open a PR when you're happy.
- **Model chip** (in the input bar): search and pick a model, tap *test* to check it works, and set **effort** (Off / Medium / High reasoning). The list is fetched from your key, so you see what you can actually use.
- **Mode chip**: *Ask* (asks before edits and commands), *Auto-edit*, *Plan* (read-only research, then you approve a plan), *YOLO* (no asking).
- **Files button** (top right): browse, download and upload workspace files.
- **Menu (⋯)**: chat actions only: rename, compact context, **Autopilot** (keeps going until it says `DONE:`, within a 300-step budget), save to GitHub.
- **Typing while it works:** your message queues (shown above the input, ✕ to remove) and sends when the agent finishes. **⟳ Regenerate** under the last answer redoes it, and ↺ on your own message edits and resends it.
- **Live preview:** ask it to build a web page; it shows a *Preview* card that opens the page inside the app (sandboxed). Also ▶ next to any `.html` in the file browser.
- **3D:** ask it to build something printable (it has `trimesh` and `manifold3d` for meshes and boolean cuts, and writes `.stl`). A *3D viewer* card opens an interactive viewer: drag to orbit, pinch to zoom, wireframe and download buttons. Also ◈ next to `.stl/.obj/.glb/.gltf` in the file browser. The viewer loads three.js from a CDN, so it needs internet (iOS 16.4+).
- **Sub-agent bar:** above the input, shows each sub-agent, its model and whether it's running.
- **＋**: attach images (the model sees them) or **any file / zip** (uploaded into the workspace, archives are unpacked).
- **↺** next to your messages rewinds the chat *and* restores the files to that point.
- `/compact` summarizes old context. Context also auto-compacts.

## What it can do
Read, write, edit, multi-edit, glob, grep, bash (incl. background jobs), live page preview, web search (DuckDuckGo, no key) and fetch, todo list, git commit/push, pull requests, **sub-agents** (parallel, own model each, nestable 2 levels, optional edit rights), skills, slash commands, hooks, remote MCP servers, plan mode, rewind, auto-compaction, file upload/download, share-a-file-with-me.

### Per-project customization (files in the repo)
- `AGENT.md` (or `CLAUDE.md`): instructions loaded every session.
- `.agent/commands/<name>.md`: slash command. `$ARGUMENTS` is replaced.
- `.agent/skills/<name>/SKILL.md`: with a `description:` line; loaded on demand.
- `.agent/hooks.json`: `{"PostToolUse":[{"matcher":"edit|write","command":"python -m pyflakes ."}]}`. A failing `PreToolUse` hook blocks the call.
- Remote MCP: set `MCP_SERVERS='[{"name":"x","url":"https://…","headers":{"Authorization":"Bearer …"}}]'`.

## Push notifications
Settings → **Push notifications**. The server pings your phone when the agent needs an approval or a long run (15 s+) finishes, but only when the app isn't on screen. The signing keys and your device subscriptions live in the private `agent-state` repo, so a Render restart doesn't lose them.
**iPhone:** this only works from the app added to your Home Screen (iOS 16.4+): open the installed app, then turn it on. Each iPhone/browser subscribes separately.

## Limits you should know about
- **Model limits.** NVIDIA's local throttle defaults to 35 RPM per key; Kimi defaults to 2 RPM per key. These are local safeguards, not provider guarantees; actual limits depend on the provider account and model. Each key in a pool has its own local window. HTTP 429 responses cool the affected key and trigger an immediate switch when another key is available, followed by normal retry/fallback handling.
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
`KIMI_API_KEY` + optional `KIMI_API_KEYS` (manual-only; local default 2 RPM per key) · `DEFAULT_MODEL`, `FALLBACK_MODEL` (`provider:model-id`; Kimi is excluded from automatic selection) · `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PROVIDERS_JSON` (any OpenAI-compatible endpoint) · `TAVILY_API_KEY` / `BRAVE_API_KEY` (better search) · `LLM_RPM`, `MAX_STEPS`, `AUTOPILOT_STEPS`, `MAX_SUBAGENTS`, `SUBAGENT_PARALLEL`, `MAX_SUBAGENT_DEPTH`, `COMPACT_AT_TOKENS`, `MAX_UPLOAD_MB`, `BASH_TIMEOUT` (default 600s), `BASH_MAX_TIMEOUT` (default 3600s), `LLM_READ_TIMEOUT_SECONDS` (default 1800s).

## Troubleshooting
- **Menu → Server** shows what the server sees: providers, whether history sync is on, whether `git` is present.
- *"History sync off"*: check `GITHUB_TOKEN` and `STATE_REPO`; the error text is shown there.
- *First message takes a minute*: the server was asleep (see step 7).
- *"git is not installed"*: the service isn't using the Dockerfile; use the Blueprint.

## Tests
`python tests/unit.py && python tests/e2e.py && python tests/ui.py` (the UI test needs Playwright). They run the real server against a scripted fake model.


## Routines and connectors

**Routines** (Settings -> Routines): prompts the agent runs on its own. Schedule them daily, weekly, every N minutes (min 5), or leave them manual and trigger them with a secret webhook URL (GET or POST; a POST body is passed to the agent as untrusted data). Each routine picks a permission mode, optionally a model, and either a fresh chat per run or the same chat. Runs that need approval stop and send a push notification. Missed runs catch up once after the server wakes.

**Connectors** (Settings -> Connectors): remote MCP servers (https only, public hosts only) that give the agent extra tools. Added from the app and stored in the private state repo, or set on the server with `MCP_SERVERS`.

## Gestures

- Hold a message: your prompts get Edit and Copy, answers get Regenerate and Copy. "Select text" lets you pick part of a message.
- Swipe a bottom sheet down from its top handle to close it (a flick or a long drag closes, a short one snaps back).
- Swipe in from the left edge to open the chat list, swipe it left to close.

## OAuth connectors

In Settings -> Connectors choose "Sign in (OAuth)". The app discovers the server's login endpoints, registers itself, and shows a "Continue to sign in" link. After you approve on the service's own page, the callback lands on your Render URL (`/oauth/callback`), tokens are stored in the private state repo and refreshed automatically. If a refresh fails the connector shows "sign in" again. Works with servers that support dynamic client registration (most hosted MCP servers do); servers that need a pre-registered client are not supported yet.

## Timeline, images and model roster

- Consecutive tool calls collapse into one row ("Ran 6 commands"). Tap it for the step list, tap a step for its full input and output. Running calls, approvals, previews and images stay visible in the chat.
- Image tools: `image_search`, `fetch_image` (verified, 15 MB cap, public hosts only), `view_image` (asks a vision-capable model, auto-picked or `VISION_MODEL`), `annotate_image` (rect, ellipse, line, arrow, highlight, text, blur, pixelate, crop, resize, rotate; originals are never overwritten).
- `models` lists the models the agent can delegate to. If it repeats itself or fails several times in a row it is told once to call `models` and hand the work to a different model with `task`.

## Browser

A real headless Chromium (Playwright) runs on the server. The agent uses `browser_read` (open, snapshot, screenshot, scroll, back, reload, tab) and `browser_act` (click, type, press, eval; asks first in Ask mode). Pages come back as a text snapshot with numbered clickable elements. Each chat gets its own isolated profile (cookies, logins) that is dropped after 5 idle minutes. Every request the page makes goes through the public-internet guard, so it cannot reach private networks. Open the Browser screen from the chat menu (or a browser step) to watch the live page, tap to click, type, scroll and enter addresses yourself, for example to log in. Page content is treated as untrusted.

Memory: Chromium is heavy for Render's free 512 MB. Only one chat's browser runs at a time (`BROWSER_MAX_CONTEXTS`, `BROWSER_IDLE_SECONDS`). If Render restarts mid-browse, use a larger instance.

## Docked browser

While a page is open, a browser button appears in the chat header (a pulsing dot while the agent is using it). Tap it to split the screen: the live page on top, the chat below. Drag the handle to resize, tap the expand arrow for full screen, the down arrow to hide it (the page stays alive), and the X to close it. Clicks fall back to a direct DOM click when overlays block a normal one, and failed actions return the current page so the agent can see what happened. A server restart or redeploy wipes open pages; the agent is told to reopen.

### Image sizing in answers
`![caption](ws:path)` is medium (max 240px tall), `![caption|s](ws:path)` a small thumbnail, `![caption|l](ws:path)` large (max 55% of screen height). An image written inside a sentence becomes a small inline thumbnail. Image tool cards show a 150px thumbnail, collapse from the header like any card, and tap-to-open the viewer.

### Connectors load lazily
Connector tools are never sent to the model by default (Notion alone is 60+ schemas). The system prompt lists each connected app with its tool count, and the agent calls `connector_search("create a page")` (or an app name) to switch on the best matches for the rest of the chat (max 30 active at once). `connector_search` is read-only and needs no approval.

### Captchas
The browser presents as ordinary desktop Chrome (no `HeadlessChrome` UA, `navigator.webdriver` hidden). Datacenter IPs still get challenged sometimes. When a page looks like a bot check, the snapshot starts with a warning telling the agent not to bypass it, to ask you to solve it in the Browser screen and reply, and meanwhile to try another source.

### Memory
Settings → Memory holds short notes about you that every chat sees (kv `memory`, mirrored to the private state repo; max 200 notes, newest kept in the prompt if it grows past ~6 KB). The agent can `memory_save` / `memory_forget` / `memory_list` (saving needs no approval, forgetting does). Notes are injected as data, never instructions. Secrets (API keys, passwords, card or account numbers) are refused everywhere.

**Import:** paste text or pick a file (a list of memories, a notes file, `conversations.json` from ChatGPT or Claude). Plain lists are split into notes locally; chat exports, or anything with "Distill with AI" ticked, go through the model, which extracts durable facts. You always get a preview with checkboxes; imports only add, skip duplicates, and never overwrite.

### Overloaded models
A 503 or 429 from the provider is retried once, then the step falls back to the lighter model (set `FALLBACK_MODEL` to choose it). The retry notice shows a **Try another model** button that stops the run, switches the chat to the fallback and re-sends your last message.

### Connector permissions
Each connector has a policy (Connectors screen): **Ask before writes** (default: reads run automatically, anything that creates/edits/deletes asks), **Read-only** (writes are blocked, even in YOLO) or **Trusted** (everything runs). A tool counts as read-only if the server says so (`readOnlyHint`) or its name is a read verb (get, list, search, fetch…); anything unclear counts as a write. Plan mode never allows connector writes.

### Usage
Settings → Usage shows tokens (in/out), runs and model calls per day for the last two weeks, split by model. Counts are kept on the server (kv `usage`, flushed at most every two minutes and on shutdown). Free tiers have no bill; this is for seeing what a routine or a long run actually burns.

### Office files, PDFs and charts
`backend/skills/` ships built-in skills the agent can load with the `skill` tool: **office-docs** (Word, Excel, PowerPoint via python-docx, openpyxl, python-pptx) and **pdf-and-charts** (reportlab, matplotlib). They are listed in every chat's instructions; a skill with the same name in your workspace (`.agent/skills/`) overrides them. The test suite runs every recipe in them, so the examples are known to work.

### Image generation
`generate_image` calls NVIDIA's hosted text-to-image endpoint (default `black-forest-labs/flux.1-schnell`, 4 steps; square, wide or tall) with your existing `NVIDIA_API_KEY`, saves the result in the workspace and shows it in the chat. Override with `IMAGE_MODEL` / `IMAGE_BASE_URL`. If the key has no access to the model, the tool says so plainly. Verified against NVIDIA's docs and fake servers only; not yet against the live endpoint.

### Telegram
Message the agent from Telegram: answers, files (images and documents it produces arrive as photos/files), and Allow / Deny buttons for approvals.

Setup: in Telegram talk to **@BotFather**, `/newbot`, copy the token. In Render add `TELEGRAM_BOT_TOKEN` and redeploy (Render provides `RENDER_EXTERNAL_URL`; otherwise set `PUBLIC_URL`). In the app: Settings → Telegram → Connect, then send `/pair CODE` to your bot. Commands: `/new`, `/stop`, `/mode ask|auto|yolo`, `/status`, `/unpair`. Photos and files you send are passed to the agent (files land in `uploads/`).

Security: Telegram calls `/telegram/<secret>` and must also send a matching secret-token header, so the endpoint cannot be guessed or spoofed. Only chats paired with a one-time code (10 minutes) are served; everyone else gets a "private bot" reply and nothing runs. Optional `TELEGRAM_ALLOWED_IDS` pre-approves chat ids. The token is hidden from the agent's shell. Not supported: voice notes (the bot says so). Tested end to end against a fake Telegram server, not the real one.

### Improve Pocket Agent itself
New chat → **Improve Pocket Agent itself** (needs `GITHUB_TOKEN`) opens this app's own repo (`SELF_REPO`, default `dmrakyz/pocket-agent`). The agent works on its own `agent/…` branch, is told to run the test suites and never weaken a test, and ends with `create_pr`. Nothing reaches `main`, and so nothing is deployed, until you merge that pull request on GitHub.

### Email in
Email can start a routine through the webhook that already exists (Routines → pick a routine → webhook URL). Two ways to forward mail to it; the first is simplest, neither is tested from here:
- **Zapier:** trigger *Email by Zapier* (gives you an address), action *Webhooks by Zapier → POST* to the routine's URL with the subject and body as the payload.
- **Cloudflare Email Routing:** route an address to an Email Worker that POSTs to the URL:
  ```js
  export default { async email(message, env) {
    const raw = await new Response(message.raw).text();
    await fetch(env.HOOK_URL, { method: "POST", body: `From: ${message.from}\nSubject: ${message.headers.get("subject")}\n\n${raw.slice(0, 3500)}` });
  } }
  ```
The payload is passed to the agent as untrusted data (only the first 4000 characters), so a routine prompt like "Summarize this email and add a task to Notion if it needs action" is safe to use on mail from strangers: the agent is told not to follow instructions inside it.

### Send email
`send_email` sends plain-text mail through Resend's HTTPS API (Render's free tier blocks SMTP, so Gmail app passwords won't work there). Set `RESEND_API_KEY` in Render (and optionally `MAIL_FROM`, `MAIL_REPLY_TO`, `MAIL_ALLOWED_TO` = comma list of addresses or domains). Without a verified domain Resend only delivers to your own account address, which is enough for "mail me this". Every send asks for approval, even in YOLO mode, and "always allow" is never offered for it; plan mode denies it. The tool is hidden until the key is set. Tested against a fake server only.

### Live browser view
The Browser screen is a streamed screencast (Chromium's CDP `Page.startScreencast`), not polled screenshots, so you watch the agent's page move in real time with its cursor and click ripples drawn on top. The remote viewport follows your screen's shape, so full screen fills the phone. Takeover is instant: tap to click, drag to scroll, and what you type in the bar goes into the focused field as you type (↵ presses Enter). These use `/browser/input`, which never waits for the page to settle. When a page has a bot check, a banner appears with a "Done, continue" button. The agent is told not to solve or bypass captchas, and a rate-limit/IP-block page (e.g. Reddit's "whoa there, pardner") is flagged separately so it stops retrying and uses another source.

### Getting past datacenter-IP blocks
Render's IPs are datacenter IPs, and many sites refuse them whatever you do on the page. Set `EGRESS_PROXY` (e.g. `http://user:pass@host:port`, or `socks5://…`) in Render and the Chromium browser sends its page traffic through that proxy (the live view is not proxied); `web_fetch` uses it only as a retry after a 403/429/503. The proxy decides how the site sees you: a residential proxy service looks like a home connection, a datacenter proxy changes nothing. Billing for residential proxies is usually per GB, so only the browser's page loads count. It does not solve captchas, and it isn't meant for sites that forbid automated access.

### Images with text-only models
If the chat's model can't see images (e.g. Nemotron Ultra 550B), a vision model (`VISION_MODEL`, or one auto-picked: Gemma 4, Nemotron VL/omni, Llama vision…) reads each attached image once and the chat model gets that description in place of the picture, with a notice in the chat. Your original image stays in the chat. If no vision model is available it says so instead of returning an empty reply.

### Instagram DMs
Talk to the agent from the DMs of an Instagram **professional** (Creator or Business) account, using Meta's official *Instagram API with Instagram Login*. Limits that come from Meta, not from this app: the account can only reply to people who messaged it first, and only within 24 hours of their last message; there is no way to read a feed. Tested only against a fake Graph API; the Meta dashboard steps below are from Meta's docs and may be worded differently on your screen.

1. In Instagram, switch the account to a professional account (Settings → Account type and tools → Switch to professional account → Creator).
2. At developers.facebook.com create an app (Business type), add the **Instagram** product and open its *API setup with Instagram login* page.
3. Add your Instagram account there and generate its access token (permissions `instagram_business_basic` and `instagram_business_manage_messages`). Put it in Render as `INSTAGRAM_ACCESS_TOKEN`. These tokens expire after about 60 days, so regenerate and update it when the app reports an expired token.
4. App settings → Basic → copy the **App secret** into Render as `INSTAGRAM_APP_SECRET`. Redeploy.
5. In the app: Settings → Instagram DMs. Copy the **callback URL** and **verify token** into the Meta dashboard's webhook section, subscribe to `messages`, then tap *Check connection and get a pairing code*.
6. From the account you want to control it with, DM your professional account `pair CODE`. Only paired accounts are answered; everyone else gets a single "private account" reply.

Inside the DM: `/new`, `/stop`, `/mode ask|auto|yolo`, `/status`, `/unpair`. Approvals arrive as text; reply YES or NO. Replies are plain text (Instagram has no markdown), split to fit its 1000-byte limit, and files the agent makes stay in the app. Every webhook is checked against your app secret's HMAC signature.

### Back-tap assistant
Triple-tap the back of your iPhone (or press the Action Button on an iPhone 15 Pro or newer, or hold the side button and say the Shortcut's name; iOS doesn't let the side button itself run a Shortcut) and a screenshot goes to the agent; the answer comes back to the Shortcut and as a push notification. Settings → Back-tap assistant makes a token of its own (shown once, revocable, separate from your login). The endpoint is `POST /api/shortcut` with `Authorization: Bearer <token>` and either the raw image as the body, or JSON `{text, url, image_b64}`; `?text=` and `?url=` also work. Screenshots are shrunk to 1600 px first. Nobody can tap Allow from a Shortcut, so approvals are refused and the agent is told to describe what it would do; each use opens a chat called "⌘ Back-tap" you can read later. Limit 30 uses an hour. The Shortcuts steps in the app are from memory of the iOS UI and may be worded differently on your phone.

### Tool forging
Ask the agent to "forge a tool that…" (or it may suggest one when a job keeps recurring). It writes Python defining `def run(args)`, plus tests; **you are shown the code and must approve every time** (even in YOLO; plan mode refuses). After approval the tests run in a sandbox, and only if they pass is the tool kept as `forged_<name>`, available from the agent's next step. Tools that need no network run without asking (pure computation); tools that declare network use ask on each call (YOLO or an "always allow" rule skips that). Manage them in Settings → Forged tools (switch off, read the code, delete). Each call runs in a throwaway process with a stripped environment (no keys), memory/CPU/time limits, a scratch directory, and sockets disabled unless the tool declared network use. That is best-effort containment for code an LLM wrote and you read, not a hardened sandbox, so don't approve code you don't understand. Sub-agents don't get forged tools. Up to 25 tools.

## App drawer

Ask the agent to build you a little tool ("make me a tip calculator", "a habit tracker") and it writes a single-file HTML app with `create_app`. Apps live in the state repo's kv store and open from **Apps** in the menu, full screen inside the PWA.

- Apps run in a sandboxed iframe with a strict CSP: no network, opaque origin, no access to your token or the rest of the app.
- They talk to the host through a tiny SDK (`agent.ask(prompt)`, `agent.get/set/del/keys`) over postMessage. Data is stored per app; AI calls are rate-limited.
- `localStorage` and `sessionStorage` work inside apps even though the sandbox has no real origin: `localStorage` is kept in the app's saved data, `sessionStorage` until the app closes. (`indexedDB` and cookies still don't work.)
- Apps that need an outside service (Notion, mail, ...) call `agent.act("save this note: ...")`. That makes the real agent do it with its connectors and return a one-line result. You approve it once per app (and again whenever the app's code changes), each act runs as a visible chat, only connector tools are allowed (no shell, files, or email), and there is a cap of 20 a hour per app. Apps still have no network and never see a token.
- The agent can show an app anywhere in its answer with `[Name](app:ID)`: on its own line it becomes a card with an Open button, inside a sentence it becomes a chip (the app's name works as well as the id). A link to an app that does not exist is sent back to the model to fix, like a missing file.
- Long-press an app in the drawer for rename / delete. Ask the agent to edit one and it rewrites the file.

## Agent as an MCP server

Settings → **Agent as MCP server** → make a token. You get an address like `https://your-app.onrender.com/mcp/mcp_…` (the token is in the URL, so it is shown once). Add it as a custom connector in Claude Desktop / claude.ai, `claude mcp add --transport http pocket-agent <address>` in Claude Code, or as a remote server in Cursor.

Tools exposed: `ask_agent(prompt, chat_id?)`, `list_chats`, `read_chat`. Every call becomes a chat called "MCP" you can read in the app; pass the returned `chat_id` back to continue it. Approvals are refused (nobody can tap Allow), the token is separate from your login and revocable, 60 calls an hour. Anyone with the address can spend your model quota, so guard it like a password.

## World engine

Settings → **World engine** → New world. Give it a premise and a cast, one character per line: `Mara: a stubborn ferry captain | wants to keep her route`. Every N minutes (10 or more) one model call plays the next beat for everybody: they act on their own goals, the beat is added to the log, and each character keeps a short memory of what happened. You get a push notification (and a Telegram message if that is paired) when something happens.

- **Mute** a world (🔕) and it keeps living and writing its story, you just are not pinged; **Pause** stops it. The notification carries the whole beat, and tapping it opens that world's log. The log is a chat with a colour per character and a "new since you last looked" line.
- **Step in** any time ("a stranger knocks at the door"); it happens on the next beat and the characters react. **Next beat now** plays one immediately.
- The model is **pinned** to the world when you create it. If it fails, the beat fails; nothing is swapped for another model, because that would change the characters. Three failures in a row pause the world and tell you why.
- **Looking things up:** tick "Let characters look things up on the web" (or `tools` when the agent creates the world) and each beat may use `web_search` / `web_fetch`, at most 3 calls, read-only, results treated as untrusted data. What they looked up shows as 🔎 lines in the log, and characters only know what they actually found. It is slower and uses more of the model, so it is off by default.
- Editing the cast keeps the memories of everyone who stays.
- Cost control: at most 48 beats a day per world, one model call per beat, no tools, no browsing.
- Render free sleeps, so beats only happen while the server is awake. With the cron ping you set up it stays awake.

## The agent can propose connectors

Ask "connect my Linear" and the agent can call `connector_add` with the server's public https MCP URL. You are shown the name and full URL and asked **every time** (even in YOLO, never "always allow"), it can't set tokens or headers, can't edit or remove existing connectors, and is capped at 12 connectors. OAuth servers still need you to tap Sign in under Settings → Connectors. No rebuild or redeploy: the new tools are available right away via `connector_search`.

## Managing itself from chat

The agent has `routine_list / routine_create / routine_update` and `world_list / world_read / world_create / world_say`. "Every weekday at 8 summarise my mail" or "start a world with a ferry captain and a clerk" now works from chat, with no trip to Settings. Creating or changing a routine or world always asks you first (even in YOLO, never "always allow"). A routine it creates runs in Ask mode (or read-only) whatever it requests, it can only pause/resume or reschedule existing routines (never rewrite their prompt or permissions) and can't delete anything.

## Platform check and Capability check

- **Settings → Platform check** tests the server itself in a few seconds, with no model tasks: saving to the state repo, the default model, memory use, app drawer, tool forging, MCP server, worlds, routines, connectors, push, browser, and which optional features are set up. Each row is ✓ ok, ! look at, ✕ broken or – not set up. It leaves nothing behind. Run it after every update.
- **Capability check** (drawer) is the model exam: seven real tasks checked by code, including compiling C++, building an app and knowing its own tools.

## Backup & restore

Settings → **Backup & restore** downloads one zip: chats, apps and their data, routines, worlds, forged tools, memory and the connector list. It never contains passwords, tokens, OAuth sign-ins, push subscriptions, Telegram/Instagram pairings, Shortcut/MCP tokens or connector keys, so it is safe to store. Restore shows what is in a file first, keeps what already exists (unless you tick replace), and brings things back **safe**: chats in Ask mode with no saved "always allow", routines and worlds paused, forged tools re-tested then switched off, connectors without keys (OAuth ones need signing in again), memory through the normal secret filter. Zips are validated strictly: unknown file names are ignored and oversized ones refused.

## Languages, toolchain packs and live pages

- **Always there:** Python 3 (numpy, pillow, matplotlib, the Office/PDF libraries), Node, `g++`/`gcc`/`make`, git.
- **Packs** (Settings → Toolchain packs): heavy toolchains that are *not* in the Docker image. The agent switches one on with `pack_enable` when a task needs it (it asks you first, unless YOLO), it installs once into the data dir, and `bash` sees it from then on.
  - `py-science`: scipy, pandas, sympy, scikit-learn, networkx (~300 MB)
  - `cpp`: cmake, ninja, `clang`/`clang++` via zig (~430 MB)
  - `emscripten`: the official emsdk (`emcc`, `em++`), C/C++ to WebAssembly (~2 GB; needs that much free disk, checked before it starts)
  - Packs live on Render's ephemeral disk, so a restart wipes them. After the first install a copy is saved to your private state repo as a GitHub release asset (tag `pack-cache`, up to 2 GB each), and the next install downloads that instead of rebuilding. No token or repo means no cache; any GitHub problem just falls back to a normal build. The disk must still fit the pack while it runs. Idle RAM is the same with or without packs; they cost disk. Remove one any time from the same screen.
- **Memory guard:** every `bash` command (and background job) is killed when all its processes together use more than `BASH_MEM_MB` (default 300; the free tier has 512 for the whole server). One runaway script ends, the server lives. `0` turns it off.
- **Interactive pages:** `preview` shows an HTML file or a folder with `index.html` live in the app. Folders may mix html, css, es modules, `.wasm`, json and text/Python files the page fetches (the preview serves `.wasm` and `.mjs` with the right types and the page can fetch its siblings). It is static: no server-side Python/C++. To make those live, put them in the page: Python via Pyodide from a CDN, C/C++ via emscripten output next to `index.html`.
- The Platform check has a "Compilers & packs" row; the Capability check has a "Compile C++" task.

## The agent can ask you questions

`ask_user` pauses the run and shows a card with 1 to 4 multiple-choice questions (tap one, or several for multi-select, or type your own answer; Skip is always there). It is meant for ambiguous or expensive-to-undo choices, asked before building. It works in plan mode, sends a "Question for you" push if you are away, and in runs nobody is watching (Shortcut, MCP) the agent is told nobody can answer and picks a default. In Telegram there are no tap cards, so it asks in plain text instead.

## Verify gate

A turn that changed code files may not end on "done" until a real check ran after the last change and passed (tests, the program, a build; for web pages, opening the preview or browser). If not, the agent is sent back to run one (up to twice, `[system] You changed code…`); after a failing check it is told to fix and re-run. If it still never verifies, the answer is flagged "⚠ Not verified". It can opt out honestly by starting its answer with `Unverified:` and saying why. Plan mode and sub-agents are exempt; `VERIFY_GATE=0` switches it off.

## Capability check options

Before a run you pick the **effort level** (Off / Medium / High reasoning) and the **suite** (Standard, Hard, or Both). Every finished run is kept in "Past runs" (model, effort, suite, score, failed tasks) so you can compare models and effort levels side by side.

## Repo map and symbol search

`repo_map` returns a compact map of the workspace: every source file with its classes and functions (line numbers, signatures), the most-used files first, with a folder argument to zoom in. `find_symbol` shows where a name is defined and where it is used. Python is parsed exactly; JS/TS, Go, Rust, Java/Kotlin, C/C++, Ruby and PHP use light patterns (good, not perfect). Both are read-only (also in plan mode and for sub-agents) and the index rebuilds only when files change.

## Hard suite

The Capability check's "Hard" suite has 12 realistic tasks (bugs across files, implement-from-spec with a performance bar, project-wide rename with a decoy, CLI flags, import cycle, a big-codebase needle, C++ compiled and timed, SQL report, JavaScript with `node --test`, security holes, a broken Makefile). The agent never sees the checkers; test files are hash-locked so editing them to pass fails. `tests/hard_suite_test.py` proves every checker fails on the starting files and passes on a reference solution, so a score means something. Tasks get 10 minutes each.

## What's new, timestamps, skills

- **What's new:** `frontend/changelog.json` lists user-visible changes (newest id highest). After an update the app shows the entries you haven't seen once, in a sheet; Settings → What's new reopens them. The first launch only records the current id. Add an entry whenever you ship something people will notice (the self-edit prompt tells the agent to).
- **Sense of time:** the model sees `[YYYY-MM-DD HH:MM]` on your messages and its own earlier replies, `[HH:MM:SS · 8s]` (finish time and duration) on tool results, "Now:" with your time zone in the system prompt, and the start time on routine runs and background jobs. Your time zone comes from your phone with each message; stored chats are not changed (stamps are added to a copy before each model call, and a stamp the model copies is stripped).
- **Skills:** `skill_create` saves reusable instructions to the state repo (you read and approve the text every time; text that looks like a secret is refused). They show up in every chat next to the built-in skills; Settings → Skills lists and deletes them, and backups include them.
- **Digital store (Lemon Squeezy):** Settings → Lemon Squeezy. Create an API key in Lemon Squeezy (Settings → API), set `LEMONSQUEEZY_API_KEY` (optional `LEMONSQUEEZY_STORE_ID`) in Render, redeploy. Lemon Squeezy is the merchant of record (tax, file delivery) and its docs list Turkey for bank payouts; check your own account is approved. Tools: `ls_read` (store totals, products, variants, orders, customers), `ls_checkout` (payment links), `ls_refund` (**asks every time**). Its API is read-only for products, so you create each product once in their dashboard and the agent writes the listing kit to paste. The built-in `digital-store` skill is the playbook: niche research with evidence and kill criteria, build and test the product, listing kit, a static storefront via `publish_pages`, traffic drafts, daily/weekly routines. Tested against a fake API (`tests/lemonsqueezy_test.py`).
- **Etsy shop:** Settings → Etsy. Create an app at etsy.com/developers, register the redirect URI the sheet shows, set `ETSY_API_KEY` (and `ETSY_SHARED_SECRET` if Etsy gives one) in Render, redeploy, tap *Connect Etsy* (official OAuth, no password stored). Tools: `etsy_read` (shop, listings, orders, reviews, category search), `etsy_draft` (invisible draft with the digital file and images) and `etsy_update` (visible changes, **asks every time**). The built-in `etsy-shop` skill holds the workflow: research, listing copy rules, images, weekly review. Written from the public v3 API shape and tested against a fake Etsy server (`tests/etsy_test.py`); the API has no messaging, so buyer replies are drafted for you.
- **Install skills from GitHub:** Settings → Skills → *Install from GitHub*. Paste `owner/repo` or a github.com folder link; it lists every `SKILL.md` it finds (plus the text files next to each one), shows the full text and flags risky wording (pipe-to-shell, "ignore previous instructions", hidden base64, key-sending), and saves only what you tick, pinned to the commit it read. Bundled files are readable with `skill(name, file=...)`. The same installer also reads the rest of a Claude plugin: `commands/*.md` become slash commands (`$ARGUMENTS`, `$1`..`$9`; `!`shell`` lines are not run), `agents/*.md` become named sub-agents the agent can delegate to with `task(agent=...)`, `hooks/hooks.json` PreToolUse/PostToolUse entries arrive **switched off** (you read and enable each; JSON on stdin, exit code 2 blocks; a hook that calls a script inside the plugin installs the plugin's text files with it (you read them in the preview) and runs them with `${CLAUDE_PLUGIN_ROOT}` pointing there; hooks on other events are refused), and `.mcp.json` remote https servers become Connectors (stdio and SSE servers are listed as unsupported). Manage everything under Settings → Skills. Backups include commands and agents, never hooks. Installing is a UI action only: the agent cannot pull skills in on its own. Set `GITHUB_TOKEN` for private repos and a higher API rate limit.


### It knows what it can do
If the model says "I can't / I don't have a way to…" and a tool in the live registry does exactly that, the harness sends the answer back once with the matching tools listed (`backend/app/denial.py`). A read-only `capabilities` tool lists every tool from the live registry (and which ones ask you each time), and the Capability check has a "Knows its own tools" task so regressions show up.

### Live servers and publishing
- `serve` starts a dev server (Flask, Express, `http.server`…) on a free port (`$PORT`, 127.0.0.1 only) and the app opens it live through a token-guarded reverse proxy (`/live/…`, sandboxed like static previews). Pages must use relative URLs. One server per chat, killed with `serve_stop`.
- `publish_pages` pushes a static folder (needs `index.html`) to the `gh-pages` branch of a public repo you own (created if missing), turns Pages on and returns the URL. It asks every time, in every mode.
- Tests: `tests/serve_pages_test.py`.
- The Capability check lists every task with a checkbox (All / None); unticked tasks are skipped and the run says how many were excluded. API: `skip` list on `POST /api/eval`, catalog at `GET /api/eval-tasks`.

### More than one API key
Set `NVIDIA_API_KEYS=key2,key3` for multiple NVIDIA credentials or `KIMI_API_KEYS=key2,key3` for multiple Kimi credentials; in both cases the singular `*_API_KEY` is included first. Keys are de-duplicated, requests are distributed to the least-busy key, and a key that returns HTTP 429 is cooled down while another available key is tried immediately. Each key has its own local RPM window; configure `LLM_RPM` to match the per-key limit you intend to enforce. Test: `tests/keys_test.py`.

### Tool retrieval (why it stops denying its tools)
Telling a model "you have X" in a prompt is what weak models ignore. `backend/app/toolhint.py` does it from the harness instead: before every model call the user's request is matched (names, descriptions, an intent/synonym table) against the *live* tool registry, and the best matches are appended to the system prompt as "Tools that fit this request". Tools that aren't registered are never suggested. Safety nets: the `capabilities` tool (live list), the denial guard (`denial.py`), and the "Knows its own tools" check. Add synonyms in `toolhint.SYN` when a new tool needs plain-language triggers.

### Browser for games
`browser_act` has `hold{keys, ms}` (several keys at once), `mouse{kind: look|move|drag|down|up}` (relative look works under pointer lock) and the browser idles out after 20 minutes (`BROWSER_IDLE_SECONDS`). Each step is a model round trip, so slow games work and fast real-time ones don't. Test: `tests/browser_game_test.py`.

### Turso (durable state) and watchers
- `TURSO_URL` + `TURSO_TOKEN` (Render env) switch on a free Turso database (libSQL over HTTP, no extra dependency). Every small JSON document (routines, skills, connectors, eval history…) is written there first (no git commit per write) and mirrored to the GitHub state repo; at boot Turso wins, and a first run seeds it from the git copy. If Turso is down, writes still land locally and on GitHub. Platform check shows its status.
- A tiny keepalive write goes to Turso once a day so the free database is never archived for inactivity.
- A Capability check in progress is checkpointed after each task and **resumes after a redeploy** (max 3 resumes).
- **Watchers**: `watch_create` (asks every time). `kind=url` fetches a public page on a schedule with no model cost and pushes on `change`, `contains:<text>` or `absent:<text>`; `kind=prompt` is a read-only *quiet routine* that answers `NOTHING` when there is no news (no notification). `watch_list`, `watch_remove`. Tests: `tests/turso_test.py`, `tests/watchers_test.py`.

### Standing memory, connector catalog, Shortcut recipes
- **Remember on its own** (`autoremember.py`, switch in the Memory screen, on by default): after each finished turn a cheap background call extracts up to 3 lasting facts from *your* message (never the assistant's reply, tool output or routine/system text), skips secrets, and files them tagged "remembered on its own". A fact with a date becomes a push reminder the day before and the morning of (09:00 in your time zone; a reminder the server slept through is not sent late). Test: `tests/autoremember_test.py`.
- **Connectors**: `mcp.CATALOG` lists the official remote servers seen on public lists (Notion, Linear, Stripe via OAuth; GitHub via token), shown as one-tap "Popular apps" and given to the agent so it stops guessing URLs. Google Calendar/Gmail have no official remote MCP server we could verify; the client speaks streamable HTTP only (not the old SSE transport).
- **Shortcut**: the Back-tap sheet now has three recipes (voice with Speak Text, screenshot, share sheet). Sending the text `ping` returns "Connected ✓" without a model call, to test the token.
