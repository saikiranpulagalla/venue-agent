# Release Status — v14 Runtime/Privacy Hardening Gate

## Verified in this build environment

- Fast deterministic/security/planner/readiness suite: **79/79 PASS**.
- Resource/LibreOffice suite: **23/23 PASS**, executed in isolated calls/processes.
- Total implemented tests: **102**.
- Mandatory real PPTX vertical slice: **2/2 PASS**.
- Multi-issue constrained-planning gate: **1/1 PASS**.
- Duplicate-text identity gate: **1/1 PASS**.
- v12 visual-safety/font/unsupported-content gate: **5/5 PASS**.
- Unknown-state API gate: **2/2 PASS**.
- v14 deep real-render readiness gate: **1/1 PASS**.
- Remaining API/holdout/no-action/prompt-injection/approval/resource regressions: **PASS**.
- HTTP production-command smoke: **PASS**.
- Frontend inline JavaScript syntax: **PASS**.
- Python compileall: **PASS**.

## v14 runtime/privacy failures closed

1. The global session-map lock is never held while waiting on a per-session lock; a queued request on session A cannot convoy unrelated session B.
2. Session lifetime is now an **idle/sliding TTL** with active-operation leases. A long valid operation cannot finish and immediately lose the session solely because it crossed the old creation-time deadline.
3. A background reaper proactively removes idle-expired session artifacts even when no new request arrives.
4. Startup cleanup is marker-protected and deletes only Venue Agent-owned session directories. A non-empty unowned session root is rejected rather than scrubbed.
5. Session directories and stored PPTX files use private permissions (`0700` directories, `0600` files).
6. Session URL IDs are non-secret handles. Every session HTTP operation requires a separate high-entropy `X-Venue-Token`; only its digest is retained server-side after issuance.
7. Judge-page refresh recovery uses same-tab `sessionStorage`; the capability is never placed in a URL or displayed by the UI.
8. API/session/output responses are non-cacheable and receive privacy/security headers; repaired PPTX downloads use `private, no-store`.
9. Request bytes are bounded by pure ASGI middleware **before multipart parsing/spooling**, including requests without a `Content-Length` header. The endpoint retains its tighter PPTX file-size limit.
10. LibreOffice conversions use a process-wide concurrency semaphore so independent sessions cannot spawn unbounded renderer work.
11. Renderer timeout terminates the entire renderer process group, preventing launcher timeout from leaving descendant processes consuming CPU/RAM.
12. `/health/deep-readiness` proves a real render of the project-owned demo fixture and caches recent success; the Docker health check uses this endpoint.
13. Competition V1 explicitly enforces one worker and declares a one-replica process-local deployment contract. The provided Docker command uses one worker and disables Uvicorn access logs.
14. Gemini planner HTTP work is bounded by `GEMINI_TIMEOUT_MS` and a deterministic circuit breaker. Failure provenance is explicit and fallback does not repeatedly hit a known-bad external dependency during cooldown.
15. Blank `VENUE_SESSION_ROOT` resolves to the dedicated temp workspace; relative, filesystem-root, and home-directory overrides are rejected.
16. Release-candidate provenance no longer depends on `.git` being present in distributed artifacts: validation binds the source to a deterministic sorted source-tree manifest digest and verifies it before/after mandatory checks.

## v13 package/runtime boundary retained

- only valid OOXML presentation packages with required parts are accepted;
- non-canonical ZIP paths, embedded OLE/ActiveX/package payloads, disguised absolute relationship targets, suspicious compression ratios, oversized slide XML/text/run workloads, oversized rendered PDFs, and excessive rendered spans fail closed before or during bounded processing.

## v12 safety-oracle contract retained

`VERIFIED` still requires source immutability, saved-output reopen, protected/rendered text preservation, target improvement, analysis-universe preservation, layout safety, and zero remaining unknown/outside-reference/unsupported-visible-content conditions. Candidate and final verification still fail closed on wrap, overflow, overlap, unrelated text movement, mapping loss, font uncertainty, unsupported content, stale approvals, tampered plan evidence, and source changes.

## Deployment/runtime status

Verified locally:

- `/health`: PASS;
- `/health/readiness`: PASS;
- `/health/deep-readiness`: PASS with a real LibreOffice render;
- renderer concurrency/timeout process-tree tests: PASS;
- static judge homepage: PASS via production-command HTTP smoke;
- capability-authenticated session status/deletion: PASS;
- one-worker process-local session contract: implemented/tested;
- non-root Dockerfile + deep-readiness health check + `--no-access-log`: present.

Not verified in this execution environment:

1. Docker/Podman image build/runtime (no container runtime is installed here);
2. public hosted deployment and its reverse-proxy/platform behavior;
3. live Gemini request with a real credential;
4. real user-research evidence;
5. unrestricted Chromium/Playwright browser E2E;
6. multi-replica operation — **not supported by competition V1 by design**;
7. final WCC video/submission workflow.

## Release-command note

The outer engineering wrapper can terminate long compound commands before completion. v14 resource results above come from observed successful pytest exit codes for all 23 resource tests, plus the production HTTP smoke. The release-candidate script now also binds the source tree by digest so those results can be tied to an exact distributed source snapshot without inventing missing Git history.

## Gate commands

```bash
python scripts/release_gate.py
python scripts/release_gate.py --full
python scripts/http_smoke.py
python scripts/check_frontend_js.py
python scripts/release_candidate.py
```
