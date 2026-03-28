# FAQ

## Why not just use a prompt file?

Because the hard part is not prompting Codex once. The hard part is making repeated changes safe, measurable, and inspectable. The runner provides git hygiene, metrics, rollback, logging, and repeatable iteration.

## What makes a good metric?

A good metric is mechanical, fast, and stable. It should print a number and finish quickly enough that multiple iterations are practical.

## Why does `autore run` insist on a clean git tree?

Because autoresearch needs clean experimental diffs. If unrelated files are already dirty, it becomes much harder to trust keep/discard decisions.

## When should I use `--resume`?

Use `autore run --resume --iterations N` when you already have a branch and `.autoresearch/results.tsv` from a prior run and want to continue from the current best metric.

## What should I watch during a long run?

Usually `autore watch --follow` is enough. If you want Codex's final summary instead of its thinking trail, use `autore watch --stream stdout --follow`.

## Can I run this nightly?

Yes. The repo now includes a ready-to-adapt GitHub Actions example in `examples/nightly.yml` and docs in `docs/nightly.md`.
