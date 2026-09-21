# SelekyT — Data Room: honest inventory

**Date of record:** 2026-09-19. Checked against repository state at `4cd542c` on `main`
(193 commits). **Note on volatility:** this repository is being committed to by
parallel workstreams as this page is written — HEAD moved from `8aab1b5` to
`4cd542c` during its drafting. Counts and hashes below were accurate at the
timestamp on each line and should be re-read, not inherited.

## What this document is

An inventory of what a diligence team would ask for, what exists, and what is
missing. It is written so that a person opening a virtual data room could plan
it from this page alone — and so that nobody plans it before the preconditions
in the final section are met.

**Evidence convention (used throughout):**

- **VERIFIED** — I ran the command or read the file on this host today. Command
  and output are named.
- **RESEARCHED** — a live URL, fetched 2026-09-19, with the retrieval date.
- **ASSUMPTION** — my inference. Discardable, and never load-bearing here.

---

## The headline, stated once

**A data room opened today would contain a repository and no company.** The
engineering evidence is real and unusually strong for the stage; every other
section a diligence team opens would be empty, or would contain a documented
absence rather than an answer. There is no traction section to build, because
there is no traction — that is a fact, not a formatting problem.

The single most important thing a reviewer would find is not in the data room at
all: it is the absence of one experiment. **No NLE has ever opened a SelekyT
timeline** (VERIFIED — `docs/NLE_COMPATIBILITY.md`, and no NLE is installed on
this host). Until that changes, the one differentiated claim the venture makes —
an editable timeline handed back to a human editor — is asserted, not
demonstrated. `docs/INVESTOR_READINESS.md` calls TRACTION and MOAT BLOCKING for
exactly this reason.

---

## Verification I ran before writing anything here

Two commands, both from the repo root, today:

```sh
PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py out/web/pipeline/project
.venv/bin/python -m pytest -q
```

Results, both real:

- NLE compatibility audit: **ALL CHECKS PASS**, exit code 0. Every EDL parsed by
  `pycmx` (an independent CMX 3600 reader), every FCPXML parsed as XML, all six
  cuts' EDL/FCPXML/render durations agreeing within 0.00–1.63 frames. Full output
  in `docs/nle-audit-output.txt`. (VERIFIED — I ran it.)
- Test suite: **1287 passed, 13 skipped in 260.7 s**, exit code 0, re-run
  2026-09-21. (VERIFIED — I ran it. The skips are not one group: 7 are opt-in
  `SELEKYT_PROXY_MEDIA` measurements needing a non-redistributable recording,
  5 are WER records that predate the `wer_err_total` field, 1 is a language
  with no run record. None is a missing optional backend.)

**Drift warning, since it matters for diligence accuracy.** Three documents
recorded three different test counts within the same hour today: `docs/AI_EXECUTION_STATE.md`
originally recorded 819, `docs/RED_TEAM.md` recorded 824, and the real number
when I ran the suite was **834**. `docs/AI_EXECUTION_STATE.md` has since been
corrected, and the count has moved again to **1287 passed / 13 skipped**
(2026-09-21) as more regression tests landed — which is the point of the
paragraph below. It moved 1080 → 1215 → 1236 → 1240 → 1251 → 1287 during the desktop work
alone: 1215 was the baseline before the packaging contract existed, 1236 was
quoted in a commit message, and 1240 is the count after the four release-label
tests landed — those tests shipped citing the count from *before* they
existed, which is the drift this paragraph is about, happening again in real
time. The state
file carries its own self-checkpoint
note about the drift — the correction is real, and the lesson is that any
document quoting a test count must be refreshed at the time it is shared, or it
will be wrong by the time a reviewer reads it. This is the project's own stated
discipline, and it applies to this page too.

---

## 1. Technology and engineering — **EXISTS**

This is the strongest section of the room, and it is genuinely strong.

