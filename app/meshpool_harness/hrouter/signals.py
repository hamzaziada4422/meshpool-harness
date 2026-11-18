"""Free, deterministic signals about a request: no model call, a few milliseconds.

Everything here reads the request text, the attached file names and (cheaply) the project folder. Each detector
returns plain data so route.py can weigh them and tests can pin them.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ---------- languages ----------------------------------------------------------------------------------------------
# Ordered by how much code is written in them with AI help (GitHub Octoverse 2025: TypeScript and Python lead,
# then JavaScript, Java, C#, Go, Rust, C/C++, PHP, Ruby, shell). Packs exist for the ones with fast local checks.
EXT_LANG = {
    ".py": "python", ".pyi": "python", ".ipynb": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".php": "php", ".rb": "ruby",
    ".swift": "swift", ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".sql": "sql", ".scala": "scala",
    ".dart": "dart", ".lua": "lua", ".r": "r", ".R": "r",
}
MARKERS = [  # project files that name the main language (first match wins)
    ("Cargo.toml", "rust"), ("go.mod", "go"), ("tsconfig.json", "typescript"), ("pyproject.toml", "python"),
    ("setup.py", "python"), ("requirements.txt", "python"), ("Pipfile", "python"), ("pom.xml", "java"),
    ("build.gradle", "java"), ("build.gradle.kts", "kotlin"), ("package.json", "javascript"), ("composer.json", "php"),
    ("Gemfile", "ruby"), ("Package.swift", "swift"), ("CMakeLists.txt", "cpp"), ("Makefile", "c"),
]
WORD_LANG = [  # a language named in the request
    (r"\bpython\b|\bdjango\b|\bflask\b|\bfastapi\b|\bpandas\b|\bpytest\b|\bpip\b", "python"),
    (r"\btypescript\b|\btsx?\b|\breact\b|\bnext\.?js\b|\bangular\b|\bvue\b|\bsvelte\b|\btsc\b", "typescript"),
    (r"\bjavascript\b|\bnode(\.?js)?\b|\bnpm\b|\bjsx?\b|\bexpress\b", "javascript"),
    (r"\brust\b|\bcargo\b|\bcrate\b|\bborrow checker\b", "rust"), (r"\bgolang\b|\bgo (mod|build|test|vet)\b", "go"),
    (r"\bjava\b|\bspring( boot)?\b|\bmaven\b|\bgradle\b|\bjvm\b", "java"), (r"\bkotlin\b", "kotlin"),
    (r"\bc#|\bcsharp\b|\.net\b|\bdotnet\b", "csharp"), (r"\bc\+\+|\bcpp\b", "cpp"), (r"\bphp\b|\blaravel\b", "php"),
    (r"\bruby\b|\brails\b", "ruby"), (r"\bswift\b|\bswiftui\b", "swift"), (r"\bbash\b|\bshell script\b|\bzsh\b", "shell"),
    (r"\bsql\b|\bpostgres(ql)?\b|\bmysql\b|\bsqlite\b", "sql"),
]

# ---------- pasted errors ------------------------------------------------------------------------------------------
ERRORS = [  # (language, pattern) for text that is an error report rather than prose
    ("python", r"Traceback \(most recent call last\)|^\s*File \"[^\"]+\", line \d+|^(\w+\.)*\w*(Error|Exception): |"
               r"SyntaxError:|IndentationError:|ModuleNotFoundError:"),
    ("typescript", r"error TS\d{3,5}:|\.tsx?\(\d+,\d+\): error"),
    ("javascript", r"^\s+at .+\(.+\.m?[jt]sx?:\d+:\d+\)|(TypeError|ReferenceError|SyntaxError): .+\n\s+at |"
                   r"Uncaught \w+Error|ERR_[A-Z_]{4,}"),
    ("rust", r"error\[E\d{4}\]|^\s*--> [\w./-]+\.rs:\d+:\d+|thread '.+' panicked at"),
    ("go", r"^panic: |goroutine \d+ \[running\]|\.go:\d+:\d+: "),
    ("java", r"Exception in thread \"|^\s+at [\w.$]+\([\w]+\.java:\d+\)|\.java:\d+: error:"),
    ("csharp", r"error CS\d{4}:|Unhandled exception\. System\."),
    ("cpp", r"\.(c|cc|cpp|h|hpp):\d+:\d+: (fatal )?error:|Segmentation fault|undefined reference to"),
    ("shell", r"command not found|: line \d+: syntax error"),
]
_ERR = [(lang, re.compile(p, re.M)) for lang, p in ERRORS]

# ---------- sub-tasks for code -------------------------------------------------------------------------------------
SUB = [  # (sub, pattern, weight). Checked in order; the heaviest total wins.
    ("debug", r"\b(bug|debug\w*|fix(es|ing)?|broken|crash\w*|fails?|failing|failure|error|exception|traceback|"
              r"stack ?trace|doesn'?t work|not working|wrong (output|result)|regression|segfault|panic|hangs?|"
              r"infinite loop|flaky|why (does|is|do) .{0,40}(fail|error|crash|break))\b", 1.0),
    ("test", r"\b(write|add|generate|increase) (unit |integration |e2e )?tests?\b|\btest coverage\b|"
             r"\bcoverage\b", 1.5),
    ("refactor", r"\b(refactor\w*|clean ?up|restructure|rename (all|every)|extract (a |the )?(function|method|class|"
                 r"module)|migrat\w+|upgrade (to|from)|port (it |this )?(to|from)|convert (this |the )?(code|project|"
                 r"codebase)|modernize|deduplicate|split (this|the) (file|module|class))\b", 1.5),
    ("review", r"\b(review (my|this|the)|code review|pr review|review (the )?(pr|pull request|diff|changes)|"
               r"audit (the|this|my) code|security review|look over)\b", 2.0),
    ("terminal", r"\b(install\w*|set ?up|configure|docker\w*|kubernetes|k8s|deploy\w*|ci( pipeline)?|github actions|"
                 r"env(ironment)? var\w*|permission denied|brew|apt(-get)?|systemd|nginx|ssh|cron|shell command|"
                 r"terminal)\b", 1.0),
    ("explain", r"^\s*(what|how|why|explain|walk me through|help me understand)\b.{0,80}\b(code|function|class|"
                r"module|this|does|work)\b|\bexplain (this|the|how)\b", 1.0),
    ("build", r"\b(build|create|implement\w*|add (a |an |the )?(feature|endpoint|route|page|component|command|"
              r"option|flag|button|api)|write (a|an|the) (script|function|class|program|app|cli|server|bot)|"
              r"scaffold|prototype|make (a|an) (app|website|tool|script|game|api))\b", 1.0),
]
_SUB = [(s, re.compile(p, re.I | re.M), w) for s, p, w in SUB]

FAMILY_WORDS = {
    "browser": r"\b(click|fill (in|out) (the|a|this) form|log ?in to|sign ?in to|on (the )?website|in the browser|"
               r"browse to|navigate to|scrape|book (a|the) |submit (the|a) form)\b",
    "research": r"\b(research\w*|sources?|citations?|cite|look up|find out|latest|news|market (size|share|map)|"
                r"competitors?|landscape|due diligence|literature|papers?|arxiv|study|studies|10-k|filings?)\b",
    "data": r"\b(analy[sz]e|analysis|dataset|csv|parquet|statistic\w*|regression|correlat\w*|cohort|segment\w*|"
            r"pivot|chart|plot|visuali[sz]\w*|dashboard|forecast\w*|trend|sql query|duckdb|kpis?|metrics)\b",
    "docs": r"\b(deck|slides?|powerpoint|pptx|presentation|spreadsheet|excel|xlsx|workbook|financial model|"
            r"word doc\w*|docx|memo|report|proposal|letter|contract|pdf|one[- ]pager|brief)\b",
    "automation": r"\b(every (day|morning|week|monday|hour)|schedule\w*|remind me|each (day|week)|daily|weekly|"
                  r"when (a|an|new) .{0,30}(arrives|comes in|is added)|watch (the|this|my) folder|automat\w+)\b",
    "writing": r"\b(rewrite|rephrase|reword|proofread|polish|shorten|tighten|translate|tone|friendlier|more formal|"
               r"summari[sz]e (this|the following|it)|tl;?dr|draft (an?|the|my) (email|reply|message|post|tweet|bio)|"
               r"reply to (this|the|her|him|them)|write (an?|the|my) (email|reply|message|post|tweet|caption|bio))\b",
    "code": r"\b(code|repo(sitory)?|function|class|method|module|script|compile\w*|tests?|unit test|api|endpoint|"
            r"bug|stack ?trace|traceback|refactor|pull request|commit|lint|type ?error|npm|pip|cargo|git)\b",
}
_FAM = {k: re.compile(v, re.I) for k, v in FAMILY_WORDS.items()}

OFFICE_EXT = {".pptx": "docs", ".ppt": "docs", ".key": "docs", ".xlsx": "docs", ".xlsm": "docs", ".xls": "docs",
              ".docx": "docs", ".doc": "docs", ".pdf": "docs", ".csv": "data", ".tsv": "data", ".parquet": "data",
              ".json": "data", ".jsonl": "data", ".sav": "data", ".dta": "data"}


def languages_in(text: str, files: list[str] | None = None) -> dict[str, float]:
    """Language -> evidence weight from file extensions, pasted errors, code fences and words."""
    w: dict[str, float] = {}
    for f in files or []:
        lang = EXT_LANG.get(Path(f).suffix) or EXT_LANG.get(Path(f).suffix.lower())
        if lang:
            w[lang] = w.get(lang, 0) + 3
    for lang, rx in _ERR:
        if rx.search(text or ""):
            w[lang] = w.get(lang, 0) + 4
    for m in re.finditer(r"```(\w+)", text or ""):
        tag = {"py": "python", "ts": "typescript", "js": "javascript", "rs": "rust", "golang": "go", "sh": "shell",
               "bash": "shell", "c++": "cpp", "cs": "csharp", "kt": "kotlin", "rb": "ruby"}.get(m.group(1).lower(),
                                                                                                m.group(1).lower())
        if tag in set(EXT_LANG.values()):
            w[tag] = w.get(tag, 0) + 2
    for m in re.finditer(r"\b[\w/.-]+(\.[A-Za-z]{1,5})\b", text or ""):
        lang = EXT_LANG.get(m.group(1))
        if lang:
            w[lang] = w.get(lang, 0) + 1.5
    for p, lang in WORD_LANG:
        if re.search(p, text or "", re.I):
            w[lang] = w.get(lang, 0) + 1
    return w


def project_language(workdir: str | None) -> str | None:
    """The main language of a project folder, from its marker files (and, failing that, its top-level sources)."""
    if not workdir:
        return None
    root = Path(workdir)
    try:
        names = {p.name for p in root.iterdir()}
    except OSError:
        return None
    if "package.json" in names and ("tsconfig.json" in names or any(n.endswith((".ts", ".tsx")) for n in names)):
        return "typescript"
    for marker, lang in MARKERS:
        if marker in names:
            return lang
    counts: dict[str, int] = {}
    for n in names:
        lang = EXT_LANG.get(Path(n).suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else None


def is_repo(workdir: str | None) -> bool:
    if not workdir:
        return False
    root = Path(workdir)
    return (root / ".git").exists() or any((root / m).exists() for m, _ in MARKERS)


def pasted_error(text: str) -> str | None:
    """The language of an error report pasted into the request, or None."""
    for lang, rx in _ERR:
        if rx.search(text or ""):
            return lang
    return None


def code_sub(text: str, has_error: bool = False) -> tuple[str, list[str]]:
    """The kind of code work: debug, test, refactor, review, terminal, explain or build."""
    score: dict[str, float] = {}
    hits: list[str] = []
    for sub, rx, weight in _SUB:
        ms = rx.findall(text or "")
        if ms:
            score[sub] = score.get(sub, 0) + weight * len(ms)
            hits.append(sub)
    if has_error:
        score["debug"] = score.get("debug", 0) + 3
    if not score:
        return "build", []
    order = [s for s, _, _ in SUB]
    best = max(score, key=lambda s: (score[s], -order.index(s)))
    return best, hits


def families(text: str, files: list[str] | None = None) -> dict[str, float]:
    """Family -> evidence weight from words and attachments."""
    w: dict[str, float] = {}
    for fam, rx in _FAM.items():
        n = len(rx.findall(text or ""))
        if n:
            w[fam] = w.get(fam, 0) + n
    for f in files or []:
        ext = Path(f).suffix.lower()
        if ext in OFFICE_EXT:
            w[OFFICE_EXT[ext]] = w.get(OFFICE_EXT[ext], 0) + 2
        elif EXT_LANG.get(ext):
            w["code"] = w.get("code", 0) + 2
    return w


_SNIPPET = re.compile(r"\b((only|just) (the|a|an) (query|regex|command|code|answer|snippet|formula|function|"
                      r"one[- ]liner|sql)|one[- ]liner|give (me )?(a|an|the) (regex|query|sql query|command|formula|"
                      r"one[- ]liner|snippet|shell command|cron expression)|write (a|an|the) (sql query|regex|formula|"
                      r"one[- ]liner|cron expression|shell command))\b", re.I)


def snippet_request(text: str) -> bool:
    """A request for one short piece of code or a formula, answered in the reply (no project, no files)."""
    return bool(_SNIPPET.search(text or "")) and len((text or "").split()) <= 60


def size(text: str) -> str:
    """short | medium | long, by how much the user wrote."""
    n = len((text or "").split())
    return "short" if n <= 25 else "medium" if n <= 150 else "long"


def env_fast_model() -> str:
    return os.environ.get("MH_MODEL_FAST", "").strip()
