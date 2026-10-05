# WCC Judging Map

This file maps the competition build to the WCC Launchpad 30 judging criteria. It is intentionally evidence-oriented: claims should be replaced with measured results, not marketing language.

## 1. User insight and problem evidence — 15

Evidence still required from the team during the event:

- real presenter/student/faculty interviews or survey responses;
- one real presentation/venue workflow if possible;
- anonymized quotes only with consent;
- actual counts, never fabricated percentages.

Product claim to validate: presenters create slides at laptop distance but often cannot test them under the actual venue geometry before presenting.

## 2. Strength of core solution — 24

Core loop implemented:

**Inspect → Analyze → Plan → Simulate → Guard → Approve → Copy → Apply → Reopen → Verify**

The core outcome is a modified copy only when a bounded repair is pre-simulated, explicitly approved, executed on an immutable-copy path, and independently rechecked.

## 3. Technical depth and reliability — 24

Implemented evidence:

- secure PPTX intake with OOXML-part validation, canonical package paths, decompression/slide-text bounds, embedded active/package rejection, and external/disguised-URI relationship rejection;
- LibreOffice render + PyMuPDF structural measurement;
- versioned public BDM reference profile;
- separate analyzability and mutation capability;
- server-generated bounded candidates;
- per-candidate simulation fingerprints;
- global constrained plan selection across multiple issues;
- combined-plan simulation;
- pre-approval PPTX-text and rendered-text preservation checks;
- plan hash binds source, ordered candidate set, parameters/evidence, constraints, and complete combined simulation snapshot;
- exact-plan human authorization;
- source immutability and idempotent execution boundary;
- saved-output reopen/rerender/reanalysis before final VERIFIED;
- deterministic fallback when the model is unavailable or invalid.

## 4. Originality and differentiation — 15

Do not claim "first" or "unique in the world".

Defensible differentiation:

- presentation generators/editors optimize the digital deck;
- room/font calculators operate on generic venue inputs;
- this workflow closes the loop across **actual deck structure + physical venue reference + constrained agent planning + approved artifact mutation + post-action verification**.

## 5. Real-world usability — 12

Judge path:

1. one-click project-owned demo;
2. quick venue input: active image height + farthest-viewer distance;
3. deterministic analysis;
4. visible candidate tradeoffs;
5. exact-plan approve/reject;
6. verification report;
7. download only after a terminal verified/review state;
8. explicit session deletion.

No login or account is required.

## 6. Responsible design and trust — 10

Hard invariants:

- original deck is immutable;
- no mutation without exact-plan approval;
- presentation content is untrusted data;
- no semantic rewriting in competition V1;
- unsupported/uncertain content never becomes green;
- model cannot invent mutation parameters or self-certify outcomes;
- prompt injection cannot escape the tool contract;
- unknown does not become pass;
- no individual readability/accessibility/AVIXA certification claim;
- uploaded/generated artifacts are ephemeral and explicitly deletable;
- queued operations re-check absolute session expiry after lock acquisition;
- rendered artifact size and text-span extraction are bounded.
