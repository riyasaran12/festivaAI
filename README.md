# FestivaAI: Festival Management Agent

**Topic 4: LLM-as-judge / LLM-simulated environments**

A planner LLM drafts a festival management plan. An independent LLM judge scores it. An LLM-simulated environment plays out emergencies against the plan. Deterministic rules decide whether the plan is accepted or sent back for revision.

## How it works

```
 festival details
       |
       v
 [Planner LLM]  gpt-oss-120b   -> JSON plan
       |
       v
 [LLM Judge]    gpt-oss-20b    -> rubric scores, issues, verdict
       |
       v
 [LLM Simulator] gpt-oss-20b   -> plays 5 incidents using ONLY the plan
                  (own API key; model set by SIM_MODEL)
       |
       v
 [Rule-based environment]      -> structure, budget arithmetic, open items,
       |                          judge findings, simulation thresholds
       v
 REVISION_REQUIRED ----> feedback to planner (max 3 attempts)
 HUMAN_REVIEW_REQUIRED / READY_FOR_HUMAN_REVIEW -> stop
```

| Role | File | What it does |
|---|---|---|
| Planner | `planner.py` | Generates / revises the plan as JSON, with JSON-retry |
| LLM-as-judge | `judge.py` | Rubric-based evaluation with schema validation and one repair pass |
| LLM-simulated environment | `simulator.py` | Plays out rain, crowd surge, medical emergencies, stall fire, and power failure against the plan |
| Rule-based environment | `festival_environment.py` | Deterministic checks and the final decision |
| Orchestration | `main.py` | `run_festival_pipeline()` is shared by the CLI and the API |
| API | `api.py` | `POST /api/generate-plan`, `POST /api/export-pdf` (open, no login) |
| PDF export | `pdf_export.py` | Builds the downloadable plan PDF with reportlab |
| UI | `frontend/src/App.jsx` | Dashboard with attempt history, judge rubric, incident results |

### Design choices

- **Separation of roles.** The planner, judge, and simulator are separate prompts. The judge sees the original requirements and never the planner's reasoning.
- **Ground truth is code.** Budget arithmetic and structure are checked deterministically. LLM outputs are evidence, not certificates. The simulator's aggregate scores are recomputed in code, not trusted from the model.
- **The simulator is strict.** It may only use resources the plan states, so vague plans fail visibly.
- **A fixed scenario suite** makes scores comparable across attempts. The UI shows how scores change after each feedback round.
- **Fail safe.** If the simulator is unavailable, the plan is never marked clean. It escalates to `HUMAN_REVIEW_REQUIRED`.

## Example run

Default settings, 3 attempts, Riverside Cultural Festival (5,000 people, INR 500,000):

| Attempt | Judge score | Simulated resilience | Checks passed |
|---|---|---|---|
| 1 | 70 | 41 | 2 / 5 |
| 2 | 80 | 45 | 2 / 5 |
| 3 | 93 (PASS) | 66 | 3 / 5 |

Attempt 3 shows why the simulator is worth having. The judge passed the plan with no issues, but the simulator found that the weather shelters (6 tents x 500 people) hold only 3,000 of 5,000 attendees, and scored the heavy-rain scenario 20/100. It also exposed a medical-staffing inconsistency (3 first-aid tents with 3 paramedics each, but only 6 medical staff in total). A judge reading the plan as text missed both. Results will vary between runs.

## Run it

Backend (inside `backend/`):

```bash
pip install -r requirements.txt
cp .env.example .env        # add your Groq keys
uvicorn api:app --reload    # http://127.0.0.1:8000
python main.py              # optional: run the CLI version
```

Frontend (inside `frontend/`):

```bash
npm install
npm run dev                 # http://localhost:5173
```

Set `VITE_API_URL` if the backend runs somewhere other than `http://127.0.0.1:8000`.

## Limitations (worth stating in your write-up)

- LLM judges and simulators are non-deterministic and can be wrong or inconsistent. Scores are signals, not measurements.
- The simulator is a language model's guess at how an incident unfolds. It is not a physical crowd model.
- Passing every check does not make a festival safe or legal. Permits, insurance, and on-site inspection need qualified humans.

## Ideas to extend

- Run the judge twice and compare, to measure judge agreement.
- Add a human-labelled set of good and bad plans to measure how well the judge matches human ratings.
- Add a baseline (single planner call, no feedback loop) and compare scores against the full loop.
