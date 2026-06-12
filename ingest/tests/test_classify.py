# ingest/tests/test_classify.py
from anatomy_ingest.classify import (
    ANATOMY_SYSTEMS,
    PAGE_TYPES,
    classify_page_type,
    extract_figures,
    map_anatomy_system,
)


def test_map_anatomy_system_keyword_hits():
    assert map_anatomy_system("Upper Limb") == "musculoskeletal"
    assert map_anatomy_system("The Heart and Great Vessels") == "cardiovascular"
    assert map_anatomy_system("Cranial Nerves") == "nervous"


def test_map_anatomy_system_override_then_default_other():
    assert map_anatomy_system("Foobar", overrides={"foobar": "respiratory"}) == "respiratory"
    assert map_anatomy_system("Totally Unknown Chapter") == "other"


def test_map_anatomy_system_result_in_enum():
    assert map_anatomy_system("Upper Limb") in ANATOMY_SYSTEMS


def test_classify_page_type():
    assert classify_page_type(n_pictures=0, n_tables=0, text_len=1200) == "pure_text"
    assert classify_page_type(n_pictures=3, n_tables=0, text_len=80) == "figure_heavy"
    assert classify_page_type(n_pictures=0, n_tables=2, text_len=120) == "table"
    assert classify_page_type(n_pictures=2, n_tables=0, text_len=900) == "mixed"
    assert classify_page_type(n_pictures=0, n_tables=0, text_len=0) in PAGE_TYPES


def test_extract_figures():
    md = "See Fig. 7-23 and Figure 8.4. Also fig 9-1 lowercase. Table 2 not a figure."
    figs = extract_figures(md)
    assert "Fig. 7-23" in figs and "Figure 8.4" in figs and "Fig. 9-1" in figs
    assert len(figs) == len(set(figs))  # 去重
