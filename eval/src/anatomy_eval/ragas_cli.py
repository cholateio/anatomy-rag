"""CLI entry point for RAGAS evaluation (``anatomy-eval-ragas``).

Usage::

    # Mock run (CI / smoke, deterministic metrics, zero cost):
    anatomy-eval-ragas --golden tests/golden_qa.seed.jsonl \\
                       --report /tmp/report.json --mock

    # Real run (gated, needs EVAL_OPENAI_KEY):
    anatomy-eval-ragas --golden tests/golden_qa.jsonl \\
                       --report eval_report.json --real

Also callable as ``python -m anatomy_eval.ragas_cli``.

Import note: ragas 0.4.3 compatibility is handled transitively.  When
``anatomy_eval.ragas_metrics`` and ``anatomy_eval.ragas_runner`` are imported
below, each calls ``_ensure_compat()`` at their module level before importing
ragas, so no explicit compat call is needed here.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from anatomy_eval.golden import GoldenQA, golden_readiness, load_golden
from anatomy_eval.ragas_metrics import OOS_PHRASE
from anatomy_eval.ragas_runner import (
    AnswerProvider,
    build_rows_from_golden,
    deterministic_metrics,
    llm_metrics,
    run_eval,
)

# ── canned mock answer provider ───────────────────────────────────────────────


def _mock_answer_provider(qa: GoldenQA) -> tuple[str, list[str]]:
    """Canned answer provider for --mock / CI runs (zero API calls, zero cost).

    For OOS questions returns the OOS phrase as answer with a matching context.
    For in-scope questions returns a stub answer with the expected concepts as
    the retrieved context (so NonLLMContextRecall has non-empty contexts to
    match against).
    """
    if qa.category == "out_of_scope":
        return OOS_PHRASE, [OOS_PHRASE]
    # Proxy retrieved context: the expected concepts (or page IDs as fallback).
    context = (
        " ".join(qa.expected_concepts) if qa.expected_concepts else " ".join(qa.expected_pages)
    )
    answer = f"（mock answer for: {qa.query[:60]}）{context}"
    return answer, [context]


# ── real answer provider (gated, --real only) ─────────────────────────────────


def _make_real_answer_provider() -> AnswerProvider:  # pragma: no cover
    """Build a real answer provider that calls the /chat SSE pipeline.

    [C-3] Implements the correct /chat contract:
    - POST ``{"messages": [{"role": "user", "content": <query>}],
              "metadata_filter": <dict|null>}``
    - Consume SSE stream (Vercel AI UI Message Stream format, DL-018):
        * ``{"type": "text-delta", "id": ..., "delta": "..."}`` → accumulate answer
        * ``{"type": "data-sources", "data": [...]}`` → retrieve contexts (snippets)
        * ``data: [DONE]`` → stop
    - Return ``(answer: str, retrieved_contexts: list[str])``.

    NOT called in --mock mode.  Requires a running backend at
    ``EVAL_BACKEND_URL`` (default ``http://localhost:8000``).  Stays
    ``pragma: no cover`` — CI does not start a backend.
    """
    import httpx  # lazy import — not installed in CI ragas job

    base_url = os.environ.get("EVAL_BACKEND_URL", "http://localhost:8000")

    def _provider(qa: GoldenQA) -> tuple[str, list[str]]:
        body = {
            "messages": [{"role": "user", "content": qa.query}],
            "metadata_filter": qa.metadata_filter,
        }
        answer_parts: list[str] = []
        sources: list[dict] = []

        with httpx.Client(timeout=60.0) as client:
            with client.stream("POST", f"{base_url}/chat", json=body) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload_str = line[len("data: "):]
                    if payload_str.strip() == "[DONE]":
                        break
                    try:
                        frame = json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue
                    frame_type = frame.get("type", "")
                    if frame_type == "text-delta":
                        delta = frame.get("delta", "")
                        if delta:
                            answer_parts.append(delta)
                    elif frame_type == "data-sources":
                        raw = frame.get("data", [])
                        if isinstance(raw, list):
                            sources = raw

        answer = "".join(answer_parts)
        # Use snippet field for retrieved_contexts (plain text, suitable for RAGAS).
        retrieved_contexts = [
            s.get("snippet", "") for s in sources if isinstance(s, dict)
        ]
        return answer, retrieved_contexts

    return _provider


# ── main ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns exit code (0=pass, 1=error)."""
    parser = argparse.ArgumentParser(
        prog="anatomy-eval-ragas",
        description="Run RAGAS evaluation on the anatomy-rag golden set.",
    )
    parser.add_argument(
        "--golden",
        required=True,
        metavar="PATH",
        help="Path to golden_qa.jsonl (e.g. tests/golden_qa.seed.jsonl for --mock).",
    )
    parser.add_argument(
        "--report",
        required=True,
        metavar="PATH",
        help="Output path for the JSON report (directory is created if needed).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Offline / CI mode: use canned answers + deterministic_metrics(). "
            "Zero API calls, zero cost."
        ),
    )
    mode.add_argument(
        "--real",
        action="store_true",
        help=(
            "Gated real run: calls the live /chat pipeline + llm_metrics(). "
            "Requires EVAL_OPENAI_KEY and a running backend. "
            "NOT run in CI (workflow_dispatch only, DL-028)."
        ),
    )
    args = parser.parse_args(argv)

    # ── Load golden ────────────────────────────────────────────────────────────
    golden_path = Path(args.golden)
    try:
        golden = load_golden(golden_path)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[anatomy-eval-ragas] ERROR loading golden: {exc}", file=sys.stderr)
        return 1

    # H-2: --real must enforce golden readiness; --mock is warning-only (DL-028).
    readiness = golden_readiness(golden)
    if not readiness["ready"]:
        if args.real:
            print(
                f"[anatomy-eval-ragas] ERROR: --real 需要黃金題庫就緒（DL-028）: "
                f"total={readiness['total']}, shortfall={readiness['shortfall']}。"
                " 補充黃金題庫至 ≥110 題且各類達標後再執行 --real。",
                file=sys.stderr,
            )
            return 1
        print(
            f"[anatomy-eval-ragas] WARNING: golden not ready for live gate "
            f"(total={readiness['total']}, shortfall={readiness['shortfall']}). "
            "Continuing (DL-028: warning only for --mock).",
            file=sys.stderr,
        )

    # ── Build rows + evaluate ──────────────────────────────────────────────────
    report_path = Path(args.report)

    if args.mock:
        rows = build_rows_from_golden(golden, _mock_answer_provider)
        result = run_eval(rows, metrics=deterministic_metrics(), llm=None, embeddings=None)
    else:  # pragma: no cover  (--real not run in CI)
        eval_key = os.environ.get("EVAL_OPENAI_KEY")
        if not eval_key:
            print(
                "[anatomy-eval-ragas] ERROR: EVAL_OPENAI_KEY not set (required for --real).",
                file=sys.stderr,
            )
            return 1
        from langchain_openai import (  # type: ignore[import]
            ChatOpenAI,
            OpenAIEmbeddings,
        )
        from ragas.embeddings import LangchainEmbeddingsWrapper  # type: ignore[import]
        from ragas.llms import LangchainLLMWrapper  # type: ignore[import]

        # H-3: read eval model from env (附錄 A: EVAL_OPENAI_MODEL, default gpt-5.5).
        eval_model = os.environ.get("EVAL_OPENAI_MODEL", "gpt-5.5")
        llm = LangchainLLMWrapper(ChatOpenAI(api_key=eval_key, model=eval_model))
        emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(api_key=eval_key))
        rows = build_rows_from_golden(golden, _make_real_answer_provider())
        result = run_eval(
            rows,
            metrics=llm_metrics(),
            llm=llm,
            embeddings=emb,
            raise_exceptions=False,
        )

    # ── Persist report ─────────────────────────────────────────────────────────
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[anatomy-eval-ragas] Report written to:", report_path)
    for name, score in result.items():
        print(f"  {name}: {score:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
