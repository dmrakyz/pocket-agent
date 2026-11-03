"""Repo map and symbol search: a compact map of what is defined where, so the agent finds the right code without reading everything.

Python is parsed with `ast` (exact). Other languages use light regexes (good, not perfect). Files are ranked by how often their
symbols are used from other files, so the map shows the load-bearing code first. The index is rebuilt only when files change."""
import ast
import os
import re
from collections import Counter
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache", "target", "vendor", ".idea", ".mypy_cache", ".pytest_cache", "site-packages"}
EXTS = {".py": "py", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".go": "go", ".rs": "rs", ".java": "jvm", ".kt": "jvm",
        ".cs": "jvm", ".swift": "jvm", ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".cxx": "c", ".rb": "rb", ".php": "php"}
MAX_FILES, MAX_BYTES = 1500, 400_000
KEYWORDS = {"if", "for", "while", "switch", "return", "else", "catch", "try", "do", "new", "delete", "sizeof", "throw", "case", "using", "namespace", "typedef"}
GENERIC = {"main", "init", "index", "data", "name", "path", "text", "value", "type", "list", "save", "load", "read", "write", "close", "open", "start", "stop", "run", "test",
           "handle", "state", "public", "check", "call", "send", "delete", "update", "create", "setup", "__init__", "self", "None", "True", "False"}
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

