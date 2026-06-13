import json

from anatomy_backend.api import ai_stream as ais


def test_part_builders_shapes():
    assert ais.start_part() == {"type": "start"}
    assert ais.text_start_part("t0") == {"type": "text-start", "id": "t0"}
    assert ais.text_delta_part("t0", "Hi") == {"type": "text-delta", "id": "t0", "delta": "Hi"}
    assert ais.text_end_part("t0") == {"type": "text-end", "id": "t0"}
    assert ais.finish_part() == {"type": "finish"}


def test_data_part_is_transient_by_default():
    p = ais.data_part("sources", {"sources": [1]})
    assert p == {"type": "data-sources", "data": {"sources": [1]}, "transient": True}


def test_headers_include_marker():
    assert ais.UI_MESSAGE_STREAM_HEADERS["x-vercel-ai-ui-message-stream"] == "v1"


def test_sse_event_is_compact_json_with_newline_sep():
    ev = ais.sse_event(ais.text_delta_part("t0", "你好"))
    # ServerSentEvent：data 為 compact JSON、sep="\n"
    # sse-starlette 存 sep 於私有 _sep（constructor 接受 sep kw，屬性名 _sep）
    assert ev.data == json.dumps(
        {"type": "text-delta", "id": "t0", "delta": "你好"},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert ev._sep == "\n"


def test_done_event():
    ev = ais.done_event()
    assert ev.data == "[DONE]" and ev._sep == "\n"
