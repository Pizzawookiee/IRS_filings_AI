"""OpenRouter-powered document extraction.

The model is an untrusted parser, never a decision maker. Its structured response is validated
against the canonical Facts model and returned only as unattested ProposedFacts.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import types
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..core.errors import DocumentInferenceError, MissingCredentialsError, UnsupportedDocumentError
from ..core.facts import Attested, Facts, ProposedFacts

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_PDF_ENGINE = "mistral-ocr"
SUPPORTED_PDF_ENGINES = {"mistral-ocr", "cloudflare-ai", "native"}
MAX_FILE_BYTES = 20 * 1024 * 1024
SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}
MEDIA_TYPE_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
DocumentType = Literal["w2", "1099", "433a", "433b", "notice", "transcript"]
SOURCE_BY_DOCUMENT_TYPE = {
    "w2": "w2",
    "1099": "1099",
    "433a": "433a",
    "433b": "433b",
    "notice": "notice",
    "transcript": "transcript",
}


class ExtractionValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    value: Any
    ref: str = Field(min_length=1, max_length=200)


class ExtractionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: DocumentType
    values: list[ExtractionValue]


def _strip_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = tuple(a for a in get_args(annotation) if a is not type(None))
        if len(args) == 1:
            return args[0]
    return annotation


def _canonical_leaf_annotations() -> dict[str, Any]:
    leaves: dict[str, Any] = {}

    def visit(model: type[BaseModel], prefix: str = "") -> None:
        for name, field in model.model_fields.items():
            path = f"{prefix}.{name}" if prefix else name
            annotation = _strip_optional(field.annotation)
            if isinstance(annotation, type) and issubclass(annotation, Attested):
                leaves[path] = annotation
            elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
                visit(annotation, path)

    visit(Facts)
    return leaves


CANONICAL_LEAVES = _canonical_leaf_annotations()
CANONICAL_PATHS = tuple(sorted(CANONICAL_LEAVES))

SYSTEM_PROMPT = """You extract facts from US tax documents for an IRS eligibility screener.
You do not give advice or decide eligibility. Classify the document as exactly one of: w2, 1099,
433a, 433b, notice, transcript. Return only facts explicitly supported by the document, using only
the allowed canonical paths. Do not infer missing values. Use ISO YYYY-MM-DD dates and plain decimal
numbers without currency symbols. For annual W-2 wages/withholding or 1099 gross receipts, convert
to monthly amounts by dividing by 12 and name the source box in ref. The value must be a JSON number,
not a calculation string or object. Example: W-2 Box 1 wages of 90000.00 must use value 7500.00.
A 1099 is gross income and must
never populate any expenses.* path. For every value provide a concise page, box, line, or section
reference. Collection-valued paths such as debt.tax_periods must be returned once with the complete
list, never as duplicate paths. If the document contains no supported values, return an empty values
array.

For Form 433-A and Form 433-B, actively map completed form fields as follows:
- taxpayer/dependent count -> household.household_size
- taxpayer address ZIP and state -> household.zip_code and household.state
- total monthly household income -> income.monthly_gross_income
- itemized monthly income -> income.income_sources
- housing/utilities -> expenses.housing_utilities
- food, clothing, housekeeping supplies, personal care, and miscellaneous -> expenses.food_clothing_misc
- vehicle loan/lease payments -> expenses.transportation_ownership
- vehicle operating and public transportation -> expenses.transportation_operating
- out-of-pocket medical costs -> expenses.healthcare_out_of_pocket
- current taxes withheld or estimated tax payments -> expenses.taxes_withheld_or_estimated
- health insurance, childcare, court-ordered payments, secured debts, and other necessary costs ->
  their matching expenses.* paths
- cash and bank balances -> assets.cash_and_bank
- investments, retirement, life-insurance cash value, real estate, vehicles, business assets, and
  other assets -> their matching assets.* paths
