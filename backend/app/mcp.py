"""Minimal client for remote (HTTP) MCP servers. Tools show up as mcp__<server>__<tool>."""
import json
import re

import httpx

from . import config, kv, oauth


_transport = None   # tests inject a fake transport


# Official remote MCP servers seen on public lists (not tested from here). Only streamable-HTTP ones: the client doesn't speak the old SSE transport.
CATALOG = [
    {"name": "notion", "title": "Notion", "url": "https://mcp.notion.com/mcp", "oauth": True, "about": "pages, databases, notes"},
    {"name": "linear", "title": "Linear", "url": "https://mcp.linear.app/mcp", "oauth": True, "about": "issues and projects"},
    {"name": "stripe", "title": "Stripe", "url": "https://mcp.stripe.com", "oauth": True, "about": "payments and customers (read carefully what you allow)"},
    {"name": "github", "title": "GitHub", "url": "https://api.githubcopilot.com/mcp/", "oauth": False, "auth_hint": "Bearer <a GitHub personal access token>", "about": "repos, issues, PRs"},
]
CATALOG_NOTE = ("Known official servers: " + "; ".join(f"{c['title']} {c['url']}" + (" (OAuth)" if c["oauth"] else " (token)") for c in CATALOG) +
                ". Google Calendar/Gmail have no official remote MCP server we could verify: say so instead of guessing a URL.")


class NeedsSignIn(Exception):
    def __init__(self):
        super().__init__("this connector needs you to sign in again (Settings > Connectors)")


