"""FastAPI 應用進入點。Phase 0：健康探針與預熱骨架（真正的 /chat 於 Phase 8 實作）。"""

from fastapi import FastAPI

app = FastAPI(title="anatomy-rag-backend", version="0.0.0")


@app.get("/healthz")
async def healthz() -> dict:
    """存活探針：容器健康檢查與負載平衡器使用；回 {"status": "ok"}。"""
    return {"status": "ok"}


@app.post("/warmup")
async def warmup() -> dict:
    """預熱骨架：Phase 0 為 no-op；後續會在此預載 encoder client 與連線池（§5.1）。"""
    return {"warmed": True}
