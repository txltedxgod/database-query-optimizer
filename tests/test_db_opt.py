from db_opt.advisor import QueryOptimizer

def test_advisor():
    o = QueryOptimizer()
    res = o.analyze_sql("SELECT * FROM orders WHERE status = 'paid'")
    assert len(res["recommended_indexes"]) > 0
