"""Runs ONE forged tool in a throwaway process. Reads {"code","args","network"} from stdin, prints a single result line.
Best-effort containment, not a security boundary: stripped environment, memory/CPU limits, scratch directory, and no sockets unless the tool declared network use."""
import json
import os
import sys

MARK = "\x00RESULT\x00"


def main():
    req = json.loads(sys.stdin.read())
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (req.get("cpu", 15), req.get("cpu", 15)))
        resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024, 20 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except Exception:  # noqa: BLE001
        pass
    if not req.get("network"):
        import socket

        def blocked(*a, **k):
            raise PermissionError("this tool did not declare network access")
        socket.socket = blocked                      # type: ignore[assignment]
        socket.create_connection = blocked           # type: ignore[assignment]
        socket.getaddrinfo = blocked                 # type: ignore[assignment]
    real_stdout = sys.stdout
    sys.stdout = sys.stderr                          # prints from the tool must not corrupt the result line
    try:
        ns: dict = {"__name__": "forged_tool"}
        exec(compile(req["code"], "forged_tool.py", "exec"), ns)
        fn = ns.get("run")
        if not callable(fn):
            raise RuntimeError("the code must define run(args)")
        out = fn(req.get("args") or {})
        if not isinstance(out, (str, dict, list, int, float, bool, type(None))):
            out = repr(out)
        res = {"ok": True, "result": out}
    except BaseException as e:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:600]}
    real_stdout.write(MARK + json.dumps(res, default=str) + "\n")
    real_stdout.flush()


if __name__ == "__main__":
    os.chdir(os.environ.get("FORGE_CWD") or ".")
    main()
