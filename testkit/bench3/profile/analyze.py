"""Break the captured requests down by component.  python analyze.py [captures_dir]  -> prints a JSON-ish summary and writes
dumps/<arm>.req<N>.<part>.txt (full text of each message) so the pieces can be read."""
import json
import re
import sys
from pathlib import Path

CAP = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "captures")
DUMP = CAP.parent / "dumps"
DUMP.mkdir(exist_ok=True)


def text_of(c):
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    out = []
    for p in c:
        if isinstance(p, dict):
            out.append(p.get("text") or (f"[{p.get('type')}:{len(json.dumps(p))}]" if p.get("type") != "text" else ""))
        else:
            out.append(str(p))
    return "\n".join(out)


def tools_of(b):
    res = []

    def walk(ts, ns=""):
        for t in ts or []:
            if t.get("type") == "namespace":
                walk(t.get("tools"), t.get("name"))
                continue
            f = t.get("function") if isinstance(t.get("function"), dict) else t
            res.append({"name": (ns + "." if ns else "") + str(f.get("name") or t.get("type")), "chars": len(json.dumps(t, separators=(",", ":"))),
                        "desc": len(f.get("description") or ""), "schema": len(json.dumps(f.get("parameters") or f.get("format") or {}, separators=(",", ":")))})
    walk(b.get("tools"))
    for it in b.get("input") if isinstance(b.get("input"), list) else []:
        if isinstance(it, dict) and it.get("type") == "additional_tools":
            walk(it.get("tools"))
    return res


def parts_of(b):
    """[(label, text)] of everything that is not a tool schema."""
    parts = []
    if b.get("instructions"):
        parts.append(("instructions", b["instructions"]))
    for i, m in enumerate(b.get("messages") or []):
        t = text_of(m.get("content"))
        if m.get("tool_calls"):
            t += json.dumps(m["tool_calls"])
        parts.append((f"msg{i}.{m.get('role')}", t))
    inp = b.get("input")
    if isinstance(inp, str):
        parts.append(("input", inp))
    for i, it in enumerate(inp if isinstance(inp, list) else []):
        if it.get("type") == "additional_tools":
            continue
        t = text_of(it.get("content")) if "content" in it else json.dumps({k: v for k, v in it.items() if k not in ("id",)})
        parts.append((f"in{i}.{it.get('role') or it.get('type')}", t))
    return parts


def main():
    out = {}
    for f in sorted(CAP.glob("*.jsonl")):
        arm = f.stem
        for l in f.read_text().splitlines():
            r = json.loads(l)
            b = r["body"]
            if "/v1/" not in r["path"]:
                continue
            tl = tools_of(b)
            parts = parts_of(b)
            for lab, t in parts:
                (DUMP / f"{arm}.req{r['seq']}.{lab}.txt").write_text(t)
            out[f"{arm}#{r['seq']}"] = {
                "bytes": r["bytes"], "path": r["path"].split("/v1")[1],
                "n_tools": len(tl), "tools_chars": sum(t["chars"] for t in tl), "tools_desc": sum(t["desc"] for t in tl),
                "tools_schema": sum(t["schema"] for t in tl),
                "text_parts": [(lab, len(t)) for lab, t in parts], "text_chars": sum(len(t) for _, t in parts),
                "top_tools": sorted(((t["name"], t["chars"]) for t in tl), key=lambda x: -x[1])[:15],
                "rest": {k: len(json.dumps(v)) for k, v in b.items() if k not in ("tools", "messages", "input")}}
    print(json.dumps(out, indent=1))
    (CAP.parent / "summary.json").write_text(json.dumps(out, indent=1))


main()
