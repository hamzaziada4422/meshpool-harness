"""Every everyday grader fails on the untouched inputs, passes on a reference solution, and never writes into the
workspace it grades.

    RW_TASK_PYTHON=/path/to/python-with-flask python -m pytest -q -p no:cacheprovider testkit/realworld/tests
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from testkit import gradekit as gk  # noqa: E402
from testkit.realworld import make_fixtures as mf  # noqa: E402
from testkit.realworld import tasks as T  # noqa: E402

BY = {t.id: t for t in T.EVERYDAY}

FLASK_REF = """
from flask import Flask, jsonify, request

from store import NoteStore


def create_app(store=None):
    app = Flask(__name__)
    st = app.config["store"] = store or NoteStore()

    @app.get("/notes")
    def list_notes():
        notes = st.all()
        tag, q = request.args.get("tag"), request.args.get("q")
        if tag:
            notes = [n for n in notes if tag.lower() in [t.lower() for t in n["tags"]]]
        if q:
            notes = [n for n in notes if q.lower() in (n["title"] + " " + n["body"]).lower()]
        return jsonify(notes)

    @app.post("/notes")
    def add_note():
        data = request.get_json(silent=True) or {}
        if not str(data.get("title") or "").strip():
            return jsonify({"error": "title is required"}), 400
        return jsonify(st.add(data["title"], data.get("body", ""), data.get("tags") or [])), 201

    @app.get("/notes/<int:note_id>")
    def get_note(note_id):
        note = st.get(note_id)
        return (jsonify(note), 200) if note else (jsonify({"error": "not found"}), 404)

    @app.patch("/notes/<int:note_id>")
    def patch_note(note_id):
        note = st.get(note_id)
        if note is None:
            return jsonify({"error": "not found"}), 404
        data = request.get_json(silent=True) or {}
        if "title" in data and not str(data["title"] or "").strip():
            return jsonify({"error": "bad title"}), 400
        if "tags" in data and not (isinstance(data["tags"], list) and all(isinstance(t, str) for t in data["tags"])):
            return jsonify({"error": "bad tags"}), 400
        for k in ("title", "body", "tags"):
            if k in data:
                note[k] = data[k]
        return jsonify(note)

    @app.delete("/notes/<int:note_id>")
    def delete_note(note_id):
        if st._notes.pop(note_id, None) is None:
            return jsonify({"error": "not found"}), 404
        return "", 204

    return app
"""


def _ws(tmp_path, tid):
    wd = tmp_path / tid
    wd.mkdir()
    BY[tid].setup(wd)
    return wd


def _solve(tid, wd: Path):
    if tid == "pdf_facts":
        (wd / "summary.md").write_text("- CEO: Marta Lindqvist\n- Revenue FY2025: USD 48.6 million\n"
                                       "- Net income FY2025: USD 5.9 million\n- Headcount: 1,284\n"
                                       "- New market in 2026: Brazil\n")
    elif tid == "csv_sales":
        e = mf.sales_expected()
        (wd / "answers.json").write_text(json.dumps({k: e[k] for k in ("q1", "q2", "q3")}))
    elif tid == "fix_test":
        p = wd / "shopcalc/discounts.py"
        p.write_text(p.read_text().replace("item_count > 50", "item_count >= 50"))
    elif tid == "flask_feature":
        (wd / "app.py").write_text(FLASK_REF)
    elif tid == "bash_rename":
        (wd / "rename.sh").write_text(r'''#!/usr/bin/env bash
set -u
dir="$1"
cd "$dir" || exit 1
target() {  # $1 base $2 ext -> unique name
  local n="$1.$2" i=1
  while [ -e "$n" ]; do n="$1_$i.$2"; i=$((i+1)); done
  printf '%s' "$n"
}
LC_ALL=C ls -1A | LC_ALL=C sort | while IFS= read -r f; do
  [ -f "$f" ] || continue
  if [[ "$f" =~ ^IMG_([0-9]{4})([0-9]{2})([0-9]{2})_([0-9]{2})([0-9]{2})([0-9]{2})\.([^.]+)$ ]]; then
    b="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}_${BASH_REMATCH[4]}-${BASH_REMATCH[5]}-${BASH_REMATCH[6]}"
    e="${BASH_REMATCH[7]}"
  elif [[ "$f" =~ ^Screenshot\ ([0-9]{4}-[0-9]{2}-[0-9]{2})\ at\ ([0-9]{2})\.([0-9]{2})\.([0-9]{2})\.([^.]+)$ ]]; then
    b="screenshot_${BASH_REMATCH[1]}_${BASH_REMATCH[2]}-${BASH_REMATCH[3]}-${BASH_REMATCH[4]}"
    e="${BASH_REMATCH[5]}"
  else
    continue
  fi
  e="${e,,}"; [ "$e" = jpeg ] && e=jpg
  mv -- "$f" "$(target "$b" "$e")"
done
''')
    elif tid == "md_to_docx":
        import docx
        d = docx.Document(str(wd / "proposal_template.docx"))
        F = mf.DOCX_FIELDS

        def fill(p):
            for r in p.runs:
                for k, v in F.items():
                    r.text = r.text.replace("{{" + k + "}}", v)
        for p in d.paragraphs:
            fill(p)
        for t in d.tables:
            for row in t.rows:
                for c in row.cells:
                    for p in c.paragraphs:
                        fill(p)
        d.save(wd / "proposal.docx")
    elif tid == "js_refactor":
        (wd / "lib/tax.js").write_text("const RATES = { EU: 0.2, UK: 0.2, US: 0.07, CA: 0.05 };\n"
                                       "function taxFor(amount, region) { return Math.round(amount * (RATES[region] || 0)); }\n"
                                       "module.exports = { taxFor };\n")
        s = (wd / "lib/cart.js").read_text()
        s = re.sub(r"if \((line|cart)\.region === 'EU'\) \{.*?\n\s*\}\n", lambda m: f"t = t + taxFor(t, {m.group(1)}.region);\n",
                   s, flags=re.S)
        s = "const { taxFor } = require('./tax');\n" + s.replace("var ", "let ")
        (wd / "lib/cart.js").write_text(s)
    elif tid == "three_files":
        (wd / "answer.json").write_text(json.dumps(mf.three_expected()))
    elif tid == "csv_clean":
        _, exp = mf.messy_rows()
        with open(wd / "clean_customers.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "name", "email", "phone", "signup_date"])
            w.writerows(exp)


@pytest.mark.parametrize("tid", list(BY))
def test_grader_fails_untouched_passes_reference(tmp_path, tid):
    wd = _ws(tmp_path, tid)
    before = gk.tree_digest(wd)
    g0 = BY[tid].grade(wd, "")
    assert gk.tree_digest(wd) == before, "grader wrote into the workspace"
    assert not g0["ok"], g0
    _solve(tid, wd)
    before = gk.tree_digest(wd)
    g1 = BY[tid].grade(wd, "")
    assert gk.tree_digest(wd) == before, "grader wrote into the workspace"
    assert g1["ok"], g1
