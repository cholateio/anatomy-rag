"""Phase 3 encoder smoke gate（D-P 種子；非 DL-013 上線 gate）——手動 GPU 腳本，非 CI。

流程：16 個英文偽頁面（PIL 渲染；含 4 近鄰干擾頁）→ 真實 ColPali encode_pages +
shared.binarize → 24 題 zh/en query 走 RealColPaliEncoder 完整管線（含 MT）→
雙軌排序皆 gate：(1) maxsim_hamming（Stage B 軌）(2) pooled cosine（Stage A 軌，DL-019）；
任一軌低於門檻 exit 1。並印每題 zh 的 translated_q（人工抽查 MT）與 encode/MT 延遲。

執行：make encoder-gate（GPU 容器內；需先 make encoder-models）
"""
import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]   # /app（容器）或 repo 根
PAGES = REPO / "eval/data/encoder_gate_pages.jsonl"
QUERIES = REPO / "eval/data/encoder_gate_queries.jsonl"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# —— fixture 契約（計畫 Task 8.1/8.2 的單一執行來源；偏離即 fail-closed，防 author-tuning）——
EXPECTED_PAGE_IDS = frozenset({
    "gate:biceps", "gate:triceps", "gate:median-nerve", "gate:ulnar-nerve",
    "gate:femoral-head", "gate:hip-capsule", "gate:brachial-plexus", "gate:mediastinum-sup",
    "gate:mediastinum-inf", "gate:heart-valves", "gate:liver", "gate:nephron",
    "gate:bronchi", "gate:skull-foramina", "gate:knee-ligaments", "gate:inguinal-canal",
})
REQUIRED_KEYWORDS = {
    "gate:biceps": ["supraglenoid tubercle", "coracoid process", "radial tuberosity",
                    "musculocutaneous nerve"],
    "gate:triceps": ["infraglenoid tubercle", "olecranon", "radial nerve"],
    "gate:median-nerve": ["flexor digitorum superficialis", "pronator teres",
                          "anterior interosseous", "carpal tunnel"],
    "gate:ulnar-nerve": ["medial epicondyle", "flexor carpi ulnaris", "Guyon"],
    "gate:femoral-head": ["medial circumflex femoral artery", "retinacular", "ligamentum teres"],
    "gate:hip-capsule": ["iliofemoral ligament", "acetabular labrum"],
    "gate:brachial-plexus": ["roots", "trunks", "divisions", "cords", "terminal branches"],
    "gate:mediastinum-sup": ["aortic arch", "brachiocephalic veins", "trachea", "thymus"],
    "gate:mediastinum-inf": ["pericardium", "esophagus", "descending thoracic aorta"],
    "gate:heart-valves": ["mitral valve", "tricuspid valve", "aortic valve", "pulmonary valve"],
    "gate:liver": ["right lobe", "left lobe", "caudate lobe", "quadrate lobe",
                   "falciform ligament"],
    "gate:nephron": ["glomerulus", "Bowman capsule", "loop of Henle", "filtration"],
    "gate:bronchi": ["main bronchus", "lobar bronchi", "segmental bronchi", "carina"],
    "gate:skull-foramina": ["foramen magnum", "jugular foramen", "foramen ovale", "optic canal"],
    "gate:knee-ligaments": ["anterior cruciate ligament", "posterior cruciate ligament",
                            "medial collateral ligament", "menisci"],
    "gate:inguinal-canal": ["deep inguinal ring", "superficial inguinal ring", "spermatic cord",
                            "external oblique aponeurosis"],
}
EXPECTED_QUERY_PAGES = {
    "gate-zh-001": "gate:biceps", "gate-zh-002": "gate:median-nerve",
    "gate-zh-003": "gate:femoral-head", "gate-zh-004": "gate:brachial-plexus",
    "gate-zh-005": "gate:mediastinum-sup", "gate-zh-006": "gate:knee-ligaments",
    "gate-zh-007": "gate:heart-valves", "gate-zh-008": "gate:liver",
    "gate-zh-009": "gate:nephron", "gate-zh-010": "gate:skull-foramina",
    "gate-zh-011": "gate:biceps", "gate-zh-012": "gate:inguinal-canal",
    "gate-en-001": "gate:biceps", "gate-en-002": "gate:median-nerve",
    "gate-en-003": "gate:femoral-head", "gate-en-004": "gate:brachial-plexus",
    "gate-en-005": "gate:mediastinum-sup", "gate-en-006": "gate:heart-valves",
    "gate-en-007": "gate:liver", "gate-en-008": "gate:nephron",
    "gate-en-009": "gate:bronchi", "gate-en-010": "gate:skull-foramina",
    "gate-en-011": "gate:knee-ligaments", "gate-en-012": "gate:inguinal-canal",
}


def check_fixtures(pages: list[dict], golden, detect_lang) -> None:
    """fixture 契約執法，fail-closed（防 author-tuning）。"""
    by_id = {p["page_id"]: p for p in pages}
    if len(pages) != 16 or set(by_id) != EXPECTED_PAGE_IDS:
        raise SystemExit(
            f"fixture 錯誤：page_id 集合與計畫不符 {sorted(set(by_id) ^ EXPECTED_PAGE_IDS)}")
    for pid, kws in REQUIRED_KEYWORDS.items():
        miss = [k for k in kws if k.lower() not in by_id[pid]["text"].lower()]
        if miss:
            raise SystemExit(f"fixture 錯誤：{pid} 缺必含關鍵詞 {miss}")
    got = {qa.id: tuple(qa.expected_pages) for qa in golden}
    want = {k: (v,) for k, v in EXPECTED_QUERY_PAGES.items()}
    if got != want:
        bad = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
        raise SystemExit(f"fixture 錯誤：查詢 id/expected_pages 與計畫不符 {bad}")
    zh = [qa for qa in golden if detect_lang(qa.query) == "zh"]
    if len(zh) != 12:
        raise SystemExit(f"fixture 錯誤：zh 題數 {len(zh)} != 12")


