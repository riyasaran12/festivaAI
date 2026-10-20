import { useRef, useState } from "react";
import "./App.css";

const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

const titleCase = (key) =>
  String(key)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

const inr = (value) => `₹${Number(value).toLocaleString("en-IN")}`;

const DECISIONS = {
  READY_FOR_HUMAN_REVIEW: {
    label: "Ready for human review",
    tone: "good",
    text: "All automated checks passed. A qualified person must still approve this plan.",
  },
  HUMAN_REVIEW_REQUIRED: {
    label: "Human review required",
    tone: "warn",
    text: "Automated checks passed, but some details need real-world confirmation.",
  },
  REVISION_REQUIRED: {
    label: "Revision required",
    tone: "bad",
    text: "The plan still fails checks after the maximum number of attempts.",
  },
};

// Turn any JSON value into readable one-line text.
function formatValue(value) {
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (value !== null && typeof value === "object") {
    return Object.entries(value)
      .map(([k, v]) => `${k.replace(/_/g, " ")}: ${formatValue(v)}`)
      .join(" · ");
  }
  return String(value ?? "Not provided");
}

function scoreTone(value, max = 100) {
  const pct = (value / max) * 100;
  if (pct >= 70) return "good";
  if (pct >= 50) return "warn";
  return "bad";
}

function ScoreBar({ value, max = 100 }) {
  const safe = Math.max(0, Math.min(max, Number(value) || 0));
  return (
    <div className="bar" role="img" aria-label={`${safe} out of ${max}`}>
      <div
        className={`bar-fill ${scoreTone(safe, max)}`}
        style={{ width: `${(safe / max) * 100}%` }}
      />
    </div>
  );
}

function Badge({ tone, children }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

function PlanValue({ value }) {
  if (Array.isArray(value)) {
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>{formatValue(item)}</li>
        ))}
      </ul>
    );
  }
  if (value !== null && typeof value === "object") {
    return (
      <ul>
        {Object.entries(value).map(([k, v]) => (
          <li key={k}>
            <strong>{k.replace(/_/g, " ")}:</strong> {formatValue(v)}
          </li>
        ))}
      </ul>
    );
  }
  return <p>{String(value ?? "Not provided")}</p>;
}

function FindingList({ title, items, empty }) {
  return (
    <div className="evaluation-item">
      <h4>{title}</h4>
      {items?.length ? (
        <ul>
          {items.map((item, index) => (
            <li key={index}>{formatValue(item)}</li>
          ))}
        </ul>
      ) : (
        <p>{empty}</p>
      )}
    </div>
  );
}

