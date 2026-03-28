setup:
	./scripts/bootstrap.sh

test:
	. .venv/bin/activate && pytest

help:
	. .venv/bin/activate && autore --help
