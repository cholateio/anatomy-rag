"""ColPali query encoder 微服務（§5.1）。

readiness：mock 同步載入（快、測試決定性）；真實模型走 lifespan 背景執行緒
（下載/載入耗時，不擋 event loop），就緒前 /healthz、/encode_query 回 503
（healthcheck 的 curl -f 因此正確視為 not-ready）。
推理經 anyio.to_thread + lock：GPU 推理序列化、event loop 不被阻塞。
"""
import base64
import logging
import os
import threading
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from colpali_service.encoder import get_encoder

logger = logging.getLogger(__name__)

_infer_lock = threading.Lock()   # GPU 推理序列化（跨 lifespan 共用無妨）

# warmup 字串 MUST 含非 glossary 的 CJK 段，否則 glossary 全吃、Marian generate 永遠冷啟
# （Codex 終審 P2）；「心臟瓣膜的位置」不在 glossary_zh_en.tsv 中，必走 MT。
WARMUP_QUERY = "心臟瓣膜的位置"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 狀態為「每個 lifespan 一份」並掛在 app.state：舊 lifespan 的慢載入執行緒只持有
    # 它自己那份 dict 的參照，寫不進新 lifespan 的狀態（stale-loader race 結構性消除）。
    state: dict = {"encoder": None, "error": None}
    app.state.enc = state

    def load() -> None:
        try:
            enc = get_encoder()
            # §5.1 SHOULD：startup dummy encode（暖 MT+ColPali；WARMUP_QUERY 必走 Marian generate）
            enc.encode_query(WARMUP_QUERY)
            state["encoder"] = enc                # ready ⇒ 已預熱（dummy encode 成功才掛上）
            logger.info("encoder 就緒：%s", enc.model)
        except Exception as e:  # noqa: BLE001 - 載入失敗必須呈現在 /healthz，不可讓執行緒靜默死亡
            state["error"] = repr(e)
            logger.exception("encoder 載入失敗")

    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        load()                                    # mock：同步、即時 ready（測試決定性）
    else:
        threading.Thread(target=load, daemon=True).start()
    yield


app = FastAPI(title="colpali-encoder", version="0.0.0", lifespan=lifespan)


class EncodeRequest(BaseModel):
    q: str


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


@app.get("/healthz")
async def healthz(request: Request):
    """readiness：模型載入完成才 200（§5.1）；載入中/失敗回 503（curl -f 視為 unhealthy）。"""
    state = request.app.state.enc
    enc = state["encoder"]
    if enc is None:
        return JSONResponse(status_code=503,
                            content={"ready": False, "error": state["error"]})
    return {"ready": True, "model": enc.model, "mt_model": enc.mt_model}


def _encode_sync(enc, q: str) -> dict:
    with _infer_lock:                                     # GPU 推理序列化
        return enc.encode_query(q)


@app.post("/encode_query")
async def encode_query(request: Request, req: EncodeRequest) -> dict:
    enc = request.app.state.enc["encoder"]
    if enc is None:
        raise HTTPException(
            status_code=503,
            detail="encoder 尚未就緒（模型載入中或失敗，見 /healthz）",
        )
    out = await anyio.to_thread.run_sync(_encode_sync, enc, req.q)
    return {
        "tokens_bin": [_b64(t) for t in out["tokens_bin"]],
        "pooled_f32": _b64(out["pooled_f32"]),     # DL-019：512B LE float32[128]
        "translated_q": out["translated_q"],        # DL-020：BM25 用；MT 失敗為 null
        "lang": out["lang"],
        "model": out["model"],
        "mt_model": out["mt_model"],
    }


@app.post("/warmup")
async def warmup(request: Request) -> dict:
    """全鏈路預熱（§5.1 SHOULD）：固定 zh 字串同時暖 MT 與 ColPali。"""
    enc = request.app.state.enc["encoder"]
    if enc is None:
        raise HTTPException(status_code=503, detail="encoder 尚未就緒")
    await anyio.to_thread.run_sync(_encode_sync, enc, WARMUP_QUERY)
    return {"warmed": True}
