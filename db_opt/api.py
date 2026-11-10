"""FastAPI service exposing SQL ESR index recommendations and EXPLAIN plan analysis."""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from db_opt.advisor import QueryOptimizer

app = FastAPI(title="Database Query Optimizer", version="1.0.0")
opt = QueryOptimizer()


class SQLReq(BaseModel):
    """Single SQL query inspection request."""

    query: str = Field(..., min_length=3, max_length=20000)


class BatchSQLReq(BaseModel):
    """Batch SQL workload inspection request."""

    queries: list[str] = Field(..., min_length=1, max_length=50)


class ExplainReq(BaseModel):
    """PostgreSQL EXPLAIN (FORMAT JSON) payload inspection request."""

    plan: list[dict[str, Any]] | dict[str, Any]


@app.get("/health", tags=["Monitoring"])
def health_check() -> dict[str, str]:
    """Liveness check endpoint."""
    return {"status": "healthy"}


@app.post("/api/v1/analyze", tags=["Advisor"])
def analyze(req: SQLReq) -> dict[str, Any]:
    """Analyze a single SQL statement for ESR indexes and anti-patterns."""
    try:
        return opt.analyze_sql(req.query)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/analyze/batch", tags=["Advisor"])
def analyze_batch(req: BatchSQLReq) -> dict[str, Any]:
    """Analyze a batch of SQL queries and deduplicate recommended indexes."""
    results: list[dict[str, Any]] = []
    unique_indexes: list[str] = []
    for sql in req.queries:
        try:
            report = opt.analyze_sql(sql)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        results.append(report)
        for ddl in report["recommended_indexes"]:
            if ddl != "No additional indexes needed" and ddl not in unique_indexes:
                unique_indexes.append(ddl)

    return {
        "total_queries": len(results),
        "unique_recommended_indexes": unique_indexes,
        "reports": results,
    }


@app.post("/api/v1/explain", tags=["EXPLAIN"])
def analyze_explain(req: ExplainReq) -> dict[str, Any]:
    """Inspect a PostgreSQL EXPLAIN (FORMAT JSON) tree for sequential scans and disk spills."""
    try:
        return opt.analyze_explain_plan(req.plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