RX = {
    "py": [("class", re.compile(r"^\s*class\s+(\w+)")), ("function", re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"))],   # only used when a file does not parse
    "js": [("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)")),
           ("interface", re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+(\w+)")),
           ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)\s*\(")),
           ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>")),
           ("method", re.compile(r"^\s{2,}(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|, ]+)?\s*\{"))],
    "go": [("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)")), ("type", re.compile(r"^type\s+(\w+)\s+(?:struct|interface)"))],
    "rs": [("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)")), ("type", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|impl(?:<[^>]*>)?)\s+(\w+)"))],
    "jvm": [("class", re.compile(r"^\s*(?:(?:public|private|protected|internal|abstract|final|open|sealed|data|static)\s+)*(?:class|interface|enum|object|struct|protocol|record)\s+(\w+)")),
            ("method", re.compile(r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|override|open|suspend|async)\s+)*(?:fun|func|void|[\w<>\[\],?]+)\s+(\w+)\s*\([^;{]*\)\s*(?::\s*[\w<>?,\[\] ]+)?\s*(?:\{|=)"))],
    "c": [("type", re.compile(r"^\s*(?:typedef\s+)?(?:class|struct|enum|union)\s+(\w+)\s*(?:[:{]|$)")),
          ("function", re.compile(r"^(?!\s)(?:[A-Za-z_][\w:<>,\*&\s]*?[\s\*&])(\w+)\s*\([^;{]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:\{|$)"))],
    "rb": [("class", re.compile(r"^\s*(?:class|module)\s+([\w:]+)")), ("function", re.compile(r"^\s*def\s+(?:self\.)?(\w+[?!]?)"))],
    "php": [("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+(\w+)")), ("function", re.compile(r"^\s*(?:(?:public|private|protected|static)\s+)*function\s+(\w+)"))],
}

_CACHE: dict[str, dict] = {}


def _py_symbols(text: str):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    out = []

    def sig(n):
        a = n.args
        names = [x.arg for x in getattr(a, "posonlyargs", []) + a.args]
        if a.vararg:
            names.append("*" + a.vararg.arg)
        names += [x.arg for x in a.kwonlyargs]
        if a.kwarg:
            names.append("**" + a.kwarg.arg)
        return f"({', '.join(names)[:80]})"

    def walk(body, parent=""):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(("method" if parent else "function", (parent + "." if parent else "") + n.name, n.lineno, ("async " if isinstance(n, ast.AsyncFunctionDef) else "") + "def " + n.name + sig(n)))
            elif isinstance(n, ast.ClassDef):
                out.append(("class", (parent + "." if parent else "") + n.name, n.lineno, "class " + n.name))
                walk(n.body, (parent + "." if parent else "") + n.name)
            elif isinstance(n, ast.Assign) and not parent:
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 2:
                        out.append(("const", t.id, n.lineno, t.id + " = …"))
    walk(tree.body)
    return out


def _rx_symbols(text: str, lang: str):
    out = []
    pats = RX.get(lang, [])
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 300:
            continue
        for kind, rx in pats:
            m = rx.match(line)
            if m and m.group(1) not in KEYWORDS:
                out.append((kind, m.group(1), i, line.strip()[:110]))
                break
    return out


def _signature(root: Path):
    sig, files = 0, []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fn:
            ext = os.path.splitext(f)[1].lower()
            if ext in EXTS:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size <= MAX_BYTES:
                    files.append((p, EXTS[ext], st))
                    sig = (sig * 31 + int(st.st_mtime_ns) + st.st_size) % (1 << 61)
                if len(files) >= MAX_FILES:
                    return sig, files
    return sig, files


def index(root: Path) -> dict:
    root = root.resolve()
    sig, files = _signature(root)
    c = _CACHE.get(str(root))
    if c and c["sig"] == sig:
        return c
    defs: dict[str, list] = {}      # path -> symbols
    toks: dict[str, Counter] = {}
    for p, lang, _ in files:
        try:
            text = Path(p).read_text("utf-8", "replace")
        except OSError:
            continue
        rel = os.path.relpath(p, root)
        syms = _py_symbols(text) if lang == "py" else None
        if syms is None:
            syms = _rx_symbols(text, lang)
        defs[rel] = syms
        toks[rel] = Counter(TOKEN.findall(text))
    by_name: dict[str, set] = {}
    for rel, syms in defs.items():
        for kind, name, line, s in syms:
            by_name.setdefault(name.split(".")[-1], set()).add(rel)
    score: Counter = Counter()
    for rel, tk in toks.items():
        for name, owners in by_name.items():
            # generic names (index, get, save, main…) are defined everywhere and say nothing about importance
            if len(owners) > 3 or len(name) < 4 or name in GENERIC or not tk.get(name):
                continue
            for o in owners:
                if o != rel:
                    score[o] += 1.0 / len(owners)
    c = {"sig": sig, "defs": defs, "score": score, "files": len(files), "root": root}
    _CACHE[str(root)] = c
    return c


def repo_map(root: Path, sub: str = "", budget: int = 6000) -> str:
    c = index(root)
    prefix = sub.strip("/")
    items = [(rel, syms) for rel, syms in c["defs"].items() if not prefix or rel == prefix or rel.startswith(prefix + "/")]
    if not items:
        return "No source files found" + (f" under {sub!r}" if sub else "") + " (looked for " + ", ".join(sorted({e[1:] for e in EXTS})) + ")."
    items.sort(key=lambda it: (-c["score"].get(it[0], 0), it[0]))
    lines, used, shown = [], 0, 0
    for rel, syms in items:
        head = rel + (f"  (used from {int(c['score'][rel] + 0.5)} place{'s' if int(c['score'][rel] + 0.5) != 1 else ''})" if c["score"].get(rel, 0) >= 1 else "")
        body, consts = [], 0
        for kind, name, line, s in syms[:60]:
            if kind == "const":
                consts += 1
                if consts > 4:
                    continue
            body.append(("  " if kind != "method" else "    ") + f"{line}: {s}")
        extra = f"\n  … {len(syms) - 60} more" if len(syms) > 60 else ""
        chunk = head + ("\n" + "\n".join(body) if body else "  (no definitions found)") + extra
        if used + len(chunk) > budget and shown:
            break
        lines.append(chunk); used += len(chunk) + 1; shown += 1
    rest = len(items) - shown
    return (f"{len(items)} source files" + (f" under {sub}" if sub else "") + ", most-used first:\n" + "\n".join(lines)
            + (f"\n… {rest} more files not shown (call repo_map with a sub-folder path)" if rest > 0 else ""))


def find_symbol(root: Path, name: str, limit: int = 40) -> str:
    c = index(root)
    name = name.strip()
    if not name:
        return "give a symbol name"
    exact, fuzzy = [], []
    low = name.lower()
    for rel, syms in c["defs"].items():
        for kind, sname, line, s in syms:
            short = sname.split(".")[-1]
            if short == name or sname == name:
                exact.append((rel, line, kind, s))
            elif low in sname.lower():
                fuzzy.append((rel, line, kind, s))
    out = []
    hits = exact or fuzzy
    if hits:
        out.append(("Definitions" if exact else f"No exact match; similar names") + ":")
        out += [f"  {r}:{l}  [{k}] {s}" for r, l, k, s in hits[:limit]]
    else:
        out.append(f"No definition named {name!r}.")
    # references: whole-word uses, outside the definition lines
    rx = re.compile(r"\b" + re.escape(name.split(".")[-1]) + r"\b")
    defsat = {(r, l) for r, l, _, _ in hits}
    refs, total = [], 0
    for rel in sorted(c["defs"], key=lambda r: -c["score"].get(r, 0)):
        try:
            for i, line in enumerate((c["root"] / rel).read_text("utf-8", "replace").splitlines(), 1):
                if rx.search(line) and (rel, i) not in defsat:
                    total += 1
                    if len(refs) < limit:
                        refs.append(f"  {rel}:{i}  {line.strip()[:120]}")
        except OSError:
            continue
        if total > 400:
            break
    out.append(f"References ({total}{'+' if total > 400 else ''}):" if refs else "References: none found")
    out += refs
    if total > len(refs):
        out.append(f"  … {total - len(refs)} more; grep for a narrower pattern")
    return "\n".join(out)
