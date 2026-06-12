"""真實 ColPali runtime（Phase 3）——import 本模組即拉 torch/transformers。

僅供 GPU 路徑（gpu extra）；torch-free 的介面/mock 在 colpali_runtime.py（D-L）。
transformers pin >=5,<6：v5 用 `dtype=`（torch_dtype 已改名）；lm_head 為 tied weights，
載入時列 missing 屬預期（colpali-engine v0.3.14 同樣忽略），其餘缺漏一律 fail-fast——
這就是 roadmap「載入無 uninitialized weights 警告」的程式化守門。
"""
import logging

import torch
from transformers import ColPaliForRetrieval, ColPaliProcessor

from anatomy_shared.colpali_runtime import EncodedVectors, check_loading_info

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "vidore/colpali-v1.3-hf"   # DECIDED（§2.3）


class RealColPaliRuntime:
    """ColPaliForRetrieval + ColPaliProcessor（bf16、SDPA）；輸出 EncodedVectors（fp32）。

    valid_mask = processor attention_mask（排除 batch padding；前綴/augmentation tokens
    是否納入跟隨 processor 原生行為，不二次裁切——見 ARCHITECTURE §4.2 註記）。
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, device: str = "cuda",
                 dtype: torch.dtype = torch.bfloat16):
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(
                "COLPALI_DEVICE=cuda 但 CUDA 不可用——§5.1 MUST NOT 靜默 CPU fallback；"
                "請檢查 nvidia-container-toolkit / make gpu-smoke"
            )
        self.model_id = model_id
        model, loading_info = ColPaliForRetrieval.from_pretrained(
            model_id, dtype=dtype, attn_implementation="sdpa", output_loading_info=True,
        )
        check_loading_info(loading_info)   # 共用守門（torch-free、已單元測試）
        self._model = model.to(device).eval()
        self._processor = ColPaliProcessor.from_pretrained(model_id)
        self._device = device
        logger.info("ColPali 載入完成：%s（device=%s, dtype=%s）", model_id, device, dtype)

    @torch.no_grad()
    def encode_query(self, q: str) -> EncodedVectors:
        batch = self._processor(text=[q], return_tensors="pt").to(self._device)
        out = self._model(**batch)
        emb = out.embeddings[0].to(torch.float32).cpu().numpy()
        mask = batch["attention_mask"][0].bool().cpu().numpy()
        return EncodedVectors(embeddings=emb, valid_mask=mask)

    @torch.no_grad()
    def encode_page(self, image) -> EncodedVectors:
        return self.encode_pages([image])[0]

    @torch.no_grad()
    def encode_pages(self, images, batch_size: int = 4) -> list[EncodedVectors]:
        """批次頁面編碼（§2.3 SHOULD；Phase 4 ingest 主要入口）。"""
        results: list[EncodedVectors] = []
        images = list(images)
        for i in range(0, len(images), batch_size):
            batch = self._processor(images=images[i: i + batch_size], return_tensors="pt")
            batch = batch.to(self._device)
            out = self._model(**batch)
            embs = out.embeddings.to(torch.float32).cpu().numpy()
            masks = batch["attention_mask"].bool().cpu().numpy()
            for e, m in zip(embs, masks, strict=True):
                # 不裁切：padding 由 valid_mask 排除（與 encode_query 同形）
                results.append(EncodedVectors(embeddings=e, valid_mask=m))
        return results
