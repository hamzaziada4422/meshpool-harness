"""mock_llm.py plus one rule for UX/jitter measurements: a prompt containing "stream long" makes the model call
three tools (write_file, bash, write_file) and then stream a long Markdown answer (headings, lists, a table, a code
block) word by word. Everything else behaves exactly like mock_llm.py.
Usage: python3 mock_llm_long.py PORT LOGFILE      env MOCK_WORD_DELAY=0.02 (seconds per streamed word)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "mock_llm.py"), encoding="utf-8").read()

RULE = '''    if "stream long" in u and "stopped your previous request" not in u:
        wf = "write_file" if "write_file" in names else ("write" if "write" in names else None)
        sh = "bash" if "bash" in names else ("terminal" if "terminal" in names else None)
        if step == 0 and wf:
            return ("tool", wf, {"path": "notes-1.md", "content": "# Notes\\n\\nfirst file\\n"})
        if step == 1 and sh:
            return ("tool", sh, {"command": "echo checking && sleep 1 && echo ok"})
        if step == 2 and wf:
            return ("tool", wf, {"path": "notes-2.md", "content": "# More notes\\n\\nsecond file\\n"})
        para = ("Here is what I found. The pool keeps a **short list** of options and `routes` each task to the best one. "
                "Costs stay low because cached tokens are reused, and the answer below is long on purpose so the page has to "
                "keep growing while it streams. ")
        body = "## Summary\\n\\n" + para * 3 + "\\n\\n### Key points\\n\\n" + "".join(f"- Point {i}: {para[:90]}\\n" for i in range(1, 9)) \\
            + "\\n| Team | Spend | Share |\\n|---|---|---|\\n| Infra | 120 | 40% |\\n| Data | 90 | 30% |\\n| Apps | 90 | 30% |\\n" \\
            + "\\n### Example\\n\\n```python\\nfor task in tasks:\\n    route(task)\\n```\\n\\n## Details\\n\\n" + para * 6 \\
            + "\\n\\n1. First step: " + para + "\\n2. Second step: " + para + "\\n3. Third step: " + para + "\\n\\n## Next steps\\n\\n" + para * 4
        return ("text", body)
'''
# "make previews": copies a deck, a workbook and a PDF from $MOCK_PREVIEW_DIR into the project, writes page.html, and
# answers with an html and an svg code block (testkit/ux_previews.js drives the in-app previews with it).
RULE2 = '''    if "make previews" in u and "stopped your previous request" not in u:
        wf = "write_file" if "write_file" in names else ("write" if "write" in names else None)
        sh = "bash" if "bash" in names else ("terminal" if "terminal" in names else None)
        d = os.environ.get("MOCK_PREVIEW_DIR", "")
        if step == 0 and sh and d:
            return ("tool", sh, {"command": "cp " + d + "/deck.pptx " + d + "/model.xlsx " + d + "/report.pdf . && echo copied"})
        if step == 1 and wf:
            return ("tool", wf, {"path": "page.html", "content": "<h1>Made page</h1>"})
        return ("text", "Made the files.\\n\\n```html\\n<h1 style=\\"font-family:sans-serif\\">Snippet page</h1><button onclick=\\"this.textContent='ran'\\">Go</button>\\n```\\n\\n```svg\\n<svg xmlns=\\"http://www.w3.org/2000/svg\\" width=\\"160\\" height=\\"90\\"><rect width=\\"160\\" height=\\"90\\" fill=\\"#1F6FD0\\"/></svg>\\n```\\n")
'''
anchor = '    if "the goal of this chat" in u and "goal test" in u:'
assert anchor in src
src = src.replace(anchor, RULE + RULE2 + anchor, 1)
src = src.replace("time.sleep(0.01)", "time.sleep(float(os.environ.get('MOCK_WORD_DELAY', '0.02')))", 1)
exec(compile(src, os.path.join(HERE, "mock_llm.py"), "exec"), {"os": os, "__name__": "__main__", "__file__": os.path.join(HERE, "mock_llm.py")})
