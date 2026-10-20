import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq

load_dotenv(Path(__file__).resolve().parent / ".env")

judge_key = os.getenv("GROQ_API_KEY_JUDGE")

if not judge_key:
    raise ValueError("GROQ_API_KEY_JUDGE missing from backend/.env")

def make_groq_llm(effort: str | None = None, **kwargs) -> ChatGroq:
    """Build a ChatGroq, optionally limiting gpt-oss reasoning effort.

    Lower effort leaves more of the output budget for the actual JSON.
    Newer langchain-groq versions take reasoning_effort as a named field;
    older ones only through model_kwargs; if neither works we skip it.
    """
    if effort is None:
        return ChatGroq(**kwargs)

    for extra in (
        {"reasoning_effort": effort},
        {"model_kwargs": {"reasoning_effort": effort}},
    ):
        try:
            return ChatGroq(**kwargs, **extra)
        except (TypeError, ValueError):
            continue

    return ChatGroq(**kwargs)


# Lower effort is faster and cheaper; raise it (medium/high) if the judge
# often returns incomplete JSON. Set JUDGE_REASONING_EFFORT in .env.
judge_llm = make_groq_llm(
    effort=os.getenv("JUDGE_REASONING_EFFORT", "low"),
    model="openai/gpt-oss-20b",
    api_key=judge_key,
    temperature=0.1,
)

# Main evaluation prompt
judge_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You are an independent evaluator for a festival-management AI agent.

Evaluate the proposed festival plan against the original requirements.

Check:
1. Whether the plan addresses the original requirements.
2. Whether the budget is within the stated limit and its arithmetic is correct.
3. Whether crowd management and staffing are reasonable.
4. Whether evacuation, fire safety, medical support, sanitation,
   accessibility, and weather risks are addressed.
5. Whether assumptions and unsupported claims are identified.
6. Whether important information is missing.

Score each rubric dimension from 0 to 10 BEFORE deciding the verdict,
and make the verdict consistent with the scores and issues.

Do not assume contact numbers, hospitals, permits, providers, site
dimensions, or emergency response times are real or confirmed unless
the input provides evidence.

Information that can only come from the real world (contact numbers,
permit numbers, insurance details, vendor contracts, site survey and
dimensions, power hookup plans) cannot be supplied by the planner. If the
plan honestly lists it under missing_information and does not claim it is
confirmed, it is NOT a blocking issue: report it at most as severity "low"
and do NOT put it in safety_critical_gaps. Rate it "high" only if the plan
claims it is confirmed, or relies on it without mentioning it is unknown.
Focus blocking findings on things the plan itself can fix (unrealistic
targets, missing procedures, wrong arithmetic, understaffing).

A PASS means no blocking issues were identified and the plan can proceed
to qualified human review. It does not certify the event as safe or legal.

Verdicts:
- PASS: no identified blocking issues.
- NEEDS_REVISION: problems potentially can be corrected.
- FAIL: serious fundamental problems prevent acceptance.

Return exactly one valid JSON object. Do not use Markdown fences.

Required JSON structure:
{{
  "overall_score": 0,
  "verdict": "NEEDS_REVISION",
  "constraint_checks": [],
  "strengths": [],
  "issues": [
    {{
      "issue": "Description",
      "severity": "high",
      "explanation": "Why this matters"
    }}
  ],
  "unsupported_assumptions": [],
  "safety_critical_gaps": [],
  "recommendations": [],
  "rubric_scores": {{
    "requirements_coverage": 0,
    "budget_accuracy": 0,
    "crowd_and_staffing": 0,
    "emergency_readiness": 0,
    "accessibility_and_sanitation": 0,
    "honesty_about_unknowns": 0
  }},
  "ready_for_human_review": false
}}

Rules:
- rubric_scores values must be integers from 0 to 10.
- overall_score must be an integer from 0 to 100.
- verdict must be PASS, NEEDS_REVISION, or FAIL.
- constraint_checks, strengths, issues, unsupported_assumptions,
  safety_critical_gaps, and recommendations must be arrays.
- Each issue must contain issue, severity, and explanation.
- Severity must be low, medium, high, or critical.
- ready_for_human_review must be a boolean.
- Keep explanations concise.
- Escape quotation marks inside JSON strings.
- Do not include comments or trailing commas.
- Ensure all strings, arrays, and objects are closed.
        """,
    ),
    (
        "human",
        """
ORIGINAL FESTIVAL DETAILS:
{festival_details}

GENERATED FESTIVAL PLAN:
{festival_plan}