def render_page(title: str, text: str):
    """白底黑字偽頁面（896×1152）；ColPali processor 會自行 resize。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (896, 1152), "white")
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(FONT, 40)
    body_font = ImageFont.truetype(FONT, 28)
    draw.text((48, 48), title, fill="black", font=title_font)
    y = 140
    for line in textwrap.wrap(text, width=52):
        draw.text((48, y), line, fill="black", font=body_font)
        y += 40
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--min-maxsim-en", type=float, default=0.9)
    ap.add_argument("--min-maxsim-zh", type=float, default=0.75)
    ap.add_argument("--min-pooled-en", type=float, default=0.75)
    ap.add_argument("--min-pooled-zh", type=float, default=0.6)
    args = ap.parse_args()

    from anatomy_eval.golden import load_golden
    from anatomy_eval.harness import evaluate_recall_by_class
    from anatomy_eval.reference import maxsim_hamming
    from anatomy_shared.binary import binarize, pool_patches
    from colpali_service.real_encoder import build_real_encoder
    from colpali_service.translate import detect_lang

    lines = PAGES.read_text(encoding="utf-8").splitlines()
    pages = [json.loads(line) for line in lines if line.strip()]
    golden = load_golden(QUERIES)
    check_fixtures(pages, golden, detect_lang)

    print("== 載入真實 encoder（含 loading_info 守門）與 MT ==")
    t0 = time.perf_counter()
    encoder = build_real_encoder()
    print(f"載入完成：{time.perf_counter() - t0:.1f}s")

    print(f"== 編碼 {len(pages)} 個偽頁面 ==")
    runtime = encoder._runtime
    encs = runtime.encode_pages([render_page(p["title"], p["text"]) for p in pages])
    if len(encs) != len(pages):                      # 截斷的批次編碼不得放行
        raise SystemExit(f"encode_pages 回傳 {len(encs)} != 頁數 {len(pages)}")
    page_bins = {}
    page_pooled = {}
    for p, enc in zip(pages, encs, strict=True):
        valid = enc.embeddings[enc.valid_mask]
        page_bins[p["page_id"]] = [binarize(v) for v in valid]
        pooled = pool_patches(enc.embeddings, valid_mask=enc.valid_mask)
        if not (np.isfinite(pooled).all() and np.linalg.norm(pooled) > 0):   # DL-019 健檢
            raise SystemExit(f"頁面 pooled 無效（NaN/零向量）：{p['page_id']}")
        page_pooled[p["page_id"]] = pooled

    # 先一次編碼全部 query（並量延遲），雙軌排序共用同一份編碼結果
    lat: list[float] = []
    enc_cache: dict[str, dict] = {}
    for qa in golden:
        t = time.perf_counter()
        enc_cache[qa.id] = encoder.encode_query(qa.query)
        lat.append(time.perf_counter() - t)
        if detect_lang(qa.query) == "zh":
            print(f"  [{qa.id}] {qa.query} -> translated_q={enc_cache[qa.id]['translated_q']!r}")

    def retrieve_maxsim(qa) -> list[str]:
        out = enc_cache[qa.id]
        return sorted(page_bins, key=lambda pid: -maxsim_hamming(out["tokens_bin"], page_bins[pid]))

    def retrieve_pooled(qa) -> list[str]:
        q = np.frombuffer(enc_cache[qa.id]["pooled_f32"], dtype="<f4")
        if not (np.isfinite(q).all() and np.linalg.norm(q) > 0):             # DL-019 健檢
            raise SystemExit(f"query pooled 無效（NaN/零向量）：{qa.id}")
        return sorted(page_pooled, key=lambda pid: -float(
            np.dot(q, page_pooled[pid]) / (np.linalg.norm(q) * np.linalg.norm(page_pooled[pid]))))

    zh = [qa for qa in golden if detect_lang(qa.query) == "zh"]
    en = [qa for qa in golden if detect_lang(qa.query) == "en"]
    reports = {
        ("maxsim", "zh"): evaluate_recall_by_class(zh, retrieve_maxsim, k=args.k),
        ("maxsim", "en"): evaluate_recall_by_class(en, retrieve_maxsim, k=args.k),
        ("pooled", "zh"): evaluate_recall_by_class(zh, retrieve_pooled, k=args.k),
        ("pooled", "en"): evaluate_recall_by_class(en, retrieve_pooled, k=args.k),
    }
    thresholds = {
        ("maxsim", "zh"): args.min_maxsim_zh, ("maxsim", "en"): args.min_maxsim_en,
        ("pooled", "zh"): args.min_pooled_zh, ("pooled", "en"): args.min_pooled_en,
    }

    lat_ms = sorted(int(s * 1000) for s in lat)
    print(f"\nencode_query 延遲（含 MT）：p50={lat_ms[len(lat_ms)//2]}ms max={lat_ms[-1]}ms")
    ok = True
    for key, rep in reports.items():
        track, lang_name = key
        passed = rep["overall"] >= thresholds[key]
        ok = ok and passed
        print(f"{track} {lang_name} recall@{args.k}={rep['overall']:.3f}"
              f"（門檻 {thresholds[key]}）{'PASS' if passed else 'FAIL'}")

    print("GATE PASS" if ok else "GATE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
