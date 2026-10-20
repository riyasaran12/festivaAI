"""
Environment for the festival agent.

Two layers decide whether a plan is acceptable:
1. Deterministic, rule-based checks (structure, budget arithmetic, open items).
2. An LLM-simulated stress test (see simulator.py) and the LLM judge's verdict.

Rule-based checks are the ground truth. LLM outputs are treated as evidence,
never as a certificate of safety.
"""

REQUIRED_SECTIONS = [
    "festival_name",
    "assumptions",
    "crowd_management",
    "staffing",
    "emergency_plan",
    "medical_support",
    "sanitation",
    "budget_allocation",
    "risks",
    "missing_information",
]

SEVERE_LEVELS = {"high", "critical", "major"}

# Resilience thresholds for the LLM-simulated incidents.
SIM_MIN_AVERAGE = 70
SIM_MIN_SCENARIO = 50

# Items that only the real world can supply. The planner cannot "fix" these,
# so they must never trigger endless revisions; they go to human review.
EXTERNAL_VERIFICATION_KEYWORDS = (
    "site layout",
    "site map",
    "site survey",
    "dimension",
    "coordinates",
    "contact",
    "phone",
    "hospital",
    "police",
    "fire department",
    "vendor",
    "contract",
    "agreement",
    "insurance",
    "permit",
    "licen",
    "authority",
    "brigade",
    "approval",
    "inspection",
    "power source",
    "cabling",
    "generator hookup",
)


def check_required_sections(plan: dict) -> dict:
    missing_sections = [
        section for section in REQUIRED_SECTIONS if section not in plan
    ]

    return {
        "check": "required_sections",
        "name": "Required plan sections",
        "passed": not missing_sections,
        "missing_sections": missing_sections,
        "message": (
            "All required sections are present."
            if not missing_sections
            else f"Missing sections: {', '.join(missing_sections)}"
        ),
    }


def check_budget(plan: dict, budget_limit: int) -> dict:
    budget = plan.get("budget_allocation", {})

    base = {"check": "budget", "name": "Budget arithmetic and limit"}

    if not isinstance(budget, dict):
        return {
            **base,
            "passed": False,
            "allocated": None,
            "budget_limit": budget_limit,
            "difference": None,
            "message": "Budget allocation must be a JSON object.",
        }

    # Support nested and flat budget structures.
    if isinstance(budget.get("breakdown"), dict):
        breakdown = budget["breakdown"]
    else:
        excluded_keys = {
            "total_budget",
            "total_budget_INR",
            "currency",
            "notes",
        }
        breakdown = {
            key: value
            for key, value in budget.items()
            if key not in excluded_keys
        }

    # Reject missing, negative, or non-numeric amounts.
    invalid_items = [
        key
        for key, value in breakdown.items()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
        )
    ]

    if not breakdown or invalid_items:
        return {
            **base,
            "passed": False,
            "allocated": None,
            "budget_limit": budget_limit,
            "difference": None,
            "invalid_items": invalid_items,
            "message": "Budget breakdown is empty or contains invalid amounts.",
        }

    allocated = sum(breakdown.values())
    declared_total = budget.get(
        "total_budget",
        budget.get("total_budget_INR"),
    )

    total_matches = (
        declared_total is None
        or (
            isinstance(declared_total, (int, float))
            and not isinstance(declared_total, bool)
            and declared_total == allocated
        )
    )

    passed = allocated <= budget_limit and total_matches

    if allocated > budget_limit:
        message = "Budget exceeds the user's limit."
    elif not total_matches:
        message = "Declared total does not match the sum of budget items."
    else:
        message = "Budget is within limit."

    return {
        **base,
        "passed": passed,
        "allocated": allocated,
        "budget_limit": budget_limit,
        "difference": budget_limit - allocated,
        "declared_total": declared_total,
        "total_matches_breakdown": total_matches,
        "message": message,
    }


def classify_missing_information(plan: dict) -> dict:
    items = plan.get("missing_information", [])

    if isinstance(items, dict):
        items = [
            f"{key}: {value}" if value else str(key)
            for key, value in items.items()
        ]
    elif not isinstance(items, list):
        items = ["The missing_information field must be a list."]

    external = []
    unresolved = []

    for item in items:
        description = str(item).strip()
        if not description:
            continue

        normalized = description.casefold()

        if any(keyword in normalized for keyword in EXTERNAL_VERIFICATION_KEYWORDS):
            external.append(description)
        else:
            unresolved.append(description)

    return {
        "external_verification_pending": external,
        "unresolved_information": unresolved,
    }


# Words a judge uses when something is simply not known/confirmed yet.
UNCONFIRMED_MARKERS = (
    "unconfirmed",
    "not confirmed",
    "unverified",
    "not verified",
    "missing",
    "pending",
    "not yet",
    "not provided",
    "not obtained",
    "not signed",
    "not secured",
    "no confirmation",
    "without confirmation",
    "lack of",
)

BLOCKING_LEVELS = SEVERE_LEVELS | {"medium"}


def _finding_text(item) -> str:
    if isinstance(item, dict):
        return " ".join(
            str(item.get(key, "")) for key in ("issue", "explanation")
        ).strip()
    return str(item).strip()


# Wording that means the PLAN is missing content (fixable by the planner),
# even if the sentence also mentions vendors, permits, etc.
PLAN_GAP_MARKERS = (
    "not detailed",
    "does not address",
    "does not include",
    "not addressed",
    "omits",
    "omitted",
    "not mentioned",
    "not explicitly",
)


