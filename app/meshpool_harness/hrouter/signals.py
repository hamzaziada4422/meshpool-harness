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
              r"infinite loop|flaky|why (does|is|do) .{0,40}(fail|error|crash|break)|returns? the wrong|wrong (value|"
              r"result|answer|month|date|total|number)s?|worked (before|last (week|month|time)|yesterday)|used to work|"
              r"too slow|far too slow|slow(er)? (on|with|than)|times? out|timed out|performance|speed (it )?up|"
              r"takes? (minutes|hours|forever|ages|too long)|make (it|this) (fast|faster|quicker)|"
              r"memory leak)\b", 1.0),
    ("debug", r"github issue|<issue>|\bexpected (output|result|behaviou?r|value)\b|\bshould (return|be|give|raise|"
              r"produce|output|work)\b|\binstead of\b|\bbut (it )?(returns|gives|got|raises|prints|produces)\b|"
              r"\bis (incorrect|wrong|broken)\b|\bincorrect(ly)?\b|\bunexpected(ly)?\b|\bnot (correct|right)\b", 1.5),
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

MAX_TEXT_CHARS = 8000


def cap_text(text):
    """User text bounded for the regex signals: the start and the end of a huge paste say what is asked, and no
    pattern can then run for long (a 3 MB unbroken token froze the whole server)."""
    if text and len(text) > MAX_TEXT_CHARS:
        h = MAX_TEXT_CHARS // 2
        return text[:h] + "\n" + text[-h:]
    return text


OFFICE_EXT = {".pptx": "docs", ".ppt": "docs", ".key": "docs", ".xlsx": "docs", ".xlsm": "docs", ".xls": "docs",
              ".docx": "docs", ".doc": "docs", ".pdf": "docs", ".csv": "data", ".tsv": "data", ".parquet": "data",
              ".json": "data", ".jsonl": "data", ".sav": "data", ".dta": "data"}


def languages_in(text: str, files: list[str] | None = None) -> dict[str, float]:
    """Language -> evidence weight from file extensions, pasted errors, code fences and words."""
    text = cap_text(text)
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
    for m in re.finditer(r"\b[\w/.-]{1,200}(\.[A-Za-z]{1,5})\b", text or ""):
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
    text = cap_text(text)
    for lang, rx in _ERR:
        if rx.search(text or ""):
            return lang
    return None


# A request whose main verb is writing tests ("write thorough tests for", "add a few pytest cases") is test work even
# when it talks about behaviour, bugs or "fails" (those describe what the tests check).
_TEST_MAIN = re.compile(r"\b(write|add|generate|create|extend|improve|increase|need|build|produce)\b(\W+[\w.\-]+){0,6}?"
                        r"\W+(unit |integration |e2e |regression |pytest |jest |property[- ]based )?(tests?|test suite|"
                        r"test cases?|specs)\b|\btest coverage\b", re.I)
# Concrete failure symptoms: the only things that make a request a bug report rather than a behaviour spec.
_SYMPTOM = re.compile(r"\b(traceback|stack ?trace|crash\w*|segfault|panic|exceptions?|raises|returns? (the )?wrong|"
                      r"wrong (output|result|value|answer|month|date|total|number)s?|used to work|worked (before|last|"
                      r"yesterday|until)|regression|doesn'?t work|not working|(?<!ties )(?<!tie )broken(?! (by|down|into|up|out))|bug|failing|fails (with|when|on|"
                      r"after|for)|assert(ion)?error|(gets?|getting|throws?|shows?|with) (an? )?(error|exception)|"
                      r"too slow|far too slow|slow(er)? (on|with|than)|times? out|timed out|performance|speed (it )?up|"
                      r"takes? (minutes|hours|forever|ages|too long)|make (it|this) (fast|faster|quicker)|"
                      r"memory leak|hangs?|infinite loop|flaky|incorrect(ly)?|unexpected(ly)?|but (it )?(returns|gives|"
                      r"got|raises|prints|produces))\b", re.I)
# Behaviour spec: new or changed behaviour stated as a requirement.
_FEATURE = re.compile(r"\b(must|should|needs? to|add|support|implement|extend|allow|make \w+(\.\w+)? (do|return|accept|"
                      r"use|treat|behave)|let (users?|me)|introduce|enable)\b", re.I)
_OTHER = ("refactor", "review", "explain")
_LEAD_FEATURE = re.compile(r"\s*(please\s+)?(add|implement|create|build|support|introduce|write a new|make it possible)\b",
                           re.I)


def code_sub(text: str, has_error: bool = False) -> tuple[str, list[str]]:
    """The kind of code work: debug, test, refactor, review, terminal, explain or build."""
    text = cap_text(text)
    if not has_error and _TEST_MAIN.search(text or "") and not re.search(
            r"\b(fix|debug)\b.{0,30}\b(failing|broken) tests?\b", text or "", re.I):
        return "test", ["test"]
    if (not has_error and re.search(r"\b(refactor\w*|restructure)\b", text or "", re.I)
            and not re.search(r"\b(traceback|crash\w*|raises \w*error|fails (with|when|on)|returns? (the )?wrong|"
                              r"too slow|times? out)\b", text or "", re.I)):
        return "refactor", ["refactor"]   # an explicit refactor request: "error messages" in the spec are behaviour to keep
    score: dict[str, float] = {}
    hits: list[str] = []
    for sub, rx, weight in _SUB:
        ms = rx.findall(text or "")
        if ms:
            score[sub] = score.get(sub, 0) + weight * len(ms)
            hits.append(sub)
    lead = re.split(r"(?<=[.:;!?])\s|\n", (text or "").strip(), 1)[0]
    if has_error:
        score["debug"] = score.get("debug", 0) + 3
    elif (_LEAD_FEATURE.match(lead) and not re.search(r"\b(fix|bug|crash\w*|traceback|broken|regression)\b", lead, re.I)
          and not any(score.get(o) for o in _OTHER)):
        score["debug"] = 0           # "Add atomic bulk ordering: ... raises BulkError ...": a spec, not a bug report
        score["build"] = score.get("build", 0) + 3
        hits.append("build")
    elif (_FEATURE.search(text or "") and not _SYMPTOM.search(text or "")
          and not any(score.get(o) for o in _OTHER)):
        score["debug"] = 0           # "must/add/should" without a failing symptom is a feature request
        score["terminal"] = score.get("terminal", 0) / 2
        score["build"] = score.get("build", 0) + 3
        hits.append("build")
    if not score:
        return "build", []
    order = [s for s, _, _ in SUB]
    best = max(score, key=lambda s: (score[s], -order.index(s)))
    return best, hits


