# SelekyT — Traction Dashboard: definition and current state

**As of 2026-09-19. Read this first.**

## This is not traction

**Every value on this dashboard is zero, N/A, or not-yet-instrumented. SelekyT
has zero users, zero revenue, zero pilots, and zero recorded customer
conversations.** There is no growth curve, no cohort table and no funnel on this
page, because there has been no traffic, no sign-up and no usage to measure.

This document defines the dashboard: what each metric *will* be, how it will be
computed, which event populates it, and what it is a proxy for. It exists so that
the first person to look at traction has a precise, falsifiable definition rather
than an impression — and so that no traction claim can be made by formatting an
empty table.

**Why every value is zero, stated once:** the product has never been deployed
anywhere a person other than the founder could reach it. There is no hosted
instance, no packaged binary, no billing system and no telemetry pipeline. Zero
is not a disappointing measurement of something that happened; it is the correct
reading of an instrument that has not been turned on.

**Evidence convention:** **VERIFIED** — repo path or a command I ran today,
2026-09-19. **RESEARCHED** — live URL with retrieval date. **ASSUMPTION** — my
inference, discardable.

---

## The instrumentation gap, stated before the metrics

There is no telemetry code. A grep across `engine/`, `selekyt/`, `providers/`
and `web/` finds no analytics, feedback, priors or event-ingestion code anywhere
(VERIFIED — the grep is recorded in `docs/RED_TEAM.md` appendix). Consequence:

> **Even a user running the product today would not populate this dashboard.**

Wiring the events named below is a precondition for any of these numbers becoming
real, and it is the reason `docs/FUNDRAISING.md` §12 requires "real telemetry
emitting activation/retention events from day one" of the hosted deployment. A
metric whose event does not exist is a definition, not a measurement. That is the
status of every row below.

---

## Section A — Acquisition

| Metric | Definition (numerator / denominator) | Populating event | Now | Target and basis |
|---|---|---|---|---|
| Unique pipelines started | Count of distinct users with ≥1 pipeline run reaching transcript ÷ total site/shell visitors | `pipeline.started` with a stable anonymous user id | **0** — no hosted instance exists (VERIFIED) | First 100 within 60 days of a hosted deployment (ASSUMPTION; no basis exists to set it — see "On targets" below) |
| Install / sign-up conversion | Sign-ups ÷ unique landing-page visitors | `signup.completed` ÷ `page.view` | **N/A** — no landing page, no sign-up flow | Set only after 500 visitors, never before |
| Trial-to-pipeline rate | Users completing ≥1 pipeline ÷ users who signed up | `pipeline.started` ÷ `signup.completed` | **N/A** | ≥50% (ASSUMPTION) |

---

## Section B — Activation

A user is *activated* when they receive a deliverable they did not have to
engineer. Definition held deliberately tight: downloading a rendered clip counts;
inspecting the QA report does not.

