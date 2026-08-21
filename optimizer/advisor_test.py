"""
Database Query Optimizer - Index Advisor Tests
"""

def suggest_index_for_query(query: str) -> list[str]:
    suggestions = []
    lower = query.lower()
    if "where" in lower and "created_at" in lower:
        suggestions.append("CREATE INDEX idx_created_at ON table_name (created_at DESC);")
    if "where" in lower and "user_id" in lower:
        suggestions.append("CREATE INDEX idx_user_id ON table_name (user_id);")
    return suggestions

def test_index_suggestion():
    query = "SELECT * FROM orders WHERE user_id = 42 AND created_at > '2026-01-01';"
    suggestions = suggest_index_for_query(query)
    assert len(suggestions) == 2
    assert any("idx_user_id" in s for s in suggestions)