def families(text: str, files: list[str] | None = None) -> dict[str, float]:
    """Family -> evidence weight from words and attachments."""
    text = cap_text(text)
    w: dict[str, float] = {}
    for fam, rx in _FAM.items():
        n = len(rx.findall(text or ""))
        if n:
            w[fam] = w.get(fam, 0) + n
    named = re.findall(r"[\w./-]{1,200}\.(?:csv|tsv|parquet|jsonl|sav|dta)\b", text or "", re.I)
    if named and not files:              # "orders.csv", "events.jsonl" named in the question: a data file, as attached
        w["data"] = w.get("data", 0) + 2
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
    text = cap_text(text)
    return bool(_SNIPPET.search(text or "")) and len((text or "").split()) <= 60


def size(text: str) -> str:
    """short | medium | long, by how much the user wrote."""
    n = len((text or "").split())
    return "short" if n <= 25 else "medium" if n <= 150 else "long"


def env_fast_model() -> str:
    return os.environ.get("MH_MODEL_FAST", "").strip()


# ---------- sub-subtasks: the kind of debug or build job --------------------------------------------------------------
VARIANTS = {
    "debug": [
        ("perf", r"\b(too slow|slow(er|ly)?|takes? (too )?long|takes? (minutes|hours|forever)|make (it|this) fast|timeouts?|timed out|performance|speed (it )?up|faster|"
                 r"memory (usage|leak)|o\(n\^?2\)|quadratic|hangs?)\b"),
        ("regression", r"\b(worked (before|last (week|month|time)|yesterday|until)|used to work|regression|broke "
                       r"(after|since|when)|since (the |our |my )?(update|upgrade|commit|release|merge|deploy)|"
                       r"stopped working)\b"),
        ("syntax", r"SyntaxError|IndentationError|TabError|unexpected token|syntax error|parse error|error TS1\d{3}|"
                   r"(fails|won't|doesn't|does not) (to )?(start|compile|parse|build)\b"),
        ("test", r"\b(failing tests?|tests? (fail|fails|failed|failing|is red|are red)|test_\w+\.py|\w+\.test\.[jt]s|"
                 r"spec fails|assertion(error)?|pytest|jest|vitest)\b"),
        ("exception", r"Traceback|Exception|\w+Error\b|panic:|segfault|crash(es|ed)?|stack ?trace|undefined is not"),
    ],
    "build": [
        ("feature", r"\bevery existing\b|\bkeep all (the )?(current|existing)\b|\ball (the )?existing (reads?|callers?|"
                    r"call sites?|code paths?)\b|\b(every|each) (read|write|caller|call site|code path)\b|"
                    r"\bbehave[s]? as if\b|\bacross (the |all )?(modules|services|codebase|files)\b"),
        ("cli", r"\b(flag|--[\w-]+|option|sub-?command|argparse|click|cli|command[- ]line)\b"),
        ("api", r"\b(endpoint|route|api|handler|rest|graphql|webhook|pagination|cursor)\b"),
        ("ui", r"\b(page|component|button|form|css|layout|modal|react|vue|svelte|html)\b"),
        ("function", r"\b(function|method|class|helper|util|module)\b"),
    ],
}
_VAR = {k: [(n, re.compile(p, re.I)) for n, p in v] for k, v in VARIANTS.items()}
# algorithm: a function specified by precise rules. One strong marker, or three weak ones.
_ALG_STRONG = re.compile(r"\bO\([^)]*\)|\btie[- ]?break\w*|\bties\b|\bhalf-open\b|\brules?:|\bedge cases?\b|"
                         r"\bcomplexity\b|\bcompetition ranking\b|\bin (linear|logarithmic) time\b|"
                         r"\bbrute[- ]force\b", re.I)
_ALG_WEAK = re.compile(r"\b(overlap\w*|touching|sorted|maximal|empty|inclusive|exclusive|stamp|closed|open|dropped|"
                       r"stable|duplicates?|boundary|descending|ascending)\b", re.I)


def _algorithm(text: str) -> bool:
    text = cap_text(text)
    return bool(_ALG_STRONG.search(text or "")) or len(set(m.lower() for m in _ALG_WEAK.findall(text or ""))) >= 3


def variant(sub: str, text: str) -> str:
    """The most specific kind of job within a sub-task (first match wins; order is by how decisive the signal is)."""
    text = cap_text(text)
    for name, rx in _VAR.get(sub, []):
        if name == "cli" and _algorithm(text):     # after "feature", before the surface-area variants
            return "algorithm"
        if rx.search(text or ""):
            return name
    return ""
