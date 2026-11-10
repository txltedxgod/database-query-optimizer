# Database Query Optimizer

PostgreSQL SQL query advisor and `EXPLAIN (FORMAT JSON)` bottleneck detector built with Python and FastAPI.

## Capabilities

- **ESR Composite Index Synthesis (`QueryOptimizer.analyze_sql`)**: Resolves tables and aliases across `FROM`/`JOIN` clauses and constructs B-Tree index recommendations following the **Equality -> Sort -> Range** column ordering rule.
- **Specialized PostgreSQL Index Advisors**: Detects leading-wildcard `LIKE`/`ILIKE` patterns (`USING gin (... gin_trgm_ops)`), JSONB containment operators (`USING gin (...)`), and non-SARGable expressions (`LOWER(col)` -> expression indexes).
- **Query Anti-Pattern Audits**: Flags `SELECT *`, non-SARGable function predicates, deep `OFFSET` pagination, and unfiltered full sequential scans.
- **EXPLAIN JSON Inspection (`QueryOptimizer.analyze_explain_plan`)**: Recursively scans PostgreSQL query plan trees for expensive sequential scans and external disk sorts.
- **REST API**: FastAPI endpoints `GET /health`, `POST /api/v1/analyze`, `POST /api/v1/analyze/batch`, and `POST /api/v1/explain`.

## Local Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt ruff
ruff check .
pytest -v tests/
```
