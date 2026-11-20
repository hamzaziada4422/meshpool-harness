"""Which built-in layouts Forge offers: render every shipped layout once with filled slots and a full argument column,
validate, and write the ones that pass the layout rules to app/engines/forge/data/layouts_offered.json.

A layout passes when no slot is cut to fit and P1, P5, P7, P8, P17 and P20 pass on its slide. Deck-level rules (P12,
P13, P15, P16) are left out: the gate deck repeats one message on purpose. Needs LibreOffice (P1 and P20 measure the
render). Run from the repo root after changing layouts.py, parts.p_text or data/layouts.json:

    MH_ALLOW_SYSTEM_SOFFICE=1 MH_FETCH_LOGOS=0 python testkit/forge_layout_gate.py /tmp/gate
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from engines.forge import layouts as LY  # noqa: E402
from engines.forge import render as R  # noqa: E402
from engines.forge import validate_pptx as VP  # noqa: E402

RULES = ("P1", "P5", "P7", "P8", "P17", "P20")
WORDS = "Revenue grows faster than costs across the core markets every quarter this year".split()
BULLETS = ["Unit costs fell 12% while list prices held, so gross margin rose four points in two years",
           "Two markets carry most of the gain, and both still have room to add volume next year",
           "Holding prices and pushing volume adds about $9M of profit if costs keep falling",
           "The risk is a price war in the south, where a rival cut prices 8% this spring"]


def slot_text(role: str, max_words: int) -> str:
    if role in ("number", "value"):
        return "42%"
    k = 2 if role in ("label", "heading", "tag", "axis", "legend", "caption") else min(max_words or 8, 8)
    return " ".join(WORDS[:max(1, min(k, max_words or k))])


def slots(lay: dict) -> dict:
    out = {}
    for i, (role, _holds, mw) in enumerate(lay["slots"], 1):
        if role == "chart":
            out[str(i)] = {"kind": "column", "categories": ["2021", "2022", "2023"],
                           "series": [{"name": "Sales", "values": [10, 14, 18]}]}
        elif role == "table":
            out[str(i)] = [["Item", "2022", "2023"], ["Alpha", "1", "2"], ["Beta", "3", "4"], ["Gamma", "5", "6"]]
        else:
            out[str(i)] = slot_text(role, mw)
    return out


def gate_slide(lay: dict, k: int) -> dict:
    return {"archetype": "layout", "layout": lay["id"], "title": f"Revenue grows faster than costs in market {k}",
            "slots": slots(lay), "takeaway": {"text": "Margins widen as the core scales"},
            "bullets": [{"text": b} for b in BULLETS], "source": {"text": "Source: company accounts 2021-2025"}}


def failures(lays: list, out: str) -> dict:
    """{layout id: sorted failing checks} for a deck of these layouts, one slide each."""
    res = R.render([gate_slide(x, i) for i, x in enumerate(lays, 1)], out, carry=False, density="balanced",
                   fix_labels=False)
    bad = collections.defaultdict(set)
    for e in res["errors"]:
        m = re.match(r"(?i)slide (\d+)", e)
        if m:
            bad[int(m.group(1))].add("fit")
    v = VP.validate(out, VP.load_rules(), render=True, density="balanced")
    for f in v["failures"]:
        if f["rule"] in RULES:
            for m in re.finditer(r"(?i)slides? (\d+)", str(f.get("message", ""))):
                bad[int(m.group(1))].add(f["rule"])
    return {lays[k - 1]["id"]: sorted(r) for k, r in bad.items() if 1 <= k <= len(lays)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", help="a scratch folder for the gate deck")
    a = ap.parse_args(argv)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    lays = list(LY.load())
    bad = failures(lays, str(Path(a.out) / "gate.pptx"))
    ok = [x["id"] for x in lays if x["id"] not in bad]
    LY.OFFERED.write_text(json.dumps({
        "about": "Built-in layouts offered to the model: those that pass P1, P5, P7, P8, P17 and P20 with no slot cut, "
                 "measured by testkit/forge_layout_gate.py. The others still render when a deck names them.",
        "ids": ok}, indent=1) + "\n")
    print(f"offered {len(ok)} of {len(lays)}; held back: "
          + json.dumps(collections.Counter(r for v in bad.values() for r in v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