| Metric | Definition | Populating event | Now | Target |
|---|---|---|---|---|
| Time to first deliverable | Median minutes from `pipeline.started` to first `render.completed` | both events | **N/A** | ≤30 min on a 45-min source. Grounded in the measured architecture: transcription is 0.1651 RTF and 16:9 render 0.1232 RTF on 4 CPU cores (`docs/BUSINESS_MODEL.md` §1, both MEASURED on the reference run's real audio and video). The model's own all-in figure is **0.2849 RTF** including decode and the Python passes, so a 45-min source is **~13 min of compute** on a 4-vCPU instance (VERIFIED rates; the sum is my arithmetic, and it is a floor — it was benchmarked on 720×486 SD, and §11 of that document flags 1080p H.265 as 3–10× unmeasured) |
| Activation rate | Users with ≥1 completed render within 24 h of first pipeline start ÷ all users starting a pipeline | `render.completed` within 24 h ÷ `pipeline.started` | **0** | ≥60% (ASSUMPTION) |
| Second-source return rate | Users starting a second pipeline within 14 days ÷ activated users | second `pipeline.started` | **0** | ≥30% (ASSUMPTION). This is the earliest honest retention signal available |

---

## Section C — Editorial quality (the section that decides the company)

Every metric in this section measures whether the machine's *judgement* is any
good. Today all of them are unmeasured, and that — not the zero traffic — is the
real traction problem: `docs/RED_TEAM.md` kill mode 5 and `docs/CUSTOMER_DISCOVERY.md`
falsifier 3 both land here.

| Metric | Definition | Populating event | Now | Target |
|---|---|---|---|---|
| Sources processed end to end | Count of distinct sources with a green full pipeline + green `tools/editorial_audit.py` | audit run committed | **1** — NASA 360 Episode 5 only (VERIFIED, `out/web/pipeline/project/`) | 20–50, deliberately the first target in `docs/FUNDRAISING.md` §12 |
| Blind head-to-head win rate | Clips ranked above the incumbent's by a blinded working editor ÷ clips rated | Editor rating panel; see `docs/RED_TEAM.md` "single cheapest experiment" | **0 / 0 — never run** | >50% against OpusClip at $29/mo. Below 50% the positioning must pivot to the audit layer (RESEARCHED basis for the comparator: <https://www.opus.pro/pricing>, retrieved 2026-09-19) |
| Misrepresents-its-source rate | Clips a blinded editor marks as misrepresenting the source ÷ clips rated | same panel | **0 / 0 — never run** | Lower than the incumbent's. This is the enterprise pitch quantified, and it has never been measured once |
| Timeline retained vs discarded | Editors who keep working in the imported SelekyT timeline ÷ editors who imported one | Observed NLE session after import | **0 / 0 — no NLE import has ever occurred** (VERIFIED — `docs/NLE_COMPATIBILITY.md`) | ≥50% retain. If editors discard the timeline and re-cut from scratch, the "editor in the loop" pillar is over |
| Revision-time ratio | Editor minutes to finish a SelekyT first pass ÷ editor minutes to cut from scratch | Timed pilot sessions, per design partner | **N/A** | ≤50% of from-scratch, the pass criterion in `docs/CUSTOMER_DISCOVERY.md` §5 step 2 |
| Audit check pass rate | Checks passing across the corpus ÷ checks run, per source and overall | `tools/editorial_audit.py` + `tools/nle_check.py` on every source | **100% on 1 source** (VERIFIED — I ran the NLE audit today: ALL CHECKS PASS, exit 0) | Reported *with* the failing checks listed publicly, not as a headline |

**Note on the only non-zero cell:** the audit pass rate is 100% on one source.
That is evidence the machinery is sound, not that the choices would survive
comparison with a human editor across sources and languages — the project's own
wording (VERIFIED — `docs/AI_EXECUTION_STATE.md` item 6). Report it as
"100% (n=1)" or do not report it.

---

## Section D — Interchange (the differentiated capability)

| Metric | Definition | Populating event | Now | Target |
|---|---|---|---|---|
| NLE import success rate | Timelines that import with all clips online ÷ timelines imported into a real NLE | Documented import attempt with NLE name and exact version recorded | **0 / 0 — never attempted** (VERIFIED). No NLE is installed on this host | 100% on Resolve free tier first, then Premiere Pro, then Final Cut Pro. `docs/NLE_COMPATIBILITY.md` states the pass criterion: sequence opens, all clips online, duration matches the render within 2 frames |
| Timeline handoff rate | Cuts where the editor opened the exported timeline ÷ cuts delivered | Editor-reported | **0 / 0** | This is Section C's "timeline retained" from the other side; both must move together |
| Round-trip fidelity | Frame-accurate agreement between the imported timeline's in-points and the rendered cut ÷ cuts checked | Measured in the NLE after import | **0 / 0** | 0–2 frames, the tolerance `tools/nle_check.py` already enforces offline (VERIFIED — today's run shows 0.00–1.63 frames vs the renders) |

