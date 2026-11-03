"""Capability-denial guard: when the model says "I can't / I don't have a way to X" but a tool
in the live registry does X, send it back once with the matching tools listed."""
import re

DENY = re.compile(
    r"\b(i\s+(?:can(?:no|')?t|cannot|am\s+not\s+able\s+to|am\s+unable\s+to|'m\s+unable\s+to|'m\s+not\s+able\s+to|don'?t\s+have\s+(?:the\s+)?(?:ability|access|a\s+way|any\s+way|tools?|permission)|do\s+not\s+have\s+(?:the\s+)?(?:ability|access|a\s+way|tools?)|have\s+no\s+(?:way|tool|ability|access))"
    r"|(?:there\s+is|there's)\s+no\s+(?:way|tool)|not\s+something\s+i\s+can|(?:isn't|is\s+not)\s+(?:something\s+)?(?:possible|supported)\s+(?:for\s+me|here)|only\s+propos\w+)", re.I)
STOP = set("the a an and or to of for in on it is be can you your this that with from into not are was have has will would could should just also any all can't cannot able ability unable way tool tools use using me my i add new one more other than then them they their its if as at by do does don't".split())
WORD = re.compile(r"[a-z][a-z_]{2,}")


def _stem(w: str) -> str:
    for suf in ("ies", "es", "s", "ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:-len(suf)] + ("y" if suf == "ies" else "")
    return w


def _words(s: str) -> set:
    return {_stem(w) for w in WORD.findall((s or "").lower()) if w not in STOP}


def find(text: str, specs: list, used: set = frozenset(), limit: int = 4) -> list:
    """Return [(tool_name, description)] the denial contradicts, [] if it looks fine."""
    text = text or ""
    m = DENY.search(text)
    if not m:
        return []
    # look at the sentence(s) holding the denial
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    sent = text[start:min(ends) if ends else len(text)]
    low = text.lower()
    tools = {}
    for sp in specs:
        f = sp.get("function") or {}
        if f.get("name"):
            tools[f["name"]] = f.get("description") or ""
    hits = []
    for n, d in tools.items():
        if n in used:
            continue
        if re.search(r"\b" + re.escape(n) + r"\b", low):          # it even names the tool while denying
            hits.append((10, n, d))
            continue
        # strong overlap: tool-name parts (connector_add -> connector, add) plus description words
        sw = _words(sent)
        nw = {_stem(p) for p in n.lower().split("_") if p not in STOP and len(p) > 2}
        dw = _words(d)
        name_hit = len(nw & sw)
        desc_hit = len((dw - nw) & sw)
        score = name_hit * 2 + desc_hit
        if name_hit >= 1 or desc_hit >= 3:
            hits.append((score, n, d))
    hits.sort(key=lambda x: -x[0])
    return [(n, d) for _, n, d in hits[:limit]]


def nudge(found: list) -> str:
    lines = "\n".join(f"- {n}: {d[:160]}" for n, d in found)
    return ("[system] You just said you can't do something, but your tool list says otherwise. Matching tools you HAVE right now:\n"
            + lines + "\nCall `capabilities` if unsure what else you can do. If one of these does what the user asked, use it now "
            "(tools that need approval will ask the user, that's fine). Only repeat the refusal if none of them fits, and then say exactly why.")
