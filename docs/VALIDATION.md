## Website compatibility update — 9 September 2026

The browser now waits for document readiness in the exact created tab and returns the resolved startup URL. The local observer supports same-origin frames and open shadow DOM; uninspectable frames no longer block unrelated controls. Native browser-state messages are replaced with sanitized local observations before model transport. New destinations require an in-app approval. Public search queries are restricted to text from the user's task.

Added regression coverage exercises startup redirects, main-document/frame/shadow input filling, frame-navigation invalidation, same-tab links, exclusion of uninspectable frame text, destination approval, restricted public search input, and blocking screenshots with uninspectable closed shadow roots. Browser integration uses real Chromium with synthetic websites; agent responses are simulated. This does not establish successful live Amazon or ITR workflows. Cross-origin frame forms, closed components, CAPTCHAs, uploads and rapidly changing page layouts remain limitations.

The historical results below describe earlier versions.

# Version 0.1 validation

Verified on 7 September 2026 in this checkout on an Apple M1 MacBook Air with 8 GB RAM and macOS 15.6.1. Only synthetic identities, documents and forms were used. No personal browser profile or paid model credential was used.

Final checks: **120 Python tests passed in 14.26 seconds**, including the real-browser integration, with no skipped tests (`GUARD_BROWSER_TESTS=1 uv run pytest -q`). The default suite skips that one opt-in integration test. Ruff, the strict TypeScript/production build, extension/demo JavaScript syntax checks, and the dependency-lock check passed. npm reported zero known vulnerabilities at verification time.

## Reproduce the checks

Run setup first, following [setup.md](../setup.md), then:

```bash
./scripts/verify.sh
GUARD_BROWSER_TESTS=1 uv run pytest tests/test_browser.py -q
uv run python scripts/smoke_test.py --product-demo --headed --approval-delay 1 --resize-during-approval
```

`verify.sh` runs the Python regression suite, Ruff, the TypeScript/production dashboard build, and real headless-browser tests of the shipped demo with and without submission. The visible-browser command adds deliberate approval delays and changes the viewport during an approval. These tests use a temporary vault and browser profile, approve synthetic test requests automatically, and clean up afterward. Application users still approve their own requests manually.

## What has been exercised

| Area | Evidence |
|---|---|
| Vault | Authenticated encryption, wrong passphrase, record versions, lock, encrypted blobs, atomic document confirmation, deletion and restart behavior. |
| Documents | Local text/PDF/image extraction, Tesseract OCR, scanned PDF handling, invalid/oversized inputs, reviewed candidates, document scope and decimal arithmetic. |
| Privacy boundary | Known-value and observed-field redaction; repeated private values in later observations; exact outgoing request hashing; schema/envelope restrictions; independently verified canonical black images. |
| Hosted adapter | Mocked HTTP transport verifies exact request bytes, endpoint, Authorization placement, structured responses, no redirects/retries, errors, timeouts and rejection before browser execution. |
| Action control | Reference/version checks, missing values, invalid action shapes, changed approvals, task cancellation, lock and fresh-state recovery; exceptional server exit and cancelled/stalled browser teardown still clean up the owned browser. |
| Browser | Real Browser Use connection, separate same-URL tabs, standard HTML input/select/click, exact target binding, stale observation rejection and disclosure/click gates. |
| Shipped demo | Six actual saved values entered; exactly one real submit event; sanitized requests checked for every seeded private value; encrypted temporary vault inspected. |
| Interfaces | Live local dashboard and native Chrome side panel, pairing, profile/document review, local sum, exact-tab selection, progress and approvals; desktop and 390 × 844 layout checks. See [interface checklist](../apps/dashboard/QA.md). |

The visible-browser stale-page test observed exactly one `stale_observation` rejection after resizing. The task captured fresh context, requested fresh approvals, filled the six fields and submitted once. It required ten model-context approvals, seven disclosure approvals (including the rejected stale attempt) and one final click approval. A stale action was not silently authorized against a new observation.

The fill-only scenario uses the exact instruction **“Stop before submitting.”** It filled all six fields, reviewed seven model contexts and six disclosures, and completed with **zero submission proposals and zero submit events**. The submit scenario reviewed nine model contexts, six disclosures and one final click, and produced exactly one submit event.

## Device measurement

One warm headless run of the shipped demo used this command:

```bash
/usr/bin/time -l .venv/bin/python scripts/smoke_test.py --product-demo
```

It completed in **5.18 seconds** with nine context approvals, six disclosure approvals and one submit approval, all automated by the test. The operating system reported maximum resident set size **191,791,104 bytes (182.91 MiB)**, peak memory footprint **97,159,552 bytes (92.66 MiB)** and zero swaps for that command. These are per-command OS statistics, **not an aggregate Chromium process-tree peak or total application memory budget**. Dependencies/browser were already installed; this excludes downloads, human review and hosted-model latency. Broader task timing and memory benchmarks remain to be collected.

After the final shutdown improvements, the same command with `--no-submit` completed in **4.98 seconds**, with maximum RSS **193,904,640 bytes (184.92 MiB)** and zero swaps. Its temporary browser and data were removed on exit. The same measurement limits apply.

