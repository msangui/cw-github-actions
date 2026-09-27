.PHONY: install test lint fmt run dry-run rebuild-feed check-config cover

DATE ?= $(shell date +%F)

install:
	pip install -e ".[dev]"

test:
	python -m pytest -q

lint:
	ruff check pipeline tests

fmt:
	ruff check --fix pipeline tests && ruff format pipeline tests

## Full run: LLM + TTS + stitch + publish (to S3 if S3_BUCKET is set, else ./output/site)
run:
	python -m pipeline run --date $(DATE)

## Text-only run: no TTS, no publish. Useful for iterating on prompts.
dry-run:
	python -m pipeline run --date $(DATE) --dry-run

## Regenerate feed.xml / feed-extended.xml from the episode.json files already in storage
rebuild-feed:
	python -m pipeline rebuild-feed

check-config:
	python -m pipeline check-config

## Regenerate assets/cover.png (needs Pillow)
cover:
	python scripts/make_cover.py
