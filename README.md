# Venue-Aware Presentation Repair Agent

A WCC Launchpad 30 Track 01 (Agentic AI) prototype that reasons over an existing presentation plus venue constraints, proposes only bounded repairs, requires explicit human approval before mutation, creates a modified copy, and independently re-verifies the saved artifact.

## Prior research disclosure

The builder previously explored room-aware structural presentation analysis in a separate project. This WCC submission is a new agentic repair-and-verification implementation created from a fresh repository during the official build period. No prior application source, UI, API, tests, or fixtures are reused here.

## Competition V1 contract

Core loop: **Inspect → Analyze → Plan → Simulate → Guard → Approve → Copy → Apply → Reopen → Verify**.

Hard invariants:

1. Original PPTX is immutable.
2. No mutation without approval of the exact plan hash.
3. Presentation content is untrusted data, never instructions.
4. Unsupported/uncertain content never becomes a green result.
5. No semantic rewriting in V1.
6. The model chooses among server-generated candidates; deterministic tools decide truth.
7. Tool success does not equal repair success.
8. Verification starts from the saved output artifact.
9. No accessibility, readability, AVIXA certification, or standards-conformance claim is made.

## Current status

The current build includes a deterministic PPTX/PDF analysis engine, secure intake, a versioned public BDM reference profile, geometry-bound structural/rendered-text mapping, immutable-copy mutation, candidate simulation, constrained global planning across multiple issues, evidence-bound exact-plan human approval, pre-approval rendered-text preservation checks, fresh saved-artifact verification, serialized per-session mutation state, bounded ephemeral session storage, FastAPI endpoints, project-owned development/holdout fixtures, and a no-build judge-facing frontend. Candidate evidence plus the full combined-plan simulation snapshot are bound into the approval hash so changing parameters or simulation evidence after planning invalidates approval. Re-analysis explicitly invalidates stale plans/approvals, and the source hash is rechecked immediately before execution.

v12 adds a fail-closed rendered-transition safety oracle: complete text reconstruction is required for automated mutation; font substitution/autofit uncertainty is non-automatic; candidate and final verification reject wrap, overflow, overlap, unrelated-text movement, or analyzability loss; and `VERIFIED` additionally requires that no unknown/outside-reference/unsupported-content condition remains. Pictures, charts, SmartArt/diagram graphics, media/OLE visuals, and inherited master/layout text are explicit unsupported coverage in V1 and therefore cannot silently receive a whole-deck `VERIFIED` result.

v13 hardened the package/runtime boundary before planning: intake rejects non-OOXML ZIPs, non-canonical part paths, embedded OLE/ActiveX/package payloads, disguised absolute-URI relationship targets, suspicious compression ratios, oversized slide XML, and excessive slide text/run counts before LibreOffice rendering. Rendered PDFs and extracted text spans are bounded.

v14 hardens the public runtime boundary: session IDs are non-secret handles and every session operation requires a separate `X-Venue-Token` capability; session operations use an idle/sliding TTL with active-operation leases plus proactive background cleanup; the global session map never waits on a per-session lock; LibreOffice work has a process-wide concurrency budget and whole-process-group timeout cancellation; request bytes are bounded before multipart parsing; API/output responses are `no-store`; browser `sessionStorage` supports refresh recovery; deep readiness proves a real demo render; the optional Gemini planner has a bounded SDK timeout plus deterministic circuit-breaker fallback; and competition V1 explicitly requires one process/one deployment replica because session state remains process-local.

## Local backend

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

## Judge UI

The backend serves a no-build static judge UI at `/`; no Node/npm toolchain is required for the competition release path. The judge UI supports editable venue inputs and repair constraints, exposes unresolved issues, provides exact-plan Approve/Reject controls, and lets the user explicitly delete the session and its artifacts.

## Tests

```bash
cd backend
pytest -q
```

## Competition evidence

- `docs/JUDGING_MAP.md` maps implemented evidence to the WCC scoring criteria.
- `docs/DEMO_SCRIPT.md` provides the 2–3 minute judge walkthrough.
- `docs/AI_TOOL_DISCLOSURE.md` records runtime AI boundaries and must be updated with every significant development AI tool actually used.
- `validation/RELEASE_STATUS.md` records the current verified and unverified release claims.


## Important limitations

V1 only auto-mutates ordinary, horizontal, structurally mapped body text with explicit font sizing and conservative eligibility checks. Titles and heading-like elements are always review-only. SmartArt, charts, images, media/OLE visuals, unsupported rotations, ambiguous mappings, inherited master/layout text, and uncertain font/autofit behavior are review-only or not analyzed; their presence prevents a whole-deck `VERIFIED` result. Venue results are model/reference outputs, not predictions of individual human readability.

## Optional bounded AI planner

Set `GEMINI_API_KEY` to enable the optional Gemini planner; `GEMINI_MODEL` defaults to `gemini-3.8-flash`. The model receives only bounded, already-safe candidate/simulation metadata, aggregate planning context, and constraints—never presentation instructions/content. It can propose only server-generated candidate IDs. Coverage, unresolved issues, and review status are recomputed server-side; the model cannot self-certify them. The client uses a bounded request timeout (`GEMINI_TIMEOUT_MS`, default 8000 ms); failures open a short circuit and fall back deterministically instead of holding a session lock indefinitely. Planner provenance/fallback reason is exposed in the plan result.


## Deployment readiness

The competition path is a single FastAPI/Uvicorn service serving both API and judge UI. The Dockerfile installs LibreOffice Impress plus DejaVu Sans, runs as a non-root user, uses exactly one worker, disables Uvicorn access logs, and health-checks `/health/deep-readiness`, which performs a cached real demo render. Project-owned demo and validation decks explicitly use DejaVu Sans to avoid theme-font substitution in the container. **Deploy exactly one replica** for competition V1; process-local sessions are intentionally not a multi-replica architecture.

Local production-command smoke:

```bash
python scripts/http_smoke.py
python scripts/check_frontend_js.py
```

Container runtime verification still needs to be performed on a machine/platform with Docker or an equivalent runtime.

## Release gate

```bash
python scripts/release_gate.py
# full isolated renderer/adversarial resource suite:
python scripts/release_gate.py --full
```

For a clean source-commit-bound release candidate, commit all intended source first and run:

```bash
python scripts/release_candidate.py
```

The RC command refuses a dirty tree, scans tracked text files for obvious secrets, verifies required documentation, reruns the mandatory release gate plus HTTP/UI smokes, then writes an ignored JSON report under `validation/reports/` so evidence generation does not mutate the verified source commit.


## Privacy / artifact lifecycle

Uploaded and generated presentation artifacts are process-local and ephemeral. Competition V1 defaults to a 45-minute **idle** TTL and a maximum of 24 sessions. Active/queued operations lease the session and refresh the idle deadline; a background reaper proactively removes expired artifacts even if the service receives no further request. Startup cleanup removes only Venue Agent-owned session directories under a marker-protected dedicated root. Session directories/files use private permissions (`0700`/`0600`). The URL contains only a non-secret random session ID; authorization requires a separate high-entropy `X-Venue-Token` capability that is stored in browser `sessionStorage`, never embedded in session URLs, and not displayed by the judge UI. Original filenames are never used as filesystem paths.
