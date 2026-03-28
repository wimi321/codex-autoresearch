# Research Notes

## What Karpathy's project proves

[karpathy/autoresearch](https://github.com/karpathy/autoresearch) is not mainly a training repo. It is a proof that an agent can compound improvements when five constraints are true:

1. The editable surface is small.
2. The metric is scalar and mechanical.
3. Verification is fast enough to run many times.
4. Reverts are cheap.
5. The human programs the system at the strategy level, not the code-diff level.

The repo is small on purpose. `train.py` is the editable arena, `prepare.py` is the trusted harness, and `program.md` is the research org's operating system.

## What Udit's project adds

[uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch) generalizes the same loop into a product:

- more commands
- more domains
- a setup wizard
- guards
- history-aware iteration
- a stronger packaging story

Its biggest contribution is not technical novelty. It translates the Karpathy loop into an understandable user workflow that people can adopt outside ML.

## What a Codex-native version should do differently

Codex already has an execution model, a CLI, prompt files, skills, non-interactive mode, and local-first workflows. So a Codex version should not only be a prompt pack.

It should be:

- a real runner around `codex exec`
- opinionated about git hygiene and rollback
- mechanical about verify and guard commands
- explicit about runtime artifacts versus product artifacts
- easy to run locally and later inside GitHub Actions

## Product thesis for this repository

This project should become the reference implementation for Codex autonomous improvement loops:

- simple enough to trust
- strict enough to be repeatable
- extensible enough to cover `fix`, `debug`, `security`, and `learn`
- documented enough that users can adapt it to their own repos quickly

That is why the first version is a runner, not a prompt collection.