function HistoryPanel({ history }) {
  if (!history?.length) return null;
  return (
    <div className="detail-card">
      <h3>Improvement across attempts</h3>
      <p className="detail-description">
        After each failed attempt the judge findings, rule-check failures, and
        simulated incidents are sent back to the planner.
      </p>
      <div className="table-scroll">
        <table className="history-table">
          <thead>
            <tr>
              <th>Attempt</th>
              <th>Judge score</th>
              <th>Simulated resilience</th>
              <th>Checks passed</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {history.map((row) => (
              <tr key={row.attempt}>
                <td>
                  {row.attempt}{" "}
                  {row.selected && <Badge tone="good">Shown below</Badge>}
                </td>
                <td>
                  {row.judge_score}/100 · {titleCase(row.judge_verdict)}
                </td>
                <td>
                  {row.simulation_score === null
                    ? "Did not run"
                    : `${row.simulation_score}/100`}
                </td>
                <td>
                  {row.checks_passed} / {row.checks_total}
                </td>
                <td>
                  <Badge tone={DECISIONS[row.decision]?.tone || "warn"}>
                    {DECISIONS[row.decision]?.label || row.decision}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JudgePanel({ evaluation }) {
  const rubric = Object.entries(evaluation.rubric_scores || {});
  const issues = evaluation.issues || [];
  return (
    <div className="detail-card">
      <h3>AI judge evaluation</h3>
      <p className="detail-description">
        An independent LLM scores the plan against the original requirements.
      </p>

      <div className="score-hero">
        <div className={`score-number ${scoreTone(evaluation.overall_score)}`}>
          {evaluation.overall_score}
          <small>/100</small>
        </div>
        <div>
          <Badge tone={evaluation.verdict === "PASS" ? "good" : "warn"}>
            {titleCase(evaluation.verdict || "unknown")}
          </Badge>
          <p>
            {evaluation.verdict === "PASS" && evaluation.ready_for_human_review
              ? "The judge considers this plan ready for human review."
              : "The judge recommends revising this plan before human review."}
          </p>
        </div>
      </div>

      {rubric.length > 0 && (
        <div className="rubric">
          {rubric.map(([name, score]) => (
            <div className="rubric-row" key={name}>
              <span>{titleCase(name)}</span>
              <ScoreBar value={score} max={10} />
              <strong>{score}/10</strong>
            </div>
          ))}
        </div>
      )}

      <div className="evaluation-item">
        <h4>Issues</h4>
        {issues.length ? (
          <ul className="issue-list">
            {issues.map((item, index) => (
              <li key={index}>
                <Badge
                  tone={
                    ["high", "critical"].includes(item.severity)
                      ? "bad"
                      : item.severity === "medium"
                        ? "warn"
                        : "good"
                  }
                >
                  {item.severity}
                </Badge>{" "}
                <strong>{item.issue}</strong>
                <p>{item.explanation}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p>None reported.</p>
        )}
      </div>

      <FindingList
        title="Safety-critical gaps"
        items={evaluation.safety_critical_gaps}
        empty="None reported."
      />
      <FindingList
        title="Recommendations"
        items={evaluation.recommendations}
        empty="None reported."
      />
      <FindingList
        title="Unsupported assumptions"
        items={evaluation.unsupported_assumptions}
        empty="None reported."
      />
      <FindingList
        title="Strengths"
        items={evaluation.strengths}
        empty="None reported."
      />
    </div>
  );
}

function SimulationPanel({ simulation }) {
  if (!simulation) {
    return (
      <div className="detail-card">
        <h3>Simulated incidents</h3>
        <p className="detail-description">
          The incident simulation did not run for this plan, so it needs extra
          manual review.
        </p>
      </div>
    );
  }
  return (
    <div className="detail-card">
      <h3>Simulated incidents</h3>
      <p className="detail-description">
        An LLM acts as the environment and plays out {simulation.scenarios_simulated}{" "}
        incidents using only what the plan states. Average resilience:{" "}
        <strong>{simulation.average_score}/100</strong>.
        {simulation.model_used && <> Simulated by {simulation.model_used}.</>}
      </p>
      <div className="sim-grid">
        {simulation.scenarios.map((item) => (
          <article className="sim-card" key={item.scenario_id}>
            <div className="sim-head">
              <h4>{item.name}</h4>
              <Badge tone={item.plan_held ? "good" : "bad"}>
                {item.plan_held ? "Plan held" : "Plan failed"}
              </Badge>
            </div>
            <p className="sim-desc">{item.description}</p>
            <div className="rubric-row">
              <ScoreBar value={item.resilience_score} />
              <strong>{item.resilience_score}/100</strong>
            </div>
            {item.timeline.length > 0 && (
              <ol className="sim-timeline">
                {item.timeline.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            )}
            {item.failure_points.length > 0 && (
              <>
                <h5>Where it broke down</h5>
                <ul>
                  {item.failure_points.map((text, index) => (
                    <li key={index}>{text}</li>
                  ))}
                </ul>
              </>
            )}
            {item.recommended_fixes.length > 0 && (
              <>
                <h5>Suggested fixes</h5>
                <ul>
                  {item.recommended_fixes.map((text, index) => (
                    <li key={index}>{text}</li>
                  ))}
                </ul>
              </>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value, tone }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function TagList({ items, tone }) {
  if (!items?.length) return null;
  return (
    <ul className="tag-list">
      {items.map((item, index) => (
        <li className={`tag ${tone || ""}`} key={index}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function CheckDetails({ check, environment }) {
  switch (check.check) {
    case "required_sections":
      return <TagList items={check.missing_sections} tone="bad" />;

    case "budget": {
      if (check.allocated === null || check.allocated === undefined) return null;
      const within = check.allocated <= check.budget_limit;
      const pct = Math.min(100, (check.allocated / check.budget_limit) * 100);
      return (
        <>
          <div className="metric-row">
            <Metric label="Budget limit" value={inr(check.budget_limit)} />
            <Metric
              label="Allocated"
              value={inr(check.allocated)}
              tone={within ? "good" : "bad"}
            />
            <Metric
              label={check.difference < 0 ? "Over by" : "Unallocated"}
              value={inr(Math.abs(check.difference))}
              tone={check.difference < 0 ? "bad" : ""}
            />
            {check.declared_total !== null && check.declared_total !== undefined && (
              <Metric
                label="Declared total"
                value={inr(check.declared_total)}
                tone={check.total_matches_breakdown ? "" : "bad"}
              />
            )}
          </div>
          <div className="bar">
            <div className={`bar-fill ${within ? "good" : "bad"}`} style={{ width: `${pct}%` }} />
          </div>
        </>
      );
    }

    case "missing_information":
      return environment.unresolved_information?.length ? (
        <ul className="check-list">
          {environment.unresolved_information.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      ) : null;

    case "judge_findings":
      return (
        <>
          <div className="metric-row">
            <Metric
              label="Judge verdict"
              value={titleCase(check.judge_verdict || "unknown")}
              tone={check.judge_verdict === "PASS" ? "good" : "warn"}
            />
            <Metric
              label="Blocking issues"
              value={check.high_severity_issue_count ?? 0}
              tone={check.high_severity_issue_count ? "bad" : "good"}
            />
            <Metric
              label="Safety gaps"
              value={check.safety_gap_count ?? 0}
              tone={check.safety_gap_count ? "bad" : "good"}
            />
            <Metric label="Sent to human review" value={check.external_findings?.length ?? 0} />
          </div>
        </>
      );

    case "simulated_incidents":
      return (
        <>
          <div className="metric-row">
            <Metric
              label={`Average (need ${check.min_average ?? 70})`}
              value={`${check.average_score}/100`}
              tone={scoreTone(check.average_score)}
            />
            <Metric
              label={`Weakest (need ${check.min_scenario ?? 50})`}
              value={`${check.lowest_score}/100`}
              tone={check.lowest_score >= (check.min_scenario ?? 50) ? "good" : "bad"}
            />
            <Metric
              label="Scenarios that failed"
              value={check.failed_scenarios?.length ?? 0}
              tone={check.failed_scenarios?.length ? "bad" : "good"}
            />
          </div>
          <TagList items={check.failed_scenarios} tone="bad" />
        </>
      );

    default:
      return null;
  }
}

function EnvironmentPanel({ environment }) {
  const checks = environment?.environment_checks || [];
  const passedCount = checks.filter((check) => check.passed).length;
  const failedCount = checks.length - passedCount;
  const unresolved = environment?.unresolved_information || [];
  const external = environment?.external_verification_pending || [];

  return (
    <div className="detail-card">
      <h3>Environment validation</h3>
      <p className="detail-description">
        Rule-based checks, plus the judge and incident-simulation outcomes.
        These decide whether the plan is revised or sent to a person.
      </p>

      <div className="env-summary">
        <div className="env-count good">
          <strong>{passedCount}</strong>
          <span>passed</span>
        </div>
        <div className={`env-count ${failedCount ? "bad" : "good"}`}>
          <strong>{failedCount}</strong>
          <span>failed</span>
        </div>
        <div className="env-count">
          <strong>{external.length}</strong>
          <span>need real-world confirmation</span>
        </div>
      </div>

      <div className="check-cards">
        {checks.map((check, index) => (
          <article
            className={`check-card ${check.passed ? "passed" : "failed"}`}
            key={index}
          >
            <div className="check-card-head">
              <span className={check.passed ? "check-status passed" : "check-status failed"}>
                {check.passed ? "✓" : "!"}
              </span>
              <h4>{check.name || check.check || `Validation ${index + 1}`}</h4>
              <Badge tone={check.passed ? "good" : "bad"}>
                {check.passed ? "Passed" : "Failed"}
              </Badge>
            </div>
            <p className="check-card-msg">
              {check.message || check.details || (check.passed ? "Passed" : "Needs attention")}
            </p>
            <CheckDetails check={check} environment={environment} />
          </article>
        ))}
      </div>

      <div className="env-lists">
        <div className="env-list fix">
          <h4>The planner can still fix</h4>
          {unresolved.length ? (
            <ul>
              {unresolved.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : (
            <p>Nothing left for the planner to resolve.</p>
          )}
        </div>
        <div className="env-list confirm">
          <h4>Needs real-world confirmation</h4>
          {external.length ? (
            <ul>
              {external.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : (
            <p>Nothing pending.</p>
          )}
        </div>
      </div>

      {environment?.notice && <p className="notice-box">{environment.notice}</p>}

    </div>
  );
}

const STORAGE_KEY = "festivaai:last-result";

// Keep the last plan in the browser so a page reload does not lose it.
function loadSavedResult() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveResult(data) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* storage full or blocked: ignore */
  }
}

function clearSavedResult() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

// Plain-language summary for the top card. A plan always exists once a run
// finishes, so say that first, then say how far it got through the checks.
function summarizeDecision(result, checks, decision) {
  const disclaimer =
    "This is an AI-generated proposal; human review is required before real-world use.";

  if (!result) return { ...decision, lines: [disclaimer] };

  const total = checks.length;
  const passed = checks.filter((c) => c.passed).length;
  const failedNames = checks
    .filter((c) => !c.passed)
    .map((c) => c.name || c.check);
  const key = result.environment?.decision;

  if (key !== "REVISION_REQUIRED") {
    return { label: decision.label, tone: decision.tone, lines: [decision.text, disclaimer] };
  }

  const judgeOk = result.evaluation?.verdict === "PASS";
  const shown =
    result.attempts > 1 && result.best_attempt
      ? `Attempt #${result.best_attempt} of ${result.attempts} is shown below. `
      : "";
  const opening = judgeOk
    ? `The AI judge passed it (${result.evaluation.overall_score}/100), but it passed ${passed} of ${total} automated checks.`
    : `It passed ${passed} of ${total} automated checks.`;

  return {
    label: "Plan generated: needs more work",
    tone: total && passed / total >= 0.5 ? "warn" : "bad",
    lines: [
      shown + opening,
      failedNames.length ? `Still failing: ${failedNames.join(" · ")}.` : "",
      result.stopped_early
        ? "The run stopped early (see the warning below)."
        : "",
      disclaimer,
    ],
  };
}

function App() {
  const [festivalName, setFestivalName] = useState("Riverside Cultural Festival");
  const [attendees, setAttendees] = useState(5000);
  const [budget, setBudget] = useState(500000);
  const [venue, setVenue] = useState("Outdoor public ground");
  const [duration, setDuration] = useState("1 day");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(loadSavedResult);
  const [restored, setRestored] = useState(() => loadSavedResult() !== null);
  const resultsRef = useRef(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");

  const handleSubmit = async (event) => {
    event.preventDefault();

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const response = await fetch(`${API_URL}/api/generate-plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          festival_name: festivalName,
          attendees: Number(attendees),
          budget: Number(budget),
          venue,
          duration,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map((d) => d.msg).join("; ")
          : data.detail;
        throw new Error(detail || "Failed to generate festival plan.");
      }

      setResult(data);
      setRestored(false);
      saveResult(data);
      // bring the finished plan into view
      setTimeout(
        () => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
        150
      );
    } catch (err) {
      setError(
        err instanceof TypeError
          ? `Could not reach the backend at ${API_URL}. Is it running?`
          : err.message || "Could not connect to the backend."
      );
    } finally {
      setLoading(false);
    }
  };

  // Asks the backend to build the PDF, then saves it. No login or token needed.
  const downloadPdf = async () => {
    setPdfBusy(true);
    setPdfError("");

    try {
      const response = await fetch(`${API_URL}/api/export-pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Could not create the PDF.");
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const slug =
        (result.request?.festival_name || "festival")
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "") || "festival";

      const link = document.createElement("a");
      link.href = url;
      link.download = `${slug}-festival-plan.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setPdfError(
        err instanceof TypeError
          ? `Could not reach the backend at ${API_URL}. Is it running?`
          : err.message || "Could not create the PDF."
      );
    } finally {
      setPdfBusy(false);
    }
  };

  const clearSaved = () => {
    clearSavedResult();
    setResult(null);
    setRestored(false);
  };

  const request = result?.request;
  const environment = result?.environment;
  const checks = environment?.environment_checks || [];
  const decision = DECISIONS[environment?.decision] || {
    label: "Not available",
    tone: "warn",
    text: "",
  };
  const summary = summarizeDecision(result, checks, decision);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">Festiva<span>AI</span></div>
        <p className="sidebar-label">WORKSPACE</p>
        <div className="nav-item active">▦ &nbsp; Dashboard</div>
        <div className="nav-item">✦ &nbsp; Festival Planner</div>
        <div className="nav-item">◉ &nbsp; Safety & Risk</div>
        <div className="nav-item">✓ &nbsp; AI Evaluation</div>
        <div className="sidebar-bottom">LLM Planner + AI Judge + Simulated Environment</div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div>
            <p className="eyebrow">FESTIVAL OPERATIONS</p>
            <h1>Festival Dashboard</h1>
          </div>
          <div className="status-pill">
            <span className="status-dot" />{" "}
            {loading ? "Agent is working" : "Agent ready to configure"}
          </div>
        </header>

        <section className="welcome">
          <div>
            <p className="eyebrow light">INTELLIGENT EVENT PLANNING</p>
            <h2>Plan smarter. Celebrate safer.</h2>
            <p>
              A planner LLM drafts the plan, an independent AI judge scores it,
              and a simulated environment plays out rain, crowd surges, medical
              emergencies, fires, and power failures to find where it breaks.
            </p>
          </div>
          <div className="welcome-icon">✳</div>
        </section>

        <section className="stats">
          <article className="stat-card">
            <span>PROJECT STATUS</span>
            <strong>{loading ? "Working" : result ? "Plan generated" : "Ready"}</strong>
            <small>Planner · Judge · Simulator</small>
          </article>
          <article className="stat-card">
            <span>EXPECTED ATTENDANCE</span>
            <strong>{Number(attendees).toLocaleString("en-IN")}</strong>
            <small>Festival attendees</small>
          </article>
          <article className="stat-card">
            <span>PLANNING BUDGET</span>
            <strong>{inr(budget)}</strong>
            <small>Configured budget limit</small>
          </article>
        </section>

        <section className="workspace">
          <div className="section-heading">
            <div>
              <p className="eyebrow">NEW PLAN</p>
              <h2>Festival details</h2>
              <p>Configure the event before generating a plan.</p>
            </div>
            <span className="step-label">STEP 01 / 03</span>
          </div>

          <form onSubmit={handleSubmit}>
            <div className="form-grid">
              <label className="field full-width">
                Festival name
                <input
                  value={festivalName}
                  onChange={(e) => setFestivalName(e.target.value)}
                  required
                />
              </label>

              <label className="field">
                Expected attendance
                <input
                  type="number"
                  min="1"
                  value={attendees}
                  onChange={(e) => setAttendees(e.target.value)}
                  required
                />
              </label>

              <label className="field">
                Total budget (INR)
                <input
                  type="number"
                  min="1"
                  value={budget}
                  onChange={(e) => setBudget(e.target.value)}
                  required
                />
              </label>

              <label className="field">
                Venue type
                <select value={venue} onChange={(e) => setVenue(e.target.value)}>
                  <option>Outdoor public ground</option>
                  <option>Indoor auditorium</option>
                  <option>Park or open space</option>
                  <option>University campus</option>
                </select>
              </label>

              <label className="field">
                Duration
                <select value={duration} onChange={(e) => setDuration(e.target.value)}>
                  <option>Half day</option>
                  <option>1 day</option>
                  <option>2 days</option>
                  <option>3 days</option>
                  <option>More than 3 days</option>
                </select>
              </label>
            </div>

            <div className="form-footer">
              <p>✦ &nbsp; AI-generated plans require human safety review.</p>
              <button type="submit" disabled={loading}>
                {loading ? "Generating festival plan..." : "Generate festival plan"}
              </button>
            </div>
          </form>
        </section>

        {loading && (
          <section className="result-panel loading-panel" role="status">
            <div className="loading-spinner" />
            <h2>Building your festival plan...</h2>
            <p>
              The planner drafts, the judge scores, the simulator stress-tests,
              and the environment decides whether to revise. This can take a
              minute or two.
            </p>
          </section>
        )}

        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}

        {result && (
          <section className="results-section" ref={resultsRef}>
            {restored && (
              <p className="saved-note">
                Showing your last saved plan.{" "}
                <button type="button" onClick={clearSaved}>
                  Clear
                </button>
              </p>
            )}
            <div className="section-heading">
              <div>
                <p className="eyebrow">AI ANALYSIS</p>
                <h2>Festival planning results</h2>
                <p>Review the proposed plan and its evaluation.</p>
              </div>
              <div className="heading-actions">
                <button
                  type="button"
                  className="download-btn"
                  onClick={downloadPdf}
                  disabled={pdfBusy}
                >
                  {pdfBusy ? "Preparing PDF..." : "⬇ Download plan (PDF)"}
                </button>
                <span className="step-label">
                  {result.attempts} attempt(s)
                  {result.attempts > 1 && result.best_attempt
                    ? ` · best: #${result.best_attempt}`
                    : ""}
                </span>
              </div>
            </div>

            {pdfError && (
              <p className="error-message" role="alert">
                {pdfError}
              </p>
            )}

            <div className={`decision-card ${summary.tone}`}>
              <div>
                <p className="eyebrow">ENVIRONMENT DECISION</p>
                <h2>{summary.label}</h2>
                {summary.lines.filter(Boolean).map((line, index) => (
                  <p className="decision-line" key={index}>
                    {line}
                  </p>
                ))}
              </div>
              <div className="decision-icon">✦</div>
            </div>

            {result.warnings?.length > 0 && (
              <div className="warning-box" role="alert">
                {result.warnings.map((text, index) => (
                  <p key={index}>{text}</p>
                ))}
              </div>
            )}

            <div className="result-grid">
              <article className="result-card">
                <span className="result-icon">✧</span>
                <p>Festival</p>
                <h3>{request?.festival_name}</h3>
                <small>{request?.duration} · {request?.venue}</small>
              </article>

              <article className="result-card">
                <span className="result-icon">◎</span>
                <p>Expected attendance</p>
                <h3>{Number(request?.attendees).toLocaleString("en-IN")}</h3>
                <small>People expected</small>
              </article>

              <article className="result-card">
                <span className="result-icon">₹</span>
                <p>Budget limit</p>
                <h3>{inr(request?.budget)}</h3>
                <small>Maximum configured budget</small>
              </article>

              <article className="result-card">
                <span className="result-icon">✓</span>
                <p>Environment checks</p>
                <h3>
                  {checks.filter((check) => check.passed).length} / {checks.length}
                </h3>
                <small>Checks passed</small>
              </article>
            </div>

            <HistoryPanel history={result.history} />

            <div className="detail-card">
              <h3>Festival plan</h3>
              <p className="detail-description">
                Proposed arrangements generated by the planner LLM.
              </p>

              <div className="detail-list">
                {Object.entries(result.plan || {}).map(([key, value]) => (
                  <div className="detail-row" key={key}>
                    <h4>{titleCase(key)}</h4>
                    <div className="detail-value">
                      <PlanValue value={value} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {result.evaluation && <JudgePanel evaluation={result.evaluation} />}

            <SimulationPanel simulation={result.simulation} />

            <EnvironmentPanel environment={environment} />
          </section>
        )}

        <footer>
          <span>FestivaAI · Festival Management Agent</span>
          <span>Planner LLM · LLM Judge · LLM-simulated environment</span>
        </footer>
      </main>
    </div>
  );
}

export default App;