Use monthly amounts where the form labels them monthly. Do not confuse an asset balance with a
monthly payment. Extract an explicit zero; do not treat a blank field as zero."""


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_type": {
                "type": "string",
                "enum": list(SOURCE_BY_DOCUMENT_TYPE),
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "enum": list(CANONICAL_PATHS)},
                        "value": {},
                        "ref": {"type": "string"},
                    },
                    "required": ["path", "value", "ref"],
                },
            },
        },
        "required": ["document_type", "values"],
    }


class OpenRouterDocumentParser:
    """Extract supported tax-document values through OpenRouter and validate every proposal."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        pdf_engine: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self.pdf_engine = (pdf_engine or os.getenv("OPENROUTER_PDF_ENGINE", DEFAULT_PDF_ENGINE)).lower().strip()
        if self.pdf_engine not in SUPPORTED_PDF_ENGINES:
            raise DocumentInferenceError(
                "OPENROUTER_PDF_ENGINE must be mistral-ocr, cloudflare-ai, or native"
            )
        self.timeout_seconds = self._timeout_from_env() if timeout_seconds is None else timeout_seconds
        if self.timeout_seconds <= 0:
            raise DocumentInferenceError("timeout_seconds must be greater than zero")
        self.client = client

    @staticmethod
    def _timeout_from_env() -> float:
        raw = os.getenv("OPENROUTER_TIMEOUT_SECONDS")
        if not raw:
            return DEFAULT_TIMEOUT_SECONDS
        try:
            value = float(raw)
        except ValueError as exc:
            raise DocumentInferenceError("OPENROUTER_TIMEOUT_SECONDS must be a number") from exc
        if value <= 0:
            raise DocumentInferenceError("OPENROUTER_TIMEOUT_SECONDS must be greater than zero")
        return value

    def parse(self, path: str | Path) -> ProposedFacts:
        file_path = Path(path)
        media_type = (
            MEDIA_TYPE_BY_SUFFIX.get(file_path.suffix.lower())
            or mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        return self.parse_bytes(file_path.read_bytes(), file_path.name, media_type)

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        media_type: str,
        expected_document_type: DocumentType | None = None,
    ) -> ProposedFacts:
        media_type = media_type.lower().split(";", 1)[0].strip()
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise UnsupportedDocumentError(
                f"Unsupported document type {media_type!r}; use PDF, PNG, JPEG, or WebP"
            )
        if not data:
            raise UnsupportedDocumentError("The uploaded document is empty")
        if len(data) > MAX_FILE_BYTES:
            raise UnsupportedDocumentError("The uploaded document exceeds the 20 MB limit")
        if not self.api_key:
            raise MissingCredentialsError("Set OPENROUTER_API_KEY before extracting documents")

        filename_type = self._document_type_from_filename(filename)
        expected_type = expected_document_type or filename_type
        payload = self._build_payload(data, filename, media_type, expected_document_type=expected_type)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "IRS Resolve",
        }
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = self._post_with_routing_fallback(client, headers, payload)
            if response.status_code >= 400:
                self._raise_api_error(response)
            try:
                return self._parse_response(
                    response,
                    filename=filename,
                    expected_document_type=expected_type,
                )
            except DocumentInferenceError as exc:
                if not str(exc).startswith("Invalid value for "):
                    raise
                raw = self._raw_extraction(response)
                repair_payload = self._build_repair_payload(raw, expected_type)
                repaired_response = self._post_with_routing_fallback(client, headers, repair_payload)
                if repaired_response.status_code >= 400:
                    self._raise_api_error(repaired_response)
                return self._parse_response(
                    repaired_response,
                    filename=filename,
                    expected_document_type=expected_type,
                )
        except httpx.TimeoutException as exc:
            raise DocumentInferenceError("OpenRouter timed out while extracting the document") from exc
        except httpx.HTTPError as exc:
            raise DocumentInferenceError(f"Could not reach OpenRouter: {exc}") from exc
        finally:
            if owns_client:
                client.close()

    def _post_with_routing_fallback(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        response = client.post(OPENROUTER_URL, headers=headers, json=payload)
        if self._is_parameter_routing_failure(response):
            fallback_payload = {**payload, "provider": {"require_parameters": False}}
            response = client.post(OPENROUTER_URL, headers=headers, json=fallback_payload)
        return response

    @staticmethod
    def _is_parameter_routing_failure(response: httpx.Response) -> bool:
        if response.status_code != 404:
            return False
        try:
            message = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            return False
        return "no endpoints found that can handle the requested parameters" in message.lower()

    def _build_payload(
        self,
        data: bytes,
        filename: str,
        media_type: str,
        *,
        expected_document_type: DocumentType | None = None,
    ) -> dict[str, Any]:
        encoded = base64.b64encode(data).decode("ascii")
        data_url = f"data:{media_type};base64,{encoded}"
        if media_type == "application/pdf":
            attachment: dict[str, Any] = {
                "type": "file",
                "file": {"filename": filename, "file_data": data_url},
            }
        else:
            attachment = {"type": "image_url", "image_url": {"url": data_url}}

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Extract supported proposed facts. "
                                + (
                                    f"The upload category is {expected_document_type}; return "
                                    f'document_type exactly as "{expected_document_type}".'
                                    if expected_document_type
                                    else "Classify this tax document and return document_type."
                                )
                            ),
                        },
                        attachment,
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "irs_document_extraction",
                    "strict": True,
                    "schema": _response_schema(),
                },
            },
            "temperature": 0,
            "max_completion_tokens": 4096,
            "stream": False,
            "provider": {"require_parameters": True},
        }
        payload["plugins"] = [{"id": "response-healing"}]
        if media_type == "application/pdf":
            payload["plugins"].insert(0, {"id": "file-parser", "pdf": {"engine": self.pdf_engine}})
        return payload

    def _build_repair_payload(
        self,
        raw: dict[str, Any],
        expected_document_type: DocumentType | None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Correct this extraction so it exactly matches the JSON schema. Every monetary "
                        "or decimal fact value must be a JSON number, never text, a calculation, an array, "
                        "or an object. Preserve only facts supported by the original extraction. "
                        + (
                            f'Set document_type to "{expected_document_type}". '
                            if expected_document_type else ""
                        )
                        + "Invalid extraction JSON:\n"
                        + json.dumps(raw, default=str)
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "irs_document_extraction_repair",
                    "strict": True,
                    "schema": _response_schema(),
                },
            },
            "plugins": [{"id": "response-healing"}],
            "provider": {"require_parameters": True},
            "temperature": 0,
            "max_completion_tokens": 4096,
            "stream": False,
        }

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        messages = {
            401: "OpenRouter rejected the API key",
            402: "OpenRouter credits are exhausted",
            429: "OpenRouter rate limit reached; try again shortly",
        }
        message = messages.get(response.status_code, f"OpenRouter request failed ({response.status_code})")
        try:
            detail = response.json().get("error", {}).get("message")
        except (ValueError, AttributeError):
            detail = None
        if detail and response.status_code not in (401, 402):
            message = f"{message}: {detail}"
        raise DocumentInferenceError(message)

    @staticmethod
    def _message_content(message: dict[str, Any]) -> str | dict[str, Any]:
        if message.get("refusal"):
            raise DocumentInferenceError("The model refused to process this document")
        if isinstance(message.get("parsed"), dict):
            return message["parsed"]
        content = message.get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        return ""

    @staticmethod
    def _decode_json_content(content: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        text = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
        except (json.JSONDecodeError, TypeError) as exc:
            raise DocumentInferenceError(
                "OpenRouter returned malformed JSON; retry the extraction"
            ) from exc
        if not isinstance(decoded, dict):
            raise DocumentInferenceError("OpenRouter extraction must be a JSON object")
        return decoded

    @staticmethod
    def _document_type_from_filename(filename: str) -> str | None:
        normalized = re.sub(r"[^a-z0-9]", "", Path(filename).stem.lower())
        markers = (
            ("433a", "433a"),
            ("433b", "433b"),
            ("1099", "1099"),
            ("w2", "w2"),
            ("transcript", "transcript"),
            ("notice", "notice"),
        )
        matches = {document_type for marker, document_type in markers if marker in normalized}
        return matches.pop() if len(matches) == 1 else None

    def _raw_extraction(self, response: httpx.Response) -> dict[str, Any]:
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DocumentInferenceError("OpenRouter response did not contain a model message") from exc
        return self._decode_json_content(self._message_content(message))

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        filename: str = "",
        expected_document_type: DocumentType | None = None,
    ) -> ProposedFacts:
        try:
            body = response.json()
        except ValueError as exc:
            raise DocumentInferenceError("OpenRouter returned a non-JSON HTTP response") from exc
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("OpenRouter response missing choices[0].message; keys=%s", sorted(body) if isinstance(body, dict) else [])
            raise DocumentInferenceError("OpenRouter response did not contain a model message") from exc

        content = self._message_content(message)
        if not content:
            finish_reason = body.get("choices", [{}])[0].get("finish_reason", "unknown")
            logger.warning("OpenRouter returned empty extraction content; finish_reason=%s", finish_reason)
            raise DocumentInferenceError(
                f"OpenRouter returned no extraction content (finish reason: {finish_reason})"
            )
        raw = self._decode_json_content(content)
        if "document_type" not in raw:
            inferred_type = expected_document_type or self._document_type_from_filename(filename)
            if inferred_type:
                raw = {**raw, "document_type": inferred_type}
        try:
            extraction = ExtractionEnvelope.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors(include_input=False)[0]
            location = ".".join(str(part) for part in first.get("loc", ())) or "response"
            detail = first.get("msg", "invalid value")
            logger.warning(
                "OpenRouter extraction schema mismatch; location=%s type=%s",
                location,
                first.get("type", "unknown"),
            )
            raise DocumentInferenceError(
                f"OpenRouter extraction schema mismatch at {location}: {detail}"
            ) from exc
        if not extraction.values:
            raise DocumentInferenceError("No supported facts were found in the document")

        source = SOURCE_BY_DOCUMENT_TYPE[extraction.document_type]
        proposals: dict[str, dict[str, Any]] = {}
        for item in extraction.values:
            if item.path not in CANONICAL_LEAVES:
                raise DocumentInferenceError(f"Model returned unknown fact path: {item.path}")
            if extraction.document_type == "1099" and item.path.startswith("expenses."):
                raise DocumentInferenceError("A 1099 extraction cannot propose expense values")
            if item.path in proposals:
                raise DocumentInferenceError(f"Model returned duplicate fact path: {item.path}")
            normalized_value = self._normalize_scalar(item.path, item.value)
            wrapped = {
                "value": normalized_value,
                "provenance": {"source": source, "ref": item.ref, "attested": False},
            }
            try:
                validated = TypeAdapter(CANONICAL_LEAVES[item.path]).validate_python(wrapped)
            except ValidationError as exc:
                raise DocumentInferenceError(f"Invalid value for {item.path}: {exc.errors()[0]['msg']}") from exc
            proposals[item.path] = {
                "value": validated.value,
                "source": source,
                "ref": item.ref,
                "attested": False,
            }
        return ProposedFacts(document_type=extraction.document_type, values=proposals)

    @staticmethod
    def _normalize_scalar(path: str, value: Any) -> Any:
        """Normalize unambiguous currency formatting for Decimal leaves only."""
        annotation = CANONICAL_LEAVES[path].model_fields["value"].annotation
        if annotation is not Decimal:
            return value
        period = None
        if isinstance(value, dict):
            monthly_keys = [key for key in ("monthly", "monthly_amount") if key in value]
            annual_keys = [key for key in ("annual", "annual_amount") if key in value]
            amount_keys = [key for key in ("amount", "value") if key in value]
            currency = str(value.get("currency") or "USD").upper()
            if currency not in {"USD", "$"}:
                return value
            if len(monthly_keys) == 1:
                value = value[monthly_keys[0]]
                period = "monthly"
            elif len(annual_keys) == 1 and path == "income.monthly_gross_income":
                value = value[annual_keys[0]]
                period = "annual"
            elif len(amount_keys) == 1:
                period = str(value.get("period") or value.get("unit") or "").lower().strip() or None
                value = value[amount_keys[0]]
            else:
                return value
        if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
            return value
        candidate = value.strip() if isinstance(value, str) else str(value)
        arithmetic = re.fullmatch(
            r"\s*\$?([\d,]+(?:\.\d+)?)\s*(?:/|÷)\s*12(?:\s*months?)?\s*"
            r"(?:=\s*\$?([\d,]+(?:\.\d+)?))?\s*",
            candidate,
            flags=re.IGNORECASE,
        )
        if arithmetic and path == "income.monthly_gross_income":
            annual = Decimal(arithmetic.group(1).replace(",", ""))
            calculated = annual / Decimal(12)
            stated = arithmetic.group(2)
            if stated is not None:
                stated_number = Decimal(stated.replace(",", ""))
                if abs(stated_number - calculated) > Decimal("0.01"):
                    return value
                return stated_number
            return calculated
        result_first = re.fullmatch(
            r"\s*\$?([\d,]+(?:\.\d+)?)\s*\(\s*\$?([\d,]+(?:\.\d+)?)\s*"
            r"(?:/|÷)\s*12(?:\s*months?)?\s*\)\s*",
            candidate,
            flags=re.IGNORECASE,
        )
        if result_first and path == "income.monthly_gross_income":
            stated_number = Decimal(result_first.group(1).replace(",", ""))
            annual = Decimal(result_first.group(2).replace(",", ""))
            if abs(stated_number - annual / Decimal(12)) <= Decimal("0.01"):
                return stated_number
            return value
        lowered = candidate.lower()
        suffixes = {
            "/month": "monthly", " per month": "monthly", " monthly": "monthly",
            " (monthly)": "monthly", " (per month)": "monthly",
            "/mo": "monthly", " per mo": "monthly", " annually": "annual",
            " annual": "annual", " per year": "annual", "/year": "annual",
        }
        for suffix, detected_period in suffixes.items():
            if lowered.endswith(suffix):
                candidate = candidate[:-len(suffix)].strip()
                period = period or detected_period
                break
        negative = candidate.startswith("(") and candidate.endswith(")")
        if negative:
            candidate = candidate[1:-1].strip()
        if candidate.upper().startswith("USD "):
            candidate = candidate[4:].strip()
        if candidate.upper().endswith(" USD"):
            candidate = candidate[:-4].strip()
        if candidate.startswith("$"):
            candidate = candidate[1:].strip()
        candidate = candidate.replace(",", "")
        try:
            number = Decimal(candidate)
        except InvalidOperation:
            return value
        number = -number if negative else number
        monthly_path = path == "income.monthly_gross_income" or path.startswith("expenses.")
        if period in {"month", "monthly", "per_month", "mo"} and not monthly_path:
            return value
        if period in {"year", "annual", "annually", "yearly", "per_year"}:
            if path != "income.monthly_gross_income":
                return value
            number /= Decimal(12)
        elif period not in {None, "month", "monthly", "per_month", "mo"}:
            return value
        return number
