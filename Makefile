.PHONY: install test test-int live export sheets report bench pdf serve lint
install:  ; pip install -r requirements-dev.txt
test:     ; pytest -q
test-int: ; TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql+asyncpg://aip:aip@localhost:5432/aip} TEST_REDIS_URL=$${TEST_REDIS_URL:-redis://localhost:6379/1} pytest -q
live:     ; python -m src.main crawl --vertical all && python -m src.main export && python -m src.main report
export:   ; python -m src.main export
sheets:   ; python -m src.main export --sheets --share-public
report:   ; python -m src.main report
bench:    ; python scripts/benchmark.py
pdf:      ; python scripts/build_architecture_pdf.py
serve:    ; python -m src.main serve
lint:     ; ruff check src scripts tests
