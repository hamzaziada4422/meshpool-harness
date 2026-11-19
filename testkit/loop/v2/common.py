"""V2 suite helpers: a task is (id, category, difficulty, split, prompt, files, hidden, check, ref).

`ref` holds the reference solution files. It never reaches a harness (only `files` are written into its workspace);
build.py uses it to prove each task fails as given and passes when solved, before the task is admitted."""
TASKS: list = []


def task(id, category, prompt, files, hidden, check, ref, difficulty="hard", split="eval", mode="code", timeout=180):
    TASKS.append({"id": f"v2-{id}", "category": category, "difficulty": difficulty, "split": split, "mode": mode,
                  "prompt": prompt, "files": files, "ref": ref,
                  "grade": {"hidden": hidden, "check": check, "timeout": timeout}})
