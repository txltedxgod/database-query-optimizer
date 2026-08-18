from fastapi import FastAPI
from pydantic import BaseModel
from db_opt.advisor import QueryOptimizer

app = FastAPI(title="Database Query Optimizer", version="0.1.0")
opt = QueryOptimizer()

class SQLReq(BaseModel):
    query: str

@app.post("/api/v1/analyze")
def analyze(req: SQLReq):
    return opt.analyze_sql(req.query)