Evaluate the plan against the original requirements.
        """,
    ),
])

# JSON mode asks Groq for syntactically valid JSON, so repairs are rarer.
judge_chain = judge_prompt | judge_llm.bind(
    response_format={"type": "json_object"}
)
judge_chain_plain = judge_prompt | judge_llm


# Repair prompt for malformed JSON or missing evaluation fields
repair_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You repair malformed JSON evaluation responses.

Convert the supplied response into exactly one valid JSON object
matching this festival evaluation schema:

- overall_score: integer from 0 to 100
- verdict: PASS, NEEDS_REVISION, or FAIL
- constraint_checks: array
- strengths: array
- issues: array of objects containing issue, severity, explanation
- unsupported_assumptions: array
- safety_critical_gaps: array
- recommendations: array
- rubric_scores: object with integer values 0-10 for requirements_coverage,
  budget_accuracy, crowd_and_staffing, emergency_readiness,
  accessibility_and_sanitation, honesty_about_unknowns
- ready_for_human_review: boolean

Preserve the original evaluation's meaning. Do not invent findings.
If a field is missing, use conservative defaults:
- overall_score: 0
- verdict: NEEDS_REVISION
- array fields: []
- ready_for_human_review: false

For issue severity, use low, medium, high, or critical.

Return JSON only. Do not include Markdown fences or commentary.
        """,
    ),
    (
        "human",
        """
The following response could not be parsed or validated.

Problem:
{parse_error}

Original response:
{bad_response}

Repair the response into valid JSON matching the required schema.
        """,
    ),
])

repair_chain = repair_prompt | judge_llm.bind(
    response_format={"type": "json_object"}
)
repair_chain_plain = repair_prompt | judge_llm



# ---------- Rate-limit handling (shared by planner, judge, simulator) ----------

def is_rate_limit_error(exc: Exception) -> bool:
    return (
        type(exc).__name__ == "RateLimitError"
        or getattr(exc, "status_code", None) == 429
        or "rate_limit_exceeded" in str(exc)
    )


def retry_delay_seconds(exc: Exception, fallback: float) -> float:
    """Read Groq's 'try again in 3.99s' hint; fall back to a default."""
    match = re.search(r"try again in ([0-9hms.]+)", str(exc))
    if not match:
        return fallback

    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", match.group(1))
    if not parts:
        return fallback

    return sum(float(value) * units[unit] for value, unit in parts)


MAX_RATE_LIMIT_WAIT = 90.0  # seconds; longer than this means a daily cap


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


def is_json_validate_error(exc: Exception) -> bool:
    """Groq JSON mode rejected the model's output (often an empty reply)."""
    return "json_validate_failed" in str(exc)


def invoke_with_retry(chain, payload, max_retries: int = 5, fallback_chain=None):
    """chain.invoke() that survives 429 rate limits and JSON-mode failures.

    - Rate limit: wait the time Groq reports, then retry.
    - json_validate_failed: retry once, then switch to fallback_chain
      (the same prompt without JSON mode; our own parser handles the text).
    """
    active = chain
    json_failures = 0

    for attempt in range(max_retries + 1):
        try:
            return active.invoke(payload)
        except Exception as exc:
            if attempt == max_retries:
                raise

            if is_rate_limit_error(exc):
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

            if is_json_validate_error(exc):
                json_failures += 1
                if json_failures >= 2 and fallback_chain is not None:
                    print("JSON mode failed twice; retrying without JSON mode...")
                    active = fallback_chain
                elif json_failures >= 3:
                    raise
                else:
                    print(f"JSON mode rejected the output; retrying ({json_failures})...")
                continue

            raise


def strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def get_text(response) -> str:
    """Extract text from a LangChain model response."""
    return strip_reasoning(_raw_text(response))


def _raw_text(response) -> str:
    content = response.content

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))

        return "\n".join(parts)

    return str(content)


def parse_json_response(content: str) -> dict:
    """Parse a JSON object, optionally surrounded by Markdown fences."""
    content = content.strip()

    content = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(r"\s*```\s*$", "", content)

    try:
        result = json.loads(content)

        if isinstance(result, dict):
            return result

        raise ValueError("The JSON response must be an object.")

    except json.JSONDecodeError:
        pass

    # Try extracting the outermost JSON object.
    start = content.find("{")
    end = content.rfind("}")

    if start != -1 and end > start:
        result = json.loads(content[start:end + 1])

        if isinstance(result, dict):
            return result

    raise ValueError("Model response does not contain a valid JSON object.")


