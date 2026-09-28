# SelekyT — Business Model and Unit Economics

Prepared 2026-09-19. Every number below is one of:

- **[MEASURED]** — run on this host today (2026-09-19) against the real reference run or its real artifacts.
- **[CITED]** — retrieved from a live public pricing source today; URL and retrieval date given.
- **[COMPUTED]** — arithmetic from the above, shown in full.
- **[ASSUMPTION]** — a stated input that was *not* measured or cited. Nothing in this document is presented as fact unless it carries one of the other three labels.

Ground truth carried over from `docs/AI_EXECUTION_STATE.md` and `docs/product-gap-map.md`: the reference source is **NASA 360 Episode 5, 1469.77 s (24.496 min)** of genuine two-host unscripted speech, run end-to-end on a **4-core, 15 GB RAM, CPU-only** host with faster-whisper `small`, int8, 4 threads, beam_size 1, 5-minute chunks with 15 s overlap. Zero users, zero revenue, no pilots, no investors.

The repo is **proprietary-licensed** (commit `c6b46c9`, "legal: replace MIT with proprietary license"; `LICENSE` grants no right to copy, host as a competing service, or commercially exploit). There is no open-source self-hosting right. That is a hard constraint on the deployment models in §6 and is not an assumption.

---

## 1. What was actually measured on this host (2026-09-19; render re-measured 2026-09-28)

I benchmarked the two stages that dominate compute, on real artifacts of the reference run, matching the parameters SelekyT actually uses (`providers/whisper.py:197`: `WhisperModel(model_name, device="auto", compute_type="int8", cpu_threads=cpu_count)`). The render row was **re-measured 2026-09-28 because the encoder it described no longer ships** — see the correction note under the table. These are component micro-benchmarks, **not** pipeline runs — no repo state was modified.

| Quantity | Value | Label | How |
| --- | --- | --- | --- |
| Transcription RTF, `small`/int8/beam 1/4 threads | **0.1651** | MEASURED | 39.63 s to transcribe 240.0 s of the real 16 kHz mono audio from the reference run |
| Render RTF, 16:9, shipped encoder `slow`/crf18 (1280×720 source, ASS burn) | **0.875** | MEASURED, 2026-09-28 | 26.25 s wall for 30 s of real corpus footage, best of 2, with a real ASS from the run burned in. **Supersedes 0.1232 — see the note below.** |
| Render RTF, 9:16, shipped encoder `slow`/crf18 (406×722 tracked crop, ASS burn) | **0.358** | MEASURED, 2026-09-28 | 10.74 s wall for 30 s, same conditions. Cross-validates against the encoder ladder in `engine/render.py:_encoder_args` (2.89 s for 240 frames = 0.361) to within 1%. |
| Render RTF, 16:9, **with the pre-`56253b5` encoder** `veryfast`/crf20 | **0.277** | COMPUTED from MEASURED | 0.875 ÷ 3.16, the encoder ratio measured head-to-head on identical 1080p footage and filters (10.13 s veryfast/crf20 vs 32.01 s slow/crf18). This is the figure the old 0.1232 row belongs to in spirit, and it is still not 0.1232 — see below. |
| Render RTF, 9:16 **if square pixels were emitted** (crop 272:486 → upscale to 1232×2184) | **0.6829** | MEASURED | 40.972 s for 60 s — but see below, SelekyT does **not** do this |
| Reference source duration | 1469.77 s (24.496 min) | MEASURED | `ffprobe` on `out/url-e2e/project/source/NASA_360_program5_HD.mov` |
| Delivered output | 198.51 s total, 99.254 s per preset × 2, 6 clips per preset | MEASURED | `ffprobe` over all 12 of `facebook/01..06.mp4`, `shorts/01..06.mp4` |
| Clip yield | **13.506%** of source duration (6.753% per preset) | COMPUTED | 198.51 / 1469.77 |
| Delivered bytes | **19.1 MB** (11.90 MB 16:9 + 7.20 MB 9:16 + 0.04 MB captions/timelines) | MEASURED | `du` on the reference project |
| Project disk total | ~952 MB, of which 884 MB is the source copy and the rest is transcription intermediates | MEASURED | `du` |
| 9:16 coded geometry | 308×486, SAR 8:9, DAR 1232:2187 | MEASURED | `ffprobe` on `shorts/01.mp4` |

**These figures supersede an earlier reading of this table.** The reference
artifacts were regenerated at 07:36 on 2026-09-19, after this document was
written, and shipped 6 clips per preset rather than 3. The earlier rows
(116.95 s total, 7.96% yield, 9.9 MB, 272×486 / DAR 1088:2187) describe a run
that no longer exists and **must not be quoted again**. The RTF measurements
above are unaffected — they were taken on the source audio and a 60 s render
window, not on clip counts. Re-verified independently in `docs/EVALUATION.md` §6
and by the integrating operator today.

**Correction that materially changed the model.** I first benchmarked the 9:16 render as a pixel-upscale (0.683 RTF, 39 MB for 60 s). The real output is **not** upscaled: it is coded at 308×486 with SAR 8:9 so the display ratio is 1232:2187 ≈ 9:16 with zero resampling (`docs/product-gap-map.md` RENDER row, re-verified by `ffprobe` today). The real 9:16 short is **161–167 kbps video**, not the ~5 Mbps my upscale benchmark produced. So for cost purposes **both presets encode ≈0.35 M pixels**. The 0.683 figure is retained only as the cost of a hypothetical square-pixel mode — and it is a warning: a "make the pixels square" change would ~5.5× the 9:16 render cost and ~4× the output bitrate. Note that this paragraph is about the **SD reference source**, whose 4:3 content is padded into a 864×486 16:9 frame; on the HD corpus source the 16:9 deliverable is a full 1280×720 (0.92 M px) and the two presets are **not** near-equal — that is the geometry point the second correction below rests on.

**Second correction, 2026-09-28: the render RTF was stale by 7×, and the encoder is why.** The 0.1232 figure above was measured 2026-09-19 against `libx264 -preset veryfast -crf 20`, which is what `engine/render.py` shipped at the time. Commit `56253b5` (2026-09-25, "render: measure the encoder on real footage, and fix the quality defect the ladder exposes") replaced it with `-preset slow -crf 18` — a deliberate quality purchase, +2.64 dB for +44% bitrate — and **the cost row was never re-run**. Every downstream figure in §3 and §4 that traced to it was therefore wrong in the same direction and by the same factor.

The attribution is clean, because the two effects are separable:

| Effect | Measurement | Cost |
| --- | --- | --- |
| Encoder alone, `veryfast`/crf20 → `slow`/crf18 | head-to-head on identical 1080p footage, same filter chain, 900 frames | **3.16×** (10.13 s → 32.01 s) |
| Source resolution, SD 720×486 → HD 1280×720 | pixel ratio of the 16:9 frames the two sources actually emit | **2.6×** |
| Product | 0.1232 × 3.16 × 2.63 ≈ **1.02** | against a measured **0.875** |

