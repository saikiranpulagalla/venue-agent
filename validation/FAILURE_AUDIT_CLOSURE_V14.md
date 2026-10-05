# Failure Audit Closure — v14 Runtime / Privacy Boundary

This gate follows the v13 package/runtime-input boundary and the v12 safety-oracle gate. It targets failures that appear only once the prototype is exposed as a shared public service: scheduling fairness, lifecycle cleanup, confidentiality, HTTP ingress, renderer cancellation, deployment topology, external-planner latency, and release provenance.

| Severity | Former failure | v14 closure | Regression evidence |
|---|---|---|---|
| P1 | Waiting for session A held the global store lock and convoyed unrelated session B | Global lock only reserves/releases leases; per-session wait occurs after global lock release | `test_waiting_on_one_session_does_not_block_unrelated_session` |
| P1 | Independent sessions could start unbounded LibreOffice conversions | Process-wide bounded renderer semaphore + bounded queue wait | `test_global_renderer_budget_rejects_excess_cross_session_work` |
| P1 | Renderer timeout could leave descendants alive | Renderer launched in its own process group; timeout TERM→KILLs the group | `test_render_timeout_terminates_descendant_process_group` |
| P1 | TTL deletion happened only opportunistically | Background reaper + idle/sliding TTL + active-operation lease | `test_proactive_reaper_deletes_idle_expired_artifacts_without_new_request`; `test_operation_lease_refreshes_idle_ttl_even_when_waiting` |
| P1 | Session ID was both URL identifier and bearer secret | Non-secret ID + separate `X-Venue-Token` capability; server retains digest | `test_session_public_id_does_not_authorize_without_separate_capability`; store capability test |
| P1 | Large multipart data could reach parser/spool before endpoint byte check | Pure ASGI request-body limiter counts streamed bytes before multipart handling | `test_body_limit_rejects_chunked_request_without_content_length` |
| P1 | Startup cleanup removed every child under configured root | Dedicated root ownership marker + per-session marker; unowned roots fail closed | `test_startup_clears_only_owned_orphaned_session_directories`; `test_nonempty_unowned_session_root_is_rejected` |
| P1 | Evidence file was not cryptographically tied to distributed source | Release candidate computes deterministic file-manifest/source-tree SHA and checks it before/after mandatory gates | release-candidate manifest report |
| P2 | Session files were broadly readable | Private `0700` directories and `0600` PPTX/marker/rendered artifacts | `test_session_artifacts_use_private_permissions` |
| P2 | Long valid work could cross TTL then lose session on next click | Idle TTL renewed on operation reservation/acquisition/completion | operation-lease regression |
| P2 | Browser refresh lost in-memory session handle and left orphan slot | ID/token stored in same-tab `sessionStorage`; authenticated status endpoint restores UI | judge-page regression + capability status smoke |
| P2 | Repaired PPTX could be browser/proxy cached | `private, no-store, max-age=0`, no-referrer, nosniff | closed-loop output/header regression |
| P2 | Readiness checked only binary/version presence | Cached `/health/deep-readiness` performs an actual demo PPTX→PDF conversion | `test_deep_readiness_performs_real_cached_demo_render`; HTTP smoke |
| P2 | Process-local sessions silently break with multi-worker/replica deployment | >1 worker env rejected; Docker fixed to one worker; readiness/docs declare one-replica requirement | deployment-contract regressions |
| P2 | Gemini call could hold session lock behind unbounded external latency | SDK timeout configured in milliseconds; failure opens deterministic fallback circuit | Gemini adapter timeout + circuit regression |
| P2 | Blank/relative session-root configuration could target unintended paths | blank→dedicated temp default; relative/root/home overrides rejected | deployment root regressions |

## Regression status

The v12/v13 correctness and package boundaries remain unchanged: visual wrap/overlap protection, analysis-universe preservation, complete text mapping, unknown-state semantics, OOXML relationship/package hardening, source/approval binding, prompt-injection boundary, and deterministic post-save verification remain covered by the existing suite.

## Deliberate v14 limits

- The session backend remains process-local; **one deployment replica is required**.
- App-level request limiting bounds bytes before multipart parsing inside Uvicorn, but a hosting provider/reverse proxy may still buffer traffic before it reaches the process; configure a platform ingress limit too when available.
- Deep readiness is cached to avoid turning health checks into render load.
- Browser E2E remains unverified in this administrator-restricted environment; the HTTP/API/JS contract is tested separately.
- Live Gemini network execution remains unverified without a real credential; fallback behavior and timeout/circuit configuration are tested with a bounded fake client.
