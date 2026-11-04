"""Per-request tool retrieval. A weak model forgets what it can do, so the harness remembers for it: before every model
call the user's request is matched against the LIVE tool registry (names, descriptions, synonyms) and the best matches
are put in front of the model: "Tools that fit this request". This is what a tool-search step does in large agent setups."""
import math
import re
from collections import Counter

from .denial import _stem, STOP, WORD

CORE = {"read", "write", "edit", "multiedit", "glob", "grep", "bash", "bash_output", "kill_bash", "todo_write", "task", "skill",
        "web_search", "web_fetch", "capabilities", "ask_user", "exit_plan_mode"}
# plain-words intents that never appear in a tool description; extend freely
SYN = {
    "connector_add": "mcp connector integration plugin app notion github linear spotify slack gmail calendar drive jira asana zapier server connect hook up link account",
    "connector_search": "mcp connector integration connected app notion github linear slack gmail calendar drive jira asana",
    "skill_create": "skill skills remember procedure workflow checklist house style playbook template reusable instructions teach learn recipe",
    "routine_create": "schedule scheduled routine recurring every daily weekly morning remind reminder cron repeat later automatically timer",
    "routine_update": "change reschedule pause resume routine schedule",
    "routine_list": "routines scheduled recurring list schedule",
    "world_create": "world simulation roleplay characters npc story town",
    "send_email": "email mail send message inbox notify",
    "ls_read": "lemon squeezy store sales orders revenue products customers payments income earnings digital store how much money",
    "ls_checkout": "checkout link buy link payment link sell product price page lemon squeezy purchase",
    "ls_refund": "refund money back order lemon squeezy",
    "etsy_read": "etsy shop store orders sales reviews listings revenue customers business income earnings how is my shop doing",
    "etsy_draft": "etsy listing list sell product upload new listing digital download template create listing draft shop",
    "etsy_update": "etsy edit listing price change tags title description publish unpublish activate deactivate sale",
    "forge_tool": "new tool build tool custom tool function plugin extend capability",
    "create_app": "app mini app tool widget tracker calculator counter timer dashboard page phone",
    "preview": "preview html page show website open page",
    "serve": "server flask express node fastapi backend api localhost dev server run web app live",
    "serve_stop": "stop server shut down server",
    "publish_pages": "publish deploy host github pages website public online share site",
    "pack_enable": "install numpy pandas scipy matplotlib torch compiler gcc clang emscripten wasm webassembly toolchain",
    "generate_image": "draw generate image picture illustration logo art create image",
    "image_search": "find pictures photos images search images",
    "fetch_image": "download image photo picture",
    "view_image": "look at image see picture photo screenshot describe image",
    "view_3d": "3d model stl obj glb viewer mesh",
    "memory_save": "remember memorize save fact preference note about me",
    "memory_forget": "forget delete memory remove remembered",
    "browser_read": "browse website open url page web screenshot look at site",
    "browser_act": "click type login form game keyboard mouse play press button fill sign in",
    "repo_map": "overview structure codebase architecture project map",
    "find_symbol": "where defined function class symbol definition usages",
    "git_sync": "commit push github save work sync",
    "create_pr": "pull request pr review merge",
    "share_file": "send file download give file deliver export",
    "models": "model switch compare llm",
    "watch_create": "watch monitor track notify tell me when alert let me know keep an eye price stock restock available drops changes updates news deadline",
    "watch_list": "watchers watching monitors what are you watching",
    "watch_remove": "stop watching unwatch remove watcher",
}
_idf: dict = {}


def _idf_for(specs: list) -> dict:
    key = len(specs)
    if _idf.get("key") != key:
        n = len(specs) or 1
        df = Counter()
        for sp in specs:
            f = sp["function"]
            df.update({w for w in _words(f["name"].replace("_", " ") + " " + (f.get("description") or ""))})
        _idf.clear()
        _idf.update({"key": key, "w": {w: math.log(1 + n / c) for w, c in df.items()}})
    return _idf["w"]


def _words(s: str) -> set:
    return {_stem(w) for w in WORD.findall((s or "").lower()) if w not in STOP}


def match(text: str, specs: list, limit: int = 4, floor: float = 3.0) -> list:
    """-> [(name, short description)] of tools that fit the request, best first."""
    q = _words(text)
    if not q:
        return []
    idf = _idf_for(specs)
    out = []
    for sp in specs:
        f = sp["function"]
        n = f["name"]
        if n.startswith("mcp__"):
            continue
        syn = _words(SYN.get(n, ""))
        name_w = _words(n.replace("_", " "))
        desc_w = _words((f.get("description") or "")[:400])
        score = 0.0
        score += 3.0 * sum(idf.get(w, 1.0) for w in q & syn) / 3
        score += 2.5 * sum(idf.get(w, 1.0) for w in q & name_w) / 2
        score += 0.6 * sum(idf.get(w, 1.0) for w in (q & desc_w) - syn - name_w) / 2
        if n in CORE:
            score *= 0.5            # the model uses these anyway; only surface them when the fit is strong
        if score >= floor:
            out.append((score, n, (f.get("description") or "").split(". ")[0][:150]))
    out.sort(key=lambda x: -x[0])
    return [(n, d) for _, n, d in out[:limit]]


def section(text: str, specs: list) -> str:
    m = match(text, specs)
    if not m:
        return ""
    return ("\nTOOLS THAT FIT THIS REQUEST (picked by the system from your live tool list; they are yours to call, and tools marked as needing approval just ask the user):\n"
            + "\n".join(f"- {n}: {d}" for n, d in m)
            + "\nDo not tell the user you can't do something one of these does. Call it.")
