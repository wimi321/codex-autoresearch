# Codex Autoresearch

[![CI](https://github.com/wimi321/codex-autoresearch/actions/workflows/ci.yml/badge.svg)](https://github.com/wimi321/codex-autoresearch/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/wimi321/codex-autoresearch?style=social)](https://github.com/wimi321/codex-autoresearch)

English | [简体中文](docs/README.zh-CN.md)

Codex Autoresearch is a Codex-native implementation of the Karpathy loop: one metric, one focused change, one verification step, repeated until the repository gets better.

It takes the core ideas from [karpathy/autoresearch](https://github.com/karpathy/autoresearch) and the product framing from [uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch), then rebuilds them for OpenAI Codex as a real executable runner.

## One command setup

```bash
make setup
```

That command:

1. creates a virtualenv
2. installs the package
3. writes an auto-detected `autoresearch.toml`
4. runs `autore doctor`

Then run:

```bash
. .venv/bin/activate
autore run --iterations 5
```

During long runs, live execution logs are written under `.autoresearch/runs/iteration-XXXX/`.

## Why this project

Most "autoresearch" adaptations stop at prompt files. Codex can do more.

This project treats Codex as the autonomous worker inside a strict outer loop:

- Git is memory.
- The verify command is truth.
- Guard commands prevent regressions.
- One iteration means one reversible change.
- Results are logged to `.autoresearch/results.tsv`.

## Why it feels simple

- `autore init --preset auto` picks a sane starter config
- `autore doctor` tells you if the repo is actually runnable
- `autore run` establishes a baseline and runs bounded Codex iterations
- `autore status` prints the research log
- Automatic branch creation for isolated runs
- Keep/discard logic based on mechanical metrics
- Optional guard command support
- Per-iteration log files for long-running Codex sessions
- TSV logging for every iteration

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick start

### Fast path

```bash
autore init --preset auto
autore doctor
autore run --iterations 5
```

### Smallest demo

Want the smallest possible proof that the loop works?

See [examples/demo-repo](examples/demo-repo/README.md).

### Python repo example

```toml
[research]
goal = "Increase test coverage from 72 to 90"
metric = "coverage percent"
direction = "higher"
verify = "pytest --cov=src 2>&1 | grep TOTAL"
scope = ["src/**", "tests/**"]
guard = "pytest"
iterations = 10
```

### Node repo example

```toml
[research]
goal = "Reduce bundle size below 200 KB without breaking tests"
metric = "bundle size kb"
direction = "lower"
verify = "npm run build 2>&1 | grep 'First Load JS'"
scope = ["src/**"]
guard = "npm test"
iterations = 10
```

The runner will:

1. create a fresh `autoresearch/<timestamp>` branch
2. record the baseline metric
3. write an iteration prompt for Codex
4. run `codex exec` for one change
5. commit the experiment
6. verify the metric and optional guard
7. keep or revert the commit
8. log the outcome

## Commands

- `autore init --preset auto`: generate a starter config based on the repo
- `autore doctor`: verify `git`, `codex`, and config prerequisites
- `autore run --iterations N`: run a bounded research loop
- `autore status`: print the latest TSV log
- `autore watch --follow`: watch the newest iteration log in real time
- `make setup`: bootstrap the whole project locally

## Long Runs

When an iteration takes a while, you can inspect its files directly:

```bash
autore watch --follow
autore watch --stream stdout --follow
autore watch --stream results
```

Timeouts are configurable in `autoresearch.toml`:

```toml
[runtime]
codex_timeout_seconds = 1800
verify_timeout_seconds = 300
guard_timeout_seconds = 300
```

## Repository layout

- `src/codex_autoresearch/cli.py`: CLI entrypoint
- `src/codex_autoresearch/runner.py`: outer loop orchestration
- `src/codex_autoresearch/prompting.py`: Codex iteration prompt builder
- `src/codex_autoresearch/gittools.py`: git safety and rollback helpers
- `docs/architecture.md`: design notes and roadmap
- `examples/autoresearch.toml`: sample config
- `examples/demo-repo/`: copyable end-to-end demo

## What makes this Codex-native

Instead of pretending Codex is just another chat model, the runner is built around real Codex workflows:

- `codex exec` as the non-interactive worker
- prompt files generated per iteration
- clean repo invariants before every run
- branch-per-run isolation
- local-first execution with minimal dependencies

## Near-term roadmap

- Unbounded overnight mode with resumable sessions
- richer metric parsing and named extractors
- built-in profiles for `fix`, `debug`, `security`, and `learn`
- GitHub Actions support for scheduled research jobs
- prettier reports and experiment summaries
- benchmark examples against real repositories

## Inspiration

- [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
- [uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch)
- [openai/codex](https://github.com/openai/codex)
