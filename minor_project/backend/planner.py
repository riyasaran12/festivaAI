import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq

from judge import get_text, invoke_with_retry, make_groq_llm, parse_json_response

load_dotenv(Path(__file__).resolve().parent / ".env")

planner_key = os.getenv("GROQ_API_KEY_PLAN")

if not planner_key:
    raise ValueError("GROQ_API_KEY_PLAN missing from backend/.env")

planner_llm = make_groq_llm(
    effort="low",
    model="openai/gpt-oss-120b",
    api_key=planner_key,
    temperature=0.2,
    max_tokens=8192,
)

MAX_JSON_RETRIES = 3

REQUIRED_KEYS = {
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
}

planner_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You are an expert festival management planner.

Create a practical festival management plan covering:
- Crowd management and entry/exit flow
- Staff and volunteer allocation
- Emergency response and evacuation
- Medical support
- Sanitation and waste management
- Budget and resource allocation
- Weather and operational risks

Never invent confirmed facts. Put unknown details in
missing_information.

Prioritize human safety. Do not claim the plan guarantees safety.

The plan will be stress-tested by a simulator that plays out rain, crowd
surges, medical emergencies, fires and power failures using ONLY what your
plan states. Name concrete resources, locations, headcounts, thresholds and
procedures (who does what, when) instead of vague intentions.

Budget rules:
- budget_allocation must be an object shaped like
  {{"total_budget": 0, "breakdown": {{"item_name": 0}}}}
- Every breakdown value is a plain number in INR (no strings, no nesting).
- The breakdown must sum EXACTLY to total_budget, and total_budget must not
  exceed the stated budget.

missing_information must only list details that genuinely need real-world
confirmation (permits, insurance, vendor and authority contacts, site layout).

Return ONLY valid JSON with these keys:
festival_name, assumptions, crowd_management, staffing,
emergency_plan, medical_support, sanitation,
budget_allocation, risks, missing_information.

JSON hygiene (the output is parsed by a strict JSON parser):
- Numbers must be plain digits like 50000. No commas inside numbers
  (not 5,00,000), no currency symbols, no units.
- Never write arithmetic such as 50000 + 20000 or 10 * 500. Compute the
  result yourself and write the single number.
- Do not use double quotes inside string values; use single quotes instead.
- No comments and no trailing commas.

Use double quotes for all keys and string values.
Do not include Markdown code fences or text outside the JSON.
Ensure all braces, brackets, and quotation marks are correctly closed.
        """,
    ),
    (
        "human",
        """
Festival details:
{festival_details}

Revision instructions:
{revision_instructions}

Return the complete festival plan as a single valid JSON object.
""",
    ),
])

# JSON mode makes the API guarantee syntactically valid JSON output.
planner_chain = planner_prompt | planner_llm.bind(
    response_format={"type": "json_object"}
)
planner_chain_plain = planner_prompt | planner_llm


def build_revision_instructions(
    previous_plan: dict | None,
    feedback: dict | None,
    budget_limit: int | None,
) -> str:
    if previous_plan is None:
        return ""

    budget_text = (
        f"INR {budget_limit}" if budget_limit else "the stated budget"
    )

    return f"""
    Revise the previous plan using the feedback.

    PREVIOUS PLAN:
    {json.dumps(previous_plan, ensure_ascii=False)}

    FEEDBACK (judge findings, rule-based checks and simulated incidents):
    {json.dumps(feedback or {}, ensure_ascii=False)}

    Requirements:
    - Fix all budget arithmetic and keep total spending within {budget_text}.
    - Make staff and volunteer totals match their breakdowns.
    - Address every judge issue, failed environment check and simulated
      failure point with a concrete change to the plan.
    - Never claim permits, insurance, or emergency-service coordination
      are confirmed unless verified.
    - Keep unknown details in missing_information.
    - Return the COMPLETE plan, not a partial plan.
    - Return only valid JSON, with no Markdown or extra text.
    """


def generate_festival_plan(
    festival_details: str,
    previous_plan: dict | None = None,
    feedback: dict | None = None,
    budget_limit: int | None = None,
):
    revision_instructions = build_revision_instructions(
        previous_plan, feedback, budget_limit
    )

    last_error = None
    instructions = revision_instructions

    for try_number in range(1, MAX_JSON_RETRIES + 1):
        response = invoke_with_retry(planner_chain, {
            "festival_details": festival_details,
            "revision_instructions": instructions,
        }, fallback_chain=planner_chain_plain)

        try:
            plan = parse_json_response(get_text(response))
        except ValueError as exc:  # JSONDecodeError is a ValueError
            last_error = exc
            print(f"Planner JSON invalid (try {try_number}): {exc}")
            instructions = (
                revision_instructions
                + f"\n\nYour previous reply was rejected by the JSON parser: "
                f"{exc}. Common causes: arithmetic expressions, numbers with "
                "commas, or unescaped double quotes inside strings. "
                "Return corrected, strictly valid JSON."
            )
            continue

        missing = REQUIRED_KEYS - plan.keys()
        if missing:
            last_error = ValueError(
                f"Planner output is missing keys: {sorted(missing)}"
            )
            print(f"Planner output incomplete (try {try_number}): {last_error}")
            continue

        return plan

    raise ValueError(
        f"Planner did not return a valid plan after {MAX_JSON_RETRIES} "
        f"tries: {last_error}"
    )


if __name__ == "__main__":
    details = """
    Festival: Riverside Cultural Festival
    Duration: 1 day
    Expected attendance: 5000 people
    Budget: INR 500000
    Location: Outdoor public ground
    Requirements: Music performances, food stalls,
    family activities and accessible facilities.
    """

    plan = generate_festival_plan(details, budget_limit=500000)
    print(json.dumps(plan, indent=2))