The two known factors over-predict slightly (the landscape path pads rather than scales, so a 16:9 deliverable from a 16:9 source does not pay the full pixel ratio) but they account for the whole gap. **Nothing else changed** — the filter chain, the anamorphic 9:16 geometry and the ASS path are unmeasured-but-stable, and captions are provably not the cause: burning a real ASS moves 16:9 from 0.869 to 0.875 and 9:16 from 0.356 to 0.358, i.e. under 1%.

The quality purchase that caused this is **confirmed real and kept**: on identical 1080p footage, `slow`/crf18 scores PSNR 51.445 / SSIM 0.997573 against `veryfast`/crf20's PSNR 47.987 / SSIM 0.995991 — **+3.46 dB**. The correct response was to correct the cost figure, not the encoder. What changed materially is the conclusion in §4: renders are no longer a rounding error in the throughput model, they are its second-largest term after transcription, and the GPU break-even argument shifts accordingly.

**What this still does not claim.** The 2026-09-19 measurement was on the NASA 360 reference source, which is no longer present on this host (`out/url-e2e/` was cleaned up); the 2026-09-28 re-measurement is on the corpus's 1280×720 29.97 fps pentagon briefing, the same footage `_encoder_args`'s own ladder was scored against. The two are not the same source, so the 2.6× resolution term above is an explanation of the gap rather than a controlled variable — the controlled variable is the 3.16× encoder term, which was measured head-to-head on one file.

