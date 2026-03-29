# Nightly Runs

If you want Codex Autoresearch to keep working while you sleep, the shortest path is:

```bash
autore nightly --force
```

That writes `.github/workflows/autoresearch-nightly.yml` for you.

## Fast Path

### Generate the workflow

```bash
autore nightly --force
```

### Or let onboarding do it too

```bash
autore onboard --write-nightly
```

## What the generated workflow does

- runs every day at `01:00 UTC`
- supports manual `workflow_dispatch`
- installs the project in a fresh virtualenv
- runs `autore doctor --fix`
- optionally runs your guard command before the loop
- resumes a bounded autoresearch loop
- uploads results and logs as GitHub Actions artifacts

## Typical repo flow

1. Run `autore onboard --write-nightly`
2. Commit `autoresearch.toml` and `.github/workflows/autoresearch-nightly.yml`
3. Make sure your GitHub runner can authenticate `codex`
4. Trigger the workflow once manually
5. Review `.autoresearch/results.tsv` artifacts after the run

## Local equivalent

If you want to simulate the same idea locally:

```bash
. .venv/bin/activate
autore doctor --fix
autore run --resume --iterations 5 --skip-branch
```

## Example workflow

See [examples/nightly.yml](../examples/nightly.yml).

## Before you turn it on

Check these four things:

- `codex` is available on the runner
- the runner can authenticate with Codex
- your repo already has a valid `autoresearch.toml`
- your `verify` and `guard` commands are deterministic enough for unattended runs
