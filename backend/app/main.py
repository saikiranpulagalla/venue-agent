from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
import hashlib
import os
import tempfile
import threading
import time
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.agent.planner import GeminiPlanner
from app.domain.models import PlanningContext, RepairConstraints, VenueProfile, WorkflowState
from app.pptx.mutations import ScaleMutation, apply_scale_plan
from app.pptx.renderer import MAX_CONCURRENT_RENDERS, RenderError, libreoffice_version, render_pptx_to_pdf
from app.repair.candidates import generate_candidates
from app.repair.simulation import simulate_candidate, simulate_plan
from app.repair.verifier import verify_output
from app.security.http import MAX_REQUEST_BODY_BYTES, RequestBodyLimitMiddleware, SecurityHeadersMiddleware
from app.security.intake import IntakeError, sha256_path, validate_pptx
from app.storage.sessions import SessionStore
from app.venue.analyzer import VenueAnalyzer
from app.venue.reference import PublicBDMReference


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = PublicBDMReference(ROOT / "reference" / "PUBLIC_BDM_REFERENCE_V1.json")
ANALYZER = VenueAnalyzer(REFERENCE)
def _resolve_session_root(raw: str | None) -> Path:
    value = (raw or "").strip()
    if not value:
        return Path(tempfile.gettempdir()) / "venue-agent-sessions"
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError("VENUE_SESSION_ROOT must be an absolute dedicated directory")
    resolved = path.resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise RuntimeError("VENUE_SESSION_ROOT cannot be the filesystem root or home directory")
    return resolved


SESSION_ROOT = _resolve_session_root(os.getenv("VENUE_SESSION_ROOT"))
STORE = SessionStore(
    SESSION_ROOT,
    ttl_seconds=int(os.getenv("VENUE_SESSION_TTL_SECONDS", "2700")),
    max_sessions=int(os.getenv("VENUE_MAX_SESSIONS", "24")),
)
PLANNER = GeminiPlanner()


def _enforce_single_worker_config() -> None:
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = os.getenv(name)
        if raw:
            try:
                if int(raw) > 1:
                    raise RuntimeError(f"{name} must be 1 because competition V1 session state is process-local")
            except ValueError as e:
                raise RuntimeError(f"{name} must be an integer") from e


_enforce_single_worker_config()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    STORE.start_reaper()
    try:
        yield
    finally:
        STORE.stop_reaper()