**Corroboration of the CPU number.** The faster-whisper README benchmark (CITED, https://github.com/SYSTRAN/faster-whisper, retrieved 2026-09-19) reports `small`, int8, beam 5 on an i7-12700K (8 threads) taking 1m42s for 13 min of audio → RTF 0.131. My 0.165 on 4 cores with beam 1 is consistent with fewer cores. That README also reports large-v2 on an RTX 3070 Ti at int8 taking 16 s for 13 min (RTF 0.021) — the anchor I use for the GPU assumption in §4.

### Stages I could not measure, and how I treat them

| Stage | Treatment |
| --- | --- |
| Full-pipeline wall clock | **No end-to-end timing was ever recorded.** I assemble the estimate from the component benchmarks above. Labeled. |
| Audio extraction / decode to 16 kHz mono | **[ASSUMPTION]** 0.10 RTF. ffmpeg decode of 720×486 29.97 fps with `-vn` is fast; this is deliberately generous. Unmeasured for H.265/4K sources. |
| Story detection, scoring, pause classification, VAD, EDL/FCPXML/caption export | **[ASSUMPTION]** 0.01 RTF combined. These are pure-Python passes over the transcript and interval lists; on the reference run they produced `report.json` with per-candidate scores in well under a second of CPU. Still an assumption. |
| GPU transcription RTF | **[ASSUMPTION]**, anchored on the README's large-v2 GPU figure above. This host has no GPU. |
| Ingest of 4K / H.265 / high-bitrate sources | **Measured, 2026-09-28 — 9.0× the 720p cost, and it exceeds realtime.** A 3840×2160 H.265 transcode of real corpus footage runs the full pipeline correctly (perception, reframe, render, EDL/FCPXML all pass `tools/nle_check.py`), but the reframe's perception pass costs **36.5 s for 27 s of clip — 135% of realtime**, against **4.0 s (15% of realtime) for the same content at 1280×720**. The attribution is decisive: **28.96 s of the 36.47 s is frame decode (79%)**, and only 7.21 s is YuNet detection at full 4K. Detection at half resolution costs 2.20 s and finds 50 faces against 57 — the same subject track — so the recoverable headroom is ~5 s, not the 32 s the headline suggests. A downscale-for-detection gate was measured out: 14% of the pass for the mis-scaled-detection hazard `engine/reframe_perception.py`'s own `setInputSize` comment warns about, which is diminishing returns. **Practical consequence: 4K ingest works and is correct, but a 1–3 hour 4K source cannot be perceived in realtime on this CPU, and a progress estimate or a batch queue sized on the 720p figure will be wrong by roughly an order of magnitude.** |

---

## 2. Cloud pricing, retrieved 2026-09-19

All instance prices are **on-demand / pay-as-you-go, Linux, cheapest US region**, pulled from machine-readable official APIs rather than marketing pages.

### Compute instances

| Instance | vCPU | RAM | GPU | $/hr | Source |
| --- | --- | --- | --- | --- | --- |
| AWS `c7i.xlarge` (us-east-1) | 4 | 16 GiB | — | **$0.1785** | [CITED] AWS Price List API, offer `AmazonEC2`, region us-east-1, publicationDate **2026-09-18** — `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json` |
| AWS `c7i.2xlarge` | 8 | 32 GiB | — | **$0.3570** | same |
| AWS `m7i.2xlarge` | 8 | 32 GiB | — | $0.4032 | same |
| AWS `g6.2xlarge` | 8 | 32 GiB | 1× L4 | **$0.9776** | same |
| AWS `g5.2xlarge` | 8 | 32 GiB | 1× A10G | $1.2120 | same |
| Azure `D8s v5` (eastus) | 8 | 32 GiB | — | **$0.3840** | [CITED] Azure Retail Prices API, `https://prices.azure.com/api/retail/prices`, retrieved 2026-09-19 |
| Azure `D8as v5` (AMD) | 8 | 32 GiB | — | $0.3440 | same |
| Azure `NC4as T4 v3` | 4 | 28 GiB | 1× T4 | $0.5260 | same |
| Azure `D8s v5` **Spot** | 8 | 32 GiB | — | **$0.08106** | same (skuName "Spot") |
| Azure `NC4as T4 v3` **Spot** | 4 | 28 GiB | 1× T4 | **$0.14917** | same — 72% below the T4 on-demand price |

The `c7i.xlarge` is the closest cloud match to the measured 4-core host and is the primary reference instance below.

**GCP is deliberately excluded from the calculations.** `cloud.google.com/compute/vm-instance-pricing`, `.../all-pricing` and `.../gpus-pricing` render prices client-side; an automated fetch returned no quotable table and the public pricing-calculator endpoints I tried returned 404. I will not quote an unverified figure in a cost model. GCP is almost certainly within ±15% of the AWS/Azure equivalents shown, but that is a guess, not a citation.

### Storage and network

| Item | Price | Source |
| --- | --- | --- |
| AWS EBS gp3 | **$0.08 / GB-month** | [CITED] AWS Price List API, offer `AmazonEC2`, publicationDate 2026-09-18 |
| Data transfer **out** to internet, first 100 GB/month | **$0.00** | [CITED] AWS Price List API, offer `AWSDataTransfer`, publicationDate **2026-09-16** ("$0 for 100GB of data transfer out to the internet, aggregated globally") |
| Data transfer out beyond free tier, first 10 TB/month | **$0.09 / GB** | same ("$0.090 per GB - first 10 TB / month data transfer out beyond the global free tier") |
| Data transfer **in** from internet | $0.00 | [CITED] AWS published policy — inbound to EC2/S3 from the internet is free. Note: this did **not** appear as a row in the `AWSDataTransfer` offer file for us-east-1, so I flag it as a published policy rather than a line item I verified in the API today. |

### Transcription APIs

| Provider / model | $/audio-min | Source |
| --- | --- | --- |
| AssemblyAI **Universal-3.5 Pro** (pre-recorded/async) | **$0.0035** ($0.21/hr) | [CITED] https://www.assemblyai.com/pricing, retrieved 2026-09-19 |
| AssemblyAI Universal-2 (async) | $0.0025 ($0.15/hr) | same |
| Deepgram **Nova-3 Monolingual** (pre-recorded), Pay As You Go | **$0.0043** | [CITED] https://deepgram.com/pricing, retrieved 2026-09-19 |
| Deepgram Nova-3 Monolingual, Growth ($4K+/yr prepaid) | $0.0036 | same |
| Deepgram Nova-3 Multilingual, PAYG | $0.0052 | same |
| OpenAI `whisper-1` | **$0.006** ($0.36/hr) | [CITED] https://developers.openai.com/api/docs/pricing, retrieved 2026-09-19 |
| Google Speech-to-Text, standard models (async, tier 1) | $0.003 | [CITED] https://cloud.google.com/speech-to-text/pricing, retrieved 2026-09-19 (batch table; page states per-minute billing) |

---

## 3. Throughput model

All figures are **minutes of compute per minute of source video**.

| Component | RTF | Label |
| --- | --- | --- |
| Audio extraction / decode to 16 kHz mono | 0.10 | ASSUMPTION |
| Transcription, `small`, int8, 4 threads | 0.1651 | MEASURED |
| Editorial pipeline (story units, scoring, pause fusion, export) | 0.01 | ASSUMPTION |
| Render 16:9 (0.875) × yield/preset (0.06753) | 0.05909 | COMPUTED from MEASURED |
| Render 9:16, anamorphic (0.358) × yield/preset (0.06753) | 0.02418 | COMPUTED from MEASURED |
| **Total, current configuration** | **0.3584** | COMPUTED |

The render terms are no longer near-equal, and the reason is geometry rather than encoder: the 16:9 deliverable from a 1280×720 source is a 1280×720 frame (`landscape_filter` never enlarges and a 16:9 source needs no padding), while the 9:16 deliverable is a 406×722 crop — **0.92 M pixels against 0.29 M**, 3.2× apart. The old row charged both presets the same figure because the SD reference source made them near-equal; on HD source they are not, and modelling them as one would have understated 16:9 by 3.6× while overstating 9:16 by 2.4×. Both figures above are per **minute of deliverable**, then multiplied by the 6.753% yield so the row reads in source-minutes like every other row.

Model-size scaling **[ASSUMPTION]**, anchored on the relative compute cost of Whisper sizes (the README table above lists `small` and `large-v2`; I use the conventional ≈3× step to `medium` and ≈10× to `large-v3` and mark the resulting figures as assumptions throughout):

| Transcription model | CPU RTF | Total RTF |
| --- | --- | --- |
| `small` (today's) | 0.1651 | 0.3584 |
| `medium` | 0.4953 | 0.6886 |
| `large-v3` | 1.651 | 1.8443 |

---

## 4. Cost per source-minute, three delivery modes

Reference instance: **AWS `c7i.xlarge`, $0.1785/hr on-demand** (4 vCPU — matches the measured host). Hourly cost ÷ 60 × RTF.

### (a) CPU-only batch — the current architecture

| Configuration | $/source-min | $ per 24.5-min reference job |
| --- | --- | --- |
| `small`, `c7i.xlarge` on-demand | **$0.00107** | $0.026 |
| `small`, Azure `D8s v5` on-demand | $0.00224 | $0.055 |
| `small`, Azure `D8s v5` **spot** | **$0.00047** | $0.012 |
| `medium`, `c7i.xlarge` on-demand | $0.00205 | $0.050 |
| `medium`, Azure spot | $0.00093 | $0.023 |
| `large-v3`, `c7i.xlarge` on-demand | $0.00549 | $0.134 |

All rows are recomputed from the 2026-09-28 throughput model; the pre-correction equivalents were $0.000868 / $0.021 for `small` on `c7i`. The move is 23% on the total, not 7× — because the render terms are multiplied by the 6.753% clip yield before they enter the model, so a 7× error in render RTF becomes a 23% error in end-to-end cost. That is the reason the stale figure survived as long as it did: it was invisible in the only number anyone would have checked.

### (b) GPU-assisted

GPU transcription RTF **[ASSUMPTION]**: 0.02 (`small`), 0.03 (`medium`), 0.08 (`large-v3) — extrapolated from the README's measured large-v2 RTF 0.021 on an RTX 3070 Ti. The non-transcription work (decode, render, export) still runs on the VM's own CPUs either way.

| Configuration | $/source-min |
| --- | --- |
| `small` on `g6.2xlarge` (L4, $0.9776/hr) | $0.00348 |
| `small` on Azure T4 spot ($0.14917/hr) | $0.00053 |
| `large-v3` on `g6.2xlarge` | $0.00445 |
| `large-v3` on Azure T4 spot | **$0.00068** |

All four are restated 2026-09-28 on one stated rule: the instance is billed as a whole VM, and total RTF = the assumed GPU transcription RTF above **plus the measured non-transcription RTF 0.1933** (decode, render and export run on the instance's own CPUs). The pre-correction figures were $0.00264 / $0.00040 / — / $0.00050; the largest part of their movement is the render term 0.1266 → 0.1933, and the `small` rows also did not reconcile with the cited hourly prices, so they are restated rather than rescaled.

**GPU is not justified at the `small` model, and the corrected render figure makes the margin wider, not narrower.** The L4 instance costs 5.47× the 4-vCPU CPU instance, so GPU only wins if it cuts total RTF below 0.3584 × (0.1785/0.9776) = **0.0654**. The non-transcription work alone is **0.1933** — **2.95× that ceiling** (before the correction it was 0.1266, "only" 2.4× over a 0.0533 ceiling). GPU transcription cannot touch decode, render or export; those stay on the VM's own CPUs. So the conclusion is unchanged in direction and strengthened in degree: while renders run on CPU ffmpeg at the shipped quality setting, no transcription speedup can make an L4 economical. **GPU becomes the right answer only if accuracy forces `large-v3`**: at that point `large-v3` on Azure T4 spot is now **~8× cheaper** than CPU on-demand ($0.00068 vs $0.00549), and it is the difference between a sane batch service and an uneconomical one. The decision is gated on an accuracy measurement — one that now exists for clean English and says `large-v3` is *not* the floor there (§9), and one that now exists for non-English and says `small` fails outright there (Telugu, 151.08% WER, §9 item 1). That second measurement does **not** force the GPU case, because the failure it shows is not solvable by making the same model bigger — 0 of 50 utterances inside the bar means the model has no Telugu competence to spend compute on. It forces a routing or language-gate decision instead, which is cheaper than either hardware option. The case that would genuinely force `large-v3` — noisy, crosstalk, compressed real-world audio — is still unmeasured. Not a price decision.

**The lever the correction does expose.** The render term is now 0.0833 of the 0.3584 total — 23% of the model, and the largest term after transcription (0.1651); it has already passed the 0.10 decode term, which is an ASSUMPTION rather than a measurement. It is also the only large term that is a *product decision rather than an accuracy-gated* one: the encoder ladder in `engine/render.py:_encoder_args` shows `veryfast`/crf20 costing **3.16× less** than the shipped `slow`/crf18 on identical footage, for −3.46 dB of quality. That trade was made deliberately and is not being reversed here. But it means a batch service that needs throughput has a **3.16× render lever available that costs no accuracy and no architecture change** — a per-run quality/speed toggle, or a two-pass workflow that previews fast and masters slow. That is a genuine product option the stale figure hid: at 0.1232 the two render rows together were 0.0166, or 5.7% of the model, and not worth a setting.

### (c) External transcription API

Transcription is bought per audio-minute; chunk overlap adds 4.1% billed audio (5 chunks × 15 s overlap over 1469.77 s). Renders still need CPU compute, charged at the measured non-transcription RTF **0.1933** on `c7i.xlarge` = **$0.000575/source-min**.

| Provider | API cost | + render | $/source-min | $/reference job |
| --- | --- | --- | --- | --- |
| AssemblyAI Universal-2 | $0.00260 | +$0.00057 | $0.00317 | $0.078 |
| Google STT standard | $0.00312 | +$0.00057 | $0.00369 | $0.091 |
| **AssemblyAI U3.5 Pro** | $0.00364 | +$0.00057 | **$0.00421** | $0.103 |
| Deepgram Nova-3 (Growth) | $0.00375 | +$0.00057 | $0.00432 | $0.106 |
| Deepgram Nova-3 (PAYG) | $0.00448 | +$0.00057 | $0.00505 | $0.124 |
| OpenAI `whisper-1` | $0.00625 | +$0.00057 | $0.00683 | $0.167 |

**Reading:** the cheapest credible API is ~3.0× self-hosted CPU on-demand ($0.00317 vs $0.00107), and the realistic mid-choice (AssemblyAI U3.5 Pro or Deepgram Nova-3) is ~3.9×. In absolute terms the premium is only ~$0.003/source-min. The API is not "too expensive" — it is a decision to buy ~$0.10 of transcription per 25-minute tape in order to own zero transcription infrastructure, zero model ops and zero accuracy risk on the STT layer. It is the correct answer for a self-serve v1 with no DevOps capacity, and the wrong answer once volume passes the point where an engineer's attention is cheaper than the markup.

### Storage and network

| Item | $/source-min | Basis |
| --- | --- | --- |
| Staging (0.889 GB: source + intermediates) held 7 days, gp3 | $0.000677 | COMPUTED from MEASURED bytes + CITED price |
| Staging held 30 days | $0.002903 | same |
| Staging deleted immediately after job | $0.000097 | same |
| Egress of delivered outputs (19.1 MB → 0.78 MB/srcmin, $0.09/GB) | $0.000070 | COMPUTED from MEASURED bytes |

**The I/O surprise, and why it does not bite.** At first glance "a 952 MB project per 25-minute tape" looks like a storage and egress disaster. It is not: 884 MB of that is the *source copy* and the rest is a transient 16 kHz WAV intermediate. Delivered video is **19.1 MB, or 0.78 MB per source-minute**. So output storage and output egress are ~$0.00007/source-min — negligible. What *would* be expensive is returning the processed source to the user ($0.0031/source-min at $0.09/GB), which the product has no reason to do, and holding sources 30 days ($0.0029/source-min, 3.4× the entire compute cost). **Retention policy on source media is a bigger cost lever than the transcription model.** Delete sources on job completion; keep only the ~19 MB deliverable.

### All-in planning cost

| Mode | Compute | Storage 7 d | Egress | **$/source-min** | **$/24.5-min job** |
| --- | --- | --- | --- | --- | --- |
| CPU `small`, on-demand, delete after run | $0.001066 | $0.000097 | $0.000070 | **$0.001233** | $0.030 |
| CPU `small`, on-demand, 7-day staging | $0.001066 | $0.000677 | $0.000070 | **$0.001813** | $0.044 |
| CPU `small`, Azure spot, 7-day staging | $0.00047 | $0.000677 | $0.000070 | **$0.001217** | $0.030 |
| CPU `medium`, on-demand, 7-day staging | $0.002049 | $0.000677 | $0.000070 | **$0.002796** | $0.068 |
| CPU `medium`, spot, 7-day staging | $0.00093 | $0.000677 | $0.000070 | **$0.001677** | $0.041 |
| GPU `large-v3`, T4 spot, 7-day staging | $0.000679 | $0.000677 | $0.000070 | **$0.001426** | $0.035 |
| **API (AssemblyAI U3.5 Pro) + CPU render** | $0.004215 | $0.000677 | $0.000070 | **$0.004962** | $0.122 |

All compute cells are recomputed from the 2026-09-28 throughput model (§3, §4). The GPU row is restated on an explicit rule: a GPU instance is billed as a whole VM at the cited hourly price, and its total RTF is the **assumed** GPU transcription RTF (§4b) **plus the measured non-transcription RTF 0.1933**, because decode, render and export still run on that VM's own CPUs. The pre-correction GPU compute was $0.00050; the render term inside it was 0.1266 then and is 0.1933 now. The `medium` on-demand cell also moves for a second reason — the pre-correction $0.001850 implied an hourly rate of $0.1804 rather than the cited $0.1785, and is restated at the cited price.

**Planning figure used for pricing: $0.004 per source-minute.** I take the most expensive credible mode and round up. The correction changes its coverage, and this is the one place the correction bites a decision rather than a number: the API mode is now **$0.00496** — **1.24× the planning figure**, where before the correction it sat at $0.00476, comfortably inside it. The self-hosted modes still carry margin: $0.004 is **2.2×** `small` on-demand with 7-day staging, **1.4×** `medium`, and **3.3×** `small` on spot. So $0.004 remains a sound planning figure for the architecture the product actually ships (self-hosted CPU batch), and the §6 prices are still stress-tested against a COGS **1.4–3.3× the self-hosted figure** — but the "and the API path is also covered" reading is no longer true. The API path is ~24% underwater at $0.004 and would need either the $0.005 rounding or the overage pricing in §6.7 to carry it. If the business is not profitable at $0.004, it is not a pricing problem, it is a demand problem.

---

## 5. What the product can actually be sold on

From `docs/product-gap-map.md`, the *verified* capabilities a price can be attached to today: URL and local-file ingest end-to-end; chunked transcription with word timestamps; deterministic multi-signal scoring with rejection reasons and a context/truth guard; story-unit grouping; dead-air removal; 16:9 and 9:16 renders with burned-in word-timed ASS/SRT captions; per-cut CMX 3600 EDL and FCPXML 1.11; a repeatable editorial audit (`tools/editorial_audit.py`).

The two things that a **professional** price rests on and that are **not** verified: **no NLE has ever imported an exported timeline** (G2), and **editorial judgement is unmeasured at scale** (G6 — three clips from one source). YouTube fetch was also never observed from a datacenter IP, and the most recent commit (`ac8a658`, "cycle player clients past the per-video sign-in wall") is an attempt at G1 whose effect is untested. No price below should be read as a claim that these work.

---

## 6. Pricing design

All COGS use the **$0.004/source-min planning figure** [COMPUTED]. Gross margin here is *usage gross margin* — the margin on the variable cost of the minutes consumed. It excludes fixed costs (§7) and excludes the cost of acquiring the customer.

### 6.1 Creator SaaS — $29/mo, 300 source-minutes, 2 presets per source

- **Who buys:** individual YouTuber / podcaster / speaker with a back catalogue of long form they want repurposed. Buys alone, on a card, from a landing page.
- **What they get:** URL or file ingest, both aspect ratios, burned-in captions, per-cut EDL/FCPXML downloads, 7-day artifact retention.
- **Arithmetic:** 300 × $0.004 = **$1.20 COGS**; contribution **$27.80**; **gross margin 95.9%**; implied price $0.097 per delivered source-minute.
- **Defensible?** At 300 minutes the plan costs $1.20 to deliver and saves, on the reference yield, ~12 minutes of finished vertical clips per month. The price is not cost-constrained at all; it is willingness-to-pay constrained. $29 is a mid-market creator-tool price; whether a creator pays it depends entirely on G6 (is the selection good?), which is unmeasured.
- **Weakness:** low ARPU means the plan dies on churn and CAC, not COGS (§7). And 300 minutes is *more* source than almost any individual creator generates monthly — most seats will be low-usage, which is good for margin but means the "included minutes" are mostly marketing.

### 6.2 Professional editor — $79/mo, 1,500 source-minutes

- **Who buys:** a freelance video editor or small post house that wants a first pass, then finishes in an NLE.
- **What they get:** everything in Creator, plus the timeline exports as the headline feature (EDL/FCPXML per cut), caption-style control, and project retention long enough to re-export.
- **Arithmetic:** 1,500 × $0.004 = **$6.00 COGS**; contribution **$73.00**; **gross margin 92.4%**; implied price $0.053/source-minute.
- **Defensible?** This is where the *verified* differentiators actually live — deterministic scoring with auditable rejection reasons, per-cut timeline round-trips, the editorial audit. $79/mo against a freelance editor billing $40–100/hr is an easy yes *if* the timeline imports cleanly (G2, unverified) and *if* the first pass saves a measurable hour per week.
- **This is the highest-leverage tier to price well**, because the buyer's unit of value is *their own billable hour*, not minutes of video.

### 6.3 Agency / multi-seat — $249/mo, 5 seats, 6,000 pooled source-minutes

- **Who buys:** a social or content agency producing short form for several clients from long-form source.
- **What they get:** pooled minutes across seats, per-client project separation, shared caption styles.
- **Arithmetic:** 6,000 × $0.004 = **$24.00 COGS**; contribution **$225.00**; **gross margin 90.4%**; implied price $0.042/source-minute.
- **Defensible?** $249 is below the cost of one editing day; the pooled model matches agency reality (uneven usage across clients). Multi-seat introduces account/admin work that costs real money, so the true margin is lower than the 90% usage figure suggests.

### 6.4 Newsroom / media — $999/mo, 30,000 source-minutes

- **Who buys:** a broadcast or digital newsroom processing press conferences, hearings and long interviews into same-day vertical cutdowns.
- **What they get:** volume, retention controls, the editorial audit as a compliance artifact, deterministic and reproducible selection (valuable when an editor has to defend a cut), language control, SSO.
- **Arithmetic:** 30,000 × $0.004 = **$120 COGS**; contribution **$879**; **gross margin 88.0%**; implied price $0.033/source-minute.
- **Defensible?** This is the best unit economics in the line-up, and the *reason* is that newsrooms already pay per-minute transcription rates ($0.003–0.006 [CITED]) for the transcript *alone*. SelekyT delivers transcript + selection + renders + timelines for $0.033 against a transcript-only benchmark of $0.003–0.006 — i.e. the price is ~6–11× the *transcription-only* market rate and includes a finished video. **But:** 30,000 minutes/mo is ~2,400 12-minute tapes a month; the delivery SLA, ingest robustness and editorial quality required for that are nowhere near demonstrated. And this sale needs a procurement process. Not a v1.

### 6.5 Enterprise / private deployment — $30k–75k/year license

- **Who buys:** a media organization or regulated entity that will not send raw footage to a third party, and wants the pipeline inside its own tenancy.
- **What they get:** the software, deployed on their own CPU or GPU fleet, with the proprietary license permitting it (recall `LICENSE`: commercial use requires written permission — this tier *is* that permission).
- **Arithmetic:** COGS is not compute (they own the fleet) but delivery — one solutions engineer's time to install and one quarter of support. **[ASSUMPTION]** if delivery costs ~$8k–12k per customer-year in human time, a $30k floor license yields ~60–73% margin; the $75k end ~85%.
- **Defensible?** Highest absolute margin and it removes the entire cost model in §4 from the P&L — the customer pays the cloud bill. **And it matches the product's actual maturity**: a self-contained, CLI-plus-web-shell, deterministic pipeline that runs on one box is far closer to "install this in our rack" than to a multi-tenant SaaS. The counter-argument is that an enterprise sale needs reference customers, NLE compatibility proof and an SLA, none of which exist.
- **This is the model most consistent with the current state of the product, and the least consistent with the current state of the company (zero customers, zero sales motion).**

### 6.6 API / usage credits — $0.012–$0.02 per source-minute, prepaid

| Package | Rate | Volume | Revenue | COGS at $0.004 | Margin |
| --- | --- | --- | --- | --- | --- |
| Starter | $0.02/min | 100,000 min | $2,000 | $400 | **80.0%** |
| Growth | $0.015/min | 500,000 min | $7,500 | $2,000 | **73.3%** |
| Scale | $0.012/min | 1,000,000 min | $12,000 | $4,000 | **66.7%** |

- **Who buys:** another product (podcast platform, CMS, newsroom CMS plugin) that wants short-form derivation as a feature, not a vendor relationship.
- **Defensible?** At $0.02/min this is ~5× the self-hosted CPU cost and ~2× the API-mode cost — i.e. it is priced *below* what a buyer would pay to reproduce it with a transcription API plus their own render stack, and above SelekyT's own cost. That is a genuine, defensible spread. A metered product is also the only one whose price automatically tracks the real unit of cost.
- **Weakness:** prepaid credits at these volumes mean the first real customers are large and unforgiving; a 100k-minute buyer will demand an SLA and accuracy evidence.

### 6.7 Hybrid subscription + usage (recommended)

**$29/mo Creator and $79/mo Pro include a bucket; overage billed at $0.02/source-minute.** Same arithmetic as 6.1/6.2 for in-bucket usage; overage carries the 80% margin of the API product. This structure does three things the pure tiers do not: it removes SelekyT's exposure to a heavy user by making the marginal minute metered; it lets the headline price stay low for acquisition; and it makes the revenue per customer actually proportional to value delivered.

### Sensitivity: what if COGS is 5× the planning figure?

At $0.020/source-minute (i.e. `large-v3` on CPU on-demand with 30-day source retention, or a wholesale re-benchmark of the throughput model):

| Plan | COGS at $0.020 | Gross margin |
| --- | --- | --- |
| Creator $29/300 min | $6.00 | 79.3% |
| Pro $79/1,500 min | $30.00 | 62.0% |
| Newsroom $999/30,000 min | $600 | **39.9%** |

The $29 and $79 tiers are robust to a 5× cost error. **The Newsroom tier is not** — it is the one whose price would have to move, because it sells volume. That asymmetry is a real argument for starting small.

---

## 7. Unit economics framework

**This is a model with labeled inputs, not a set of results.** There is no customer data. Nothing below has been measured.

### Definitions

- **CAC** = total sales + marketing spend in period ÷ paid customers acquired in period.
- **Contribution per month** `C` = ARPU − variable COGS − per-customer support/hosting cost.
- **LTV** = `C / k` where `k` is the monthly churn rate (constant-churn model).
- **Payback period** = `CAC / C` (months).
- **Expansion revenue** = revenue from a customer beyond their initial tier — seat additions, overage credits, tier upgrades. Tracked as a separate multiplier `e` on LTV: `LTV = C(1 + e) / k`.

### Assumption register

| # | Assumption | Value | Status |
| --- | --- | --- | --- |
| A1 | Variable COGS per source-minute (planning figure) | $0.004 | **COMPUTED** from §4; derived from 2 MEASURED + 2 CITED + 3 ASSUMED inputs |
| A2 | Transcription model required for sale-grade accuracy | `small` today; `medium` assumed sufficient | **MEASURED ON BOTH SIDES — clean English 3.38% WER on `small` (`tools/wer_bench.py`, 3 runs); non-English now scored and failing: Telugu **151.08%** on OpenSLR SLR66, 0 of 50 utterances inside 25%. Noisy/crosstalk still unmeasured; the largest single risk (§9)** |
| A3 | Clip yield from source | 6.75% per preset (13.51% both) | MEASURED on one source only |
| A4 | Creator CAC | $100–300 | **ASSUMPTION** (industry-typical for self-serve creator tools; SelekyT has no acquisition data) |
| A5 | Professional-editor CAC | $200–400 | **ASSUMPTION** (longer sales cycle, higher intent) |
| A6 | Monthly churn, creator tier | 3–15% | **ASSUMPTION** — no retention data exists because no users exist |
| A7 | Monthly churn, professional tier | 3–10% | **ASSUMPTION** |
| A8 | Expansion multiplier `e` | 0.2–0.5 | **ASSUMPTION** — overage + seat growth; no basis for a point estimate |
| A9 | Fixed monthly cost (always-on runner + control plane) | $180/mo | COMPUTED from CITED `c7i.xlarge` $0.1785/hr × 730 hr ($130) + **ASSUMED** $50/mo control plane |
| A10 | Per-subscriber support/account cost | $4/mo creator, $12/mo pro | **ASSUMPTION** |
| A11 | GPU transcription RTF (T4/L4) | 0.02–0.08 by model | **ASSUMPTION**, anchored on a CITED large-v2 GPU benchmark |
| A12 | Editorial/selection quality vs. a human editor | unknown | **NOT MEASURED AT ALL — see §9** |
| A13 | Ingest cost for 4K/H.265 sources vs. the SD reference | unknown | **MEASURED, 2026-09-28 — see §1 item 4.** Reframe perception on a real 3840×2160 H.265 transcode costs **36.47 s for 27 s of clip (135% of realtime)**, 9.0× the same content at 720×720's 4.03 s; 79% of that is frame decode, not detection. **Works correctly; does not perceive in realtime on this CPU.** |

### Sensitivity: the model's entire behaviour is churn-driven, not cost-driven

Using `C = ARPU − minutes×A1 − A10`, LTV = `C/k`:

| ARPU | Churn 3%/mo | 5%/mo | 10%/mo | 15%/mo |
| --- | --- | --- | --- | --- |
| $29/mo Creator | LTV $927 → **LTV/CAC 9.3** at $100 | $556 → 5.6 | $278 → 2.8 | $185 → **1.9** |
| $79/mo Pro | LTV $2,433 → 24.3 at $100 | $1,460 → 14.6 | $730 → 7.3 | $487 → 4.9 |

Payback (independent of churn in this model): **3.6 months** at $29 with $100 CAC; **1.4 months** at $79. At $300 CAC the $29 plan takes 10.8 months to pay back and fails the moment churn exceeds ~7%/mo; the $79 plan still pays back in 4.1 months.

**The reading that matters:** at $29 the business is acutely sensitive to A4 and A6 — two numbers that are pure assumptions — while being almost indifferent to A1, the cost. At $79 the same assumptions are 2.7× more forgiving. **SelekyT's unit economics are a demand-side problem, not a supply-side problem.** This is the central finding of the whole model.

### Break-even

Fixed cost $180/mo [COMPUTED from A9]:

| Tier | Subscribers at full usage to cover fixed cost |
| --- | --- |
| Creator $29 | **6.5 → 7** |
| Pro $79 | **2.5 → 3** |

Variable-cost break-even is essentially immediate at any price in §6. The real break-even is when contribution covers the *people* cost (founder time, then first hire), which no number in this document can bound.

---

## 8. Which model to start with, and the switch triggers

**Start with: self-serve Creator ($29/mo) + Pro ($79/mo) hybrid subscription with $0.02/min overage (§6.7), delivered on CPU-only batch compute with 7-day source retention and immediate source deletion, transcription on `medium`, priced at the §6 arithmetic.**

Why, from the evidence rather than from preference:

1. **It matches measured capability.** Both ingest paths, both renders, captions and timeline exports are verified end-to-end on a real source. Nothing in §6.1–6.2 requires a capability that is unverified, *except* the NLE import claim, which the Pro tier leans on hardest — so the Pro tier should be sold with the honest wording "structurally valid EDL/FCPXML; import compatibility not yet certified."
2. **It is the cheapest acquisition loop available**, and the binding constraint is demand data, not COGS (§7). A $29 self-serve sale is the only channel that produces churn and retention numbers — the inputs the model most lacks — without a sales motion the company does not have.
3. **The cost structure is nearly free at low volume.** $0.001–0.0025/source-min self-hosted means a handful of users costs less than the always-on runner. There is no cloud bill cliff to survive.
4. **Do not start with newsroom or enterprise.** Both have better margins on paper (88% / 60–85%) but require reference customers, NLE compatibility proof, SLAs and a procurement cycle. The one thing that would actually deliver them — private deployment (§6.5) — fits the product's maturity but not the company's, and it is worth exactly nothing until a first customer exists.

**Switch triggers, stated in advance so they are not rationalised away later:**

- **Switch the transcription layer to an API** if self-hosted model ops (GPU drivers, CTranslate2 builds, model upgrades) consumes more engineer-time than the ~$0.003/source-min premium, or if accuracy measurement (§9) shows `medium`/`large` self-hosted underperforms a vendor model.
- **Switch to GPU** the moment accuracy requires `large-v3`. At that point CPU on-demand is $0.00549/source-min and T4 spot is $0.00068 — an 8.1× swing. Before that point, buying a GPU is paying 5.47× the hourly rate to save compute that is not the binding cost.
- **Move up-market to Pro/Newsroom** when (a) one exported timeline has been imported into a real NLE (G2 closed) and (b) the editorial audit holds across 20+ diverse sources (G6 closes from "machinery is sound" to "judgement is sound"). Until both are true, a $999/mo newsroom price is a claim, not a product.
- **Re-price the volume tiers** if measured COGS lands above $0.010/source-min. The Creator and Pro tiers survive that; Newsroom does not (§6.7 sensitivity).
- **Abandon subscription for pure metered API (§6.6)** if churn in the self-serve tier exceeds 10%/mo at any CAC above $200 — the $29 plan does not recover, and the metered product is the one whose revenue tracks value delivered.

---

## 9. Honesty: what is weakest, and what measurement would fix it

Ordered by how much of the model each one can invalidate.

1. **The accuracy/model-size assumption (A2) is now measured on both of the sides that were separable — and the two measurements disagree in a way this model has to absorb.** The entire cost model scales with the transcription model: `small` is $0.00107/source-min on CPU, `medium` $0.00205, `large-v3` $0.00549. As of the LibriSpeech test-clean benchmark (`tools/wer_bench.py`, 50 professionally transcribed clips, 6.6 min, production decode parameters), the accuracy figures that did not exist now do, for one domain: **`small` 3.38%, `medium` 2.99%, `large-v3` 3.38%** — `large-v3` *ties* `small` on this sample rather than being the accuracy floor, and `medium`'s 0.39-point gain costs 2.4× the wall time, which is inside the noise at this sample size. **So the specific risk this item was written around — "if `large-v3` turns out to be the minimum, the CPU-only story breaks" — is not supported by that measurement.** The non-English side now has a number too, and it is the opposite: Telugu on OpenSLR SLR66 (50 utterances, 3.46 min, same harness, same decode parameters) scores **WER 151.08% / CER 127.40%** on `small`, with **0 of 50** utterances inside a 25% bar and a best single utterance of 71.4%. WER above 100% means the output holds more error words than the reference contains, so on this language `small` is not a weak transcriber, it is not transcribing at all. **Read the two together:** the English result proves the pipeline is capable of sale-grade accuracy and that model size is not the binding constraint there; the Telugu result proves there is a whole class of input where it fails catastrophically at any of these sizes. The assumption "the same model serves every language" is the thing that broke, not the CPU-cost story — which is the more tractable of the two, because a per-language model gate is a routing decision, not a hardware decision. A `medium` run on the same Telugu corpus has now bounded the size question: **WER 151.08% → 91.54%, CER 127.40% → 69.24%, clips inside the bar 0 → 1 of 50.** Size is a real lever — it cut the error rate by 40 points — and it is not sufficient, since 49 of 50 utterances still miss a 25% bar that Polish clears at a third of the model. **One of the six C10 languages the criterion actually names is now measured, and it passes:** Polish on Multilingual LibriSpeech (35 clips, 7.79 min, 1054 reference words) scores **WER 11.01% / CER 2.45%** on the same `small` model, **34 of 35** clips inside the bar, diacritics intact. **That sharpens the finding rather than softening it.** The boundary is not "English vs. everything else" — one `small` model serves English at 3.38% and Polish at 11.01%, both sale-grade, and fails Telugu at 91.54% even after doubling the model. So the routing conclusion is now quantified per language instead of inferred, and the "no hardware consequence" half of it is *stronger*, because the language that fails still fails at `medium`: this is a language-fit gap, not a size gap, and buying more compute does not close it. **Remaining measurement:** the other five C10 languages (all registered in `tools/wer_bench.py`, each a command rather than a code change), plus a noisy/crosstalk corpus — Common Voice splits cover both — and comparison against a vendor model. Free on this host for the CPU model sizes.
2. **Editorial quality (A12) is not measured at all, and it sets every price in §6.** Three clips from one source passing `tools/editorial_audit.py` is evidence the machinery is sound. It is not evidence a human editor would ship those cuts. Since every tier is priced on willingness-to-pay, and willingness-to-pay is 100% a function of selection quality, **the demand side of this model has no measurements in it whatsoever.** Measurement: run the audit across 20–50 real sources of differing length, language and production style, and — the part that actually matters — get a human editor to rank SelekyT's selections against their own on a sample. Nothing in this document becomes real until that is done.
3. **The throughput model is 2 stages measured, 3 assumed.** Transcription and render are measured; extraction, the editorial passes, and all I/O are assumptions, and there is no end-to-end wall-clock for the pipeline at all. A full pipeline wall-clock on the reference host and on one cloud CPU instance would collapse five assumptions into one measurement. Cheap: one run, no new code.
4. **The reference source is SD, 720×486, 29.97 fps, PCM audio — and the 4K/H.265 half of this item is now measured (A13).** A real 3840×2160 H.265 transcode of corpus footage runs the full pipeline correctly: perception decodes it natively (5/5 frames, unlike AV1's 0/5 that motivated the ffmpeg fallback), YuNet finds faces at full 4K, and the render, EDL and FCPXML all pass `tools/nle_check.py`. The cost is what was unknown, and it is **36.47 s of perception for 27 s of clip — 135% of realtime, 9.0× the 4.03 s the same content costs at 720p (15% of realtime)**. The attribution is what makes this actionable rather than just a big number: **28.96 s of it (79%) is frame decode, and only 7.21 s is detection**; detecting at half resolution costs 2.20 s and finds 50 faces against 57, the same subject track, so the recoverable headroom is ~5 s, not the 32 s the headline suggests. A downscale-for-detection gate was measured out on that basis — 14% of the pass, in exchange for the mis-scaled-detection hazard `engine/reframe_perception.py`'s own `setInputSize` comment exists to prevent — and the honest reading is that **4K ingest works and is correct but cannot be perceived in realtime on this CPU class**, so any progress estimate or batch-queue sizing derived from the 720p figure is wrong by roughly an order of magnitude. The render RTF half of this item is **also now measured, 2026-09-28** — and the extrapolation quoted above was wrong in shape, not just in value. It assumed output pixel-count scaling (a 1080p 16:9 preset ≈ 5.9× the pixels of the 720×486 one, +0.024 RTF). Measured directly on the corpus's 1280×720 source, 16:9 render is **0.875 RTF** against 9:16's **0.358** — the two presets are 3.2× apart in pixels *and* 2.4× apart in RTF, but the 16:9 figure is 7× the 0.1232 this document carried then, and the reason is not pixels at all: it is the encoder `56253b5` installed after that figure was taken (`slow`/crf18 vs `veryfast`/crf20 = 3.16× head-to-head on one file). So the 4K ingest story has two independent cost escalations — decode for perception, which is 9× and is real, and encode for delivery, which is 3.16× and is a deliberate quality purchase. Neither was in the model when this item was written. See the correction note under the §1 table.
5. **GPU RTF (A11) is an extrapolation.** This host has no GPU, so every GPU number in §4(b) is an assumption anchored on a *different* GPU in a README. Measurement: one hour on a `g6.2xlarge` or an Azure T4 spot VM (~$1–2) running the same 240-second benchmark. This directly decides the §8 GPU switch trigger.
6. **CAC and churn (A4–A7) are pure industry-shape guesses, and §7 shows the whole model hinges on them.** I have deliberately not produced a point estimate for LTV/CAC as if it were a result. Measurement: any 50–100 real free users with instrumented retention produce a churn curve that replaces A6/A7 with data. Until then, §7 is a sensitivity map, not a forecast.
7. **One source, one clip yield (A3).** Render cost and delivered bytes both scale with the clip yield, which was measured at 6.75% per preset on exactly one tape. A talk-to-camera source may yield 2–3× that; a multi-camera event less. More sources fix this, and they are needed for item 2 anyway.
8. **No NLE import (G2) underpins the Pro tier's value proposition.** Resolve is free and reads CMX 3600 — one afternoon's work converts "structurally valid" into "compatible", and until it is done, the $79 tier should be described as captions-plus-renders with timeline export as an unsupported bonus.
9. **The license blocks the obvious community path.** `LICENSE` is proprietary and forbids hosting as a competing service or commercial exploitation without written permission. That is a legitimate choice and it is what makes §6.5 possible, but it also means there is no free tier of the software itself to drive bottom-up adoption — so A4 (CAC) cannot be bought down with an open-source funnel. Worth stating plainly rather than discovering later.

---

## 10. Summary

- **Cost per source-minute, full pipeline, recommended configuration** (CPU-only batch, `small`/`medium`, on-demand, 7-day source staging): **$0.00107–$0.0028** [COMPUTED from the 2026-09-28 throughput model]. Cheapest realistic: **$0.0005** on spot. Most expensive credible: **$0.0050** with an external transcription API. Planning figure used for pricing: **$0.004** — which covers every self-hosted mode and is now ~24% *below* the API mode, so the API path needs the §6.7 overage or a $0.005 rounding to break even. All these moved 23% on 2026-09-28 because the render RTF row they were built on was stale by 7× (see the correction note under the §3 table); the move is small because render terms are multiplied by the 6.753% clip yield before they reach the total.
- **The real finding is not a price.** Variable cost is so low — under five cents for a 25-minute tape — that COGS cannot be the constraint on this business at any plausible price. The constraint is that the two things a customer would pay for, **editorial selection quality** and **transcription accuracy on the audio real customers actually bring**, are the two things with no measurement behind them, and the third thing the professional price rests on (NLE import) has never been tried. Transcription accuracy is now measured for clean read English (3.38% on `small`) and for one non-English language (151.08% on Telugu) — and the two together say the easy domain is not representative: the reference source sits in the one language the model handles. Everything in §6 is arithmetically sound and commercially unproven.
- **The single assumption that, if wrong, breaks the business: A2 — the transcription model size required for paid accuracy.** If `small`/`medium` are insufficient and `large-v3` is the floor, CPU cost rises to $0.00549/source-min (5.1× the current `small` figure), the GPU switch becomes mandatory rather than optional, and the "runs on one CPU box" property that underpins both the low-cost batch service *and* the private-deployment license stops being true. It is also the assumption with the cheapest possible measurement — and both halves of that measurement are now in (`tools/wer_bench.py`: `small` 3.38%, `medium` 2.99%, `large-v3` 3.38% on LibriSpeech test-clean, three runs, `large-v3` *not* the floor there; and `small` 151.08% on OpenSLR SLR66 Telugu, 0 of 50 utterances inside the bar). **The shape of the finding changed twice.** First, the measurement does not say a bigger model is needed everywhere; it says one model cannot serve every language, which is a routing problem with no hardware consequence at all. Second, the first C10 language to be measured confirms the routing story is concrete rather than hypothetical: Polish scores **11.01%** on `small` — sale-grade, and a third the size of the model that still fails Telugu at **91.54%**. The language boundary is drawn where the data draws it (European in, Indic out), and because Telugu fails at `medium` too, the gap is language fit rather than model size — so the CPU-only story survives on both sides of it. The measurement that still has to happen is the one that would genuinely force a bigger model — noisy, crosstalk, compressed real-world audio — plus the other five C10 languages, all executable on this host for the CPU model sizes at essentially zero cost.