## Limits of this evidence

- No live paid model call was made. The hosted integration is implemented and tested with a simulated HTTP provider; model/account compatibility and real-model task quality still need a configured credential.
- Canary checks demonstrate the tested privacy cases. They are not a measured recall rate for arbitrary names, identifiers, screenshots or financial documents, and are not proof that all private content will be detected.
- Browser tests validate the application/model boundary. They do not assert that Chromium or an arbitrary destination website produces no network traffic. Entering a value can disclose it to that website immediately.
- Automated tests use supported standard forms. Production tax portals, CAPTCHAs, embedded frames and custom controls are outside the demonstrated scope. No real return was prepared or filed.
- Browser-owned cache/cookies and operating-system memory/backups are outside vault encryption. No forensic-erasure or protection-from-local-malware claim is made.

## Differences from the original design baseline

| Design item | Delivered behavior |
|---|---|
| Stock Browser Use agent loop | Browser Use browser runtime with a product-owned restricted loop and one model gateway. No unrestricted tool or agent feature-parity claim. |
| Keychain unlock integration | Scrypt-derived passphrase key and AES-GCM local storage. No key stored beside ciphertext; no recovery account. |
| Selective screenshot OCR/face masks | Text-only by default. Optional outbound screenshots are fully black; selective visual reasoning is deferred. Document OCR is implemented separately. |
| Extension UI framework | Native Manifest V3 HTML/CSS/JavaScript side panel; dashboard uses React and TypeScript. |
| Reconnectable events | Authenticated polling of companion-owned state; closing the UI does not own or terminate the task. |
| Multi-person financial context | One user's reviewed records with explicit document/calculation scope; ownership, currency and period require user review. |
| Packaging | Locked source checkout, local setup/start/verification scripts. No signed installer or Chrome Web Store release. |

These constraints are visible in the product and in the README. Version 0.1 is a usable supervised form-assistant prototype, not an unattended tax-filing product.

## English/Hindi, local speech and per-step visual planning — 2026-09-15

- `pytest tests -q`: **349 passed, 11 opt-in tests skipped**.
- Dashboard and extension Chromium UI suite: **8 passed**. Includes language switching without losing a Hindi task draft; extension original/redacted previews, pixel-coordinate masks, invalidation of the old receipt and approval of the new image.
- Opt-in real Browser Use tests passed for reference filling/image review/resume, login continuation, and the shipped portal with reviewed documents. Login used synthetic Aadhaar/password vault records, verified their local fill, left CAPTCHA blank and resumed in the same tab. Provider responses were mocked; no real UIDAI authentication or download was attempted.
- The installed local multilingual Whisper small model transcribed synthetic macOS English and Hindi speech without an API key. The Hindi output contained spelling errors, so editable transcript review remains required. This is a functional check, not a speech accuracy benchmark.
- Ruff, JavaScript syntax checks, TypeScript/Vite production build and `git diff --check` passed. The updated companion returned HTTP 200 for `/health` and `/`.
- Prompt expansion is a local structured template preserving the original objective; the main agent then builds the stage-specific plan. Initial website selection is text-only because there is no page yet. Every subsequent action-planning call and completion verification includes an approved fresh redacted screenshot when vision is enabled.

## Conditional image review and readable actions — 2026-09-15

The default image policy is now `sensitive`: detected personal-value masks require approval; routine layout/media masks and empty password/OTP masks do not. All remain opaque and canonically verified. `always` remains available as “Review every image instead”. Images include the fresh sanitized DOM, button labels/indices, task context and reference catalog. A clear first action no longer has to wait for a planning-only response.

Approval cards display a plain-language action and destination, with the unchanged exact payload under collapsed technical details. Tests cover automatic routine images returning an actionable index, sensitive review, updated masks, fallback review, readable click requests, and both interfaces.

Validation: 354 backend tests passed with 11 opt-in skips before the final empty-field refinement; its focused suite then passed (30 tests, one opt-in skip), followed by all 22 visual-privacy tests with real Chromium. Nine UI tests passed. The combined native agent/login/portal run passed all 19 tests, including the three real browser workflows. Ruff, the production build and diff checks passed. The idle companion was restarted and its live schema confirmed `image_review` defaults to `sensitive`.

## Screenshot recovery — 2026-09-15

Incomplete privacy scans and transient capture failures now retry three captures with 0.5/1-second delays. No preceding browser action is replayed. A PNG with validated viewport/pixel dimensions can become a manual review candidate; it is explicitly marked as an incomplete automatic scan and always requires image approval, including when zero masks were detected. The fixed screenshot supports additional pixel masks and exact-request approval in both interfaces. The transport independently rejects manual recovery candidates without the reviewed request hash. Invalid image dimensions, scale or rectangles produce a resumable human handoff instead of an ended task. Only allowlisted collector diagnostic codes enter logs; original screenshots and warning contents are not logged.