def validate_evaluation(evaluation: dict) -> dict:
    """Validate required fields, values, and data types."""
    if not isinstance(evaluation, dict):
        raise TypeError("The evaluation must be a JSON object.")

    required_fields = {
        "overall_score",
        "verdict",
        "constraint_checks",
        "strengths",
        "issues",
        "unsupported_assumptions",
        "safety_critical_gaps",
        "recommendations",
        "ready_for_human_review",
    }

    missing = required_fields - evaluation.keys()

    if missing:
        raise ValueError(f"Missing judge fields: {sorted(missing)}")

    score = evaluation["overall_score"]

    if not isinstance(score, int) or isinstance(score, bool):
        raise TypeError("overall_score must be an integer.")

    if not 0 <= score <= 100:
        raise ValueError("overall_score must be between 0 and 100.")

    if evaluation["verdict"] not in {
        "PASS",
        "NEEDS_REVISION",
        "FAIL",
    }:
        raise ValueError("Invalid judge verdict.")

    list_fields = [
        "constraint_checks",
        "strengths",
        "issues",
        "unsupported_assumptions",
        "safety_critical_gaps",
        "recommendations",
    ]

    for field in list_fields:
        if not isinstance(evaluation[field], list):
            raise TypeError(f"{field} must be a list.")

    if not isinstance(evaluation["ready_for_human_review"], bool):
        raise TypeError("ready_for_human_review must be a boolean.")

    for index, issue in enumerate(evaluation["issues"]):
        if not isinstance(issue, dict):
            raise TypeError(f"Issue {index} must be an object.")

        for field in ("issue", "severity", "explanation"):
            if field not in issue:
                raise ValueError(
                    f"Issue {index} is missing field: {field}"
                )

            if not isinstance(issue[field], str):
                raise TypeError(
                    f"Issue {index} field '{field}' must be a string."
                )

        if issue["severity"] not in {
            "low",
            "medium",
            "high",
            "critical",
        }:
            raise ValueError(
                f"Issue {index} has an invalid severity."
            )

    evaluation["rubric_scores"] = normalise_rubric(
        evaluation.get("rubric_scores")
    )

    return evaluation


RUBRIC_DIMENSIONS = (
    "requirements_coverage",
    "budget_accuracy",
    "crowd_and_staffing",
    "emergency_readiness",
    "accessibility_and_sanitation",
    "honesty_about_unknowns",
)


def normalise_rubric(raw) -> dict:
    """rubric_scores is advisory, so clamp/ignore bad values instead of failing."""
    if not isinstance(raw, dict):
        return {}

    rubric = {}
    for name in RUBRIC_DIMENSIONS:
        value = raw.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rubric[name] = max(0, min(10, int(round(value))))
    return rubric


def evaluate_festival_plan(
    festival_details: str,
    festival_plan: dict,
) -> dict:
    """Evaluate a plan. Re-ask if fields are missing; repair bad syntax once."""
    plan_json = json.dumps(
        festival_plan,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    payload = {
        "festival_details": festival_details,
        "festival_plan": plan_json,
    }

    incomplete = False
    raw_response = ""
    parse_error = ""

    # Up to two fresh judge calls. A reply that is valid JSON but missing
    # fields is re-asked rather than "repaired", because repair would fill
    # the gaps with empty lists and could turn a partial answer into a pass.
    for call_number in (1, 2):
        response = invoke_with_retry(
            judge_chain, payload, fallback_chain=judge_chain_plain
        )
        raw_response = get_text(response)

        try:
            return validate_evaluation(parse_json_response(raw_response))
        except (ValueError, TypeError) as exc:
            parse_error = str(exc)
            incomplete = parse_error.startswith("Missing judge fields")
            print(f"Judge output invalid (call {call_number}): {parse_error}")

            if incomplete:
                # Diagnose: was the reply cut off (finish_reason == "length")?
                metadata = getattr(response, "response_metadata", None) or {}
                try:
                    returned = sorted(parse_json_response(raw_response))
                except ValueError:
                    returned = "unparseable"
                print(
                    f"  finish_reason={metadata.get('finish_reason')}, "
                    f"reply_chars={len(raw_response)}, keys_returned={returned}"
                )

            if not incomplete:
                break  # syntax problem: go straight to the repair step

    print("Requesting one JSON repair attempt...")

    repaired_response = invoke_with_retry(repair_chain, {
        "parse_error": parse_error,
        "bad_response": raw_response,
    }, fallback_chain=repair_chain_plain)

    repaired_text = get_text(repaired_response)

    try:
        evaluation = validate_evaluation(parse_json_response(repaired_text))
    except (ValueError, TypeError) as repair_exc:
        print(f"Judge JSON repair failed: {repair_exc}")
        print("Original response:")
        print(repr(raw_response))
        print("Repaired response:")
        print(repr(repaired_text))

        raise RuntimeError(
            "Judge could not produce valid evaluation JSON. "
            "Inspect the responses printed above."
        ) from repair_exc

    if incomplete:
        # Missing fields were defaulted, so the result cannot be trusted
        # as a clean pass. Force it to need review.
        evaluation["verdict"] = (
            "NEEDS_REVISION" if evaluation["verdict"] == "PASS"
            else evaluation["verdict"]
        )
        evaluation["ready_for_human_review"] = False
        evaluation["issues"].append({
            "issue": "Judge response was incomplete",
            "severity": "medium",
            "explanation": (
                "The judge omitted required fields twice, so this "
                "evaluation was reconstructed and is not a clean pass."
            ),
        })

    return evaluation