| Item | Status | Evidence |
|---|---|---|
| Source tree, runnable | EXISTS | `/home/azureuser/selektyt`; 193 commits at `013ce46`, 2026-09-16 → 2026-09-20 (VERIFIED, `git log`) |
| One real end-to-end run on real input | EXISTS | `out/web/pipeline/project/` — NASA 360 Episode 5, US-government public domain, 1469.77 s, 29.97 fps, two-host unscripted speech, 6 clips, 16:9 + 9:16 renders, per-cut EDL + FCPXML, captions, QA |
| Independent interchange audit | EXISTS | `tools/nle_check.py`, exit 0 today (VERIFIED); output archived at `docs/nle-audit-output.txt` |
| Editorial audit tool | EXISTS | `tools/editorial_audit.py` (VERIFIED — `docs/release-validation.md`) |
| Test suite | EXISTS | 1287 passed / 13 skipped, 260.7 s (VERIFIED 2026-09-21) |
| Determinism across entry points | EXISTS | the web shell reproduced the CLI selection exactly — on one source (VERIFIED, `docs/product-gap-map.md` "The critical path") |
| Architecture and technical audit | EXISTS | `docs/TECHNICAL_AUDIT.md` — 15 ranked defects, five of them security, written against the real code |

**The honest qualifiers a reviewer would reach within ten minutes:**

- **No NLE has ever opened an export.** The audit proves the files are
  well-formed, self-consistent, and duration-accurate against the renders. It
  cannot prove an editor can open them, because nothing on this host can.
  (VERIFIED — `docs/NLE_COMPATIBILITY.md`; gap G2.)
- **One source.** Every quality claim rests on a single 24:29 tape. Editorial
  judgement at scale is unmeasured (gap G6). (VERIFIED — `docs/AI_EXECUTION_STATE.md`
  item 6.)
- **Security findings are unremediated.** `docs/TECHNICAL_AUDIT.md` ranks three
  Critical and two High severity security defects, including a bypassable
  `guardedPath` prefix check and cookie material persisted into state and served
  unauthenticated. These are documented, which is a credit; they are not fixed,
  which is a liability.
- **CI does not run the tools that catch the real bug classes.**
  `tools/nle_check.py` and `tools/editorial_audit.py` are not in the CI run. The
  dependency half of this was closed — `pycmx>=1.5` is in the `nle`, `dev` and
  `all` extras, so the independent-parse check *can* run in CI now — but CI
  still does not invoke either tool, so a broken render/interchange agreement is
  only caught on a manual audit. (VERIFIED — `docs/TECHNICAL_AUDIT.md`.)

---

## 2. Intellectual property and licensing — **PARTIAL**

| Item | Status | Evidence |
|---|---|---|
| Copyright and licence | EXISTS | `LICENSE` — "SELEKYT PROPRIETARY LICENSE, Copyright (c) 2026 Bharath Bheemreddy, All rights reserved"; commercial use requires written permission (VERIFIED — I read the file) |
| Provenance of the editorial heuristics | PARTIAL | `docs/autocut-provenance.md` and `docs/EDITORIAL.md` record the derivation. `docs/INVESTOR_READINESS.md` flags "written confirmation of the AutoCut permission" as the one specific, fixable IP exposure. Not in writing yet. |
| Third-party licence scan | MISSING | No dependency licence scan exists; `faster-whisper` is not even a base dependency (`docs/TECHNICAL_AUDIT.md` defect 6) |
| Patent filings | MISSING | None. `docs/FUNDRAISING.md` §12 budgets trademark filings only |
| Trademark | MISSING | No registration |

The licensing posture is coherent — proprietary, source-available, commercial
use by written permission — but the paper trail behind the one reused component
is the question a diligence lawyer would ask first, and it does not yet have a
written answer.

---

## 3. Traction and customers — **MISSING (entirely)**

| Item | Value | Basis |
|---|---|---|
| Users | **0** | VERIFIED — `docs/RED_TEAM.md` ground truth, and no telemetry exists that could have recorded any |
| Revenue | **$0** | VERIFIED — no billing, payment, subscription or invoicing code exists anywhere in `engine/`, `selekyt/`, `providers/` or `web/` (grep recorded in `docs/RED_TEAM.md` appendix) |
| Pilots / design partners | **0** | VERIFIED — `docs/INVESTOR_READINESS.md` scores TRACTION BLOCKING |
| Trial signups / waitlist | **0** | No product is hosted anywhere a stranger could reach it |
| Customer conversations | **0** recorded | No discovery interview log exists in the repo |
| Installations by a non-founder | **0** | No packaged binary; the Tauri shell compiles and passes clippy but has never produced a binary or rendered a webview (gap G4) |

**This is the section that cannot be drafted around.** A reviewer opening a
folder labelled "Traction" and finding a definition document (`docs/TRACTION_DASHBOARD.md`)
is receiving an honest answer. That document exists precisely so that no
traction claim is ever made by implication of a formatted dashboard.