class MCP:
    def __init__(self):
        self.tools: dict[str, dict] = {}     # full name -> {server, tool, schema}
        self.sessions: dict[str, str] = {}
        self.report: dict[str, str] = {}

    async def _rpc(self, srv: dict, method: str, params: dict | None = None, notify: bool = False):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             **(srv.get("headers") or {})}
        if srv.get("oauth"):
            auth = await oauth.header(srv["name"])
            if not auth:
                raise NeedsSignIn()
            h["Authorization"] = auth
        if srv["name"] in self.sessions:
            h["Mcp-Session-Id"] = self.sessions[srv["name"]]
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            body["id"] = 1
        async with httpx.AsyncClient(timeout=60, transport=_transport) as c:
            r = await c.post(srv["url"], headers=h, json=body)
        if r.headers.get("Mcp-Session-Id"):
            self.sessions[srv["name"]] = r.headers["Mcp-Session-Id"]
        if notify or r.status_code == 202:
            return None
        r.raise_for_status()
        if "text/event-stream" in r.headers.get("content-type", ""):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:])
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == 1:
                        return self._result(msg)
            raise RuntimeError("no response in event stream")
        return self._result(r.json())

    @staticmethod
    def _result(msg: dict):
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        return msg.get("result")

    @staticmethod
    def servers() -> list:
        env = [dict(x, source="env") for x in config.mcp_servers()]
        names = {x["name"] for x in env}
        return env + [dict(x, source="app") for x in (kv.get("connectors", []) or []) if x.get("name") not in names]

    async def refresh(self) -> dict:
        old, fresh = self.tools, {}
        report = {}
        for srv in self.servers():
            try:
                await self._rpc(srv, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                    "clientInfo": {"name": "mobile-agent", "version": "1"}})
                await self._rpc(srv, "notifications/initialized", notify=True)
                res = await self._rpc(srv, "tools/list")
                n = 0
                for t in (res or {}).get("tools", []):
                    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", t["name"])
                    fresh[f"mcp__{srv['name']}__{safe}"] = {"server": srv, "name": t["name"], "def": t}
                    n += 1
                report[srv["name"]] = f"{n} tools"
            except NeedsSignIn:
                report[srv["name"]] = "needs sign-in"
            except httpx.HTTPStatusError as e:
                report[srv["name"]] = "needs sign-in" if e.response.status_code == 401 and srv.get("oauth") else f"error: HTTP {e.response.status_code}"
            except Exception as e:  # noqa: BLE001
                report[srv["name"]] = f"error: {type(e).__name__}: {e}"
            if srv["name"] not in {v["server"]["name"] for v in fresh.values()}:   # a hiccup must not make a server's tools vanish
                fresh.update({k: v for k, v in old.items() if v["server"]["name"] == srv["name"]})
        self.tools = fresh
        self.report = report
        return report

    def schemas(self, active=()) -> list[dict]:
        """Only tools the agent switched on with connector_search are sent to the model."""
        out = []
        on = set(active)
        for full, t in self.tools.items():
            if full not in on:
                continue
            d = t["def"]
            out.append({"type": "function", "function": {
                "name": full, "description": (d.get("description") or "")[:900],
                "parameters": d.get("inputSchema") or {"type": "object", "properties": {}}}})
        return out

    # ---- permissions: classify tools as read or write, per-connector policy lives in kv `connector_perms`
    WRITE_WORDS = frozenset("create update delete remove add set save move duplicate send post write edit archive upload attach convert "
                            "run execute merge close assign publish deploy invite revoke reply rename restore trash insert append "
                            "replace import apply cancel submit commit push reset clear".split())
    READ_WORDS = frozenset("get list search find fetch read query view describe whoami check lookup show count status resolve ask".split())

    def kind(self, full: str) -> str:
        """'read' or 'write'. Trust the server's readOnlyHint, else guess from the name, and treat unknowns as writes."""
        t = self.tools.get(full)
        if not t:
            return "write"
        ann = (t["def"].get("annotations") or {})
        if ann.get("readOnlyHint") is True:
            return "read"
        if ann.get("destructiveHint") is True:
            return "write"
        words = {self._stem(w) for w in re.findall(r"[a-z0-9]+", t["name"].lower())}
        if words & {self._stem(w) for w in self.WRITE_WORDS}:
            return "write"
        return "read" if words & self.READ_WORDS else "write"

    def perm(self, server: str) -> str:
        v = (kv.get("connector_perms", {}) or {}).get(server)
        return v if v in ("ask", "readonly", "trusted") else "ask"

    def counts(self) -> dict:
        c: dict = {}
        for t in self.tools.values():
            c[t["server"]["name"]] = c.get(t["server"]["name"], 0) + 1
        return c

    STOP = frozenset("a an the to of in on at for and or but my me i it its is are be do with from into as by this that these those "
                     "called named name please can you could would should some any all one use using".split())
    SYN = {"add": "create", "make": "create", "new": "create", "build": "create", "remove": "delete", "find": "search", "lookup": "search",
           "show": "get", "read": "get", "fetch": "get", "edit": "update", "change": "update", "modify": "update"}

    @staticmethod
    def _stem(w: str) -> str:
        return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w

    def search(self, query: str, limit: int = 10) -> list:
        """Keyword search over tool names/descriptions (whole words, filler words ignored).
        Returns [(full_name, def)], best first."""
        words = [self.SYN.get(self._stem(w), self._stem(w)) for w in re.findall(r"[a-z0-9]+", query.lower())]
        servers = {t["server"]["name"].lower() for t in self.tools.values()}
        named = servers & set(words)                       # "notion ..." means only Notion's tools
        toks = [w for w in words if w not in self.STOP and w not in named and len(w) > 1]
        scored = []
        for full, t in self.tools.items():
            srv = t["server"]["name"].lower()
            if named and srv not in named:
                continue
            d = t["def"]
            nw = [self.SYN.get(self._stem(w), self._stem(w)) for w in re.findall(r"[a-z0-9]+", t["name"].lower()) if w != srv]
            dw = {self._stem(w) for w in re.findall(r"[a-z0-9]+", (d.get("description") or "").lower())}
            sc = 0
            for w in toks:
                if w in nw:
                    sc += 4
                elif len(w) >= 3 and any(x.startswith(w) or w.startswith(x) and len(x) >= 4 for x in nw):
                    sc += 2
                if w in dw:
                    sc += 1
            if toks and sc and all(any(w == x for x in nw) for w in toks):
                sc += 3                                    # every word hit the name: almost certainly the tool
            if not toks and named:
                sc = 1                                     # just an app name: list its tools
            if sc:
                scored.append((-sc, full, d))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [(f, d) for _, f, d in scored[:limit]]

    def menu(self, hits: list, cap: int = 80) -> str:
        """Names of the other tools on the servers that were hit, so the model can see the whole menu and search again."""
        got = {h[0] for h in hits}
        out = []
        for srv in dict.fromkeys(self.tools[h[0]]["server"]["name"] for h in hits):
            rest = [full.split("__", 2)[-1] for full, t in self.tools.items() if t["server"]["name"] == srv and full not in got]
            if rest:
                out.append(f"Other {srv} tools (search for them by name to switch them on): " + ", ".join(rest[:cap]) + (" …" if len(rest) > cap else ""))
        return "\n".join(out)

    async def call(self, full: str, args: dict) -> str:
        t = self.tools.get(full)
        if not t:
            raise RuntimeError(f"unknown MCP tool {full}")
        res = await self._rpc(t["server"], "tools/call", {"name": t["name"], "arguments": args})
        parts = []
        for c in (res or {}).get("content", []):
            parts.append(c.get("text") if c.get("type") == "text" else f"[{c.get('type')} content]")
        text = "\n".join(p for p in parts if p) or json.dumps(res)
        if (res or {}).get("isError"):
            text = "Error: " + text
        return text
