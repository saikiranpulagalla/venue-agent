# Product Contract

## Target user
Students, educators, hackathon teams, and speakers preparing a deck for a classroom, seminar hall, or auditorium they cannot fully test beforehand.

## Job to be done
Given a PPTX, a venue profile, and user constraints, identify structurally supported viewing-demand issues, generate bounded repair candidates, simulate those candidates, ask the user to approve one exact plan, create a modified copy, and independently verify the saved copy.

## Invariants
- INV-01 Original source bytes never change.
- INV-02 No mutation can run without a valid approval for the exact plan hash.
- INV-03 Approval is single-use and idempotency-protected.
- INV-04 Slide content is untrusted data.
- INV-05 Unsupported or ambiguous content is never silently treated as safe.
- INV-06 No semantic rewriting in competition V1.
- INV-07 Protected text is byte/normalized-text invariant after repair.
- INV-08 Tool success is not repair success.
- INV-09 Verification reopens and re-analyzes the saved artifact.
- INV-10 Unknown never becomes VERIFIED.
- INV-11 The planner selects only server-generated candidate IDs.
- INV-12 No accessibility/readability/certification claim.

- INV-13 A repair may not create a new rendered-layout regression (wrap, overflow, overlap, or unrelated text movement).
- INV-14 Previously analyzable content may not become unknown after a repair.
- INV-15 `VERIFIED` requires no remaining unknown/uncovered analysis condition.

- INV-16 A session ID is not an authorization secret; every session API operation requires the separate bearer capability.
- INV-17 Session cleanup is idle-TTL based with active-operation leases and proactive expiry reaping.
- INV-18 The runtime must bound concurrent renderer work and terminate the full renderer process group on timeout.
- INV-19 Competition V1 is a single-worker, single-replica process-local session deployment.
- INV-20 API and repaired-artifact responses containing private session data are non-cacheable.
- INV-21 Titles and heading-like elements are never automatically mutated in competition V1.
- INV-22 `VERIFIED` requires explicit coverage of unsupported visible constructs; the downloaded artifact bytes must match the saved verification hash.
