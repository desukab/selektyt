# SelekyT editorial rules

SelekyT is built around retention signals rather than arbitrary editing numbers.

## What the platform data supports

YouTube describes performance in three broad buckets: appeal, engagement (whether viewers stick around), and satisfaction. Its retention report highlights gradual declines, dips, spikes/rewatches, and top moments. It specifically recommends improving the opening when viewers leave early and moving compelling later moments earlier when appropriate.

For Shorts, YouTube exposes "stayed to watch", average view duration and average percentage viewed as important engagement measures. These are the signals SelekyT is designed to improve indirectly through editing quality, not to fake.

Meta's Reels guidance emphasizes native 9:16 creative, audio and keeping key messages in the safe zone. SelekyT therefore treats vertical reframing as a composition problem, not a simple center crop.

## Editing logic

1. **Hook immediately.** Start at the strongest complete thought, not the speaker's greeting or housekeeping.
2. **Promise a payoff.** The opening should create a question, contradiction, stakes, surprise or useful outcome that the clip can actually answer.
3. **Protect complete thoughts.** Never cut a sentence simply to hit a target duration.
4. **Remove dead air carefully.** Silence, repeated setup and obvious filler can be removed when doing so does not damage natural delivery.
5. **Create forward motion.** Each beat should add information, escalate the question, change the visual, or deliver a payoff.
6. **Vary visual rhythm.** Avoid a fixed cut-every-X-seconds rule. Cut when the information or visual reason changes.
7. **Pay off the opening.** A clip that hooks but never resolves its question is scored down.
8. **Avoid context traps.** Sensational wording cannot outrun the source. Absolute claims require stronger evidence/context.
9. **Minimal captions.** Captions should aid comprehension, not become a second narrator. Preserve the source language where possible.
10. **Vertical composition.** Prefer the speaker/subject as the focal point, then use motion/saliency fallback. Keep important content away from UI-heavy edges.

## Regional languages

The default transcription path preserves the detected source language. Users can force a language with `--language te`, `--language hi`, `--language ta`, etc. The caption renderer should use a Unicode font with Indic coverage when available; language-specific font profiles will be expanded in the next milestone.

## Future learning loop

A later analytics module should accept real channel retention data and learn per-channel priors for hook types, clip length, cut density, caption density and story structures. Those priors should override generic heuristics only when enough real observations exist.
