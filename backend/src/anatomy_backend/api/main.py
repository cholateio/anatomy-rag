"""FastAPI 應用進入點。Phase 0：健康探針與預熱骨架（真正的 /chat 於 Phase 8 實作）。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from anatomy_backend.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動即觸發設定驗證（fail-fast）。

    缺必填變數、或 DATABASE_URL 未連 PgBouncer :6432（§0.3）會在此拋錯，
    使容器啟動失敗、healthcheck 永不轉 healthy —— 而非以「healthy」假象掩蓋錯誤設定，
    拖到日後某功能首次存取設定時才爆。
    """
    get_settings()
    yield


app = FastAPI(title="anatomy-rag-backend", version="0.0.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    """存活探針：容器健康檢查與負載平衡器使用；回 {"status": "ok"}。"""
    return {"status": "ok"}


@app.post("/warmup")
async def warmup() -> dict:
    """預熱骨架：Phase 0 為 no-op；後續會在此預載 encoder client 與連線池（§5.1）。"""
    return {"warmed": True}