---

## 4. Financials — **MISSING**

No financial statements, no projections model, no bank statements, no payroll
records, no cap table beyond the single founder. What does exist is cost and
unit-economics *modelling*, which is a different thing:

- `docs/BUSINESS_MODEL.md` — measured throughput model, cloud pricing retrieved
  2026-09-19, pricing tiers designed, sensitivity analysis. This is analysis, not
  a financial record.
- Measured compute rates (VERIFIED — `docs/BUSINESS_MODEL.md` §1, benchmarked on
  the real artifacts of the reference run on this 4-core host): transcription
  0.165 RTF CPU with `small`/int8; render 0.1232 RTF; all-in planning cost
  $0.00104–$0.00476 per source-minute depending on delivery mode.

A diligence team asking for "the financials" today would receive a cost model
with zero dollars of history behind it. That is the correct thing to give them,
labelled as such.

---

## 5. Market — **PARTIAL**

What exists is competitive and pricing research. What does not exist is a market
size, and the absence is deliberate, not an oversight.

- **No market size is claimed anywhere in this document set, and none should
  be.** Third-party market-size figures could not be verified: Grand View
  Research, Goldman Sachs, Fortune Business Insights, Mordor Intelligence and
  Precedence Research all returned 403/404 (RESEARCHED attempt, recorded in
  `docs/RED_TEAM.md` appendix). A data room that omits a TAM is acceptable; one
  with an invented TAM is a defect.
- What can be cited instead is pricing anchors (RESEARCHED — see
  `docs/RED_TEAM.md` §10 and my own fetches in `docs/INVESTOR_PIPELINE.md`):
  OpusClip Pro $29/mo, Descript Creator $24/mo annual, DaVinci Resolve $0 free
  tier. From those, $100M ARR at $24–29/mo implies roughly 300,000–400,000
  paying subscribers (ASSUMPTION/DERIVED — arithmetic on cited anchors).
- The competitive landscape is mapped in depth at `docs/COMPETITIVE_MAP.md`.