def is_external_finding(item) -> bool:
    """True if a judge finding only says a real-world detail is unconfirmed.

    The planner cannot fix these (permits, insurance, contracts, contacts,
    site survey), so they go to human review instead of blocking the plan.
    """
    text = _finding_text(item).casefold()
    if any(m in text for m in PLAN_GAP_MARKERS):
        return False
    return any(k in text for k in EXTERNAL_VERIFICATION_KEYWORDS) and any(
        m in text for m in UNCONFIRMED_MARKERS
    )


def check_judge_findings(evaluation: dict) -> dict:
    verdict = str(evaluation.get("verdict", "FAIL")).upper()
    issues = evaluation.get("issues", [])
    safety_gaps = evaluation.get("safety_critical_gaps", [])

    if not isinstance(issues, list):
        issues = [{"severity": "high", "issue": "Invalid issues format"}]
    if not isinstance(safety_gaps, list):
        safety_gaps = ["Invalid safety-critical gaps format"]

    external_findings = []
    blocking_issues = []

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "")).lower()
        if severity not in BLOCKING_LEVELS:
            continue
        if is_external_finding(issue):
            external_findings.append(str(issue.get("issue", "")).strip())
        else:
            blocking_issues.append(issue)

    blocking_gaps = []
    for gap in safety_gaps:
        if is_external_finding(gap):
            external_findings.append(_finding_text(gap))
        else:
            blocking_gaps.append(gap)

    # A FAIL verdict always blocks. NEEDS_REVISION only blocks when something
    # the plan itself can fix is left; PASS must have no blocking findings.
    passed = (
        verdict in {"PASS", "NEEDS_REVISION"}
        and not blocking_issues
        and not blocking_gaps
    )

    if passed:
        details = "No judge findings the plan itself can fix."
        if external_findings:
            details += (
                f" {len(external_findings)} item(s) need real-world "
                "confirmation and were sent to human review."
            )
    else:
        details = (
            f"Verdict {verdict}; {len(blocking_issues)} blocking issue(s); "
            f"{len(blocking_gaps)} safety-critical gap(s)."
        )

    return {
        "check": "judge_findings",
        "name": "LLM judge findings",
        "passed": passed,
        "judge_verdict": verdict,
        "high_severity_issue_count": len(blocking_issues),
        "safety_gap_count": len(blocking_gaps),
        "external_findings": external_findings,
        "details": details,
    }


def check_simulation(simulation: dict) -> dict:
    average = simulation["average_score"]
    lowest = simulation["lowest_score"]
    failed = simulation["failed_scenarios"]

    passed = average >= SIM_MIN_AVERAGE and lowest >= SIM_MIN_SCENARIO

    if passed:
        details = (
            f"Average resilience {average}/100, weakest scenario "
            f"{lowest}/100."
        )
    else:
        details = (
            f"Average resilience {average}/100 (need {SIM_MIN_AVERAGE}), "
            f"weakest scenario {lowest}/100 (need {SIM_MIN_SCENARIO})."
        )

    if failed:
        details += f" Plan did not hold in: {', '.join(failed)}."

    return {
        "check": "simulated_incidents",
        "name": "LLM-simulated incident stress test",
        "passed": passed,
        "average_score": average,
        "lowest_score": lowest,
        "min_average": SIM_MIN_AVERAGE,
        "min_scenario": SIM_MIN_SCENARIO,
        "failed_scenarios": failed,
        "details": details,
    }


def evaluate_environment(
    plan: dict,
    evaluation: dict,
    budget_limit: int,
    simulation: dict | None = None,
    simulation_error: str | None = None,
) -> dict:
    checks = [
        check_required_sections(plan),
        check_budget(plan, budget_limit),
    ]

    information = classify_missing_information(plan)

    # Missing details that the planner can reasonably supply.
    unresolved = information["unresolved_information"]

    # External confirmations require real-world verification.
    external_pending = list(information["external_verification_pending"])

    checks.append({
        "check": "missing_information",
        "name": "Open planning questions",
        "passed": len(unresolved) == 0,
        "details": (
            "No unresolved information."
            if not unresolved
            else f"Unresolved information: {unresolved}"
        ),
    })

    judge_check = check_judge_findings(evaluation)
    checks.append(judge_check)

    # Findings that only need real-world confirmation go to human review.
    for finding in judge_check["external_findings"]:
        if finding and finding not in external_pending:
            external_pending.append(finding)

    if simulation is not None:
        checks.append(check_simulation(simulation))
    elif simulation_error:
        # An unavailable simulator must not trigger endless revisions,
        # but it must never be silently treated as a pass either.
        external_pending.append(
            f"LLM incident simulation did not run: {simulation_error}"
        )

    all_passed = all(check["passed"] for check in checks)

    if not all_passed:
        decision = "REVISION_REQUIRED"
    elif external_pending:
        decision = "HUMAN_REVIEW_REQUIRED"
    else:
        decision = "READY_FOR_HUMAN_REVIEW"

    return {
        "environment_checks": checks,
        "all_checks_passed": all_passed,
        "decision": decision,
        "external_verification_pending": external_pending,
        "unresolved_information": unresolved,
        "notice": (
            "Automated checks do not establish that a festival plan "
            "is safe or approved. Verify external details and obtain "
            "qualified human approval before real-world use."
        ),
    }