**This section is the point of the product, and every cell is zero because no
experiment has been run.** That is the single most important thing on this page.

---

## Section E — Retention and revenue

| Metric | Definition | Populating event | Now | Target |
|---|---|---|---|---|
| 30-day retained users | Users with ≥1 pipeline in the 30 days after their first ÷ users who activated | `pipeline.started` cohorts | **0** | Only meaningful once Section C's blind A/B has been won |
| Paying customers | Distinct accounts with a completed payment | `payment.succeeded` — no billing code exists (VERIFIED — `docs/RED_TEAM.md` appendix) | **0** | First 5 paying design partners is the round's stated milestone (`docs/FUNDRAISING.md` §12) |
| MRR | Sum of active subscriptions | billing system | **$0** | $0 today and honestly so. `docs/BUSINESS_MODEL.md` designs tiers ($29/$79/$249/$999) but no price has been charged to anyone |
| Gross margin | (Revenue − variable COGS) ÷ revenue | Billing + cost ledger | **N/A** | Supply side is not the risk: measured all-in compute is $0.00104–$0.00476 per source-minute (VERIFIED — `docs/BUSINESS_MODEL.md` §4), so at the $29/300-minute plan COGS is ~$1.43 against $29. The model is demand-constrained, not cost-constrained (VERIFIED — `docs/BUSINESS_MODEL.md` §7) |
| Churn | Cancelled ÷ active, monthly | billing system | **N/A** | The model's sensitivity analysis shows churn is the assumption the whole business turns on (VERIFIED — `docs/BUSINESS_MODEL.md` §7). It has no data behind it |

---

## Section F — The learning loop (the moat, if it exists)

`docs/MOAT.md` is explicit: there is no moat today and no flywheel today. The
pipeline emits a rich feature record and a completely empty label record. These
metrics track whether the second half gets built.

| Metric | Definition | Populating event | Now | Target |
|---|---|---|---|---|
| Human keep/reject decisions captured | Decisions recorded on ranked candidates ÷ candidates surfaced | `candidate.kept` / `candidate.rejected` — **no such events exist** | **0** | The minimum viable learning unit defined in `docs/MOAT.md` §4.1 |
| AI→published diffs captured | Sources where the shipped edit is diffable against the machine's proposal | Post-export comparison | **0** | `docs/MOAT.md` §2.2, DESIGNED only |
| Editorial profiles trained per channel | Channels with a conditioned profile | none — no training code | **0** | Phase 2 of `docs/MOAT.md`; gated behind Phase 1, which is gated behind the NLE round trip |
| Post-publish performance joined to cuts | Cuts with platform performance attached | platform API integration | **0** | Phase 3 of `docs/MOAT.md`, DESIGNED only |

**Do not present any of these as a roadmap metric to an investor.** `docs/MOAT.md`
ranks the per-channel editorial profile as the only genuine moat candidate and
states it does not exist at all today. Zero is the accurate figure.

---

## Reporting rules

1. **Never present this dashboard without the "as of 2026-09-19: no data"
   headline.** A table of zeros without the headline invites a reader to assume
   the numbers are early-but-rising. They are absent.
2. **Every zero carries its reason.** "0 — no hosted instance exists" and
   "0 — never attempted" are different facts and must not be collapsed into one
   cell.
3. **Report n.** A 100% audit pass rate on one source is a different claim from
   a 100% pass rate on fifty, and the difference is the whole of gap G6.
4. **Targets are assumptions until a measurement justifies them.** Every target
   above is marked ASSUMPTION. The first real measurement should overwrite it,
   down as readily as up.
5. **When a metric moves off zero, record which event started firing and when.**
   The transition from "definition" to "measurement" is itself the news, and it
   is the first honest traction milestone available to this company.
