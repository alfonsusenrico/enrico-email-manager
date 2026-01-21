import json
from dataclasses import dataclass
from typing import Any, List

import tiktoken
from openai import OpenAI


@dataclass(frozen=True)
class LLMResult:
    category: str
    confidence: float
    summary: str
    usage: Any


class OpenAIClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def summarize(self, email_text: str, categories: List[str], max_input_tokens: int) -> LLMResult:
        trimmed_text = self._trim_to_tokens(email_text, max_input_tokens)
        system_prompt = (
            "You are a concise personal email assistant (Jarvis-like). Summarize the email and classify it.\n"
            "Tone: calm, confident, minimal, and helpful. No greetings, no fluff, no emojis.\n"
            "Focus only on the core, user-relevant information. Strip boilerplate like unsubscribe, "
            "marketing footers, social links, legal disclaimers, and tracking text.\n"
            "Prefer 1 sentence; 2 sentences max. Use short, direct sentences.\n"
            "Use 'you' when describing impact or required action, but avoid verbose phrasing "
            "like 'You received...'.\n"
            "For statements/bills: include statement type, amount due, minimum payment, and due date "
            "when present; add key balances/points only if material.\n"
            "For alerts: state what happened and what you should do (if anything).\n"
            "If the email is purely marketing with no actionable info, say so briefly.\n"
            "Do not mention email metadata (subject line, sent date/time) unless the body explicitly includes it.\n"
            "Return strict JSON that matches the provided schema.\n"
            "Use one of the provided categories and set confidence between 0 and 1.\n"
            "Categories:\n"
            + "\n".join(f"- {category}" for category in categories)
        )

        json_schema = {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": categories},
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
            },
            "required": ["category", "confidence", "summary"],
            "additionalProperties": False,
        }

        response = self._client.responses.create(
            model=self._model,
            instructions=system_prompt,
            input=trimmed_text,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "email_summary",
                    "schema": json_schema,
                    "strict": True,
                }
            },
        )

        output_text = response.output_text or ""
        try:
            data = json.loads(output_text) if output_text else {}
        except json.JSONDecodeError:
            data = {}

        category = data.get("category") or "Other"
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        summary = data.get("summary") or ""

        usage = response.usage or {}
        return LLMResult(
            category=category,
            confidence=confidence,
            summary=summary,
            usage=usage,
        )

    def _trim_to_tokens(self, text: str, max_tokens: int) -> str:
        encoder = self._get_encoder()
        tokens = encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return encoder.decode(tokens[:max_tokens])

    def _get_encoder(self):
        try:
            return tiktoken.encoding_for_model(self._model)
        except KeyError:
            return tiktoken.get_encoding("o200k_base")