**A material competitive change I verified today** (2026-09-19): OpusClip's
pricing page now advertises "Export to Adobe Premiere Pro & DaVinci Resolve" on
Pro and Business plans (RESEARCHED — <https://www.opus.pro/pricing>). This
differs from the record in `docs/CUSTOMER_DISCOVERY.md` and `docs/RED_TEAM.md`,
both of which state OpusClip advertises no NLE/timeline export. The intersection
SelekyT claims to occupy — autonomous selection *plus* editable timeline export —
is closing from the other side. Any data-room claim about being the only product
in that intersection must be re-checked against live competitor pages the week it
is made.

---

## 6. Team — **PARTIAL (BLOCKING for an institutional round)**

| Item | Status | Evidence |
|---|---|---|
| Founder | EXISTS | Bharath Bheemreddy, copyright holder in `LICENSE` (VERIFIED) |
| Employees / co-founders | **0** | VERIFIED — `docs/INVESTOR_READINESS.md` |
| A second engineer to audit the machine-written core | **MISSING** | `docs/INVESTOR_READINESS.md` calls this "the only gap that compounds" |
| A professional editor on the team or on retainer | **MISSING** | `docs/RED_TEAM.md` kill mode 4: no editor has ever rated a cut; "who is your editor?" is answered with an algorithm |
| Advisors of record | **0** | VERIFIED — `docs/INVESTOR_READINESS.md` |

One thing a reviewer will notice in the git history: 193 commits on `main` across
three author identities — "Devika Boddu" (60), "desukab" (49), and "Ubuntu" (48)
from the build host, summing to 157 across all refs including preserved agent
worktrees (VERIFIED — `git shortlog -sne --all`, 2026-09-19). The two human
identities share one email address. This is one human founder plus
machine-assisted authorship. It is not a team, and it should be presented as what
it is rather than left for a reviewer to infer.

---

## 7. Legal, compliance and security — **PARTIAL**

- **Security posture is documented and unfixed.** Three Critical findings (no
  authentication or origin policy on the localhost control plane; a bypassable
  `guardedPath` prefix check; cookie credentials persisted and served
  unauthenticated) and two High findings (SSRF via arbitrary `source` URLs;
  caption-style render-cache staleness that ships a stale MP4 beside fresh
  captions). (VERIFIED — `docs/TECHNICAL_AUDIT.md` defects 1–5.) An enterprise
  reviewer fails this product on `guardedPath` alone in under a minute; that is
  the stated conclusion of the audit.
- **No SOC 2, no DPA template, no data-retention policy.** All three are
  prerequisites for any customer handling source footage under contract.
- **One privacy defect with a known fix.** `pipeline_state.json` persists the
  full yt-dlp `media_info` dict including live session cookies; the fix is to
  scrub at write time, not upload time. (VERIFIED — `docs/MOAT.md` §6.1.)
- **No corporate-entity paper trail in the room.** `docs/FUNDRAISING.md` §12
  budgets accounting, entity and compliance hygiene into the round, which is the
  admission that it does not exist yet.

---

## 8. Risk register — **EXISTS (unusually good)**

The strongest single asset in the room is the disclosure set itself:

- `docs/RED_TEAM.md` — 10 ranked kill modes, likelihood and impact each rated,
  plus the single most likely way the company dies.
- `docs/TECHNICAL_AUDIT.md` — 15 ranked defects with file and line.
- `docs/product-gap-map.md` — per-capability status distinguishing "code exists
  and unit tests pass" from "a user can rely on this".
- `docs/NLE_COMPATIBILITY.md` — the one gap that gates everything, with the
  exact experiment that closes it and the pass criterion stated in advance.
- `docs/MOAT.md` — the flywheel as a design document, with each phase marked
  VERIFIED or DESIGNED.

A reviewer who reads this set and finds the engineering credible has also been
handed every reason to decline. That is the correct trade and it should not be
softened. It is also the reason a data room opened now is premature: the
disclosure is honest, and the honest answer in sections 3, 4 and 6 is "zero".

---

## What must be true before this data room is worth opening

Ranked by how much each one moves a diligence outcome. Gap labels are those used
across `docs/product-gap-map.md`, `docs/AI_EXECUTION_STATE.md` and
`docs/CUSTOMER_DISCOVERY.md`.

| Rank | Must be built | Gap | Cost | Why it gates the room |
|---|---|---|---|---|
| **1** | **One SelekyT timeline imported into one real NLE** (Resolve free tier first; pass criterion: sequence opens with all clips online and duration matches the render within 2 frames) | **G2** | **$0 capital**, one machine with a display | The only differentiated claim. Until it is closed, every editor-facing claim in the room is unverified at the exact point it is sold, and the moat document's ranking-2 asset is "claimed, not held". `docs/NLE_COMPATIBILITY.md` gives the experiment. |
| **2** | **A measured editorial-quality benchmark on more than one source** — the blind A/B against an incumbent described in `docs/RED_TEAM.md`, on 10+ sources, rated by 3–5 blinded working editors | **G6** | ~$29 subscription plus a few hundred dollars of editor time | Kill modes 3, 4 and 5 all collapse onto this. It is also the only experiment that could turn "editorial intelligence" from a founder narrative into a number. |
| **3** | **The product reachable at a URL by someone who is not the founder**, with telemetry emitting activation and retention events from day one | G3-adjacent | Small | Until a stranger can use it, traction is not possible and the traction dashboard cannot leave zero. Also the first moment any usage claim is possible at all. |
| **4** | **The three Critical security findings fixed** | — | Engineering time | An enterprise reviewer fails the product on `guardedPath` before evaluating anything else (`docs/TECHNICAL_AUDIT.md`). Cheapest credibility unit available. |
| **5** | **A second engineer, and a professional editor on retainer** | — | The largest line in `docs/USE_OF_FUNDS.md` | The only gap that compounds. A solo founder cannot simultaneously close G2, run the benchmark programme, host the product and audit the machine-written core. |
| **6** | **AutoCut permission in writing** | — | Negligible | The one specific IP exposure a diligence lawyer finds on the first pass. |
| **7** | **Video upload in the web shell**, so the CLI/web disagreement is resolved | **G3** | Engineering time | Needed for the pilot programme to be usable by an editor who is not a developer. |
| **8** | **One packaged desktop build** | **G4** | Engineering time | Last, deliberately. Until G2 and G6 are closed, packaging distributes an unverified claim. |

**The ordering rule that matters more than any single item:** items 1 and 2
together cost well under $1,000 and no engineering headcount, and they settle
whether there is a company to diligence at all. `docs/USE_OF_FUNDS.md` funds them
first for exactly that reason. Opening a data room before item 1 is closed is
spending the room's credibility on a claim a reviewer can falsify in an
afternoon.
