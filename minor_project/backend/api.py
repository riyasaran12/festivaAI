import json
import os
import re
import threading

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from judge import RateLimitExhausted, is_rate_limit_error
from main import run_festival_pipeline
from pdf_export import build_plan_pdf

app = FastAPI(title="Festival Management Agent API")

# Local dev origins by default. For a deployed frontend, set CORS_ORIGINS in
# .env as a comma-separated list, e.g. https://festiva.example.com
DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
] or DEFAULT_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,  # no cookies or login are used
    allow_methods=["*"],
    allow_headers=["*"],
)

# One plan at a time: each run spends a lot of LLM tokens, so a double click
# or a second browser tab must not start a second run in parallel.
_generation_lock = threading.Lock()

MAX_EXPORT_BYTES = 2_000_000


class FestivalRequest(BaseModel):
    festival_name: str = Field(min_length=1, max_length=120)
    attendees: int = Field(gt=0, le=1_000_000)
    budget: int = Field(gt=0, le=10_000_000_000)
    venue: str = Field(min_length=1, max_length=80)
    duration: str = Field(min_length=1, max_length=40)

    @field_validator("festival_name", "venue", "duration")
    @classmethod
    def collapse_whitespace(cls, value: str) -> str:
        # These go into the LLM prompt. Collapsing newlines and runs of
        # spaces stops a field from smuggling in extra instruction lines.
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


@app.get("/")
def home():
    return {"message": "Festival Management Agent API is running"}


@app.post("/api/generate-plan")
def generate_plan(request: FestivalRequest):
    festival_details = f"""
    Festival: {request.festival_name}
    Duration: {request.duration}
    Expected attendance: {request.attendees} people
    Budget: INR {request.budget}
    Location: {request.venue}

    Requirements:
    Music performances, food stalls, family activities,
    accessible facilities, crowd management, emergency
    planning, medical support, sanitation, and risk assessment.
    """

    if not _generation_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A plan is already being generated. Wait for it to finish.",
        )

    try:
        result = run_festival_pipeline(
            festival_details,
            budget_limit=request.budget,
        )
    except RateLimitExhausted as exc:
        # The message names the limit, the model and the reset time.
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        # Returned as JSON so the browser can show the real reason.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        if is_rate_limit_error(exc):
            raise HTTPException(
                status_code=429,
                detail=(
                    "The LLM provider's rate limit was reached and retries "
                    "ran out. Wait a minute and try again, or use a model "
                    "with a higher limit."
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    finally:
        _generation_lock.release()

    result["request"] = request.model_dump()
    return result


@app.post("/api/export-pdf")
def export_pdf(payload: dict = Body(...)):
    """Turn a generated result into a downloadable PDF.

    Open by design: no login or token. The plan is sent in the request body
    and nothing is stored on the server.
    """
    if not isinstance(payload.get("plan"), dict):
        raise HTTPException(
            status_code=422,
            detail="The request must include the generated plan.",
        )

    if len(json.dumps(payload)) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="The plan is too large to export.")

    try:
        pdf_bytes = build_plan_pdf(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not build the PDF: {type(exc).__name__}: {exc}",
        ) from exc

    name = (payload.get("request") or {}).get("festival_name") or "festival"
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "festival"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-festival-plan.pdf"'
        },
    )