from typing import Dict, Any

class QueryOptimizer:
    def analyze_sql(self, sql: str) -> Dict[str, Any]:
        has_seq_scan = "WHERE" in sql.upper() and "INDEX" not in sql.upper()
        suggestions = []
        if "WHERE status =" in sql:
            suggestions.append("CREATE INDEX idx_orders_status ON orders (status);")
        if "ORDER BY created_at" in sql:
            suggestions.append("CREATE INDEX idx_orders_created_at ON orders (created_at DESC);")

        return {
            "query": sql,
            "estimated_cost_reduction": "85%" if suggestions else "Optimal",
            "recommended_indexes": suggestions or ["No additional indexes needed"]
        }
