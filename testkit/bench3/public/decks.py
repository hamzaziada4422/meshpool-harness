"""Deck tasks: read, understand and edit an existing PowerPoint deck.

The other suites only build new decks (Office-7 pptx_chart). These five start from the same 8-slide deck.pptx
(built here with python-pptx, so every run starts from identical bytes) and check that an agent can:

  deck_read      answer questions whose answers sit in a chart's data, a table, a bullet list and speaker notes
  deck_edit      fix a typo everywhere, rename a presenter and retitle a slide, keeping the chart and the title format
  deck_add       add a slide from a text file in the right place, with the neighbouring slide's layout
  deck_reorder   delete one slide and move another to the end
  deck_chart     add a Q4 category to the existing chart from a CSV, keeping it a real (editable) chart

Run: python testkit/bench3/run.py --tasks-module testkit/bench3/public/decks.py --tasks all --arms codex,mp-work ...
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from testkit import gradekit as gk  # noqa: E402
from testkit.bench3.tasks import Task  # noqa: E402

REGIONS = {"North America": [100, 110, 118], "EMEA": [90, 96, 131], "APAC": [60, 72, 80]}
Q4 = {"North America": 125, "EMEA": 140, "APAC": 88}
HEADCOUNT = [("Engineering", 42), ("Sales", 17), ("Operations", 9), ("G&A", 6)]          # total 74
RISKS = ["Supplier concentration in APAC", "FX exposure on EMEA contracts", "Hiring pace in Engineering",
         "Churn in the SMB segment"]
TITLES = ["FY26 Reveune Review", "Agenda", "Reveune by region", "Headcount", "Key risks", "DRAFT - do not present",
          "Appendix", "Next steps"]


def build_deck(path: Path) -> None:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt

    prs = Presentation()
    title_layout, content_layout, only_title = prs.slide_layouts[0], prs.slide_layouts[1], prs.slide_layouts[5]

    s = prs.slides.add_slide(title_layout)
    s.shapes.title.text = TITLES[0]
    s.placeholders[1].text = "Presented by Dana Lee, Finance"

    s = prs.slides.add_slide(content_layout)
    s.shapes.title.text = "Agenda"
    body = s.placeholders[1].text_frame
    body.text = "Revenue by region"
    for t in ("Headcount", "Key risks", "Next steps"):
        body.add_paragraph().text = t

    s = prs.slides.add_slide(only_title)
    s.shapes.title.text = TITLES[2]
    r = s.shapes.title.text_frame.paragraphs[0].runs[0]
    r.font.size, r.font.bold = Pt(40), True                       # deck_edit must keep this
    cd = CategoryChartData()
    cd.categories = ["Q1", "Q2", "Q3"]
    for name, vals in REGIONS.items():
        cd.add_series(name, vals)
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.7), Inches(1.6), Inches(8.6), Inches(4.6), cd)
    tb = s.shapes.add_textbox(Inches(0.7), Inches(6.4), Inches(8.6), Inches(0.6))
    tb.text_frame.text = "Reveune grew in every region in Q3 (USD millions)."

    s = prs.slides.add_slide(only_title)
    s.shapes.title.text = "Headcount"
    tbl = s.shapes.add_table(len(HEADCOUNT) + 1, 2, Inches(1.5), Inches(1.8), Inches(6), Inches(3)).table
    tbl.cell(0, 0).text, tbl.cell(0, 1).text = "Team", "People"
    for i, (team, n) in enumerate(HEADCOUNT, 1):
        tbl.cell(i, 0).text, tbl.cell(i, 1).text = team, str(n)

    s = prs.slides.add_slide(content_layout)
    s.shapes.title.text = "Key risks"
    body = s.placeholders[1].text_frame
    body.text = RISKS[0]
    for t in RISKS[1:]:
        body.add_paragraph().text = t

    s = prs.slides.add_slide(content_layout)
    s.shapes.title.text = TITLES[5]
    s.placeholders[1].text = "Numbers below are not final."

    s = prs.slides.add_slide(content_layout)
    s.shapes.title.text = "Appendix"
    s.placeholders[1].text = "Definitions and data sources."

    s = prs.slides.add_slide(content_layout)
    s.shapes.title.text = "Next steps"
    body = s.placeholders[1].text_frame
    body.text = "Finalise the FY27 plan"
    body.add_paragraph().text = "Agree regional targets"
    s.notes_slide.notes_text_frame.text = "Remind everyone: the board deadline for the FY27 plan is March 14."
    prs.save(path)


def _setup(extra=None):
    def setup(workdir):
        w = Path(workdir)
        build_deck(w / "deck.pptx")
        for name, text in (extra or {}).items():
            (w / name).write_text(text, encoding="utf-8")
    return setup


def _open(workdir):
    from pptx import Presentation
    f = Path(workdir) / "deck.pptx"
    return Presentation(f) if f.is_file() else None


def _title(slide):
    t = slide.shapes.title
    return (t.text_frame.text if t is not None and t.has_text_frame else "").strip()


def _texts(slide):
    out = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            out.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            out += [c.text for r in sh.table.rows for c in r.cells]
    return "\n".join(out)


def _chart(slide):
    return next((sh.chart for sh in slide.shapes if getattr(sh, "has_chart", False) and sh.has_chart), None)


def _missing_deck(prs):
    return gk.result(False, "deck.pptx is missing or not a deck", 0, 1) if prs is None else None


# =========================================================================== deck_read
_READ_PROMPT = ("Read deck.pptx and answer these four questions. Write the four answers, one per line, to answers.txt "
                "and also give them in your reply.\n"
                "1. In the revenue chart, which region had the highest Q3 revenue, and how much was it?\n"
                "2. What is the total headcount across all teams?\n"
                "3. How many key risks does the deck list?\n"
                "4. According to the presenter's notes, what is the board deadline for the FY27 plan?\n"
                "Do not change deck.pptx.")


def _setup_read(workdir):
    _setup()(workdir)
    (Path(workdir) / ".deck.sha").write_text(hashlib.sha256((Path(workdir) / "deck.pptx").read_bytes()).hexdigest())


def grade_read(workdir, reply):
    w = Path(workdir)
    ans = ((w / "answers.txt").read_text(errors="replace") if (w / "answers.txt").is_file() else "") + "\n" + (reply or "")
    low = ans.lower()
    ck = gk.Checks()
    ck.add("Q1: EMEA, 131", "emea" in low and "131" in ans)
    ck.add("Q2: 74 people", bool(re.search(r"\b74\b", ans)))
    ck.add("Q3: 4 risks", bool(re.search(r"\b(4|four)\b", low)))
    ck.add("Q4: March 14", bool(re.search(r"march\s*14|14\s*march|14(th)?\s+of\s+march|03[/-]14", low)))
    sha = (w / ".deck.sha").read_text().strip() if (w / ".deck.sha").is_file() else ""
    ck.add("deck.pptx unchanged", (w / "deck.pptx").is_file()
           and hashlib.sha256((w / "deck.pptx").read_bytes()).hexdigest() == sha)
    return ck.result()


# =========================================================================== deck_edit
_EDIT_PROMPT = ("In deck.pptx: fix the typo \"Reveune\" everywhere it appears (it should be \"Revenue\"), change the "
                "presenter on the title slide from Dana Lee to Sam Ortiz, and rename the \"Next steps\" slide to "
                "\"Plan for Q1\". Keep everything else as it is and save over deck.pptx.")


def grade_edit(workdir, reply):
    prs = _open(workdir)
    if (m := _missing_deck(prs)):
        return m
    sl = list(prs.slides)
    ck = gk.Checks()
    ck.add("still 8 slides", len(sl) == 8, f"{len(sl)} slides")
    every = "\n".join(_texts(s) for s in sl)
    ck.add("no 'Reveune' left", "reveune" not in every.lower())
    if len(sl) == 8:
        ck.add("title slide says Revenue", _title(sl[0]) == "FY26 Revenue Review", _title(sl[0]))
        sub = _texts(sl[0])
        ck.add("presenter is Sam Ortiz", "Sam Ortiz" in sub and "Dana Lee" not in sub)
        ck.add("revenue slide title fixed", _title(sl[2]) == "Revenue by region", _title(sl[2]))
        ck.add("revenue note fixed", "Revenue grew in every region" in _texts(sl[2]))
        ck.add("chart still there and unchanged", (c := _chart(sl[2])) is not None
               and list(c.plots[0].categories) == ["Q1", "Q2", "Q3"])
        runs = [r for p in sl[2].shapes.title.text_frame.paragraphs for r in p.runs]
        ck.add("revenue title keeps 40 pt bold", bool(runs) and all(r.font.size is not None and r.font.size.pt == 40
                                                                    and r.font.bold for r in runs))
        ck.add("'Next steps' renamed", _title(sl[7]) == "Plan for Q1", _title(sl[7]))
        ck.add("its bullets kept", "Agree regional targets" in _texts(sl[7]))
        ck.add("its notes kept", "March 14" in sl[7].notes_slide.notes_text_frame.text if sl[7].has_notes_slide
               else False)
    return ck.result()


# =========================================================================== deck_add
_HIGHLIGHTS = ("Highlights for the FY26 review:\n- EMEA revenue rose 36% from Q2 to Q3\n"
               "- Engineering hiring finished ahead of plan\n- SMB churn fell for the third quarter in a row\n")
_ADD_PROMPT = ("Add a new slide to deck.pptx right after the Agenda slide, titled \"Highlights\", with one bullet "
               "for each highlight in highlights.txt. Use the same layout as the Agenda slide. Save over deck.pptx.")


def grade_add(workdir, reply):
    prs = _open(workdir)
    if (m := _missing_deck(prs)):
        return m
    sl = list(prs.slides)
    ck = gk.Checks()
    ck.add("9 slides", len(sl) == 9, f"{len(sl)} slides")
    titles = [_title(s) for s in sl]
    ck.add("Highlights is slide 3", len(sl) > 2 and titles[2] == "Highlights", " | ".join(titles[:4]))
    if "Highlights" in titles:
        s = sl[titles.index("Highlights")]
        agenda = sl[titles.index("Agenda")] if "Agenda" in titles else None
        ck.add("same layout as Agenda", agenda is not None and s.slide_layout.name == agenda.slide_layout.name,
               s.slide_layout.name)
        body = [p.text.strip() for sh in s.shapes if sh.has_text_frame and sh != s.shapes.title
                for p in sh.text_frame.paragraphs if p.text.strip()]
        ck.add("three bullets", len(body) == 3, f"{len(body)} lines")
        text = " ".join(body).lower()
        ck.add("bullets carry the highlights", all(k in text for k in ("emea", "hiring", "churn")))
        W, H = prs.slide_width, prs.slide_height
        ck.add("nothing off the slide", all(sh.left is None or (0 <= sh.left and sh.left + sh.width <= W
                                            and 0 <= sh.top and sh.top + sh.height <= H) for sh in s.shapes))
    ck.add("other slides in order", [t for t in titles if t != "Highlights"] == TITLES, " | ".join(titles))
    return ck.result()


# =========================================================================== deck_reorder
_REORDER_PROMPT = ("In deck.pptx, delete the slide titled \"DRAFT - do not present\" and move the Appendix slide to "
                   "the end of the deck. Save over deck.pptx.")


def grade_reorder(workdir, reply):
    prs = _open(workdir)
    if (m := _missing_deck(prs)):
        return m
    titles = [_title(s) for s in prs.slides]
    want = [t for t in TITLES if t not in ("DRAFT - do not present", "Appendix")] + ["Appendix"]
    ck = gk.Checks()
    ck.add("slide order", titles == want, " | ".join(titles))
    ck.add("draft gone", not any("DRAFT" in t for t in titles))
    sl = list(prs.slides)
    if "Next steps" in titles:
        s = sl[titles.index("Next steps")]
        ck.add("notes kept on Next steps", s.has_notes_slide and "March 14" in s.notes_slide.notes_text_frame.text)
    if TITLES[2] in titles:
        ck.add("chart kept", _chart(sl[titles.index(TITLES[2])]) is not None)
    # a deleted slide must leave the package too, or PowerPoint can complain and the file keeps the draft text
    import zipfile
    with zipfile.ZipFile(Path(workdir) / "deck.pptx") as z:
        xml = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.startswith("ppt/slides/slide"))
    ck.add("draft slide removed from the file", "DRAFT - do not present" not in xml)
    return ck.result()


# =========================================================================== deck_chart
_Q4_CSV = "region,q4_revenue\nNorth America,125\nEMEA,140\nAPAC,88\n"
_CHART_PROMPT = ("Update the revenue chart in deck.pptx with the Q4 numbers in q4.csv: add Q4 next to Q1 to Q3 for "
                 "every region. Keep it a real PowerPoint chart I can edit (not a picture). Save over deck.pptx.")


def grade_chart(workdir, reply):
    prs = _open(workdir)
    if (m := _missing_deck(prs)):
        return m
    sl = list(prs.slides)
    ck = gk.Checks()
    ck.add("still 8 slides", len(sl) == 8, f"{len(sl)} slides")
    charts = [(s, _chart(s)) for s in sl if _chart(s) is not None]
    ck.add("one editable chart", len(charts) == 1, f"{len(charts)} charts")
    ck.add("no picture added instead", not any(sh.shape_type == 13 for s in sl for sh in s.shapes))
    if charts:
        c = charts[0][1]
        cats = [str(x) for x in c.plots[0].categories]
        ck.add("categories Q1-Q4", cats == ["Q1", "Q2", "Q3", "Q4"], ",".join(cats))
        series = {s.name: list(s.values) for s in c.plots[0].series}
        for region, old in REGIONS.items():
            got = series.get(region)
            ck.add(f"{region} values", got is not None and [float(v) for v in got] == [float(v) for v in old + [Q4[region]]],
                   str(got))
    return ck.result()


TASKS = [
    Task("deck_read", "office", _READ_PROMPT, _setup_read, grade_read, 600),
    Task("deck_edit", "office", _EDIT_PROMPT, _setup(), grade_edit, 600),
    Task("deck_add", "office", _ADD_PROMPT, _setup({"highlights.txt": _HIGHLIGHTS}), grade_add, 600),
    Task("deck_reorder", "office", _REORDER_PROMPT, _setup(), grade_reorder, 600),
    Task("deck_chart", "office", _CHART_PROMPT, _setup({"q4.csv": _Q4_CSV}), grade_chart, 600),
]


def get(ids):
    if isinstance(ids, str):
        ids = [s.strip() for s in ids.split(",") if s.strip()]
    by = {t.id: t for t in TASKS}
    return [by[i] for i in ids]
