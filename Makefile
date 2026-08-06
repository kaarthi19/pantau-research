.PHONY: install dry-run run collect score render digest test vacuum clean

install:
	pip install -r requirements.txt

dry-run:            ## collect + per-source counts, no writes
	python -m argus.run --dry-run

run:                ## full pipeline (keyword mode unless a provider key is set)
	python -m argus.run

collect:
	python -m argus.run --stage collect

score:
	python -m argus.run --stage score

render:
	python -m argus.run --stage render

digest:
	python -m argus.run --stage digest --force-digest

test:
	python -m pytest tests/ -q

vacuum:             ## reclaim space after pruning — occasional, produces a big diff
	python -m argus.run --stage score --vacuum

clean:
	rm -rf **/__pycache__ .pytest_cache
