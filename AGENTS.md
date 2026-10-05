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
