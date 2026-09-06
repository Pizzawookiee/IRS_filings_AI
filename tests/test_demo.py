"""Smoke test for the Streamlit demo: loading an example renders Analysis without error."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "irsresolve" / "demo" / "app.py")


def test_load_example_d_renders_analysis():
    at = AppTest.from_file(APP, default_timeout=30).run()
    [b for b in at.button if b.label.startswith("D —")][0].click().run()
    assert not at.exception
    texts = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "Partial Payment Installment Agreement" in texts  # recommended path
    assert "Collection Due Process" in texts                 # urgent banner
    assert len(at.metric) == 4                                # header chips
