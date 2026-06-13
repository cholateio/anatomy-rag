"""引擎中立查詢表示（D-K）。

self-built 引擎用 `tokens_bin`（binary）+ `pooled_f32`（Stage A halfvec）；
VectorChord（Phase 12）用 `float_multivector`（v1 encoder 未回傳，故為 None）。
capability flags 讓引擎宣告所需表示、orchestrator 早期失敗而非跑到一半。
"""
import base64
import struct
from dataclasses import dataclass, field

_TOKEN_BYTES = 16        # bit(128) = 16 bytes
_POOLED_BYTES = 512      # float32[128] LE


@dataclass(frozen=True)
class QueryRepr:
    pooled_f32: tuple[float, ...]                 # 128 維，給 Stage A
    tokens_bin: tuple[bytes, ...]                 # N × 16-byte，給 self-built Stage B
    translated_q: str | None                      # 給 BM25（null 退原文）
    lang: str
    float_multivector: tuple[tuple[float, ...], ...] | None = field(default=None)  # Phase 12

    @property
    def has_binary_tokens(self) -> bool:
        return len(self.tokens_bin) > 0

    @property
    def has_float_multivector(self) -> bool:
        return self.float_multivector is not None

    @classmethod
    def from_encode_query_response(cls, payload: dict) -> "QueryRepr":
        tokens = tuple(base64.b64decode(t) for t in payload["tokens_bin"])
        for t in tokens:
            if len(t) != _TOKEN_BYTES:
                raise ValueError(f"每個 token 必須 {_TOKEN_BYTES} bytes，收到 {len(t)}")
        pooled_raw = base64.b64decode(payload["pooled_f32"])
        if len(pooled_raw) != _POOLED_BYTES:
            raise ValueError(f"pooled_f32 必須 {_POOLED_BYTES} bytes，收到 {len(pooled_raw)}")
        pooled = struct.unpack("<128f", pooled_raw)
        return cls(
            pooled_f32=pooled,
            tokens_bin=tokens,
            translated_q=payload.get("translated_q"),
            lang=payload.get("lang", ""),
        )
