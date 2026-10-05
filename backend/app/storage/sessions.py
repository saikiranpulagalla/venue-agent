from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

from app.domain.models import AnalysisResult, PlanSimulationReport, RepairCandidate, RepairConstraints, WorkflowState


ROOT_MARKER = ".venue-agent-session-root-v1"
SESSION_MARKER = ".venue-agent-session-v1"
SESSION_DIR_PREFIX = "session-"


def _capability_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _secure_write(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


@dataclass
class Session:
    session_id: str
    root: Path
    source_path: Path
    source_sha256: str
    capability_hash: str = ""
    created_at: float = 0.0
    last_activity_at: float = 0.0
    expires_at: float = 0.0
    state: WorkflowState = WorkflowState.UPLOADED
    analysis: AnalysisResult | None = None
    candidates: list[RepairCandidate] = field(default_factory=list)
    selected_candidate_ids: list[str] = field(default_factory=list)
    selected_constraints: RepairConstraints | None = None
    plan_simulation: PlanSimulationReport | None = None
    plan_hash: str | None = None
    approved_plan_hash: str | None = None
    used_idempotency_keys: set[str] = field(default_factory=set)
    output_path: Path | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    active_operations: int = 0
    delete_requested: bool = False
    _issued_capability_token: str | None = field(default=None, repr=False, compare=False)

    def take_issued_capability_token(self) -> str:
        token = self._issued_capability_token
        if not token:
            raise RuntimeError("capability_token_already_consumed")
        self._issued_capability_token = None
        return token

    def clear_plan_state(self, *, remove_output: bool = False) -> None:
        self.selected_candidate_ids = []
        self.selected_constraints = None
        self.plan_simulation = None
        self.plan_hash = None
        self.approved_plan_hash = None
        if remove_output:
            if self.output_path is not None:
                self.output_path.unlink(missing_ok=True)
            (self.root / "output.pptx").unlink(missing_ok=True)
            self.output_path = None

    def clear_derived_state(self, *, remove_output: bool = False) -> None:
        self.candidates = []
        self.clear_plan_state(remove_output=remove_output)

    def _candidate_snapshot(self, candidate_ids: list[str]) -> list[dict]:
        by_id = {c.candidate_id: c for c in self.candidates}
        rows: list[dict] = []
        for cid in candidate_ids:
            c = by_id.get(cid)
            if c is None:
                raise ValueError(f"candidate_missing_from_snapshot:{cid}")
            rows.append({
                "candidate_id": c.candidate_id,
                "issue_element_id": c.issue_element_id,
                "operation": c.operation,
                "parameters": c.parameters,
                "predicted_state": c.predicted_state,
                "safe": c.safe,
                "actual_percent_before": c.actual_percent_before,
                "actual_percent_after": c.actual_percent_after,
                "issue_role": c.issue_role,
                "simulation_fingerprint": c.simulation_fingerprint,
            })
        return rows

    def compute_plan_hash(
        self,
        candidate_ids: list[str],
        constraints: RepairConstraints,
        plan_simulation: PlanSimulationReport | None = None,
    ) -> str:
        sim = plan_simulation if plan_simulation is not None else self.plan_simulation
        sim_snapshot = sim.model_dump(mode="json") if sim is not None else None
        payload = json.dumps({
            "source_sha256": self.source_sha256,
            "candidate_ids": candidate_ids,
            "candidate_snapshot": self._candidate_snapshot(candidate_ids),
            "constraints": constraints.model_dump(mode="json"),
            "plan_simulation": sim_snapshot,
        }, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(payload).hexdigest()


class SessionStore:
    def __init__(
        self,
        base: Path,
        *,
        ttl_seconds: int = 45 * 60,
        max_sessions: int = 24,
        clock: Callable[[], float] = time.time,
        clear_orphans_on_start: bool = True,
        reaper_interval_seconds: float | None = None,
    ):
        if ttl_seconds < 60:
            raise ValueError("ttl_seconds must be at least 60")
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self.base = base
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._reaper_interval_seconds = reaper_interval_seconds
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        self._prepare_base(clear_orphans_on_start=clear_orphans_on_start)

    def _prepare_base(self, *, clear_orphans_on_start: bool) -> None:
        if self.base.exists() and not self.base.is_dir():
            raise RuntimeError("session_root_must_be_directory")
        _secure_mkdir(self.base)
        marker = self.base / ROOT_MARKER
        existing = [p for p in self.base.iterdir() if p.name != ROOT_MARKER]
        if not marker.exists():
            if existing:
                raise RuntimeError("session_root_not_owned_by_venue_agent")
            _secure_write(marker, b"venue-agent-session-root-v1\n")
        else:
            try:
                if marker.read_text(encoding="utf-8").strip() != "venue-agent-session-root-v1":
                    raise RuntimeError("session_root_marker_invalid")
            except OSError as e:
                raise RuntimeError("session_root_marker_unreadable") from e
        if clear_orphans_on_start:
            for child in list(self.base.iterdir()):
                if (
                    child.is_dir()
                    and child.name.startswith(SESSION_DIR_PREFIX)
                    and (child / SESSION_MARKER).is_file()
                ):
                    shutil.rmtree(child, ignore_errors=True)

    def start_reaper(self) -> None:
        interval = self._reaper_interval_seconds
        if interval is None:
            interval = max(5.0, min(30.0, self.ttl_seconds / 4.0))
        with self._lock:
            if self._reaper_thread is not None and self._reaper_thread.is_alive():
                return
            self._reaper_stop.clear()
            self._reaper_thread = threading.Thread(
                target=self._reaper_loop,
                args=(float(interval),),
                name="venue-session-reaper",
                daemon=True,
            )
            self._reaper_thread.start()

    def stop_reaper(self) -> None:
        self._reaper_stop.set()
        thread = self._reaper_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._reaper_thread = None

    def _reaper_loop(self, interval: float) -> None:
        while not self._reaper_stop.wait(interval):
            try:
                self.cleanup_expired()
            except Exception:
                # Cleanup is best-effort; request-path lifecycle checks remain authoritative.
                pass

    def _remove_roots(self, roots: list[Path]) -> None:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)

    def _collect_expired_locked(self) -> list[Path]:
        now = self._clock()
        roots: list[Path] = []
        for sid, s in list(self._items.items()):
            if s.active_operations == 0 and not s.delete_requested and now >= s.expires_at:
                s.delete_requested = True
                self._items.pop(sid, None)
                roots.append(s.root)
        return roots

    def cleanup_expired(self) -> int:
        with self._lock:
            roots = self._collect_expired_locked()
        self._remove_roots(roots)
        return len(roots)

    def create(self, filename: str, content: bytes, sha256: str) -> Session:
        del filename
        roots: list[Path]
        with self._lock:
            roots = self._collect_expired_locked()
            if len(self._items) >= self.max_sessions:
                s = None
            else:
                sid = secrets.token_urlsafe(12)
                while sid in self._items:
                    sid = secrets.token_urlsafe(12)
                token = secrets.token_urlsafe(32)
                root = self.base / f"{SESSION_DIR_PREFIX}{sid}"
                _secure_mkdir(root)
                _secure_write(root / SESSION_MARKER, b"venue-agent-session-v1\n")
                source = root / "source.pptx"
                _secure_write(source, content)
                now = self._clock()
                s = Session(
                    session_id=sid,
                    root=root,
                    source_path=source,
                    source_sha256=sha256,
                    capability_hash=_capability_digest(token),
                    created_at=now,
                    last_activity_at=now,
                    expires_at=now + self.ttl_seconds,
                    _issued_capability_token=token,
                )
                self._items[sid] = s
        self._remove_roots(roots)
        if s is None:
            raise RuntimeError("session_capacity_reached")
        return s

    def authorize(self, sid: str, token: str) -> bool:
        roots: list[Path]
        with self._lock:
            roots = self._collect_expired_locked()
            s = self._items.get(sid)
            authorized = bool(
                s is not None
                and not s.delete_requested
                and token
                and secrets.compare_digest(s.capability_hash, _capability_digest(token))
            )
        self._remove_roots(roots)
        return authorized

    def get(self, sid: str) -> Session:
        roots: list[Path]
        with self._lock:
            roots = self._collect_expired_locked()
            s = self._items.get(sid)
        self._remove_roots(roots)
        if s is None or s.delete_requested:
            raise KeyError(sid)
        return s

    @contextmanager
    def locked(self, sid: str):
        """Lease and serialize one live session without convoying other sessions.

        The global map lock is used only to reserve/release the lease; it is
        never held while waiting on the per-session lock. The idle TTL is
        refreshed on entry and completion, so an operation cannot succeed and
        immediately lose its session merely because it ran longer than the old
        absolute creation-time TTL.
        """
        roots: list[Path]
        with self._lock:
            roots = self._collect_expired_locked()
            s = self._items.get(sid)
            if s is None or s.delete_requested:
                reserved = False
            else:
                now = self._clock()
                s.active_operations += 1
                s.last_activity_at = now
                s.expires_at = now + self.ttl_seconds
                reserved = True
        self._remove_roots(roots)
        if not reserved or s is None:
            raise KeyError(sid)

        s.lock.acquire()
        valid = False
        try:
            with self._lock:
                if self._items.get(sid) is s and not s.delete_requested:
                    now = self._clock()
                    s.last_activity_at = now
                    s.expires_at = now + self.ttl_seconds
                    valid = True
            if not valid:
                raise KeyError(sid)
            yield s
        finally:
            s.lock.release()
            with self._lock:
                s.active_operations = max(0, s.active_operations - 1)
                if self._items.get(sid) is s and not s.delete_requested:
                    now = self._clock()
                    s.last_activity_at = now
                    s.expires_at = now + self.ttl_seconds

    def delete(self, sid: str) -> bool:
        roots: list[Path]
        with self._lock:
            roots = self._collect_expired_locked()
            s = self._items.get(sid)
            if s is None or s.delete_requested:
                found = False
            else:
                s.delete_requested = True
                found = True
        self._remove_roots(roots)
        if not found or s is None:
            return False

        # Never hold the global lock while waiting for in-flight session work.
        with s.lock:
            with self._lock:
                if self._items.get(sid) is s:
                    self._items.pop(sid, None)
            shutil.rmtree(s.root, ignore_errors=True)
        return True

    def atomic_begin_execution(self, sid: str, plan_hash: str, idem: str) -> Session:
        # Kept for compatibility with earlier tests/internal callers. Endpoint
        # execution uses the stronger locked() boundary directly.
        with self.locked(sid) as s:
            if idem in s.used_idempotency_keys:
                raise ValueError("idempotency_key_reused")
            if s.state != WorkflowState.AWAITING_APPROVAL or s.approved_plan_hash != plan_hash:
                raise ValueError("approval_missing_or_stale")
            s.used_idempotency_keys.add(idem)
            s.state = WorkflowState.EXECUTING
            return s
