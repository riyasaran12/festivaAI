import os, shutil

path = "main.py"
if not os.path.exists(path):
    raise SystemExit("main.py not found here - run this inside the backend folder")

with open(path) as f:
    src = f.read()

if "def progress(" in src:
    raise SystemExit("progress lines already added")

edits = [
    ("    for attempt in range(1, max_attempts + 1):\n",
     '''    started = time.time()

    def progress(message):
        print(f"[{time.time() - started:4.0f}s] {message}", flush=True)

    for attempt in range(1, max_attempts + 1):
'''),
    ('        log(f"ATTEMPT {attempt}")\n',
     '        log(f"ATTEMPT {attempt}")\n'
     '        progress(f"Attempt {attempt}/{max_attempts}: planner is writing the plan...")\n'),
    ('            log("STEP 1: PLANNER", new_plan)\n',
     '            log("STEP 1: PLANNER", new_plan)\n'
     '            progress("planner done; judge is evaluating...")\n'),
    ('            log("STEP 2: JUDGE", new_evaluation)\n',
     '            log("STEP 2: JUDGE", new_evaluation)\n'
     '            progress(\n'
     '                f"judge done (score {new_evaluation.get(\'overall_score\')}, "\n'
     '                f"{new_evaluation.get(\'verdict\')}); simulator is running incidents..."\n'
     '            )\n'),
    ('        log("STEP 4: ENVIRONMENT", environment_report)\n',
     '        log("STEP 4: ENVIRONMENT", environment_report)\n'
     '        progress(f"attempt {attempt} result: {environment_report[\'decision\']}")\n'),
]

for old, new in edits:
    if old not in src:
        raise SystemExit(f"could not find this text in main.py:\n{old!r}\nSend this message to Claude.")
    src = src.replace(old, new, 1)

if "import time" not in src:
    src = src.replace("import json\n", "import json\nimport time\n", 1)

shutil.copy(path, path + ".bak3")
with open(path, "w") as f:
    f.write(src)
print("main.py updated (backup: main.py.bak3)")
