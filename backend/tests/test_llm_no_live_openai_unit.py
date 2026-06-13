"""CI 守門（輔助）：llm 測試不得建構真 OpenAI 客戶端字樣（執行期攔截見 conftest）。"""
from pathlib import Path


def test_llm_tests_never_construct_live_openai_client():
    here = Path(__file__).resolve().parent
    me = Path(__file__).name
    offenders: list[str] = []
    for f in sorted(here.glob("test_llm_*.py")):
        if f.name == me:
            continue
        src = f.read_text(encoding="utf-8")
        for needle in ("AsyncOpenAI(", "OpenAI("):
            if needle in src:
                offenders.append(f"{f.name}: 含 '{needle}'（請改用 Mock/注入 fake）")
    assert not offenders, "LLM 測試不得建構真 OpenAI 客戶端：\n" + "\n".join(offenders)


def test_default_settings_llm_mock_is_true():
    from anatomy_backend.config import Settings

    assert Settings.model_fields["llm_mock"].default is True
