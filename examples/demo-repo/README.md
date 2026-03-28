# Demo Repo

This is the smallest reproducible Codex Autoresearch demo.

## What it does

- `score.txt` starts at `10`
- the metric is the numeric value in that file
- lower is better
- Codex is allowed to edit only `score.txt`
- one iteration should reduce the score toward `0`

## Run it

From the project root:

```bash
rm -rf /tmp/codex-autoresearch-demo
cp -R examples/demo-repo /tmp/codex-autoresearch-demo
cd /tmp/codex-autoresearch-demo

git init -b main
git config user.name demo
git config user.email demo@example.com
git add .
git commit -m "init demo"

. /absolute/path/to/codex-autoresearch/.venv/bin/activate
autore doctor
autore run --iterations 1
```

Expected result:

- baseline metric: `10`
- best metric after one iteration: `0`
- `score.txt` becomes `0`
- `.autoresearch/results.tsv` records a `keep`
