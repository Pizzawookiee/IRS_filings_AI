"""Constrained Sonnet synthesis over facts and deterministic engine output."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..core.errors import DocumentInferenceError, MissingCredentialsError
from ..core.facts import Facts
from ..core.result import EligibilityResult
from ..ingest.openrouter import DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS, OPENROUTER_URL


class ProgramSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    summary: str = Field(min_length=1, max_length=800)
    why: str = Field(min_length=1, max_length=1200)


class SynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    situation_summary: str = Field(min_length=1, max_length=1500)
    confidence: Literal["high", "medium", "low"]
    primary: ProgramSynthesis
    alternatives: list[ProgramSynthesis] = Field(default_factory=list, max_length=10)
    urgent_summary: list[str] = Field(default_factory=list, max_length=10)
    uncertainties: list[str] = Field(default_factory=list, max_length=10)
    next_steps: list[str] = Field(default_factory=list, min_length=1, max_length=10)
    referral_guidance: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def no_invented_numbers(self):
        texts = [self.situation_summary]
        texts.extend([self.primary.summary, self.primary.why])
        for item in self.alternatives:
            texts.extend([item.summary, item.why])
        texts.extend(self.urgent_summary + self.uncertainties + self.next_steps + self.referral_guidance)
        if any(re.search(r"[$\d]", text) for text in texts):
            raise ValueError("synthesis narrative must not introduce numbers")
        return self


SYSTEM_PROMPT = """You explain an IRS tax-debt screening result in calm, plain language.
The deterministic eligibility result is authoritative. Do not calculate eligibility, introduce a
program code, form, citation, dollar amount, date, deadline, percentage, or other number. Mention
only outcome codes supplied in the allowed lists. Explain uncertainty honestly and recommend a tax
professional for judgment-heavy cases. This is screening information, not tax or legal advice."""


def _schema(allowed_outcomes: list[str]) -> dict[str, Any]:
    program = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "outcome": {"type": "string", "enum": allowed_outcomes},
            "summary": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["outcome", "summary", "why"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "situation_summary": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "primary": program,
            "alternatives": {"type": "array", "items": program},
            "urgent_summary": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "referral_guidance": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "situation_summary", "confidence", "primary", "alternatives", "urgent_summary",
            "uncertainties", "next_steps", "referral_guidance",
        ],
    }


class OpenRouterAnalysisSynthesizer:
    def __init__(self, *, api_key: str | None = None, model: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        try:
            self.timeout = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        except ValueError as exc:
            raise DocumentInferenceError("OPENROUTER_TIMEOUT_SECONDS must be a number") from exc
        if self.timeout <= 0:
            raise DocumentInferenceError("OPENROUTER_TIMEOUT_SECONDS must be greater than zero")
        self.client = client

    def synthesize(self, facts: Facts, eligibility: EligibilityResult) -> SynthesisResult:
        if not self.api_key:
            raise MissingCredentialsError("Set OPENROUTER_API_KEY before generating a personalized summary")
        allowed = [
            eligibility.primary.outcome,
            *(item.outcome for item in eligibility.alternatives),
            *(item.outcome for item in eligibility.urgent),
            *(item.outcome for item in eligibility.stacked_relief),
        ]
        allowed = list(dict.fromkeys(allowed))
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "confirmed_facts": facts.model_dump(mode="json"),
                    "eligibility_result": eligibility.model_dump(mode="json"),
                    "allowed_outcomes": allowed,
                    "required_primary": eligibility.primary.outcome,
                    "allowed_alternatives": [item.outcome for item in eligibility.alternatives],
                }, default=str)},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "irs_resolution_synthesis", "strict": True, "schema": _schema(allowed),
            }},
            "plugins": [{"id": "response-healing"}],
            "provider": {"require_parameters": True},
            "temperature": 0,
            "max_completion_tokens": 4096,
            "stream": False,
        }
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout)
        try:
            response = client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "X-OpenRouter-Title": "IRS Resolve"},
                json=payload,
            )
            if self._is_parameter_routing_failure(response):
                fallback_payload = {**payload, "provider": {"require_parameters": False}}
                response = client.post(
                    OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "X-OpenRouter-Title": "IRS Resolve"},
                    json=fallback_payload,
                )
        except httpx.TimeoutException as exc:
            raise DocumentInferenceError("OpenRouter timed out while synthesizing the analysis") from exc
        except httpx.HTTPError as exc:
            raise DocumentInferenceError(f"Could not reach OpenRouter: {exc}") from exc
        finally:
            if owns_client:
                client.close()
        if response.status_code >= 400:
            messages = {
                401: "OpenRouter rejected the API key",
                402: "OpenRouter credits are exhausted",
                429: "OpenRouter rate limit reached; try again shortly",
            }
            raise DocumentInferenceError(
                messages.get(response.status_code, f"OpenRouter analysis request failed ({response.status_code})")
            )
        try:
            message = response.json()["choices"][0]["message"]
            if message.get("refusal"):
                raise DocumentInferenceError("The model refused to synthesize the analysis")
            raw = message.get("parsed") or message.get("content")
            if isinstance(raw, str):
                text = raw.strip()
                fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
                raw = json.loads(fenced.group(1) if fenced else text)
            result = SynthesisResult.model_validate(raw)
        except DocumentInferenceError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise DocumentInferenceError("OpenRouter returned an invalid analysis synthesis") from exc
        if result.primary.outcome != eligibility.primary.outcome:
            raise DocumentInferenceError("Sonnet changed the deterministic primary outcome")
        allowed_alternatives = {item.outcome for item in eligibility.alternatives}
        if any(item.outcome not in allowed_alternatives for item in result.alternatives):
            raise DocumentInferenceError("Sonnet introduced an unsupported alternative")
        return result

    @staticmethod
    def _is_parameter_routing_failure(response: httpx.Response) -> bool:
        if response.status_code != 404:
            return False
        try:
            message = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            return False
        return "no endpoints found that can handle the requested parameters" in message.lower()
