from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

app = FastAPI()

MODEL_NAME = "BAAI/bge-reranker-base"
model = CrossEncoder(MODEL_NAME)

class RerankReq(BaseModel):
    query: str
    documents: list[str]
    top_k: int = 5

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_NAME}

@app.post("/rerank")
def rerank(req: RerankReq):
    pairs = [[req.query, d] for d in req.documents]
    scores = model.predict(pairs).tolist()

    idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    top = idx[: min(req.top_k, len(idx))]

    return {
        "results": [
            {"doc": req.documents[i], "score": float(scores[i]), "rank": r + 1}
            for r, i in enumerate(top)
        ]
    }
