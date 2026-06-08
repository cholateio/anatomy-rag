"""ColPali query encoder 微服務。Phase 0：決定性 mock；Phase 3：真實 ColPali + readiness。"""
import base64

from fastapi import FastAPI
from pydantic import BaseModel

from colpali_service.encoder import get_encoder

app = FastAPI(title="colpali-encoder", version="0.0.0")
_encoder = get_encoder()


class EncodeRequest(BaseModel):
    q: str


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


@app.get("/healthz")
async def healthz() -> dict:
    """readiness：模型載入完成才 ready（§5.1）。mock 即 ready。"""
    return {"ready": getattr(_encoder, "ready", False), "model": getattr(_encoder, "model", "")}


@app.post("/encode_query")
async def encode_query(req: EncodeRequest) -> dict:
    out = _encoder.encode_query(req.q)
    return {
        "tokens_bin": [_b64(t) for t in out["tokens_bin"]],
        "pooled_bin": _b64(out["pooled_bin"]),
        "model": out["model"],
    }


@app.post("/warmup")
async def warmup() -> dict:
    _encoder.encode_query("warmup")  # 預熱（Phase 3 真實模型 dummy encode）
    return {"warmed": True}
