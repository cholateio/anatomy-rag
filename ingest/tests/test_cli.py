import subprocess
import sys

import pytest
from anatomy_ingest.cli import build_parser, chunk_pages, plan_pages
from anatomy_ingest.source import synthetic_source


def test_parser_required_args():
    p = build_parser()
    ns = p.parse_args(["--pdf", "x.pdf", "--book-meta", "x.yaml", "--kb-version", "3"])
    assert ns.kb_version == 3 and ns.batch_size == 8 and ns.resume is False and ns.book_id is None


def test_parser_flags():
    p = build_parser()
    ns = p.parse_args(["--synthetic", "5", "--book-meta", "x.yaml", "--kb-version", "1",
                       "--batch-size", "2", "--resume", "--book-id", "b-123"])
    assert ns.synthetic == 5 and ns.batch_size == 2 and ns.resume is True and ns.book_id == "b-123"


@pytest.mark.parametrize("args", [
    ["--synthetic", "0", "--book-meta", "x.yaml", "--kb-version", "1"],    # synthetic 0
    ["--synthetic", "-3", "--book-meta", "x.yaml", "--kb-version", "1"],   # 負
    ["--synthetic", "2", "--book-meta", "x.yaml", "--kb-version", "0"],    # kb 0
    ["--synthetic", "2", "--book-meta", "x.yaml", "--kb-version", "1",
     "--batch-size", "0"],  # batch 0
])
def test_parser_rejects_nonpositive_ints(args):
    with pytest.raises(SystemExit) as e:
        build_parser().parse_args(args)
    assert e.value.code == 2  # argparse 參數錯誤退碼 2


def test_chunk_pages():
    items = list(range(1, 8))
    assert chunk_pages(items, 3) == [[1, 2, 3], [4, 5, 6], [7]]


def test_plan_pages_resume_skips_completed():
    pages = list(synthetic_source(4, {"book_title": "A", "edition": 1}))
    todo, skipped = plan_pages(pages, completed={2, 3})
    assert [sp.parse.page_num for sp in todo] == [1, 4]
    assert skipped == [2, 3]


def test_help_exits_0_without_optional_deps(monkeypatch):
    """`--help` 不得因 import asyncpg/yaml/docling 等重依賴而失敗（Codex high #7）：
    隱藏這些模組仍要能印 usage。子行程驗證 import 鏈確實 dependency-light。"""
    code = (
        "import sys, builtins\n"
        "real_import = builtins.__import__\n"
        "blocked = {'asyncpg','yaml','docling','pdf2image','boto3','torch','transformers'}\n"
        "def guard(name, *a, **k):\n"
        "    if name.split('.')[0] in blocked: raise ImportError('blocked '+name)\n"
        "    return real_import(name, *a, **k)\n"
        "builtins.__import__ = guard\n"
        "from anatomy_ingest.cli import main\n"
        "sys.argv=['x','--help']\n"
        "try:\n"
        "    main()\n"
        "except SystemExit as e:\n"
        "    sys.exit(e.code or 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"--help 應退 0；stderr={r.stderr}"
    assert "usage" in r.stdout.lower()
