"""Unit and API tests for PostgreSQL ESR index advisor and EXPLAIN analyzer."""

from fastapi.testclient import TestClient

from db_opt.advisor import QueryOptimizer
from db_opt.api import app


def test_advisor_basic_equality() -> None:
    o = QueryOptimizer()
    res = o.analyze_sql("SELECT * FROM orders WHERE status = 'paid'")
    assert res["tables"] == ["orders"]
    assert res["recommended_indexes"] == ["CREATE INDEX idx_orders_status ON orders (status);"]
    assert "avoid_select_star" in res["anti_patterns"]


def test_esr_composite_index_ordering_and_aliases() -> None:
    o = QueryOptimizer()
    sql = """
        SELECT o.id, o.total
        FROM public.orders AS o
        JOIN users u ON o.user_id = u.id
        WHERE o.created_at >= '2026-01-01' AND o.tenant_id = 42 AND o.status = 'paid'
        ORDER BY o.priority DESC
        LIMIT 25
    """
    res = o.analyze_sql(sql)
    # Equality (tenant_id, status) -> Sort (priority DESC) -> Range (created_at)
    assert res["recommended_indexes"][0] == (
        "CREATE INDEX idx_orders_tenant_id_status_priority_desc_created_at "
        "ON orders (tenant_id, status, priority DESC, created_at);"
    )
    # Joined secondary table FK gets indexed too
    assert any("ON users (id)" in idx for idx in res["recommended_indexes"])
    assert "avoid_select_star" not in res["anti_patterns"]


def test_non_sargable_and_wildcard_like_detection() -> None:
    o = QueryOptimizer()
    sql = (
        "SELECT id FROM users "
        "WHERE LOWER(email) = 'alice@example.com' AND bio ILIKE '%rust%' OFFSET 500"
    )
    res = o.analyze_sql(sql)
    assert "non_sargable_predicate" in res["anti_patterns"]
    assert "leading_wildcard_like" in res["anti_patterns"]
    assert "deep_offset_pagination" in res["anti_patterns"]
    assert any("ON users (LOWER(email))" in idx for idx in res["recommended_indexes"])
    assert any("USING gin (bio gin_trgm_ops)" in idx for idx in res["recommended_indexes"])


def test_clean_point_lookup_minimal_optimal() -> None:
    o = QueryOptimizer()
    res = o.analyze_sql("INSERT INTO audit_events (event_type) VALUES ('login')")
    assert res["estimated_cost_reduction"] == "Optimal"
    assert res["recommended_indexes"] == ["No additional indexes needed"]


def test_explain_plan_and_api_endpoints() -> None:
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "healthy"}

    batch_resp = client.post(
        "/api/v1/analyze/batch",
        json={
            "queries": [
                "SELECT id FROM orders WHERE status = 'paid' LIMIT 10",
                "SELECT count(id) FROM orders WHERE status = 'open' LIMIT 10",
            ]
        },
    )
    assert batch_resp.status_code == 200
    assert batch_resp.json()["unique_recommended_indexes"] == [
        "CREATE INDEX idx_orders_status ON orders (status);"
    ]

    explain_resp = client.post(
        "/api/v1/explain",
        json={
            "plan": [
                {
                    "Execution Time": 142.5,
                    "Plan": {
                        "Node Type": "Sort",
                        "Sort Method": "external merge Disk",
                        "Total Cost": 950.4,
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Relation Name": "orders",
                                "Actual Rows": 50000,
                                "Total Cost": 600.0,
                            }
                        ],
                    },
                }
            ]
        },
    )
    assert explain_resp.status_code == 200
    body = explain_resp.json()
    assert body["status"] == "ACTION_REQUIRED"
    assert len(body["bottlenecks"]) == 2
