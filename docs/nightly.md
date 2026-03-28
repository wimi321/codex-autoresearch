# Nightly Runs

Codex Autoresearch works best when you can give it a bounded loop and let it run unattended.

## Local nightly run

```bash
. .venv/bin/activate
autore run --iterations 10 --resume
```

## GitHub Actions pattern

See [examples/nightly.yml](../examples/nightly.yml) for a scheduled workflow template.

What you need to provide:

- a machine or runner with Codex CLI available
- authentication for Codex on that runner
- a repository-specific `autoresearch.toml`
- a branch strategy you are comfortable with

## Recommended pattern

1. Run on a dedicated branch
2. Use bounded iterations
3. Push results to a branch or open a PR
4. Review kept commits in daylight
