import json
import time
import os

from planner import generate_festival_plan
from judge import evaluate_festival_plan
from simulator import simulate_festival
from festival_environment import evaluate_environment


MAX_ATTEMPTS = max(1, min(6, int(os.getenv("MAX_ATTEMPTS", "3"))))


def build_feedback(evaluation: dict, environment_report: dict, simulation: dict | None) -> dict:
    """Everything the planner needs to fix the next revision."""
    feedback = {
        "judge_verdict": evaluation.get("verdict"),
        "judge_score": evaluation.get("overall_score"),
        "issues": evaluation.get("issues", []),
        "safety_critical_gaps": evaluation.get("safety_critical_gaps", []),
        "recommendations": evaluation.get("recommendations", []),
        "environment_checks": environment_report["environment_checks"],
        "required_action": (
            "Revise the plan to address these failures. "
            "Recalculate the budget and verify all arithmetic."
        ),
    }

    if simulation:
        feedback["simulated_incident_failures"] = [
            {
                "scenario": item["name"],
                "resilience_score": item["resilience_score"],
                "plan_held": item["plan_held"],
                "failure_points": item["failure_points"],
                "recommended_fixes": item["recommended_fixes"],
            }
            for item in simulation["scenarios"]
            if not item["plan_held"] or item["resilience_score"] < 70
        ]

    return feedback


DECISION_RANK = {
    "READY_FOR_HUMAN_REVIEW": 2,
    "HUMAN_REVIEW_REQUIRED": 1,
    "REVISION_REQUIRED": 0,
}


def attempt_score(evaluation, simulation, environment_report) -> tuple:
    """Higher is better: decision first, then checks passed, then scores."""
    checks = environment_report["environment_checks"]
    return (
        DECISION_RANK.get(environment_report["decision"], 0),
        sum(1 for check in checks if check["passed"]),
        (evaluation.get("overall_score") or 0)
        + (simulation["average_score"] if simulation else 0),
    )


