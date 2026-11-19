"""InfiAgent-DABench (Apache-2.0) as a loop suite: real data-analysis questions with closed-form answers, graded with
the benchmark's own rule (eval_closed_form.py): every @name[value] in the reply must equal the label, exactly or
within 1e-6 numerically. A question counts as solved only if all its sub-answers are right.

    python testkit/loop/dabench.py --n 24 --seed 7      # writes suites/dabench.json (data from ~/.cache/meshpool-bench)
"""
import argparse, json, random, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path.home() / ".cache" / "meshpool-bench" / "InfiAgent" / "examples" / "DA-Agent" / "data"


def extract(text):
    m = re.findall(r"@(\w+)\[(.*?)\]", text or "")
    return dict(m)


def is_equal(resp, label):
    if resp == label:
        return True
    try:
        return abs(float(resp) - float(label)) < 1e-6
    except (TypeError, ValueError):
        return False


def grade(task, answer):
    got = extract(answer)
    labels = task["grade"]["dabench"]
    bad = [k for k, v in labels.items() if not is_equal(got.get(k), v)]
    return (not bad), ("ok" if not bad else f"wrong or missing: {', '.join(f'{k} (expected {labels[k]}, got {got.get(k)})' for k in bad)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    qs = [json.loads(l) for l in open(SRC / "da-dev-questions.jsonl")]
    labels = {json.loads(l)["id"]: json.loads(l)["common_answers"] for l in open(SRC / "da-dev-labels.jsonl")}
    pool = [q for q in qs if q.get("level") in ("medium", "hard")]
    random.Random(a.seed).shuffle(pool)
    out = []
    for q in pool[:a.n]:
        csv = (SRC / "da-dev-tables" / q["file_name"]).read_text(errors="replace")
        out.append({"id": f"dab-{q['id']}", "mode": "work", "category": "data-analysis", "difficulty": q["level"],
                    "prompt": (f"Using {q['file_name']} in the current folder, answer: {q['question']}\n\nConstraints: "
                               f"{q['constraints']}\n\nGive the final answer in exactly this format: {q['format']}"),
                    "files": {q["file_name"]: csv},
                    "grade": {"dabench": {k: v for k, v in labels[q["id"]]}}})
    (HERE / "suites" / "dabench.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {len(out)} DABench questions ({sum(t['difficulty'] == 'hard' for t in out)} hard)")


if __name__ == "__main__":
    main()
