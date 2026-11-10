"""PostgreSQL SQL query heuristic advisor and EXPLAIN JSON plan analyzer."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

_SQL_KEYWORDS = frozenset(
    {
        "where",
        "join",
        "inner",
        "left",
        "right",
        "full",
        "cross",
        "outer",
        "on",
        "using",
        "group",
        "order",
        "having",
        "limit",
        "offset",
        "union",
        "except",
        "intersect",
        "set",
        "as",
        "and",
        "or",
        "not",
        "null",
        "true",
        "false",
        "select",
        "from",
        "returning",
    }
)

_NON_SARGABLE_FUNCS = frozenset(
    {"lower", "upper", "trim", "btrim", "date", "extract", "coalesce", "substring"}
)

_COMMENT_LINE_RE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LIT_RE = re.compile(r"'(?:''|[^'])*'")
_IDENT_PART = r"[a-zA-Z_][a-zA-Z0-9_$]*"
_QUALIFIED_COL = rf"(?:({_IDENT_PART})\.)?({_IDENT_PART})"

_FROM_JOIN_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+((?:{_IDENT_PART}\.)?{_IDENT_PART})(?:\s+(?:AS\s+)?({_IDENT_PART}))?",
    re.IGNORECASE,
)
_WILDCARD_LIKE_RE = re.compile(
    rf"{_QUALIFIED_COL}\s+(I?LIKE)\s+('(?:''|[^'])*')",
    re.IGNORECASE,
)
_FUNC_PRED_RE = re.compile(
    rf"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*{_QUALIFIED_COL}\s*\)\s*(?:=|IN\b|>|<)",
    re.IGNORECASE,
)
_JSONB_RE = re.compile(rf"{_QUALIFIED_COL}\s*(?:@>|\?\||\?&|\?)", re.IGNORECASE)
_EQ_RE = re.compile(rf"{_QUALIFIED_COL}\s*(?:=|IN\s*\(|IS\s+NULL\b)", re.IGNORECASE)
_RANGE_RE = re.compile(rf"{_QUALIFIED_COL}\s*(?:>=|<=|<>|!=|>|<|BETWEEN\b)", re.IGNORECASE)
_JOIN_EQ_RE = re.compile(
    rf"\bON\s+({_IDENT_PART})\.({_IDENT_PART})\s*=\s*({_IDENT_PART})\.({_IDENT_PART})",
    re.IGNORECASE,
)
_ORDER_CLAUSE_RE = re.compile(
    r"\bORDER\s+BY\s+(.+?)(?=\bLIMIT\b|\bOFFSET\b|\bFOR\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
_GROUP_CLAUSE_RE = re.compile(
    r"\bGROUP\s+BY\s+(.+?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
_WHERE_CLAUSE_RE = re.compile(
    r"\bWHERE\s+(.+?)(?=\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|\bUNION\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
_OFFSET_RE = re.compile(r"\bOFFSET\s+(\d+)\b", re.IGNORECASE)


def _strip_comments(sql: str) -> str:
    cleaned = _COMMENT_BLOCK_RE.sub(" ", sql)
    return _COMMENT_LINE_RE.sub(" ", cleaned)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _normalize_ident(name: str) -> str:
    return name.strip().strip('"').lower()


def _sanitize_index_part(expr: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", expr.lower()).strip("_")


class QueryOptimizer:
    """Heuristic PostgreSQL index advisor and EXPLAIN JSON analyzer."""

    @staticmethod
    def _extract_tables(masked_sql: str) -> tuple[list[str], dict[str, str], str]:
        tables: list[str] = []
        alias_map: dict[str, str] = {}
        for raw_table, raw_alias in _FROM_JOIN_RE.findall(masked_sql):
            table = _normalize_ident(raw_table.split(".")[-1])
            _append_unique(tables, table)
            alias_map[table] = table
            if raw_alias:
                alias_cand = _normalize_ident(raw_alias)
                if alias_cand not in _SQL_KEYWORDS:
                    alias_map[alias_cand] = table

        primary_table = tables[0] if tables else "records"
        return tables, alias_map, primary_table

    @staticmethod
    def _resolve_table(alias: str | None, alias_map: dict[str, str], default_table: str) -> str:
        if not alias:
            return default_table
        return alias_map.get(_normalize_ident(alias), _normalize_ident(alias))

    def analyze_sql(self, sql: str) -> dict[str, Any]:
        """Analyze a SQL query and return ESR composite indexes and anti-pattern diagnostics."""
        if not sql or not sql.strip():
            raise ValueError("SQL query must not be empty.")

        no_comments = _strip_comments(sql)
        normalized_ws = re.sub(r"\s+", " ", no_comments).strip()
        masked_sql = _STRING_LIT_RE.sub("'?'", normalized_ws)

        tables, alias_map, primary_table = self._extract_tables(masked_sql)

        eq_cols: dict[str, list[str]] = defaultdict(list)
        sort_cols: dict[str, list[tuple[str, str]]] = defaultdict(list)
        range_cols: dict[str, list[str]] = defaultdict(list)
        expr_cols: dict[str, list[str]] = defaultdict(list)
        gin_trgm_cols: dict[str, list[str]] = defaultdict(list)
        gin_jsonb_cols: dict[str, list[str]] = defaultdict(list)

        warnings: list[dict[str, str]] = []
        anti_patterns: list[str] = []

        def add_warning(code: str, severity: str, message: str, remediation: str) -> None:
            if code not in anti_patterns:
                anti_patterns.append(code)
            warnings.append(
                {
                    "code": code,
                    "severity": severity,
                    "message": message,
                    "remediation": remediation,
                }
            )

        if re.search(r"\bSELECT\s+\*", masked_sql, re.IGNORECASE):
            add_warning(
                "avoid_select_star",
                "MEDIUM",
                "SELECT * fetches unnecessary columns and prevents Index-Only Scans.",
                "Project only the explicit columns needed by the caller.",
            )

        # Inspect raw LIKE literals before masking so prefix vs wildcard is preserved
        for alias, raw_col, op, literal in _WILDCARD_LIKE_RE.findall(normalized_ws):
            col = _normalize_ident(raw_col)
            if col in _SQL_KEYWORDS:
                continue
            table = self._resolve_table(alias, alias_map, primary_table)
            inner = literal[1:-1]
            if inner.startswith("%"):
                _append_unique(gin_trgm_cols[table], col)
                add_warning(
                    "leading_wildcard_like",
                    "HIGH",
                    f"Leading-wildcard {op.upper()} on {table}.{col} "
                    "cannot use a standard B-Tree index.",
                    f"Enable pg_trgm and create a GIN trigram index on {table}({col}).",
                )
            elif inner.endswith("%"):
                _append_unique(range_cols[table], col)

        # Functional predicates on WHERE/JOIN clauses
        where_match = _WHERE_CLAUSE_RE.search(masked_sql)
        where_body = where_match.group(1) if where_match else ""

        masked_without_funcs = where_body
        for func_name, alias, raw_col in _FUNC_PRED_RE.findall(where_body):
            fn = func_name.lower()
            col = _normalize_ident(raw_col)
            if fn in _NON_SARGABLE_FUNCS and col not in _SQL_KEYWORDS:
                table = self._resolve_table(alias, alias_map, primary_table)
                expr = f"{fn.upper()}({col})"
                _append_unique(expr_cols[table], expr)
                add_warning(
                    "non_sargable_predicate",
                    "HIGH",
                    f"Wrapping {table}.{col} in {fn.upper()}() disables plain B-Tree lookup.",
                    f"Add an expression index on {table} ({expr}) or rewrite as a SARGable range.",
                )
        # Strip function calls from predicate copy before plain equality/range extraction
        masked_without_funcs = _FUNC_PRED_RE.sub(" ", where_body)

        for alias, raw_col in _JSONB_RE.findall(masked_without_funcs):
            col = _normalize_ident(raw_col)
            if col not in _SQL_KEYWORDS:
                tbl = self._resolve_table(alias, alias_map, primary_table)
                _append_unique(gin_jsonb_cols[tbl], col)

        for alias, raw_col in _EQ_RE.findall(masked_without_funcs):
            col = _normalize_ident(raw_col)
            if col not in _SQL_KEYWORDS:
                tbl = self._resolve_table(alias, alias_map, primary_table)
                _append_unique(eq_cols[tbl], col)

        for alias, raw_col in _RANGE_RE.findall(masked_without_funcs):
            col = _normalize_ident(raw_col)
            if col not in _SQL_KEYWORDS:
                tbl = self._resolve_table(alias, alias_map, primary_table)
                _append_unique(range_cols[tbl], col)

        for left_alias, left_col, right_alias, right_col in _JOIN_EQ_RE.findall(masked_sql):
            lt = self._resolve_table(left_alias, alias_map, primary_table)
            rt = self._resolve_table(right_alias, alias_map, primary_table)
            lc = _normalize_ident(left_col)
            rc = _normalize_ident(right_col)
            if lt != primary_table:
                _append_unique(eq_cols[lt], lc)
            elif rt != primary_table:
                _append_unique(eq_cols[rt], rc)

        # GROUP BY and ORDER BY contribute to Sort slot in ESR order
        for clause_re, default_dir in ((_GROUP_CLAUSE_RE, "ASC"), (_ORDER_CLAUSE_RE, "ASC")):
            clause_match = clause_re.search(masked_sql)
            if not clause_match:
                continue
            for item in clause_match.group(1).split(","):
                item_clean = item.strip()
                dir_match = re.search(r"\b(ASC|DESC)\b", item_clean, re.IGNORECASE)
                direction = dir_match.group(1).upper() if dir_match else default_dir
                col_part = re.sub(
                    r"\b(ASC|DESC|NULLS\s+(?:FIRST|LAST))\b",
                    "",
                    item_clean,
                    flags=re.IGNORECASE,
                ).strip()
                col_match = re.match(rf"^{_QUALIFIED_COL}$", col_part)
                if not col_match:
                    continue
                alias, raw_col = col_match.groups()
                col = _normalize_ident(raw_col)
                if col in _SQL_KEYWORDS:
                    continue
                table = self._resolve_table(alias, alias_map, primary_table)
                existing = [c for c, _ in sort_cols[table]]
                if col not in existing:
                    sort_cols[table].append((col, direction))

        offset_match = _OFFSET_RE.search(masked_sql)
        if offset_match and int(offset_match.group(1)) >= 100:
            add_warning(
                "deep_offset_pagination",
                "MEDIUM",
                f"Large OFFSET ({offset_match.group(1)}) scans and discards preceding rows.",
                "Switch to keyset/seek pagination using an indexed (sort_col, id) tuple.",
            )

        if (
            re.match(r"^\s*SELECT\b", masked_sql, re.IGNORECASE)
            and not where_body
            and not re.search(r"\bLIMIT\b", masked_sql, re.IGNORECASE)
            and tables
        ):
            add_warning(
                "unbounded_full_scan",
                "HIGH",
                f"Unfiltered SELECT on {primary_table} without WHERE or LIMIT "
                "forces a full sequential scan.",
                "Add a selective WHERE predicate or bound results with LIMIT.",
            )

        # Assemble PostgreSQL index DDLs using ESR (Equality -> Sort -> Range)
        recommendations: list[str] = []
        candidate_tables = list(
            dict.fromkeys(tables + list(eq_cols) + list(sort_cols) + list(range_cols))
        )

        for table in candidate_tables:
            ordered_cols: list[str] = []
            seen_base: set[str] = set()

            for col in eq_cols.get(table, []):
                if col not in seen_base:
                    seen_base.add(col)
                    ordered_cols.append(col)

            for col, direction in sort_cols.get(table, []):
                if col not in seen_base:
                    seen_base.add(col)
                    ordered_cols.append(f"{col} DESC" if direction == "DESC" else col)

            for col in range_cols.get(table, []):
                if col not in seen_base:
                    seen_base.add(col)
                    ordered_cols.append(col)

            if ordered_cols:
                idx_suffix = "_".join(_sanitize_index_part(c) for c in ordered_cols[:4])
                cols_sql = ", ".join(ordered_cols)
                ddl = f"CREATE INDEX idx_{table}_{idx_suffix} ON {table} ({cols_sql});"
                _append_unique(recommendations, ddl)

            for expr in expr_cols.get(table, []):
                idx_suffix = _sanitize_index_part(expr)
                ddl = f"CREATE INDEX idx_{table}_{idx_suffix} ON {table} ({expr});"
                _append_unique(recommendations, ddl)

            for col in gin_trgm_cols.get(table, []):
                ddl = (
                    f"CREATE INDEX idx_{table}_{col}_trgm ON {table} "
                    f"USING gin ({col} gin_trgm_ops);"
                )
                _append_unique(recommendations, ddl)

            for col in gin_jsonb_cols.get(table, []):
                _append_unique(
                    recommendations,
                    f"CREATE INDEX idx_{table}_{col}_gin ON {table} USING gin ({col});",
                )

        if any(w["severity"] == "HIGH" for w in warnings) or len(recommendations) >= 2:
            cost_reduction = "High (~70-90%)"
            complexity = "HIGH"
        elif recommendations or warnings:
            cost_reduction = "Moderate (~35-65%)"
            complexity = "MEDIUM"
        else:
            cost_reduction = "Optimal"
            complexity = "LOW"

        return {
            "query": sql,
            "tables": tables,
            "estimated_cost_reduction": cost_reduction,
            "complexity_tier": complexity,
            "recommended_indexes": recommendations or ["No additional indexes needed"],
            "warnings": warnings,
            "anti_patterns": anti_patterns,
        }

    def analyze_explain_plan(
        self, explain_payload: list[dict[str, Any]] | dict[str, Any]
    ) -> dict[str, Any]:
        """Inspect PostgreSQL EXPLAIN (FORMAT JSON) output for scans, sorts, and disk spills."""
        is_non_empty_list = isinstance(explain_payload, list) and bool(explain_payload)
        root = explain_payload[0] if is_non_empty_list else explain_payload
        if not isinstance(root, dict):
            raise ValueError("EXPLAIN payload must be a JSON object or non-empty list.")

        plan = root.get("Plan", root)
        findings: list[dict[str, Any]] = []

        def walk(node: dict[str, Any]) -> None:
            node_type = str(node.get("Node Type", "Unknown"))
            relation = node.get("Relation Name")
            actual_rows = int(node.get("Actual Rows") or node.get("Plan Rows") or 0)
            total_cost = float(node.get("Total Cost") or 0.0)
            sort_method = str(node.get("Sort Method") or "")

            if node_type == "Seq Scan" and (actual_rows >= 1000 or total_cost >= 250.0):
                findings.append(
                    {
                        "node_type": node_type,
                        "relation": relation,
                        "severity": "HIGH",
                        "issue": (
                            f"High-cost Sequential Scan on {relation or 'table'} "
                            f"({actual_rows} rows, cost {total_cost:.1f})."
                        ),
                    }
                )
            if "Sort" in node_type and "Disk" in sort_method:
                findings.append(
                    {
                        "node_type": node_type,
                        "relation": relation,
                        "severity": "HIGH",
                        "issue": (
                            f"External disk sort ({sort_method}); "
                            "increase work_mem or add an ESR index."
                        ),
                    }
                )

            for child in node.get("Plans", []) or []:
                if isinstance(child, dict):
                    walk(child)

        walk(plan)
        return {
            "root_node_type": plan.get("Node Type", "Unknown"),
            "total_cost": float(plan.get("Total Cost") or 0.0),
            "execution_time_ms": root.get("Execution Time"),
            "bottlenecks": findings,
            "status": "ACTION_REQUIRED" if findings else "HEALTHY",
        }
