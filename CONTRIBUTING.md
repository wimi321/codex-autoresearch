# Contributing

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e . pytest
pytest
```

## Project principles

- Codex is the inner worker, not the orchestrator.
- Mechanical metrics decide keep versus discard.
- One iteration should mean one reversible change.
- Logs under `.autoresearch/` are runtime state, not product output.
