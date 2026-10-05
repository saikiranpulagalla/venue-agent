# Architecture

```text
PPTX -> secure intake -> LibreOffice PDF render -> geometry-bound structural/rendered mapping
                                            + venue/reference -> deterministic issues
issues -> server-generated repair candidates -> isolated simulation -> deterministic policy
safe candidates -> planner selects candidate ID -> exact-plan approval -> immutable-copy mutation
saved output -> reopen -> fresh render -> fresh analysis + independent invariants -> VERIFIED/REVIEW
```

The model is not a source of truth. It may select among bounded, server-generated candidates; it cannot invent mutation values, mutate files directly, or declare success.


## State and identity boundary

Rendered spans are associated with structural PowerPoint shapes using slide-normalized geometry plus normalized text; duplicate text elsewhere on the slide is not sufficient for identity. If one rendered span can belong to multiple overlapping structural shapes, that mapping becomes `AMBIGUOUS`, `NOT_ANALYZED`, and non-mutable.

All state-changing operations for one ephemeral session are serialized. The global session-map lock is held only while reserving/releasing a session lease and is never held while waiting on a per-session lock, preventing cross-session lock convoy. Active/queued operations refresh an idle TTL; a background reaper removes idle-expired sessions. Re-analysis clears prior candidates, plan evidence, approval, and output; re-planning clears prior approval/output; approval rechecks the uploaded source SHA and the complete plan hash before any mutation. Download snapshots the terminal output while the same session lock is held.

Session identity and authorization are separate: the URL contains a non-secret session ID, while `X-Venue-Token` is the bearer capability. The server stores only its digest. Browser refresh recovery uses `sessionStorage`; the token is never placed in a URL. Competition V1 requires one worker and one deployment replica because the session store is process-local.


## Rendered transition safety oracle

Candidate simulation and final verification compare the baseline and fresh rendered analyses element-by-element. The transition audit fails closed on identity loss, analyzability regression, unexpected line-count changes, increased text-box overflow, new/increased text overlap, or movement of unrelated text. The final `VERIFIED` state additionally requires that no unknown, boundary-review, outside-reference, or unsupported-visible-content condition remains.


## Runtime resource boundary

A process-wide semaphore bounds concurrent LibreOffice conversions. Renderer queue waits are bounded; render timeout terminates the entire LibreOffice process group, not only the launcher. Request bytes are bounded by pure ASGI middleware before multipart parsing/spooling, and the upload endpoint retains its tighter PPTX byte limit. API/output responses are `no-store`; repaired PPTX downloads also use `private, no-store`. `/health/deep-readiness` performs a cached real render of the project-owned demo fixture.