def run_festival_pipeline(
    festival_details: str,
    budget_limit: int,
    max_attempts: int = MAX_ATTEMPTS,
    verbose: bool = False,
) -> dict:
    """Planner -> Judge -> Rule checks -> LLM simulation -> feedback loop.

    Shared by the CLI (this file) and the FastAPI endpoint (api.py).
    """

    def log(title, payload=None):
        if verbose:
            print(f"\n=== {title} ===")
            if payload is not None:
                print(json.dumps(payload, indent=2, ensure_ascii=False))

    previous_plan = None
    feedback = None
    plan = evaluation = environment_report = simulation = None
    history = []
    warnings = []
    completed = None  # last fully evaluated attempt
    candidates = []  # every fully evaluated attempt
    stopped_early = False
    attempt = 0

    started = time.time()

    def progress(message):
        print(f"[{time.time() - started:4.0f}s] {message}", flush=True)

    for attempt in range(1, max_attempts + 1):
        log(f"ATTEMPT {attempt}")
        progress(f"Attempt {attempt}/{max_attempts}: planner is writing the plan...")

        try:
            # 1. Planner generates or revises the plan.
            new_plan = generate_festival_plan(
                festival_details,
                previous_plan=previous_plan,
                feedback=feedback,
                budget_limit=budget_limit,
            )
            log("STEP 1: PLANNER", new_plan)
            progress("planner done; judge is evaluating...")

            # 2. Judge independently evaluates the plan.
            new_evaluation = evaluate_festival_plan(festival_details, new_plan)
            log("STEP 2: JUDGE", new_evaluation)
            progress(
                f"judge done (score {new_evaluation.get('overall_score')}, "
                f"{new_evaluation.get('verdict')}); simulator is running incidents..."
            )
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
            stopped_early = True
            break

        plan, evaluation = new_plan, new_evaluation

        # 3. LLM-simulated environment plays out incidents against the plan.
        simulation = None
        simulation_error = None
        try:
            simulation = simulate_festival(festival_details, plan)
            log("STEP 3: SIMULATED INCIDENTS", simulation)
        except Exception as exc:  # bad model id, rate limit, invalid output
            simulation_error = f"{type(exc).__name__}: {exc}"[:300]
            warnings.append(f"Attempt {attempt} simulation failed: {exc}")

        # 4. Deterministic environment checks decide the outcome.
        environment_report = evaluate_environment(
            plan=plan,
            evaluation=evaluation,
            budget_limit=budget_limit,
            simulation=simulation,
            simulation_error=simulation_error,
        )
        log("STEP 4: ENVIRONMENT", environment_report)
        failed_names = [
            check.get("name") or check.get("check")
            for check in environment_report["environment_checks"]
            if not check["passed"]
        ]
        progress(
            f"attempt {attempt} result: {environment_report['decision']}"
            + (f" (failed: {', '.join(failed_names)})" if failed_names else "")
        )

        checks = environment_report["environment_checks"]
        history.append({
            "attempt": attempt,
            "judge_score": evaluation.get("overall_score"),
            "judge_verdict": evaluation.get("verdict"),
            "simulation_score": simulation["average_score"] if simulation else None,
            "checks_passed": sum(1 for c in checks if c["passed"]),
            "checks_total": len(checks),
            "decision": environment_report["decision"],
        })

        completed = (plan, evaluation, simulation, environment_report)
        candidates.append((attempt, completed))

        if environment_report["decision"] != "REVISION_REQUIRED":
            break

        previous_plan = plan
        feedback = build_feedback(evaluation, environment_report, simulation)

    # Retries can make a plan worse, so return the BEST attempt, not the last.
    best_attempt = attempt
    if candidates:
        best_attempt, best = max(
            candidates,
            key=lambda item: attempt_score(item[1][1], item[1][2], item[1][3]),
        )
        # on ties max() keeps the earliest; prefer the later one
        for number, data in candidates:
            if attempt_score(data[1], data[2], data[3]) == attempt_score(
                best[1], best[2], best[3]
            ):
                best_attempt, best = number, data
        plan, evaluation, simulation, environment_report = best
        if best_attempt != attempt:
            progress(f"attempt {best_attempt} was the best result; showing that one")

    for row in history:
        row["selected"] = row["attempt"] == best_attempt

    return {
        "attempts": attempt,
        "best_attempt": best_attempt,
        "plan": plan,
        "evaluation": evaluation,
        "simulation": simulation,
        "environment": environment_report,
        "history": history,
        "warnings": warnings,
        "stopped_early": stopped_early,
    }


def main():
    festival_details = """
    Festival: Riverside Cultural Festival
    Duration: 1 day
    Expected attendance: 5000 people
    Budget: INR 500000
    Location: Outdoor public ground
    Requirements: Music performances, food stalls,
    family activities and accessible facilities.
    """

    result = run_festival_pipeline(
        festival_details,
        budget_limit=500000,
        verbose=True,
    )

    environment = result["environment"]
    evaluation = result["evaluation"]

    print("\n=== FINAL RESULT ===")
    print(f"Attempts completed: {result['attempts']}")
    print(f"Last judge score: {evaluation.get('overall_score')}/100")
    print(f"Last judge verdict: {evaluation.get('verdict')}")
    if result["simulation"]:
        print(f"Simulated resilience: {result['simulation']['average_score']}/100")
    print(f"Environment decision: {environment['decision']}")

    for item in result["history"]:
        print(
            f"  attempt {item['attempt']}: judge {item['judge_score']}, "
            f"sim {item['simulation_score']}, {item['decision']}"
        )

    if environment["decision"] == "READY_FOR_HUMAN_REVIEW":
        print("Automated checks passed. Human approval is still required.")
    elif environment["decision"] == "HUMAN_REVIEW_REQUIRED":
        print("External verification is required:")
        for item in environment["external_verification_pending"]:
            print(f"- {item}")
        print("Human approval is required before real-world use.")
    else:
        print("The plan still needs revision and human review.")


if __name__ == "__main__":
    main()