# Demo Repo

This is the fastest way to prove that Codex Autoresearch actually works end to end.

## What You Should Expect

- `score.txt` starts at `10`
- the metric is the number inside `score.txt`
- lower is better
- Codex is only allowed to touch `score.txt`
- one successful iteration should drive the value toward `0`

## Fastest Path

From the project root:

```bash
autore start --demo --run
```

That creates a fresh demo repo, runs one bounded loop, and prints a short summary.

## Manual Path

If you want to see each step yourself:

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
autore doctor --fix
autore run --iterations 1 --skip-branch
```

## Expected Result

- baseline metric: `10`
- best metric after one iteration: `0`
- `score.txt` becomes `0`
- `.autoresearch/results.tsv` records a `keep`
