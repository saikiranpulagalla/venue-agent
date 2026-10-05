# Failure Audit Closure — v13 Package/Runtime Boundary Hardening

This gate follows the v12 safety-oracle closure. It targets failures that could occur **before or around** the analysis/planning core: malformed OOXML packages, resource-amplification inputs, unsafe relationship targets, renderer-output growth, and session-lifecycle races.

| Severity | Former failure | v13 closure | Regression evidence |
|---|---|---|---|
| P1 | Any valid ZIP named `.pptx` could pass intake even without a real OOXML presentation skeleton | Required `[Content_Types].xml`, root relationships, presentation part, presentation relationships, and at least one slide part | `test_intake_rejects_zip_that_is_not_an_ooxml_presentation` |
| P1 | ZIP dot-segment aliases could bypass duplicate/canonical package-path assumptions | Canonical package-part validation rejects backslashes, empty segments, `.`/`..`, absolute paths, and aliases | `test_intake_rejects_package_path_aliases` |
| P1 | Embedded OLE/package/ActiveX payloads could reach LibreOffice although V1 does not need them | Competition V1 rejects embedded active/package parts and corresponding relationship types before rendering | `test_intake_rejects_embedded_ole_or_package_payloads` |
| P1 | A relationship could disguise an absolute URI by omitting `TargetMode="External"` | Internal relationship targets are URI-checked and resolved against their OPC source part; absolute schemes/paths and package-root escapes fail closed | `test_intake_rejects_disguised_absolute_uri_without_external_targetmode`; `test_intake_allows_normal_parent_relative_internal_relationship` |
| P1 | Highly compressed or structurally huge slide XML could amplify CPU/memory before the renderer boundary | Per-entry decompression ratio/size, slide XML size, aggregate slide XML, text-character, and text-run budgets | `test_intake_rejects_excessive_slide_text_before_renderer` plus existing intake-limit regressions |
| P1 | Rendered extraction had no explicit span ceiling and PyMuPDF was not guaranteed closed on limit failure | `MAX_RENDERED_TEXT_SPANS` fails closed inside a context-managed PDF document | `test_rendered_span_budget_fails_closed_and_closes_document` |
| P1 | A pathological render could leave an unbounded PDF for downstream extraction | Rendered PDF has a hard post-render byte ceiling; oversize artifact is removed | `test_rendered_pdf_size_is_bounded_and_oversize_artifact_removed` |
| P1 | A request waiting on a busy session could enter after absolute TTL expiry because expiry was checked only before waiting | `SessionStore.locked` re-checks TTL after acquiring the session lock while the store lock is still held; expired artifacts are deleted and the waiter receives `KeyError` | `test_waiting_request_cannot_enter_session_after_absolute_ttl` |

## Gate result

- Fast deterministic/security suite: **64/64 PASS**.
- Resource/LibreOffice suite: **22/22 PASS**, executed in isolated test processes/calls.
- Total implemented tests: **86**.
- HTTP production-command smoke: **PASS**.
- Frontend JavaScript syntax: **PASS**.
- Python compileall: **PASS**.
- Bundled demo and fixture PPTX files pass the strengthened intake validator.

## Truthful remaining unverified items

The environment used for this gate does **not** provide Docker or Podman, so the container image itself is not runtime-verified here. Live Gemini execution also remains unverified without a real credential. Public deployment, real user evidence, browser E2E on an unrestricted Chromium environment, and the final submission/video remain outside this code gate.

The wrapper used for this engineering session imposes a short per-command wall limit, so the long one-command `release_gate.py` sequence could not be observed to completion in a single invocation here. Its constituent mandatory/resource tests were run and passed separately; this document does not misstate that as a single-process gate pass.
