"""Optional Google Gemini provider (official `google-genai` SDK)."""

from __future__ import annotations

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from app.llm.base import (LLMError, LLMOutputError, LLMRefusalError, LLMRequest, LLMResult,
                          ModelInfo, T, Usage)


def map_gemini_error(exc: Exception) -> LLMError:
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", 0) or 0
        if code in (401, 403):
            return LLMError("Gemini rejected the API key.", code="auth_failed")
        if code == 404:
            return LLMError("Gemini model not found.", code="model_not_found")
        if code == 429:
            return LLMError("Rate limited by Gemini.", code="rate_limited", retryable=True)
        if code >= 500:
            return LLMError(f"Gemini server error ({code}).", code="api_error", retryable=True)
        return LLMError(f"Gemini API error ({code}).", code="api_error")
    if isinstance(exc, TimeoutError):
        return LLMError("Gemini request timed out.", code="timeout", retryable=True)
    return LLMError(f"Unexpected Gemini error: {type(exc).__name__}", code="unexpected")


class GeminiProvider:
    name = "gemini"
    is_demo = False

    def __init__(self, api_key: str, *, timeout_s: float = 180.0,
                 client: genai.Client | None = None) -> None:
        self._client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    async def generate(self, request: LLMRequest[T]) -> LLMResult[T]:
        contents = [types.Content(role="model" if m.role == "assistant" else "user",
                                  parts=[types.Part(text=m.content)]) for m in request.messages]
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            max_output_tokens=request.max_output_tokens,
            response_mime_type="application/json",
            response_schema=request.output_model,
        )
        try:
            resp = await self._client.aio.models.generate_content(
                model=request.model, contents=contents, config=config)
        except Exception as exc:  # SDK raises several unrelated types
            raise map_gemini_error(exc) from exc

        um = resp.usage_metadata
        usage = Usage(input_tokens=(um.prompt_token_count or 0) if um else 0,
                      output_tokens=((um.candidates_token_count or 0) +
                                     (um.thoughts_token_count or 0)) if um else 0,
                      cache_read_tokens=(um.cached_content_token_count or 0) if um else 0)
        cand = (resp.candidates or [None])[0]
        finish = str(getattr(cand, "finish_reason", "") or "")
        if "SAFETY" in finish or "PROHIBITED" in finish:
            raise LLMRefusalError("Gemini blocked this request.", category=finish, usage=usage)
        if "MAX_TOKENS" in finish:
            raise LLMOutputError("Output truncated at max_tokens.", code="truncated", usage=usage)
        parsed = resp.parsed
        if not isinstance(parsed, request.output_model):
            try:
                parsed = request.output_model.model_validate_json(resp.text or "")
            except ValidationError as exc:
                raise LLMOutputError(f"Output did not match schema: {exc}"[:500],
                                     raw=(resp.text or "")[:2000], usage=usage) from exc
        return LLMResult(parsed=parsed, usage=usage, provider=self.name,
                         model=resp.model_version or request.model, stop_reason=finish or None,
                         request_id=getattr(resp, "response_id", None))

    async def list_models(self) -> list[ModelInfo]:
        try:
            out: list[ModelInfo] = []
            async for m in await self._client.aio.models.list():
                actions = m.supported_actions or []
                if actions and "generateContent" not in actions:
                    continue
                mid = (m.name or "").removeprefix("models/")
                out.append(ModelInfo(id=mid, display_name=m.display_name or mid,
                                     provider=self.name, max_input_tokens=m.input_token_limit,
                                     max_output_tokens=m.output_token_limit))
            return out
        except Exception as exc:
            raise map_gemini_error(exc) from exc
