import json
import copy
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

from irsresolve.cli import cmd_extract
from irsresolve.core.errors import (
    DocumentInferenceError,
    MissingCredentialsError,
    UnsupportedDocumentError,
)
from irsresolve.core.facts import Facts, check_attested
from irsresolve.demo.app import extract_uploaded_document
from irsresolve.ingest.openrouter import MAX_FILE_BYTES, OpenRouterDocumentParser


def _body(document_type="w2", values=None):
    if values is None:
        values = [{"path": "income.monthly_gross_income", "value": 5000, "ref": "Box 1 / 12"}]
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"document_type": document_type, "values": values}),
                }
            }
        ]
    }


def _parser(handler):
    return OpenRouterDocumentParser(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    ("media_type", "expected_type"),
    [
        ("application/pdf", "file"),
        ("image/png", "image_url"),
        ("image/jpeg", "image_url"),
        ("image/webp", "image_url"),
    ],
)
def test_request_construction_for_supported_media(media_type, expected_type):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_body())

    result = _parser(handler).parse_bytes(b"document", "tax-doc", media_type)
    attachment = seen["messages"][1]["content"][1]
    assert attachment["type"] == expected_type
    assert seen["model"] == "anthropic/claude-sonnet-5"
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["provider"] == {"require_parameters": True}
    plugin_ids = [plugin["id"] for plugin in seen["plugins"]]
    assert "response-healing" in plugin_ids
    assert ("file-parser" in plugin_ids) is (media_type == "application/pdf")
    assert result.document_type == "w2"


def test_success_is_typed_unattested_and_has_document_provenance():
    parser = _parser(lambda request: httpx.Response(200, json=_body()))
    result = parser.parse_bytes(b"image", "w2.png", "image/png")
    proposal = result.values["income.monthly_gross_income"]
    assert str(proposal["value"]) == "5000"
    assert proposal == {
        "value": proposal["value"],
        "source": "w2",
        "ref": "Box 1 / 12",
        "attested": False,
    }


def test_proposal_cannot_reach_engine_without_confirmation():
    parser = _parser(lambda request: httpx.Response(200, json=_body()))
    proposal = parser.parse_bytes(b"image", "w2.png", "image/png").values[
        "income.monthly_gross_income"
    ]
    fixture = json.loads(Path("fixtures/case_d_ppia_cdp.json").read_text())["facts"]
    facts = copy.deepcopy(fixture)
    facts["income"]["monthly_gross_income"] = {
        "value": proposal["value"],
        "provenance": {
            "source": proposal["source"],
            "ref": proposal["ref"],
            "attested": proposal["attested"],
        },
    }
    with pytest.raises(Exception, match="income.monthly_gross_income"):
        check_attested(Facts.model_validate(facts))


def test_1099_cannot_propose_expenses():
    body = _body("1099", [{"path": "expenses.other_necessary", "value": 100, "ref": "Box 1"}])
    parser = _parser(lambda request: httpx.Response(200, json=body))
    with pytest.raises(DocumentInferenceError, match="cannot propose expense"):
        parser.parse_bytes(b"image", "1099.jpg", "image/jpeg")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"choices": [{"message": {"content": "not json"}}]}, "malformed JSON"),
        ({"choices": [{"message": {"refusal": "no", "content": ""}}]}, "refused"),
        (_body(values=[]), "No supported facts"),
        (_body(values=[{"path": "not.a.fact", "value": 1, "ref": "page 1"}]), "unknown fact path"),
        (_body(values=[{"path": "identity.age", "value": "old", "ref": "page 1"}]), "Invalid value"),
    ],
)
def test_rejects_unsafe_or_invalid_model_output(body, message):
    parser = _parser(lambda request: httpx.Response(200, json=body))
    with pytest.raises(DocumentInferenceError, match=message):
        parser.parse_bytes(b"image", "doc.png", "image/png")


def test_accepts_fenced_or_parsed_structured_content():
    raw = {"document_type": "w2", "values": [{
        "path": "income.monthly_gross_income", "value": 5000, "ref": "Box 1 / 12"
    }]}
    fenced = {"choices": [{"message": {"content": f"```json\n{json.dumps(raw)}\n```"}}]}
    parsed = {"choices": [{"message": {"content": None, "parsed": raw}}]}
    for body in (fenced, parsed):
        result = _parser(lambda request, body=body: httpx.Response(200, json=body)).parse_bytes(
            b"image", "w2.png", "image/png"
        )
        assert result.document_type == "w2"


def test_schema_error_names_location_without_echoing_value():
    body = _body(values=[{"path": "identity.age", "value": "sensitive-invalid", "ref": "page 1"}])
    parser = _parser(lambda request: httpx.Response(200, json=body))
    with pytest.raises(DocumentInferenceError) as caught:
        parser.parse_bytes(b"image", "doc.png", "image/png")
    assert "Invalid value for identity.age" in str(caught.value)
    assert "sensitive-invalid" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "rejected the API key"), (429, "rate limit"), (500, r"failed \(500\)")],
)
def test_api_status_errors_are_recoverable(status, message):
    parser = _parser(lambda request: httpx.Response(status, json={"error": {"message": "provider error"}}))
    with pytest.raises(DocumentInferenceError, match=message):
        parser.parse_bytes(b"image", "doc.png", "image/png")


def test_timeout_is_recoverable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(DocumentInferenceError, match="timed out"):
        _parser(handler).parse_bytes(b"image", "doc.png", "image/png")


def test_missing_key_unsupported_type_and_size(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingCredentialsError):
        OpenRouterDocumentParser().parse_bytes(b"x", "doc.pdf", "application/pdf")
    with pytest.raises(UnsupportedDocumentError, match="Unsupported"):
        OpenRouterDocumentParser(api_key="x").parse_bytes(b"x", "doc.txt", "text/plain")
    with pytest.raises(UnsupportedDocumentError, match="20 MB"):
        OpenRouterDocumentParser(api_key="x").parse_bytes(
            b"x" * (MAX_FILE_BYTES + 1), "doc.pdf", "application/pdf"
        )


def test_cli_reports_missing_configuration(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-test")
    assert cmd_extract(Namespace(document=str(path))) == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_streamlit_upload_boundary_uses_parser():
    class FakeParser:
        def parse_bytes(self, data, filename, media_type):
            assert (data, filename, media_type) == (b"img", "w2.png", "image/png")
            return "proposed"

    assert extract_uploaded_document(b"img", "w2.png", "image/png", FakeParser()) == "proposed"


def test_streamlit_documents_renders_real_uploader_after_questions():
    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).parents[1] / "irsresolve" / "demo" / "app.py"
    app = AppTest.from_file(app_path).run(timeout=20)
    app.session_state["questions_complete"] = True
    app.sidebar.radio[0].set_value("Documents").run(timeout=20)
    assert not app.exception
    assert len(app.get("file_uploader")) == 1
    assert any(button.label == "Extract proposed values" for button in app.button)
