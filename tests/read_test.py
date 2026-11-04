"""read tool: never silently drops the middle of a big file; hints always match what was shown."""
import asyncio, os, re, sys, tempfile
from pathlib import Path
os.environ["APP_PASSWORD"] = "x"; os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app import tools

root = Path(tempfile.mkdtemp())
(root / "big.html").write_text("\n".join(f"line {i} " + "x" * 80 for i in range(1, 4508)))      # ~4.5k lines, ~400 KB
(root / "min.js").write_text("a" * 50000 + "\nsecond line\n")
(root / "small.txt").write_text("one\ntwo\nthree\n")
ctx = tools.Ctx({}, root, None)

async def main():
    seen = set(); off = 1; calls = 0
    while True:
        out = await tools.t_read(ctx, {"path": "big.html", "offset": off}); calls += 1
        assert "characters truncated" not in out, "generic cap must never chop the middle"
        nums = [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\t", out, re.M)]
        assert nums == list(range(nums[0], nums[-1] + 1)) and nums[0] == off, "contiguous lines starting at offset"
        seen |= set(nums)
        m = re.search(r"showed lines (\d+)-(\d+) of (\d+).*offset=(\d+)", out)
        if not m:
            break
        assert int(m.group(1)) == nums[0] and int(m.group(2)) == nums[-1] and int(m.group(4)) == nums[-1] + 1
        off = nums[-1] + 1
    assert seen == set(range(1, 4508)) and calls > 1, (len(seen), calls)
    out = await tools.t_read(ctx, {"path": "min.js"})
    assert "more characters on this line" in out and "second line" in out and len(out) < 5000
    assert "showed lines" not in await tools.t_read(ctx, {"path": "small.txt"})
    assert "past the end" in await tools.t_read(ctx, {"path": "small.txt", "offset": 99})
    print("read OK", calls, "calls cover", len(seen), "lines")
asyncio.run(main())
