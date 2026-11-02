"""Minimal client for remote (HTTP) MCP servers. Tools show up as mcp__<server>__<tool>."""
import json
import re

import httpx

from . import config, kv, oauth


_transport = None   # tests inject a fake transport


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

    def counts(self) -> dict:
        c: dict = {}
        for t in self.tools.values():
            c[t["server"]["name"]] = c.get(t["server"]["name"], 0) + 1
        return c

    def search(self, query: str, limit: int = 8) -> list:
        """Keyword search over tool names/descriptions. Returns [(full_name, def)], best first."""
        toks = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1]
        if not toks:
            return []
        scored = []
        named = {t["server"]["name"].lower() for t in self.tools.values()} & set(toks)   # "notion ..." means only Notion's tools
        for full, t in self.tools.items():
            if named and t["server"]["name"].lower() not in named:
                continue
            d = t["def"]
            srv = t["server"]["name"].lower()
            name = re.sub(r"[^a-z0-9]+", " ", t["name"].lower())
            desc = (d.get("description") or "").lower()
            sc = 0
            for w in toks:
                if w == srv:
                    sc += 2
                if w in name:
                    sc += 3
                if w in desc:
                    sc += 1
            if sc:
                scored.append((-sc, full, d))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [(f, d) for _, f, d in scored[:limit]]

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