Validation: full backend suite **366 passed, 13 opt-in skips**; focused recovery/visual/native-agent suite with `GUARD_BROWSER_TESTS=1` **52 passed**; dashboard and extension Chromium UI suite **10 passed**. Coverage includes transient recovery, incomplete scans with no masks, denial, cancellation, stale approval receipts, additional masks, sanitized DOM plus image, continuation after invalid dimensions, and real Chromium animation/generated-CSS failures at device scale factor 2. English/Hindi recovery notices, the production dashboard build, Ruff, JavaScript syntax and diff checks passed. Tests use synthetic pages and mock model responses; this is not evidence of a live UIDAI login or saved Aadhaar download. The user's backend was not restarted.

## Stalled planning recovery — 2026-09-15

Empty, invalid-schema and invalid-JSON model responses now request supervised visual recovery before another action. Three calls on unchanged page evidence, three failed steps, repeated identical actions and repeated two-action cycles also trigger it. Recovery captures through the same redaction pipeline, forces exact screenshot/text approval even for text-only tasks and zero-mask images, and includes sanitized DOM indices and task context. Two visual attempts per run bound recovery; further stalls become a resumable human handoff. Privacy failures and uncertain action outcomes retain their existing protections; success still requires completion verification.

Validation: **376 backend tests passed, 14 opt-in skips**. The focused recovery/native-agent Chromium run passed **41 tests**, including an empty first model response followed by image review, reference filling, human continuation and verified completion on a synthetic portal. The dashboard production build, Ruff and diff checks passed. No live UIDAI workflow or backend restart was performed.

## Fill-first login handoff — 2026-09-15

Visible login forms now run a local preflight before model planning. Selected, uniquely matching credentials fill first; the user then completes remaining login challenges. Manual/review handoffs on login forms also run the fill check. Record types use the same normalization as field compatibility, so `Aadhaar Number` matches an Aadhaar field. Handoffs report confirmed fills, missing/ambiguous matches and unconfirmed attempts. Existing values, selected-record scope and Aadhaar-versus-address checks remain enforced. OTP fields are recognized before password handling, including masked OTP controls and `autocomplete=one-time-code`.

Validation: **383 backend tests passed, 15 opt-in skips**; **39 focused tests passed with Chromium enabled**. Synthetic login tests cover both password login and placeholder-only Aadhaar/OTP forms: Aadhaar fills before any model call, CAPTCHA/OTP stay empty, and human continuation reaches the verified destination in the same tab. Two final focused checks passed for footer-login exclusion and OTP classification. Ruff and diff checks passed. No real Aadhaar value, UIDAI authentication, or backend restart was used.

## Conditional approval checks — 2026-09-16

Prepared image/text requests and ordinary button clicks now use a separate bounded check against the same active model, endpoint and API credential as the main agent. Only literal `true`/`false` responses are accepted; uncertain, failed, malformed or changed-context checks require HITL. Candidates are locally sanitized and images must have canonical provenance and validated geometry. Images are supplied exactly once, while candidate instructions remain quoted data. Incomplete scans and forced recovery bypass remote classification and require human review directly. Existing explicit review settings, final-action restrictions, destructive-action review, destination consent and CAPTCHA/OTP handling remain enforced.

Remote checks receive the sanitized candidate before a resulting HITL prompt. They never receive the local original screenshot. The dashboard uses plain review copy and its provider settings describe remote safety checks accurately. An automatic decision is not a guarantee of complete PII detection. These checks make additional provider requests and have a 15-second deadline with no classifier-specific provider failover.

Validation: **413 backend tests passed, 15 opt-in skips**. The classifier/native-agent/login/portal Chromium run passed **51 tests**; dashboard and extension UI tests passed **10 tests**. Coverage includes strict binary parsing, same model/endpoint/key, single redacted image attachment, preserved candidate text, safe automatic continuation, flagged and denied HITL, exact mask/receipt updates, text-only review, button context, final-submit protection, failed checks, cancellation, and changed settings/context. Synthetic model peers distinguish approval checks from main-agent responses. Production TypeScript/Vite build, Ruff, JavaScript syntax and diff checks passed. No live OpenRouter request or UIDAI transaction was used, and the user's backend was not restarted.

## UIDAI login field recognition — 2026-09-16

Read-only inspection found the Aadhaar login control exposed as `name="uid"` without an associated label, placeholder, or ID. The observer now supplies an Aadhaar label only for that unlabelled input on `https://tathya.uidai.gov.in` with an Aadhaar login document title. Explicit field labels retain precedence. The existing local fill-first path then uses a unique selected, reviewed Aadhaar vault reference and leaves CAPTCHA and OTP to the human. Vault selection, compatibility, and existing-value protections are unchanged.

The synthetic UIDAI-shaped browser fixture verifies local number entry before any model planning, empty CAPTCHA/OTP, value-free public task state, human handoff, and continuation to the original destination in the same tab. All fixture network requests are intercepted locally. Negative browser cases cover other origins, lookalike hosts, HTTP, non-login titles, explicitly labelled address/OTP fields, passwords, and CAPTCHA. No real login, OTP submission, or Aadhaar download was performed; the user's backend was not restarted.

Validation: **82 tests passed** with `GUARD_BROWSER_TESTS=1` across login browser flow, browser boundaries, task progress, and field compatibility. Ruff and diff checks passed.
