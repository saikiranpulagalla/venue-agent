# Competition Demo Script — 2 to 3 minutes

## 0:00–0:20 — Problem

"This deck can look fine at laptop distance. The audience does not sit at laptop distance. This prototype reasons over both the presentation and the venue instead of treating slide design as a screen-only problem."

Use the one-click demo. Do not begin with architecture or framework names.

## 0:20–0:45 — Deterministic venue analysis

Show:

- active-image height;
- farthest-viewer distance;
- measured vs estimated basis;
- analyzed / below-target / meets-target element counts;
- limitations and unsupported content.

Say: "The model does not calculate the geometry. Deterministic code establishes what is true."

## 0:45–1:20 — Agent planning under constraints

Set a target coverage and mutation budget. Run planning.

Show the Candidate Tradeoffs section:

- one or more alternatives;
- any blocked candidate and policy reason;
- selected candidate set;
- unresolved/review-only issues;
- combined simulation evidence.

Say: "The agent can choose only server-generated candidates that were already simulated. It cannot invent a tool operation or a font scale."

## 1:20–1:45 — Human control

Show the exact candidate IDs and combined evidence, then approve.

Say: "Approval is bound to this exact source, ordered plan, constraints, candidate evidence, and combined simulation snapshot. Changing the plan invalidates approval."

## 1:45–2:15 — Independent verification

Show VERIFIED only after:

- original source hash remains unchanged;
- output reopens;
- PPTX text is preserved;
- rendered text is preserved;
- selected targets improve;
- no new supported violation appears.

Say: "The planner cannot mark its own work verified. The saved output is reopened and re-analyzed from disk."

## 2:15–2:35 — Refusal / responsible behavior

Briefly show or mention an unsupported case such as image text, chart content, ambiguous mapping, inherited font sizing, or external linked resources.

Say: "The goal is not to make the most edits. It is to make only edits the system can justify and verify. Unknown never becomes pass."

## 2:35–2:50 — Close

"The model chooses among allowed strategies. Deterministic systems decide truth. The presenter remains in control."

Then show the testing/evaluation summary and repository link.
