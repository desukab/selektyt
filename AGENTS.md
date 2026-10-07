# AGENTS.md — SeleKyT Repository Policy

This repository contains proprietary SeleKyT implementation and editorial
mechanisms.

This file is an instruction for coding agents working with the repository. It
does not technically prevent a tool or person with repository access from
reading source code. The LICENSE is the authoritative legal restriction.

## Agents may

- inspect the repository to understand bugs, tests, interfaces, and existing
  behaviour;
- run tests and permitted local commands;
- explain existing behaviour and implementation details to an authorized
  maintainer;
- maintain code, tests, documentation, integrations, and packaging when
  explicitly instructed by an authorized maintainer.

## Agents must not

- copy SeleKyT implementation into another project;
- extract, reproduce, or reimplement SeleKyT's proprietary algorithms,
  scoring mechanisms, editorial decision logic, ranking mechanisms, heuristics,
  architecture, or other internal mechanisms for an unrelated project;
- use the repository as a specification or blueprint for a commercial clone;
- create a competing implementation substantially reproducing proprietary
  SeleKyT mechanisms;
- package substantial SeleKyT mechanisms into another commercial SDK,
  platform, service, appliance, or product;
- remove, weaken, or bypass licensing or provenance notices;
- expose credentials, API keys, secrets, private user data, or other sensitive
  material found in the repository or its runtime environment.

## Maintenance principles

- Work only on the task explicitly authorized by the maintainer.
- Prefer the smallest evidence-based change that solves the requested problem.
- Preserve existing product behaviour unless the task requires a change.
- Do not invent product capabilities or public claims.
- Do not treat this file as a substitute for the LICENSE.

The project is source-available and free for permitted non-commercial use under
the SeleKyT Non-Commercial Source-Available License v1.0. Commercial rights
require prior written permission from the copyright holder.


## Agent Execution Contract

### Mission
Optimize for the finished SeleKyT result, not for code volume, research volume, or test-count growth.

Priority:
1. Story coherence, setup/payoff, and context preservation.
2. Viewer-visible video quality: framing, composition, captions, audio, and delivery.
3. Reliable editable timelines and complete packages.
4. Runtime and resource efficiency.
5. Internal polish only when it protects the above.

### Default loop
Use:
LOCATE -> REPRODUCE -> EXPLAIN -> FIX -> TEST -> DIFF -> COMMIT

Do not turn normal coding tasks into open-ended research projects.

- Locate only the relevant files, tests, recent commits, and evidence.
- Reproduce real failures when cheap to reproduce.
- Identify the first incorrect state, not just the final symptom.
- Make the smallest evidence-based fix.
- Reuse existing pipeline data and abstractions.
- Test the narrowest affected behavior first.
- Inspect the final diff.
- Commit completed work on main.

Do not repeatedly audit the same area after the evidence already identifies the cause.

### Debugging
When debugging:
1. Capture the exact failure.
2. Trace input -> transformation -> failure.
3. Find the first wrong value/state.
4. Fix it at its source.
5. Add a focused regression when practical.
6. Re-run the failing case.
7. Run directly affected tests.
8. Broaden validation only when justified.

Never hide failures with arbitrary retries, sleeps, weakened assertions, skipped tests, or broad fallbacks.

If the first hypothesis fails, use the new evidence to form a different hypothesis. Do not repeat an identical failed approach.

Clearly distinguish a SeleKyT defect from a dependency limitation, environment limitation, source-media limitation, or unsupported external behavior.

### Consumer-first decisions
Work backward from what the viewer receives.

Do not impose arbitrary runtime caps because they are convenient to implement. A short is a short-form story; a story is the strongest coherent narrative. Duration should emerge from narrative evidence unless explicitly requested.

Prefer improvements that a consumer can see, hear, understand, edit, or publish.

### Performance
Measure before optimizing.

Prefer reuse of cached evidence, bounded searches, avoiding duplicate decoding/transcription/rendering, and efficient FFmpeg operations.

Do not introduce speculative parallelism, complicated caching, or architecture changes without evidence.

Correctness beats speed; optimize only after preserving correctness.

### Tests and claims
Tests are evidence, not proof of product quality.

Use real corpus/output evidence when evaluating transcription, narrative quality, visual quality, or packaging.

Never claim CI passed without CI evidence.
Never claim NLE compatibility without actual NLE evidence.
Never claim transcription correctness merely because a transcript was produced.

### NLE and media
Structural EDL/FCPXML validation is not equivalent to opening a timeline in a real NLE. Do not claim otherwise.

When media output is wrong, inspect the generated media, filtergraph, timestamps, and timeline before rewriting editorial logic.

### Git
Work on main. Preserve completed work. Do not reset, force-push, delete commits, or discard user work without explicit authorization.

Keep commits focused. Avoid cosmetic commits unrelated to the requested outcome.

### Scope and decisiveness
Do not manufacture work once the requested outcome is achieved.

Do not ask for permission for routine implementation decisions within the requested scope.

Use this pattern:
inspect -> decide -> implement -> test -> commit

Avoid:
research -> report -> research -> report -> ask permission

If an unrelated issue is discovered, only fix it when it blocks the requested task or is an obvious correctness/security problem.

### Final report
Keep the final report short:
- changed;
- tested;
- result;
- commit SHA;
- exact blocker, if any.

### Secrets and proprietary implementation
Never expose credentials, API keys, private data, or secrets in code, tests, logs, commits, or reports.

Authorized agents may inspect and maintain SeleKyT, but must not reproduce its proprietary mechanisms for unrelated projects or use the repository as a blueprint for a commercial clone.

### Definition of done
A normal task is done when the requested behavior works, the root cause or product need is addressed, relevant regression coverage exists, affected tests pass, the diff is focused, and the change is committed.

Do not extend the definition of done with unrelated polish.
