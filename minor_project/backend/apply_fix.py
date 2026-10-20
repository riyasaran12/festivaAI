import shutil

def load(path):
    with open(path) as f:
        return f.read()

# ---------------- judge.py ----------------
src = load("judge.py")
if "RateLimitExhausted" in src:
    print("judge.py already has the fix")
else:
    shutil.copy("judge.py", "judge.py.bak")

    old_start = src.index("            if is_rate_limit_error(exc):")
    old_end = src.index("            if is_json_validate_error(exc):")
    new_block = '''            if is_rate_limit_error(exc):
                wait = retry_delay_seconds(exc, 8.0 * (attempt + 1)) + 1.0
                kind, model = describe_rate_limit(exc)

                # A long reset time means a daily cap: waiting is pointless.
                if wait > MAX_RATE_LIMIT_WAIT:
                    raise RateLimitExhausted(
                        f"Groq rate limit reached ({kind}) for model "
                        f"{model}. It resets in about {humanize_seconds(wait)}. "
                        "Wait until then, lower MAX_ATTEMPTS in backend/.env, "
                        "or upgrade the Groq plan."
                    ) from exc

                print(
                    f"Rate limited ({kind}, {model}); waiting {wait:.1f}s "
                    f"(retry {attempt + 1}/{max_retries})..."
                )
                time.sleep(wait)
                continue

'''
    src = src[:old_start] + new_block + src[old_end:]

    helpers = '''MAX_RATE_LIMIT_WAIT = 90.0  # seconds; longer than this means a daily cap


class RateLimitExhausted(RuntimeError):
    """The provider's limit will not reset soon enough to keep waiting."""


def describe_rate_limit(exc: Exception) -> tuple[str, str]:
    """Return (which limit, which model) from a Groq 429 message."""
    message = str(exc)
    kind = re.search(
        r"on (tokens per (?:day|minute)|requests per (?:day|minute))", message
    )
    model = re.search(r"for model `([^`]+)`", message)
    return (
        kind.group(1) if kind else "rate limit",
        model.group(1) if model else "unknown model",
    )


def humanize_seconds(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


'''
    marker = "def is_json_validate_error"
    src = src.replace(marker, helpers + marker, 1)

    with open("judge.py", "w") as f:
        f.write(src)
    print("judge.py patched (backup: judge.py.bak)")

# ---------------- main.py ----------------
src = load("main.py")
if "completed = None" in src:
    print("main.py already has the fix")
else:
    shutil.copy("main.py", "main.py.bak")

    a = src.index("        # 1. Planner generates or revises the plan.")
    b = src.index("        # 3. LLM-simulated environment")
    middle = '''        try:
            # 1. Planner generates or revises the plan.
            new_plan = generate_festival_plan(
                festival_details,
                previous_plan=previous_plan,
                feedback=feedback,
                budget_limit=budget_limit,
            )
            log("STEP 1: PLANNER", new_plan)

            # 2. Judge independently evaluates the plan.
            new_evaluation = evaluate_festival_plan(festival_details, new_plan)
            log("STEP 2: JUDGE", new_evaluation)
        except (ValueError, RuntimeError) as exc:
            # e.g. invalid JSON, or a rate limit that will not reset soon.
            if completed is None:
                raise
            # Keep the last fully evaluated attempt instead of losing it.
            warnings.append(
                f"Attempt {attempt} stopped early ({exc}). "
                f"Showing the result of attempt {attempt - 1}."
            )
            plan, evaluation, simulation, environment_report = completed
            attempt -= 1
            break

        plan, evaluation = new_plan, new_evaluation

'''
    src = src[:a] + middle + src[b:]

    src = src.replace(
        "    history = []\n    warnings = []\n    attempt = 0\n",
        "    history = []\n    warnings = []\n    completed = None  # last fully evaluated attempt\n    attempt = 0\n",
        1,
    )

    brk = '        if environment_report["decision"] != "REVISION_REQUIRED":\n            break\n'
    assert brk in src, "could not find the stop condition in main.py"
    src = src.replace(
        brk,
        "        completed = (plan, evaluation, simulation, environment_report)\n\n" + brk,
        1,
    )

    with open("main.py", "w") as f:
        f.write(src)
    print("main.py patched (backup: main.py.bak)")
