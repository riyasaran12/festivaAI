"""
LLM-simulated environment.

The rule-based checks in festival_environment.py can verify arithmetic and
structure, but they cannot tell whether a plan actually *works* when
something goes wrong. This module uses an LLM as a world simulator: it plays
out a fixed set of incidents against the plan, using ONLY what the plan
states, and reports where the plan breaks down.

The simulator does not improve the plan. Its findings flow back to the
planner as feedback and are also scored by festival_environment.py.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq

from judge import get_text, invoke_with_retry, parse_json_response

load_dotenv(Path(__file__).resolve().parent / ".env")

# Provider: Groq by default. Set SIM_BASE_URL (+ SIM_API_KEY) to run the
# simulator's Qwen model on any OpenAI-compatible provider instead
# (OpenRouter, Together, Alibaba DashScope, a local Ollama server, ...).
SIM_BASE_URL = os.getenv("SIM_BASE_URL")

if SIM_BASE_URL:
    sim_key = os.getenv("SIM_API_KEY")
    key_hint = "SIM_API_KEY"
else:
    sim_key = (
        os.getenv("GROQ_API_KEY_SIMULATOR")
        or os.getenv("GROQ_API_KEY_SIM")
        or os.getenv("GROQ_API_KEY_JUDGE")
    )
    key_hint = "GROQ_API_KEY_SIMULATOR (or GROQ_API_KEY_JUDGE)"

if not sim_key:
    raise ValueError(f"{key_hint} missing from backend/.env")

# Default matches the judge's model; give the simulator its own API key (and
# ideally its own Groq account) so the two do not share a rate limit.
# Override with SIM_MODEL in .env. Qwen models are handled specially.
SIM_MODEL = os.getenv("SIM_MODEL") or "openai/gpt-oss-20b"


def build_llm(model: str, hide_reasoning: bool = True) -> ChatGroq:
    base = {
        "model": model,
        "api_key": sim_key,
        "temperature": 0.3,
        "max_tokens": 6000,
    }

    if SIM_BASE_URL:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=sim_key,
            base_url=SIM_BASE_URL,
            temperature=0.3,
            max_tokens=6000,
        )

    if not hide_reasoning or "qwen" not in model.lower():
        return ChatGroq(**base)

    # Qwen reasoning models need reasoning_format="hidden" with JSON mode,
    # which also keeps thinking text out of the reply. Newer langchain-groq
    # takes it as a named field; older versions only via model_kwargs.
    try:
        return ChatGroq(**base, reasoning_format="hidden")
    except (TypeError, ValueError):
        return ChatGroq(**base, model_kwargs={"reasoning_format": "hidden"})


# Fixed scenario suite so results are comparable between attempts.
SCENARIOS = [
    {
        "id": "heavy_rain",
        "name": "Heavy rain and lightning",
        "description": (
            "Sudden heavy rain with lightning starts during the busiest "
            "performance. Ground turns muddy and people rush for cover."
        ),
    },
    {
        "id": "crowd_surge",
        "name": "Crowd surge at the main entrance",
        "description": (
            "Attendance exceeds the forecast by 20% and a headline act "
            "starts, creating a surge and crush risk at the main gate."
        ),
    },
    {
        "id": "medical_emergency",
        "name": "Simultaneous medical emergencies",
        "description": (
            "A cardiac arrest near the stage and several heat-exhaustion "
            "cases in the food area happen at the same time."
        ),
    },
    {
        "id": "stall_fire",
        "name": "Fire at the food stalls",
        "description": (
            "A cooking-gas fire starts in the food stall row while "
            "families with children are queuing nearby."
        ),
    },
    {
        "id": "power_failure",
        "name": "Power and PA failure at dusk",
        "description": (
            "Main power and the public address system fail at dusk with "
            "the crowd at its peak."
        ),
    },
]

simulator_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You are the simulation engine for a festival-management agent. You act as
the ENVIRONMENT: you play out incidents and report what happens.

You are NOT a planner and NOT a reviewer. Do not improve the plan.

Rules for every scenario:
- Use ONLY the resources, staff, procedures and locations stated in the plan.
- If the plan lacks something the incident needs (a procedure, enough
  people, a communication channel, a vehicle route), the simulation must
  show that failure. Do not invent resources to rescue the plan.
- Be realistic and strict. A plan that only names a topic ("medical
  support will be available") without numbers or procedure should score low.
- Keep each timeline to 3-5 short steps.

Scoring for resilience_score (integer 0-100):
- 80-100: incident handled with clear roles and resources.
- 50-79: handled with notable delays or gaps.
- 0-49: plan breaks down; people are at serious risk.

Return exactly one valid JSON object. No Markdown fences, no commentary.

{{
  "scenarios": [
    {{
      "scenario_id": "heavy_rain",
      "timeline": ["Step 1", "Step 2"],
      "plan_held": false,
      "resilience_score": 0,
      "failure_points": ["What went wrong"],
      "recommended_fixes": ["Concrete change to the plan"]
    }}
  ]
}}

Include exactly one entry per scenario given. Escape quotation marks inside
strings. Do not use trailing commas.
        """,
    ),
    (
        "human",
        """
FESTIVAL DETAILS:
{festival_details}

FESTIVAL PLAN:
{festival_plan}

SCENARIOS TO SIMULATE:
{scenarios}
        """,
    ),
])