app = FastAPI(title="Venue-Aware Presentation Repair Agent", version="0.9.0", lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
app.add_middleware(SecurityHeadersMiddleware)

MAX_REPAIRABLE_ISSUES = 20
MAX_CANDIDATE_SIMULATIONS = 18


def _analysis_review_reasons(summary) -> list[str]:
    reasons: list[str] = []
    if summary.analyzed_elements == 0:
        reasons.append("no_analyzable_text_elements")
    if summary.not_analyzed_elements:
        reasons.append("not_analyzed_elements_present")
    if summary.boundary_review_elements:
        reasons.append("estimated_measurement_boundary_present")
    if summary.outside_reference_elements:
        reasons.append("outside_reference_elements_present")
    if summary.unsupported_visible_content_elements:
        reasons.append("unsupported_visible_content_present")
    return reasons


def _no_action_is_proven(summary) -> bool:
    return (
        summary.analyzed_elements > 0
        and summary.below_target_elements == 0
        and not _analysis_review_reasons(summary)
    )


def _verification_output_is_mechanically_safe(report) -> bool:
    # REVIEW_REQUIRED may mean either "safe mutation, unrelated unknown coverage"
    # or "the mutation itself failed a hard invariant". Only the former may
    # remain available as a review copy.
    return all([
        report.source_sha256_unchanged,
        report.protected_text_unchanged,
        report.output_reopens,
        report.target_improved,
        report.analysis_universe_preserved,
        report.layout_safe,
    ])


def _require_session_capability(
    sid: str,
    x_venue_token: Annotated[str | None, Header(alias="X-Venue-Token")] = None,
) -> None:
    # Capability failures intentionally look identical to unknown session IDs so
    # a non-secret public ID cannot be used as an enumeration oracle.
    if not x_venue_token or not STORE.authorize(sid, x_venue_token):
        raise HTTPException(404, "Unknown session")


_READINESS_LOCK = threading.Lock()
_READINESS_LAST_PROBE_AT = 0.0
_READINESS_LAST_SUCCESS_AT = 0.0
_READINESS_LAST_DETAIL = "not_probed"
_READINESS_PROBE_TTL_S = max(30.0, float(os.getenv("VENUE_READINESS_PROBE_TTL_SECONDS", "300")))
_READINESS_SUCCESS_GRACE_S = max(_READINESS_PROBE_TTL_S, float(os.getenv("VENUE_READINESS_SUCCESS_GRACE_SECONDS", "900")))


def _deep_renderer_readiness() -> tuple[bool, str]:
    global _READINESS_LAST_PROBE_AT, _READINESS_LAST_SUCCESS_AT, _READINESS_LAST_DETAIL
    now = time.monotonic()
    with _READINESS_LOCK:
        cache_ttl = _READINESS_PROBE_TTL_S if _READINESS_LAST_SUCCESS_AT else min(10.0, _READINESS_PROBE_TTL_S)
        if _READINESS_LAST_PROBE_AT and now - _READINESS_LAST_PROBE_AT < cache_ttl:
            return bool(_READINESS_LAST_SUCCESS_AT), _READINESS_LAST_DETAIL
        demo = ROOT / "demo" / "venue-agent-demo.pptx"
        if not demo.exists():
            _READINESS_LAST_PROBE_AT = now
            _READINESS_LAST_DETAIL = "demo_fixture_missing"
            return False, _READINESS_LAST_DETAIL
        try:
            with tempfile.TemporaryDirectory(prefix="venue-readiness-") as td:
                pdf = render_pptx_to_pdf(demo, Path(td), timeout_s=10)
                if not pdf.exists() or pdf.stat().st_size <= 0:
                    raise RenderError("empty readiness PDF")
            _READINESS_LAST_PROBE_AT = now
            _READINESS_LAST_SUCCESS_AT = now
            _READINESS_LAST_DETAIL = "real_demo_render_pass"
            return True, _READINESS_LAST_DETAIL
        except RenderError as e:
            _READINESS_LAST_PROBE_AT = now
            if "capacity is busy" in str(e) and _READINESS_LAST_SUCCESS_AT and now - _READINESS_LAST_SUCCESS_AT <= _READINESS_SUCCESS_GRACE_S:
                _READINESS_LAST_DETAIL = "render_busy_using_recent_success"
                return True, _READINESS_LAST_DETAIL
            _READINESS_LAST_DETAIL = f"render_probe_failed:{type(e).__name__}"
            return False, _READINESS_LAST_DETAIL


class AnalyzeRequest(BaseModel):
    venue: VenueProfile


class PlanRequest(BaseModel):
    constraints: RepairConstraints = RepairConstraints()


class ApproveRequest(BaseModel):
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_id: str | None = None  # backwards-compatible single-candidate input
    plan_hash: str
    idempotency_key: str
    approve: bool = True

    @model_validator(mode="after")
    def normalize_candidate_ids(self):
        if not self.candidate_ids and self.candidate_id:
            self.candidate_ids = [self.candidate_id]
        elif self.candidate_id and self.candidate_ids != [self.candidate_id]:
            raise ValueError("candidate_id and candidate_ids disagree")
        if self.approve and not self.candidate_ids:
            raise ValueError("approved request requires candidate_ids")
        return self


@app.get("/health")
def health():
    return {"status": "ok", "reference_profile": REFERENCE.profile_id}


@app.get("/health/readiness")
def readiness():
    renderer = libreoffice_version()
    demo_exists = (ROOT / "demo" / "venue-agent-demo.pptx").exists()
    planner_mode = "gemini_configured_with_deterministic_fallback" if PLANNER.api_key else "deterministic_fallback"
    ready = bool(renderer and demo_exists)
    return {
        "status": "ready" if ready else "degraded",
        "reference_profile": REFERENCE.profile_id,
        "renderer_version": renderer,
        "demo_fixture": demo_exists,
        "planner_mode": planner_mode,
        "session_ttl_seconds": STORE.ttl_seconds,
        "max_sessions": STORE.max_sessions,
        "session_backend": "process_local_ephemeral",
        "required_replica_count": 1,
        "max_concurrent_renders": MAX_CONCURRENT_RENDERS,
    }


@app.get("/health/deep-readiness")
def deep_readiness():
    renderer = libreoffice_version()
    probe_ok, probe_detail = _deep_renderer_readiness()
    ready = bool(renderer and probe_ok)
    return {
        "status": "ready" if ready else "degraded",
        "reference_profile": REFERENCE.profile_id,
        "renderer_version": renderer,
        "renderer_probe": probe_detail,
        "session_backend": "process_local_ephemeral",
        "required_replica_count": 1,
        "max_concurrent_renders": MAX_CONCURRENT_RENDERS,
    }


@app.get("/api/sessions/{sid}")
def session_status(sid: str, _cap: None = Depends(_require_session_capability)):
    try:
        with STORE.locked(sid) as s:
            return {
                "session_id": s.session_id,
                "state": s.state,
                "source_sha256": s.source_sha256,
                "expires_at": s.expires_at,
                "has_output": bool(s.output_path and s.output_path.exists()),
            }
    except KeyError:
        raise HTTPException(404, "Unknown session")


@app.post("/api/demo")
def create_demo_session():
    demo = ROOT / "demo" / "venue-agent-demo.pptx"
    if not demo.exists():
        raise HTTPException(500, "Demo fixture missing")
    content = demo.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        s = STORE.create("venue-agent-demo.pptx", content, digest)
        token = s.take_issued_capability_token()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {
        "session_id": s.session_id,
        "session_token": token,
        "source_sha256": s.source_sha256,
        "state": s.state,
        "expires_at": s.expires_at,
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(400, "Only .pptx is supported")
    digest = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory(prefix="venue-intake-") as td:
        path = Path(td) / "upload.pptx"
        path.write_bytes(content)
        try:
            report = validate_pptx(path)
        except IntakeError as e:
            raise HTTPException(400, str(e))
        if report.has_external_relationships:
            raise HTTPException(400, "External linked resources are not supported in competition V1")
    try:
        s = STORE.create(file.filename or "source.pptx", content, digest)
        token = s.take_issued_capability_token()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {
        "session_id": s.session_id,
        "session_token": token,
        "source_sha256": s.source_sha256,
        "state": s.state,
        "expires_at": s.expires_at,
    }


@app.post("/api/sessions/{sid}/analyze")
def analyze(sid: str, req: AnalyzeRequest, _cap: None = Depends(_require_session_capability)):
    try:
        with STORE.locked(sid) as s:
            # Any new analysis invalidates all plan/approval/output state from
            # the previous analysis revision. This is intentionally explicit
            # rather than relying on a later hash mismatch.
            s.clear_derived_state(remove_output=True)
            s.analysis = None
            try:
                analysis = ANALYZER.analyze(s.source_path, req.venue, s.root / "analysis")
            except Exception as e:
                s.state = WorkflowState.FAILED
                raise HTTPException(422, f"Analysis failed: {type(e).__name__}: {e}")
            s.analysis = analysis
            if analysis.summary.below_target_elements == 0:
                review_reasons = _analysis_review_reasons(analysis.summary)
                s.state = WorkflowState.NO_ACTION_REQUIRED if _no_action_is_proven(analysis.summary) else WorkflowState.REVIEW_REQUIRED
                return {
                    "state": s.state,
                    "analysis": analysis,
                    "candidates": [],
                    "review_reasons": review_reasons,
                }
            if analysis.summary.below_target_elements > MAX_REPAIRABLE_ISSUES:
                s.state = WorkflowState.REVIEW_REQUIRED
                return {
                    "state": s.state,
                    "analysis": analysis,
                    "candidates": [],
                    "review_reasons": [f"repairable_issue_budget_exceeded:{MAX_REPAIRABLE_ISSUES}"],
                }
            s.state = WorkflowState.ANALYZED
            candidates = generate_candidates(analysis)
            if len(candidates) > MAX_CANDIDATE_SIMULATIONS:
                s.state = WorkflowState.REVIEW_REQUIRED
                s.candidates = []
                return {
                    "state": s.state,
                    "analysis": analysis,
                    "candidates": [],
                    "review_reasons": [
                        f"candidate_simulation_budget_exceeded:{MAX_CANDIDATE_SIMULATIONS}"
                    ] + _analysis_review_reasons(analysis.summary),
                }
            simulated = [simulate_candidate(s.source_path, analysis, c, ANALYZER) for c in candidates]
            s.candidates = simulated
            return {
                "state": s.state,
                "analysis": analysis,
                "candidates": simulated,
                "review_reasons": _analysis_review_reasons(analysis.summary),
            }
    except KeyError as e:
        if e.args == (sid,):
            raise HTTPException(404, "Unknown session")
        raise


@app.post("/api/sessions/{sid}/plan")
def plan(
    sid: str,
    req: PlanRequest = PlanRequest(),
    _cap: None = Depends(_require_session_capability),
):
    try:
        with STORE.locked(sid) as s:
            if s.analysis is None:
                raise HTTPException(409, "Analyze first")
            if s.analysis.summary.below_target_elements == 0:
                s.clear_plan_state(remove_output=True)
                clean = _no_action_is_proven(s.analysis.summary)
                s.state = WorkflowState.NO_ACTION_REQUIRED if clean else WorkflowState.REVIEW_REQUIRED
                return {
                    "state": s.state,
                    "decision": {
                        "selected_candidate_ids": [],
                        "rationale": (
                            "No supported below-target elements require repair." if clean
                            else "No automated repair is justified because analysis coverage is incomplete or outside the modeled reference."
                        ),
                        "requires_human_review": not clean,
                        "unresolved_issue_ids": [],
                        "projected_target_coverage": s.analysis.summary.target_coverage,
                        "planner_used": "deterministic",
                        "fallback_used": False,
                    },
                    "review_reasons": _analysis_review_reasons(s.analysis.summary),
                }
            if s.analysis.summary.below_target_elements > MAX_REPAIRABLE_ISSUES:
                s.clear_plan_state(remove_output=True)
                s.state = WorkflowState.REVIEW_REQUIRED
                return {
                    "state": s.state,
                    "decision": {
                        "selected_candidate_ids": [],
                        "rationale": "Repair issue count exceeds the bounded competition V1 simulation budget.",
                        "requires_human_review": True,
                        "unresolved_issue_ids": [r.element.element_id for r in s.analysis.results if r.state.value == "BELOW_TARGET"],
                        "projected_target_coverage": s.analysis.summary.target_coverage,
                        "planner_used": "deterministic",
                        "fallback_used": False,
                    },
                }

            # Re-planning invalidates any previously approvable plan before the
            # new decision is computed. Candidate simulations remain bound to
            # the current analysis revision.
            s.clear_plan_state(remove_output=True)
            summary = s.analysis.summary
            below_issue_ids = [
                r.element.element_id for r in s.analysis.results
                if r.state.value == "BELOW_TARGET"
            ]
            repairable_issue_ids = sorted({
                c.issue_element_id for c in s.candidates if c.safe and c.simulation_fingerprint
            })
            context = PlanningContext(
                analyzed_elements=summary.analyzed_elements,
                meets_target_elements=summary.meets_target_elements,
                below_target_elements=summary.below_target_elements,
                current_target_coverage=summary.target_coverage,
                below_target_issue_ids=below_issue_ids,
                repairable_issue_ids=repairable_issue_ids,
            )
            decision = PLANNER.choose(s.candidates, req.constraints, context)
            if not decision.selected_candidate_ids:
                review_reasons = _analysis_review_reasons(s.analysis.summary)
                if s.analysis.summary.target_coverage + 1e-12 >= req.constraints.minimum_target_coverage:
                    if decision.requires_human_review or review_reasons:
                        s.state = WorkflowState.REVIEW_REQUIRED
                        if decision.unresolved_issue_ids:
                            review_reasons.append("below_target_issues_remain_unresolved")
                    else:
                        s.state = WorkflowState.TARGET_ALREADY_SATISFIED
                else:
                    s.state = WorkflowState.NO_FEASIBLE_PLAN
                return {"state": s.state, "decision": decision, "review_reasons": sorted(set(review_reasons))}

            by_id = {c.candidate_id: c for c in s.candidates}
            try:
                selected = [by_id[cid] for cid in decision.selected_candidate_ids]
            except KeyError:
                raise HTTPException(500, "Planner selected an unknown candidate")
            if any(not c.safe for c in selected):
                raise HTTPException(500, "Planner selected an unsafe candidate")
            if len({c.issue_element_id for c in selected}) != len(selected):
                raise HTTPException(500, "Planner selected multiple candidates for one issue")

            combined = simulate_plan(s.source_path, s.analysis, selected, ANALYZER)
            if not combined.safe:
                s.state = WorkflowState.NO_FEASIBLE_PLAN
                return {
                    "state": s.state,
                    "decision": decision,
                    "plan_simulation": combined,
                    "review_reasons": _analysis_review_reasons(s.analysis.summary),
                }
            if combined.target_coverage_after + 1e-12 < req.constraints.minimum_target_coverage:
                s.state = WorkflowState.NO_FEASIBLE_PLAN
                combined.reasons.append("combined_plan_below_requested_target_coverage")
                combined.safe = False
                return {
                    "state": s.state,
                    "decision": decision,
                    "plan_simulation": combined,
                    "review_reasons": _analysis_review_reasons(s.analysis.summary),
                }

            s.selected_candidate_ids = list(decision.selected_candidate_ids)
            s.selected_constraints = req.constraints
            s.plan_simulation = combined
            s.plan_hash = s.compute_plan_hash(s.selected_candidate_ids, req.constraints, combined)
            s.state = WorkflowState.AWAITING_APPROVAL
            return {
                "state": s.state,
                "plan_hash": s.plan_hash,
                "decision": decision,
                "plan_simulation": combined,
            }
    except KeyError as e:
        if e.args == (sid,):
            raise HTTPException(404, "Unknown session")
        raise


@app.post("/api/sessions/{sid}/approve-and-apply")
def approve_and_apply(sid: str, req: ApproveRequest, _cap: None = Depends(_require_session_capability)):
    try:
        with STORE.locked(sid) as s:
            if not req.approve:
                if s.state != WorkflowState.AWAITING_APPROVAL:
                    raise HTTPException(409, "No plan is awaiting approval")
                if s.plan_hash != req.plan_hash:
                    raise HTTPException(409, "Stale or mismatched rejection")
                if req.candidate_ids and s.selected_candidate_ids != req.candidate_ids:
                    raise HTTPException(409, "Rejected candidate list does not match current plan")
                s.state = WorkflowState.REJECTED_BY_USER
                return {"state": s.state, "plan_hash": s.plan_hash}
            if req.idempotency_key in s.used_idempotency_keys:
                raise HTTPException(409, "idempotency_key_reused")
            if s.state != WorkflowState.AWAITING_APPROVAL:
                raise HTTPException(409, "approval_missing_or_stale")
            if s.plan_hash != req.plan_hash or s.selected_candidate_ids != req.candidate_ids:
                raise HTTPException(409, "Stale or mismatched approval")
            if s.selected_constraints is None or s.plan_simulation is None:
                raise HTTPException(409, "Missing selected plan evidence")
            if s.analysis is None or s.analysis.source_sha256 != s.source_sha256:
                raise HTTPException(409, "Analysis is not bound to the uploaded source")
            if sha256_path(s.source_path) != s.source_sha256:
                raise HTTPException(409, "Source artifact changed after upload")
            try:
                current_hash = s.compute_plan_hash(s.selected_candidate_ids, s.selected_constraints, s.plan_simulation)
            except ValueError as e:
                raise HTTPException(409, str(e))
            if current_hash != s.plan_hash:
                raise HTTPException(409, "Plan evidence changed after planning")
            if not s.plan_simulation.safe or not all([
                s.plan_simulation.source_unchanged,
                s.plan_simulation.protected_text_unchanged,
                s.plan_simulation.rendered_text_unchanged,
                s.plan_simulation.analysis_universe_preserved,
                s.plan_simulation.layout_safe,
                s.plan_simulation.output_reopens,
                bool(s.plan_simulation.simulation_fingerprint),
            ]):
                raise HTTPException(409, "Plan simulation evidence is no longer executable")
            s.approved_plan_hash = req.plan_hash
            s.used_idempotency_keys.add(req.idempotency_key)
            s.state = WorkflowState.EXECUTING

            by_id = {c.candidate_id: c for c in s.candidates if c.safe}
            selected = [by_id.get(cid) for cid in req.candidate_ids]
            if any(c is None for c in selected) or s.analysis is None:
                s.state = WorkflowState.FAILED
                raise HTTPException(409, "Candidate plan no longer executable")

            mutations: list[ScaleMutation] = []
            target_ids: list[str] = []
            for c in selected:
                assert c is not None
                issue = next((r for r in s.analysis.results if r.element.element_id == c.issue_element_id), None)
                if issue is None or issue.element.shape_id is None:
                    s.state = WorkflowState.FAILED
                    raise HTTPException(409, "Candidate target no longer resolvable")
                mutations.append(ScaleMutation(
                    slide_index=issue.element.slide_index,
                    shape_id=issue.element.shape_id,
                    scale=float(c.parameters["scale"]),
                ))
                target_ids.append(c.issue_element_id)

            output = s.root / "output.pptx"
            try:
                apply_scale_plan(s.source_path, output, mutations)
                report = verify_output(
                    s.source_path,
                    output,
                    s.analysis,
                    target_ids,
                    ANALYZER,
                    s.root / "verification",
                )
            except Exception as e:
                output.unlink(missing_ok=True)
                s.output_path = None
                s.state = WorkflowState.FAILED
                raise HTTPException(422, f"Execution failed: {type(e).__name__}: {e}")
            if _verification_output_is_mechanically_safe(report):
                s.output_path = output
                s.verified_output_sha256 = report.output_sha256
            else:
                output.unlink(missing_ok=True)
                s.output_path = None
                s.verified_output_sha256 = None
            s.state = report.final_state
            return {"state": s.state, "verification": report}
    except KeyError as e:
        if e.args == (sid,):
            raise HTTPException(404, "Unknown session")
        raise


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str, _cap: None = Depends(_require_session_capability)):
    if not STORE.delete(sid):
        raise HTTPException(404, "Unknown session")
    return {"deleted": True}


@app.get("/api/sessions/{sid}/output")
def download_output(sid: str, _cap: None = Depends(_require_session_capability)):
    try:
        with STORE.locked(sid) as s:
            if s.output_path is None:
                raise HTTPException(404, "No output available")
            if s.state not in {WorkflowState.VERIFIED, WorkflowState.REVIEW_REQUIRED}:
                raise HTTPException(409, "Output is not in a downloadable terminal state")
            if not s.output_path.exists():
                s.output_path = None
                s.verified_output_sha256 = None
                s.state = WorkflowState.FAILED
                raise HTTPException(409, "Verified output artifact is missing")
            expected_path = (s.root / "output.pptx").resolve()
            actual_path = s.output_path.resolve()
            if actual_path != expected_path or not s.verified_output_sha256:
                s.output_path = None
                s.verified_output_sha256 = None
                s.state = WorkflowState.FAILED
                raise HTTPException(409, "Verified output identity is unavailable")
            # Read once under the session lock, hash those exact bytes, and
            # serve the same snapshot. This avoids hash-then-reread TOCTOU.
            content = actual_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != s.verified_output_sha256:
                s.output_path = None
                s.verified_output_sha256 = None
                s.state = WorkflowState.FAILED
                raise HTTPException(409, "Verified output artifact changed after verification")
    except KeyError:
        raise HTTPException(404, "Unknown session")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": 'attachment; filename="venue-repaired-copy.pptx"',
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


STATIC_DIR = ROOT / "frontend" / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
