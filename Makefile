.PHONY: install dry-run run collect score render digest test clean

install:
	pip install -r requirements.txt

dry-run:            ## collect + per-source counts, no writes
	python -m radar.run --dry-run

run:                ## full pipeline (keyword mode unless ANTHROPIC_API_KEY set)
	python -m radar.run

collect:
	python -m radar.run --stage collect

score:
	python -m radar.run --stage score

render:
	python -m radar.run --stage render

digest:
	python -m radar.run --stage digest --force-digest

test:
	python -m pytest tests/ -q

clean:
	rm -rf **/__pycache__ .pytest_cache