def build_chains(hide_reasoning: bool = True) -> dict:
    llm = build_llm(SIM_MODEL, hide_reasoning)
    return {
        "json": simulator_prompt | llm.bind(
            response_format={"type": "json_object"}
        ),
        "plain": simulator_prompt | llm,
        "hide_reasoning": hide_reasoning,
    }


_chains = build_chains()


def is_model_unavailable(exc: Exception) -> bool:
    return (
        type(exc).__name__ == "NotFoundError"
        or "model_not_found" in str(exc)
        or getattr(exc, "status_code", None) == 404
    )


MAX_SIM_TRIES = 2


def _as_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalise_simulation(raw: dict) -> dict:
    """Validate the simulator output and compute scores ourselves.

    Aggregate scores are computed here rather than trusted from the LLM.
    """
    entries = raw.get("scenarios")

    if not isinstance(entries, list):
        raise ValueError("Simulation must contain a 'scenarios' list.")

    by_id = {
        entry.get("scenario_id"): entry
        for entry in entries
        if isinstance(entry, dict)
    }

    results = []

    for scenario in SCENARIOS:
        entry = by_id.get(scenario["id"])

        if entry is None:
            continue

        score = entry.get("resilience_score")

        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue

        results.append({
            "scenario_id": scenario["id"],
            "name": scenario["name"],
            "description": scenario["description"],
            "timeline": _as_str_list(entry.get("timeline")),
            "plan_held": bool(entry.get("plan_held", False)),
            "resilience_score": max(0, min(100, int(round(score)))),
            "failure_points": _as_str_list(entry.get("failure_points")),
            "recommended_fixes": _as_str_list(entry.get("recommended_fixes")),
        })

    if not results:
        raise ValueError("Simulation returned no usable scenario results.")

    scores = [item["resilience_score"] for item in results]

    return {
        "scenarios": results,
        "scenarios_simulated": len(results),
        "scenarios_requested": len(SCENARIOS),
        "average_score": round(sum(scores) / len(scores)),
        "lowest_score": min(scores),
        "failed_scenarios": [
            item["name"] for item in results if not item["plan_held"]
        ],
    }


def _invoke_simulator(payload):
    """Call the simulator; if the model rejects reasoning_format, retry without it."""
    global _chains

    try:
        return invoke_with_retry(
            _chains["json"], payload, fallback_chain=_chains["plain"]
        )
    except Exception as exc:
        if _chains["hide_reasoning"] and "reasoning_format" in str(exc):
            print(
                f"Model '{SIM_MODEL}' does not accept reasoning_format; "
                "retrying without it."
            )
            _chains = build_chains(hide_reasoning=False)
            return invoke_with_retry(
                _chains["json"], payload, fallback_chain=_chains["plain"]
            )
        raise


def simulate_festival(festival_details: str, festival_plan: dict) -> dict:
    """Run the incident suite against a plan. Raises RuntimeError on failure."""
    payload = {
        "festival_details": festival_details,
        "festival_plan": json.dumps(
            festival_plan, ensure_ascii=False, separators=(",", ":")
        ),
        "scenarios": json.dumps(SCENARIOS, ensure_ascii=False),
    }

    last_error = None

    for try_number in range(1, MAX_SIM_TRIES + 1):
        try:
            response = _invoke_simulator(payload)
        except Exception as exc:
            if is_model_unavailable(exc):
                raise RuntimeError(
                    f"Simulator model '{SIM_MODEL}' is not available from "
                    f"{SIM_BASE_URL or 'Groq'} with this key. Check the exact "
                    "model ID in that provider's model list and set "
                    "SIM_MODEL in backend/.env."
                ) from exc
            raise

        try:
            result = normalise_simulation(
                parse_json_response(get_text(response))
            )
            result["model_used"] = SIM_MODEL
            return result
        except (ValueError, TypeError) as exc:
            last_error = exc
            print(f"Simulator output invalid (try {try_number}): {exc}")

    raise RuntimeError(f"Simulator failed: {last_error}")


if __name__ == "__main__":
    sample_plan = {"festival_name": "Demo", "crowd_management": "Two gates."}
    print(json.dumps(simulate_festival("Demo festival", sample_plan), indent=2))