# ingest/tests/test_types.py
import numpy as np
import pytest
from anatomy_ingest.types import PageParse, EncodedPage


def test_pageparse_holds_fields():
    p = PageParse(page_num=3, markdown="## Heart", metadata={"page_type": "mixed"})
    assert p.page_num == 3 and p.markdown.startswith("##") and p.metadata["page_type"] == "mixed"


def test_encodedpage_patch_bins_and_pooled():
    e = EncodedPage(page_num=1, patch_bins=[b"\x00" * 16, b"\xff" * 16],
                    pooled_f32=np.zeros(128, dtype=np.float32), embed_model="mock-colpali")
    assert e.n_patches == 2 and e.pooled_f32.shape == (128,)


def test_encodedpage_rejects_wrong_bin_length():
    with pytest.raises(ValueError):
        EncodedPage(page_num=1, patch_bins=[b"\x00" * 8],
                    pooled_f32=np.zeros(128, dtype=np.float32), embed_model="m").validate()
