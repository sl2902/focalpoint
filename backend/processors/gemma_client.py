"""
Gemma 4 API client for FocalPoint backend.

Wraps the google-genai SDK to call gemma-4-26b-a4b-it. Parses the model's
JSON response and validates it through output_validator.validate_output
before returning — raw model text is never surfaced to the API layer.

Design decisions:
- JSON extraction uses a lenient regex strip so accidental markdown fences
  from the model do not break parsing.
- On any error (API failure, JSON parse error, validation failure) the
  client returns the safe INSUFFICIENT_DATA fallback from validate_output
  rather than propagating an exception, keeping the API layer stateless.
- All exceptions are logged with context but without the raw prompt text
  (which may contain journalist-sensitive location data).
"""

from __future__ import annotations

import json
import re
import threading
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from loguru import logger

from backend.config import settings
from backend.security.output_validator import AlertOutput, validate_output

# Limits concurrent Gemma API calls. Threading (not asyncio) because generate_alert
# is synchronous and may be called from thread-pool workers by FastAPI.
# A third caller waits up to 30 s before raising TimeoutError.
_GEMMA_SEM = threading.BoundedSemaphore(2)

# Model ID for the 26B backend tier (see CLAUDE.md)
_BACKEND_MODEL = "gemma-4-26b-a4b-it"

# E4B is used for transcription — only E2B/E4B support audio input.
_TRANSCRIBE_MODEL = "gemma-4-e4b-it"

# Strip markdown code fences that the model may accidentally emit.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Response schema that constrains Gemma 4 output to exactly the AlertOutput
# structure. Enforced at the API level before our Pydantic validator runs.
_ALERT_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "severity": genai_types.Schema(
            type=genai_types.Type.STRING,
            enum=["GREEN", "AMBER", "RED", "CRITICAL", "INSUFFICIENT_DATA"],
        ),
        "summary": genai_types.Schema(
            type=genai_types.Type.STRING,
            max_length=800,  # prevents the model consuming all tokens on one field
        ),
        "source_citations": genai_types.Schema(
            type=genai_types.Type.ARRAY,
            max_items=10,
            items=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "id": genai_types.Schema(type=genai_types.Type.STRING),
                    "description": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        max_length=120,
                    ),
                },
                required=["id", "description"],
            ),
        ),
        "region": genai_types.Schema(
            type=genai_types.Type.STRING,
        ),
        "timestamp": genai_types.Schema(
            type=genai_types.Type.STRING,
        ),
    },
    required=["severity", "summary", "source_citations", "region", "timestamp"],
)

# Generation config: temperature 0 for deterministic safety assessments.
# response_schema enforces AlertOutput structure at the API level.
_GENERATION_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=1024,
    response_mime_type="application/json",
    response_schema=_ALERT_RESPONSE_SCHEMA,
)

# Web search config: same temperature but includes the Google Search grounding
# tool. response_mime_type and response_schema are omitted — both are
# incompatible with tool use. system_instruction replicates the schema
# field-length constraints (summary ≤150 words, ≤5 citations) so the model
# stays concise without the hard schema guardrails.
# 8192 tokens: grounding tool calls + JSON response share this budget.
# Active conflict zones (Sudan, Yemen, Palestine) can exhaust smaller limits
# because the search tool emits several thousand tokens of results before
# the model starts writing the JSON. Model max is 32768; 8192 is a safe
# ceiling that leaves room for both tool output and the final response.
_WEB_SEARCH_GENERATION_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=8192,
    tools=[{"google_search": {}}],
    system_instruction=(
        "You are a conflict intelligence analyst. "
        "Respond with a single JSON object only — no markdown, no prose. "
        "Fields: severity (GREEN|AMBER|RED|CRITICAL|INSUFFICIENT_DATA), "
        "summary (≤150 words), "
        "source_citations (array, ≤5 items, each with id and description ≤20 words), "
        "region (string), timestamp (ISO-8601). "
        "Be concise. Do not repeat information across fields."
    ),
)

# Transcription config: plain text response, no JSON schema.
_TRANSCRIBE_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=512,
)


def _extract_grounding_urls(response) -> list[tuple[str, str]]:
    """
    Pull source URLs out of the grounding metadata on a Gemini response.

    Returns a list of (uri, title) pairs ordered as they appear in
    grounding_chunks. Real publisher URLs (e.g. reuters.com/...) are
    preferred; vertexaisearch.cloud.google.com redirect URIs are included
    as fallbacks for chunks where the real URL is unavailable.
    Returns an empty list on any error or when metadata is absent.
    """
    try:
        candidates = response.candidates or []
        if not candidates:
            return []
        metadata = getattr(candidates[0], "grounding_metadata", None)
        if not metadata:
            return []
        # grounding_chunks holds publisher URLs; web_search_queries holds
        # the text queries used for search and is not a source of URLs.
        chunks = getattr(metadata, "grounding_chunks", None) or []
        result: list[tuple[str, str]] = []
        n_real = n_redirect = 0
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            uri = getattr(web, "uri", None)
            if not uri:
                continue
            title = getattr(web, "title", None) or uri
            is_redirect = "vertexaisearch.cloud.google.com" in uri
            if is_redirect:
                n_redirect += 1
                logger.debug(f"grounding: chunk redirect (no real URL) — {uri!r}")
            elif re.match(r"^https?://[^/]+/?$", uri):
                n_real += 1
                logger.debug(f"grounding: chunk bare-domain uri={uri!r} (no article path)")
            else:
                n_real += 1
                logger.debug(f"grounding: chunk real uri={uri!r}")
            result.append((uri, str(title)))
        logger.debug(
            f"grounding: extracted {len(result)} URLs from metadata"
            f" ({n_real} real, {n_redirect} redirect fallbacks)"
        )
        return result
    except Exception as exc:
        logger.debug(f"grounding: could not extract URLs — {exc}")
        return []


def _resolve_redirect_url(url: str, timeout: float = 4.0) -> str:
    """Follow a vertexaisearch redirect URL and return the final publisher URL.

    Sends a HEAD request (enough to obtain the Location header chain without
    downloading the response body). Returns the original URL on any failure so
    callers can always safely use the result.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.head(url)
        final_url = str(response.url)
        if "vertexaisearch.cloud.google.com" not in final_url:
            return final_url
        return url
    except Exception as exc:
        logger.debug(f"grounding: redirect resolution failed for {url[:80]!r} — {exc}")
        return url


def _apply_grounding_urls_to_citations(
    citations: list,
    grounding_urls: list[tuple[str, str]],
) -> list:
    """
    Replace vertexaisearch redirect citation IDs with real article URLs from grounding_chunks.

    Iterates citations in order. For each citation whose id is a vertexaisearch
    redirect URL, substitutes the next available real publisher URL from
    grounding_urls (real URLs only — redirect fallbacks are skipped as
    replacements). If real URLs are exhausted the redirect URL is kept as-is.
    Logs both the redirect URL and the substituted real URL at DEBUG level for
    comparison.
    """
    real_urls = [
        (uri, title)
        for uri, title in grounding_urls
        if "vertexaisearch.cloud.google.com" not in uri
    ]
    real_url_iter = iter(real_urls)
    updated = []
    for citation in citations:
        if "vertexaisearch.cloud.google.com" in citation.id:
            redirect_url = citation.id
            try:
                real_url, _title = next(real_url_iter)
                logger.debug(
                    f"grounding: redirect {redirect_url!r}"
                    f" → real {real_url!r}"
                )
                citation = citation.model_copy(update={"id": real_url})
            except StopIteration:
                logger.debug(
                    f"grounding: no real URL available for redirect {redirect_url!r}"
                    f" — keeping redirect as fallback"
                )
        updated.append(citation)
    return updated


def _extract_json(raw_text: str) -> dict:
    """
    Strip optional markdown fences and parse the remaining text as JSON.

    Raises json.JSONDecodeError if the text is not valid JSON after stripping.
    """
    cleaned = _JSON_FENCE_RE.sub("", raw_text).strip()
    return json.loads(cleaned)


# Matches the summary value including escaped chars, stopping at an unescaped
# closing quote or end-of-string (the latter handles truncation mid-field).
_TRUNCATED_SUMMARY_RE = re.compile(
    r'"summary"\s*:\s*"((?:[^"\\]|\\.)*?)(?:"|$)', re.DOTALL
)


def _recover_truncated_json(text: str) -> dict:
    """
    Best-effort extraction of a valid alert dict from a token-truncated response.

    Extracts severity (required), summary (possibly truncated), and any
    complete source_citations entries present before truncation. Raises
    json.JSONDecodeError if severity cannot be found — the caller should
    treat that as an unrecoverable failure and return _fallback().
    """
    # severity is mandatory — bail early if absent
    severity_m = re.search(r'"severity"\s*:\s*"([^"]+)"', text)
    if not severity_m:
        raise json.JSONDecodeError("severity field missing in truncated response", text, 0)
    severity = severity_m.group(1)

    # summary — may be cut off mid-string; accept the partial value
    summary_m = _TRUNCATED_SUMMARY_RE.search(text)
    if summary_m:
        summary = summary_m.group(1)[:800]
    else:
        summary = "Assessment truncated — token limit reached."

    # complete citation objects — both field orders tolerated, cap at 3
    citations: list[dict] = []
    for m in re.finditer(
        r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"\s*\}',
        text,
    ):
        citations.append({"id": m.group(1), "description": m.group(2)})
        if len(citations) >= 3:
            break
    if not citations:
        for m in re.finditer(
            r'\{\s*"description"\s*:\s*"([^"]+)"\s*,\s*"id"\s*:\s*"([^"]+)"\s*\}',
            text,
        ):
            citations.append({"id": m.group(2), "description": m.group(1)})
            if len(citations) >= 3:
                break

    region_m = re.search(r'"region"\s*:\s*"([^"]+)"', text)
    timestamp_m = re.search(r'"timestamp"\s*:\s*"([^"]+)"', text)

    return {
        "severity": severity,
        "summary": summary,
        "source_citations": citations,
        "region": region_m.group(1) if region_m else "",
        "timestamp": timestamp_m.group(1) if timestamp_m else "",
    }


class GemmaClient:
    """
    Synchronous Gemma 4 26B client using the google-genai SDK.

    Instantiate once per backend process (API key is read from settings
    at construction time). Pass the instance to alert_generator.

    When settings.OLLAMA_ENABLED is True, alert generation is routed to
    the local Ollama server (OLLAMA_BASE_URL/api/generate, model gemma4:26b)
    instead of Google AI Studio. The /transcribe path always uses the local
    Gemma 4 E4B model and is unaffected by this setting.
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.GOOGLE_AI_STUDIO_API_KEY
        self._client = genai.Client(
            api_key=key,
            # HttpOptions.timeout is in milliseconds. 180 000 ms = 180 s covers
            # both connect and read phases; the genai SDK does not expose them
            # separately through HttpOptions.
            http_options={"timeout": 180_000},
        )
        if settings.OLLAMA_ENABLED:
            logger.info(
                f"gemma_client: inference backend = Ollama"
                f" ({settings.OLLAMA_BASE_URL}, model=gemma4:26b)"
            )
        else:
            logger.info("gemma_client: inference backend = Google AI Studio (gemma-4-26b-a4b-it)")

    def generate_alert(
        self,
        prompt: str,
        region: str,
        use_web_search: bool = False,
        audio_bytes: bytes | None = None,
        audio_mime_type: str | None = None,
    ) -> AlertOutput:
        """
        Send *prompt* to Gemma 4 26B and return a validated AlertOutput.

        On any failure — API error, JSON parse error, or schema validation
        failure — logs a warning and returns the safe INSUFFICIENT_DATA
        fallback so the caller always receives a well-formed AlertOutput.

        Args:
            prompt:          Fully assembled prompt from prompt_builder.build_prompt.
            region:          Region label threaded into the fallback AlertOutput.
            use_web_search:  When True, enables the Google Search grounding tool so
                             the model can fetch live sources when GDELT Doc API has
                             no usable articles.
            audio_bytes:     Raw audio bytes for multimodal input. When provided,
                             Gemma receives both the audio and the text prompt.
            audio_mime_type: MIME type of the audio (e.g. "audio/wav", "audio/mp4").

        Returns:
            Validated AlertOutput. Never raises.
        """
        if settings.OLLAMA_ENABLED:
            logger.info(
                f"gemma_client: Ollama inference for region={region!r}"
                f" use_web_search={use_web_search}"
            )
            return self._ollama_generate_alert(prompt, region, use_web_search=use_web_search)

        logger.debug(
            f"gemma_client: Google AI Studio inference for region={region!r}"
            f" use_web_search={use_web_search}"
        )
        acquired = _GEMMA_SEM.acquire(blocking=False)
        if not acquired:
            logger.warning(
                f"gemma_client: at capacity (2 concurrent calls)"
                f" — queuing request for region={region!r} (timeout=30s)"
            )
            acquired = _GEMMA_SEM.acquire(blocking=True, timeout=30)
            if not acquired:
                raise TimeoutError(
                    f"gemma_client: timed out waiting for Gemma slot (30s) — region={region!r}"
                )
        try:
            return self._generate_alert_inner(
                prompt=prompt,
                region=region,
                use_web_search=use_web_search,
                audio_bytes=audio_bytes,
                audio_mime_type=audio_mime_type,
            )
        finally:
            _GEMMA_SEM.release()

    def _ollama_chat(
        self, system_content: str, user_content: str, num_predict: int = 1024
    ) -> dict:
        """
        POST to the Ollama /api/chat endpoint and return the full response dict.

        /api/chat is the correct endpoint for instruction-tuned models — it applies
        the model's chat template (turn markers, BOS/EOS tokens) which /api/generate
        bypasses. Using chat format ensures the model generates text output rather
        than consuming tokens silently via context processing.

        Generated text is at response["message"]["content"] (not response["response"]).
        Timing fields (total_duration, prompt_eval_count, eval_count, done_reason)
        remain at the top level, identical to /api/generate.
        """
        url = f"{settings.OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": "gemma4:26b",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user",   "content": user_content},
            ],
            "stream": False,
            "options": {
                "num_ctx": 24576,
                "num_predict": num_predict,
                "temperature": 0,
                "think": False,
                "thinking_budget": 0,
                "thinkingBudget": 0,
            },
        }
        response = httpx.post(url, json=payload, timeout=None)
        response.raise_for_status()
        return response.json()

    def _ollama_web_search(self, query: str) -> list[dict]:
        """
        Call the Ollama cloud web search API and return result dicts.

        Each result has keys: title, url, content.
        Returns an empty list on any error (auth failure, rate limit, network
        error) — callers must handle the empty-list case gracefully.
        """
        if not settings.OLLAMA_API_KEY:
            logger.warning("ollama: OLLAMA_API_KEY not set — web search unavailable")
            return []
        try:
            resp = httpx.post(
                "https://ollama.com/api/web_search",
                json={"query": query, "max_results": 3},
                headers={"Authorization": f"Bearer {settings.OLLAMA_API_KEY}"},
                timeout=15.0,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            logger.debug(
                f"ollama: web search returned {len(results)} results"
                f" for query={query!r}"
            )
            return results
        except Exception as exc:
            logger.warning(
                f"ollama: web search failed for query={query!r}"
                f" — {type(exc).__name__}: {exc}"
            )
            return []

    def _ollama_generate_alert(
        self, prompt: str, region: str, use_web_search: bool = False
    ) -> AlertOutput:
        """
        Generate an alert using the local Ollama server.

        When use_web_search=True: calls the Ollama web search API, injects
        results as a [WEB SEARCH RESULTS] context block before [USER QUERY].
        If web search returns no results, falls back to the data-gap variant
        (the prompt was already built without the Google Search tool instruction,
        so it contains the [DATA AVAILABILITY NOTE] block — the model reasons
        from CPJ/RSF historical data only).

        No semaphore (local process), no safety-filter retries, no grounding
        metadata. Falls back to INSUFFICIENT_DATA on any API or parse error.
        """
        # /no_think suppresses chain-of-thought reasoning tokens in Ollama's
        # Gemma 4 implementation. Must be the very first text in the prompt.
        augmented_prompt = "/no_think\n" + prompt
        web_results_injected = False
        if use_web_search:
            search_query = f"journalist safety {region} conflict news"
            web_results = self._ollama_web_search(search_query)
            if web_results:
                lines = [
                    "[WEB SEARCH RESULTS — USE THESE AS PRIMARY LIVE SOURCES]\n"
                    "Cite each source using its url as the citation id.\n"
                ]
                for r in web_results:
                    lines.append(
                        f"- title: {r.get('title', '')[:100]}\n"
                        f"  url: {r.get('url', '')}\n"
                        f"  content: {r.get('content', '')[:200]}\n"
                    )
                lines.append("[END WEB SEARCH RESULTS]\n")
                results_block = "\n" + "".join(lines)
                # Insert before [USER QUERY so the model sees retrieved data,
                # then live web results, then the journalist's question.
                augmented_prompt = prompt.replace(
                    "[USER QUERY", results_block + "[USER QUERY", 1
                )
                web_results_injected = True
                logger.debug(
                    f"ollama: injected {len(web_results)} web search results"
                    f" into prompt for region={region!r}"
                )
            else:
                logger.warning(
                    "ollama: web search unavailable"
                    f" — using historical data fallback for region={region!r}"
                )
                # augmented_prompt stays as the original data-gap prompt

        num_predict = 8192

        # Appended last so it is the final text the model reads before generating.
        # Placement at the end is intentional — models are more likely to follow
        # an instruction that immediately precedes the generation start position.
        augmented_prompt += (
            "\n\nCRITICAL INSTRUCTION: Your entire response must be a single valid JSON object "
            "starting with { and ending with }. Keep summary to maximum 100 words. "
            "Keep source_citations to maximum 3 entries. "
            "Do not include any text, thinking, or explanation outside the JSON object. "
            "Begin your response now with {"
        )

        # Split at the first [USER QUERY marker — everything before becomes the
        # system message (instructions + grounding data + web results if injected),
        # everything from [USER QUERY onward becomes the user message.
        _split = "[USER QUERY"
        _idx = augmented_prompt.find(_split)
        if _idx != -1:
            system_content = augmented_prompt[:_idx].rstrip()
            user_content   = augmented_prompt[_idx:]
        else:
            system_content = "You are a conflict safety analyst."
            user_content   = augmented_prompt

        system_content = (
            "DO NOT use extended thinking. Respond with JSON immediately.\n"
            + system_content
        )

        _ollama_options = {
            "num_ctx": 24576,
            "num_predict": num_predict,
            "temperature": 0,
            "think": False,
            "thinking_budget": 0,
        }
        estimated_tokens = len(augmented_prompt) // 4
        logger.debug(
            f"ollama: sending chat for region={region!r}"
            f" prompt_chars={len(augmented_prompt)}"
            f" estimated_prompt_tokens~{estimated_tokens}"
            f" system_chars={len(system_content)} user_chars={len(user_content)}"
            f" options={_ollama_options}"
        )
        t0 = time.perf_counter()
        try:
            resp_data = self._ollama_chat(
                system_content, user_content, num_predict=num_predict
            )
        except Exception as exc:
            logger.warning(
                f"ollama: API call failed for region={region!r}"
                f" — {type(exc).__name__}: {exc}"
            )
            return _fallback(region)
        wall_s = time.perf_counter() - t0

        logger.debug(
            f"ollama: full response keys and values for region={region!r}:"
            f" { {k: str(v)[:200] for k, v in resp_data.items()} }"
        )

        # Diagnostic 1: any keys that indicate the model returned thinking metadata.
        thinking_keys = {
            k: v for k, v in resp_data.items()
            if "think" in k.lower() or "thought" in k.lower()
        }
        logger.debug(
            f"ollama: thinking metadata for region={region!r}: {thinking_keys}"
        )

        raw_text = resp_data.get("message", {}).get("content", "")

        # Fallback: some Ollama builds put the entire CoT+JSON in message['thinking']
        # and leave message['content'] empty even when think=False is set.
        # Search for the last {...} block inside the thinking field and use it.
        if not raw_text:
            thinking = resp_data.get("message", {}).get("thinking", "")
            if thinking:
                logger.debug(
                    f"ollama: content empty, searching thinking field"
                    f" ({len(thinking)} chars) for JSON for region={region!r}"
                )
                m = re.search(r'\{.*\}', thinking, re.DOTALL)
                if m:
                    raw_text = m.group(0)
                    logger.info(
                        f"ollama: extracted JSON from thinking field for region={region!r}"
                    )

        # Diagnostic 2: delimiter tokens that indicate thinking bled into the
        # response field despite think=False / thinking_budget=0.
        _THINK_MARKERS = ("<think>", "</think>", "|think|", "[THINK]")
        has_think_tokens = any(marker in raw_text for marker in _THINK_MARKERS)
        logger.debug(
            f"ollama: has_think_tokens={has_think_tokens} for region={region!r}"
            + (f" — markers found: {[m for m in _THINK_MARKERS if m in raw_text]}"
               if has_think_tokens else "")
        )

        logger.debug(
            f"ollama: raw response first 500 chars for region={region!r}:"
            f" {raw_text[:500]!r}"
        )

        # Ollama returns nanosecond duration fields — log them alongside wall time
        # so we can separate model inference from network round-trip overhead.
        total_ns = resp_data.get("total_duration") or 0
        prompt_eval_count = resp_data.get("prompt_eval_count") or 0
        eval_count = resp_data.get("eval_count") or 0
        done_reason = resp_data.get("done_reason", "unknown")
        total_s = total_ns / 1e9
        logger.debug(
            f"ollama: region={region!r}"
            f" wall={wall_s:.2f}s total_duration={total_s:.2f}s"
            f" network_overhead={wall_s - total_s:.2f}s"
            f" prompt_eval_count={prompt_eval_count} eval_count={eval_count}"
            f" done_reason={done_reason!r}"
        )

        if done_reason == "length":
            logger.warning(
                f"ollama: output truncated at num_predict={num_predict}"
                f" eval_count={eval_count} for region={region!r}"
                f" — increase num_predict if JSON is invalid"
            )

        if not raw_text:
            done_reason = resp_data.get("done_reason", "unknown")
            resp_keys = list(resp_data.keys())
            logger.warning(
                f"ollama: empty response for region={region!r}"
                f" done_reason={done_reason!r} resp_keys={resp_keys}"
            )
            return _fallback(region)

        logger.debug(f"ollama: raw response for region={region!r} — {raw_text!r}")

        try:
            raw_dict = _extract_json(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning(f"ollama: JSON parse failed for region={region!r} — {exc}")
            try:
                raw_dict = _recover_truncated_json(raw_text)
                logger.info(
                    f"ollama: partial JSON recovery succeeded for region={region!r}"
                    f" severity={raw_dict.get('severity')!r}"
                    f" citations={len(raw_dict.get('source_citations', []))}"
                )
            except (json.JSONDecodeError, Exception) as recover_exc:
                logger.warning(
                    f"ollama: partial JSON recovery also failed for region={region!r}"
                    f" — {recover_exc}"
                )
                return _fallback(region)

        return validate_output(raw_dict, region)

    def _generate_alert_inner(
        self,
        prompt: str,
        region: str,
        use_web_search: bool = False,
        audio_bytes: bytes | None = None,
        audio_mime_type: str | None = None,
    ) -> AlertOutput:
        """Inner implementation of generate_alert (called with semaphore already held)."""
        config = _WEB_SEARCH_GENERATION_CONFIG if use_web_search else _GENERATION_CONFIG

        # Build contents — multimodal list when audio provided, plain string otherwise.
        if audio_bytes and audio_mime_type:
            contents: list | str = [
                genai_types.Part(
                    inline_data=genai_types.Blob(data=audio_bytes, mime_type=audio_mime_type)
                ),
                genai_types.Part(text=prompt),
            ]
        else:
            contents = prompt

        try:
            response = self._client.models.generate_content(
                model=_BACKEND_MODEL,
                contents=contents,
                config=config,
            )
        except httpx.RemoteProtocolError as exc:
            logger.warning(
                f"gemma_client: RemoteProtocolError for region={region!r}, retrying — {exc}"
            )
            try:
                response = self._client.models.generate_content(
                    model=_BACKEND_MODEL,
                    contents=contents,
                    config=config,
                )
            except httpx.RemoteProtocolError as exc2:
                # Retry also disconnected — try web search config which omits
                # response_schema and may avoid the same server-side issue.
                logger.warning(
                    f"gemma_client: RemoteProtocolError retry also failed for"
                    f" region={region!r} — trying web search config: {exc2}"
                )
                try:
                    ws_response = self._client.models.generate_content(
                        model=_BACKEND_MODEL,
                        contents=contents,
                        config=_WEB_SEARCH_GENERATION_CONFIG,
                    )
                    ws_text = ws_response.text
                    if ws_text:
                        ws_grounding_urls = _extract_grounding_urls(ws_response)
                        return self._structure_web_response(ws_text, region, ws_grounding_urls)
                except Exception as exc3:
                    logger.warning(
                        f"gemma_client: web search fallback after RemoteProtocolError"
                        f" also failed for region={region!r} — {type(exc3).__name__}: {exc3}"
                    )
                return _fallback(region)
            except Exception as exc2:
                logger.warning(
                    f"gemma_client: retry after RemoteProtocolError failed for"
                    f" region={region!r} — {type(exc2).__name__}: {exc2}"
                )
                return _fallback(region)
        except genai_errors.ServerError as exc:
            # 5xx from the Gemini API (commonly 504 DEADLINE_EXCEEDED under load).
            # Sleep 3 s before retrying — immediate retry hits the same overloaded
            # backend. One retry only; if it fails again return the safe fallback.
            logger.warning(
                f"gemma_client: ServerError for region={region!r}, retrying in 3 s — {exc}"
            )
            time.sleep(3)
            try:
                response = self._client.models.generate_content(
                    model=_BACKEND_MODEL,
                    contents=contents,
                    config=config,
                )
            except Exception as exc2:
                logger.warning(
                    f"gemma_client: retry after ServerError failed for"
                    f" region={region!r} — {type(exc2).__name__}: {exc2}"
                )
                return _fallback(region)
        except Exception as exc:
            logger.warning(
                f"gemma_client: API call failed for region={region!r} — {type(exc).__name__}: {exc}"
            )
            return _fallback(region)

        raw_text = response.text
        if not raw_text:
            # Log finish_reason and safety_ratings to distinguish safety blocks
            # from other empty-response causes (e.g. schema constraint failures).
            finish_reason = "unknown"
            try:
                candidate = (response.candidates or [None])[0]
                finish_reason = str(getattr(candidate, "finish_reason", "unknown")) if candidate else "no candidates"
                safety_ratings = getattr(candidate, "safety_ratings", []) if candidate else []
                ratings_str = ", ".join(
                    f"{r.category}={r.probability}" for r in (safety_ratings or [])
                )
                logger.warning(
                    f"gemma_client: empty response from model for region={region!r}"
                    f" — finish_reason={finish_reason}, safety_ratings=[{ratings_str}]"
                )
            except Exception:
                logger.warning(
                    f"gemma_client: empty response from model for region={region!r}"
                )

            # Two retry branches based on finish_reason and whether web search
            # was already active:
            #   safety block (use_web_search=False): retry with web search config
            #   MAX_TOKENS (use_web_search=True):    token budget exhausted by tool
            #       calls — retrying with the same config won't help; return fallback
            #       and let the next scheduler cycle try again with a fresh connection.
            is_safety_block = "MAX_TOKENS" not in finish_reason
            if not use_web_search and is_safety_block:
                logger.info(
                    f"gemma_client: safety filter retry — re-sending region={region!r}"
                    f" with web search config (no response_schema)"
                )
                try:
                    ws_response = self._client.models.generate_content(
                        model=_BACKEND_MODEL,
                        contents=contents,
                        config=_WEB_SEARCH_GENERATION_CONFIG,
                    )
                    ws_text = ws_response.text
                    if not ws_text:
                        logger.warning(
                            f"gemma_client: web search retry also returned empty"
                            f" for region={region!r} — returning fallback"
                        )
                        return _fallback(region)
                    ws_grounding_urls = _extract_grounding_urls(ws_response)
                    try:
                        raw_dict = _extract_json(ws_text)
                        result = validate_output(raw_dict, region)
                        if ws_grounding_urls and any(
                            "vertexaisearch.cloud.google.com" in c.id
                            for c in result.source_citations
                        ):
                            updated = _apply_grounding_urls_to_citations(
                                result.source_citations, ws_grounding_urls
                            )
                            result = result.model_copy(update={"source_citations": updated})
                        return result
                    except json.JSONDecodeError:
                        return self._structure_web_response(ws_text, region, ws_grounding_urls)
                except Exception as exc:
                    logger.warning(
                        f"gemma_client: web search retry failed for region={region!r}"
                        f" — {type(exc).__name__}: {exc}"
                    )

            elif use_web_search and not is_safety_block:
                logger.warning(
                    f"gemma_client: MAX_TOKENS with web search for region={region!r}"
                    f" — tool calls exhausted the token budget; returning fallback"
                )

            return _fallback(region)

        logger.debug(f"gemma_client: raw response for region={region!r} — {raw_text!r}")

        # Extract real publisher URLs from grounding metadata while we still
        # have the response object — these replace the expiring redirect URLs
        # that the model embeds in its citation ids.
        grounding_urls = _extract_grounding_urls(response) if use_web_search else []

        try:
            raw_dict = _extract_json(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning(
                f"gemma_client: JSON parse failed for region={region!r} — {exc}"
            )
            if use_web_search:
                # Web-search responses are prose, not JSON. Pass the grounded
                # text (plus real URLs from metadata) to a second JSON-constrained
                # call so we always get a typed AlertOutput.
                logger.info(
                    f"gemma_client: structuring web-search prose for region={region!r}"
                )
                return self._structure_web_response(raw_text, region, grounding_urls)
            return _fallback(region)

        result = validate_output(raw_dict, region)

        # When web search was active and the model embedded internal Vertex
        # redirect URLs, replace them directly with real publisher URLs from
        # grounding_chunks — no extra API round-trip needed.
        if use_web_search and grounding_urls and any(
            "vertexaisearch.cloud.google.com" in c.id
            for c in result.source_citations
        ):
            logger.info(
                f"gemma_client: replacing vertexaisearch redirect URLs with"
                f" grounding chunk real URLs for region={region!r}"
            )
            updated = _apply_grounding_urls_to_citations(result.source_citations, grounding_urls)
            result = result.model_copy(update={"source_citations": updated})
        if use_web_search and not grounding_urls and any(
            "vertexaisearch.cloud.google.com" in c.id
            for c in result.source_citations
        ):
            logger.info(
                f"gemma_client: grounding metadata absent for region={region!r}"
                f" — resolving redirect URLs directly"
            )
            resolved_citations = []
            for citation in result.source_citations:
                if "vertexaisearch.cloud.google.com" in citation.id:
                    resolved = _resolve_redirect_url(citation.id)
                    if resolved != citation.id:
                        logger.debug(
                            f"grounding: resolved redirect → {resolved!r}"
                        )
                        citation = citation.model_copy(update={"id": resolved})
                resolved_citations.append(citation)
            result = result.model_copy(update={"source_citations": resolved_citations})

        if result.severity == "INSUFFICIENT_DATA":
            logger.warning(
                f"gemma_client: INSUFFICIENT_DATA for region={region!r} — retrying once"
            )
            try:
                retry_response = self._client.models.generate_content(
                    model=_BACKEND_MODEL,
                    contents=contents,
                    config=config,
                )
                retry_text = retry_response.text
                if retry_text:
                    logger.debug(f"gemma_client: retry raw response for region={region!r} — {retry_text!r}")
                    retry_grounding_urls = (
                        _extract_grounding_urls(retry_response) if use_web_search else []
                    ) or grounding_urls
                    if use_web_search:
                        try:
                            result = validate_output(_extract_json(retry_text), region)
                            if retry_grounding_urls and any(
                                "vertexaisearch.cloud.google.com" in c.id
                                for c in result.source_citations
                            ):
                                updated = _apply_grounding_urls_to_citations(
                                    result.source_citations, retry_grounding_urls
                                )
                                result = result.model_copy(update={"source_citations": updated})
                        except json.JSONDecodeError:
                            result = self._structure_web_response(
                                retry_text, region, retry_grounding_urls
                            )
                    else:
                        result = validate_output(_extract_json(retry_text), region)
            except Exception as exc:
                logger.warning(
                    f"gemma_client: retry failed for region={region!r} — {type(exc).__name__}: {exc}"
                )

        return result

    def _structure_web_response(
        self,
        grounded_text: str,
        region: str,
        grounding_urls: list[tuple[str, str]] | None = None,
    ) -> AlertOutput:
        """
        Convert free-form web-search prose into a structured AlertOutput.

        Called when generate_alert's web-search response is not JSON-parseable.
        Passes the grounded text (plus real publisher URLs from grounding metadata)
        to a second JSON-constrained call so we always get a typed AlertOutput.
        Falls back to _fallback(region) if the second call also fails.

        Args:
            grounded_text:  Free-form prose from the web-search response.
            region:         Region label for the fallback path.
            grounding_urls: Real (uri, title) pairs from grounding_metadata.
                            Injected into the prompt so the model cites permanent
                            publisher URLs instead of expiring redirect URLs.
        """
        urls = grounding_urls or []
        if urls:
            sources_block = "\n\nVerified source URLs (use these exact URLs as citation ids):\n"
            for uri, title in urls[:10]:
                sources_block += f"  - {uri}  ({title})\n"
        else:
            sources_block = ""

        structure_prompt = (
            f"You have gathered the following live intelligence about journalist "
            f"safety in {region}:\n\n"
            f"{grounded_text[:3000]}"
            f"{sources_block}\n\n"
            "Based solely on the intelligence above, produce your JSON safety assessment. "
            "For each citation, use one of the verified source URLs above as the id field "
            "if the source is listed; otherwise use CPJ:<detail> or RSF:<detail>. "
            "Never use vertexaisearch.cloud.google.com URLs."
        )
        try:
            response = self._client.models.generate_content(
                model=_BACKEND_MODEL,
                contents=structure_prompt,
                config=_GENERATION_CONFIG,
            )
            text = response.text
            if not text:
                logger.warning(
                    f"gemma_client: _structure_web_response got empty response for region={region!r}"
                )
                return _fallback(region)
            logger.debug(
                f"gemma_client: structured web response for region={region!r} — {text!r}"
            )
            return validate_output(_extract_json(text), region)
        except Exception as exc:
            logger.warning(
                f"gemma_client: _structure_web_response failed for region={region!r}"
                f" — {type(exc).__name__}: {exc}"
            )
            return _fallback(region)

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language: str = "en",
    ) -> str:
        """
        Transcribe audio using Gemma 4 and return the transcript as plain text.

        Args:
            audio_bytes: Raw audio bytes.
            mime_type:   MIME type of the audio (e.g. "audio/wav", "audio/mp4").
            language:    BCP-47 language hint (e.g. "en", "fr", "ar").

        Returns:
            Transcribed text, or empty string on any failure. Never raises.
        """
        prompt = (
            f"Transcribe the following audio recording exactly as spoken. "
            f"The expected language is '{language}'. "
            "Return only the transcribed text with no additional commentary, "
            "labels, or formatting."
        )
        contents = [
            genai_types.Part(
                inline_data=genai_types.Blob(data=audio_bytes, mime_type=mime_type)
            ),
            genai_types.Part(text=prompt),
        ]
        try:
            response = self._client.models.generate_content(
                model=_TRANSCRIBE_MODEL,
                contents=contents,
                config=_TRANSCRIBE_CONFIG,
            )
            text = (response.text or "").strip()
            if not text:
                logger.warning("gemma_client: transcribe_audio returned empty response")
            return text
        except Exception as exc:
            logger.warning(
                f"gemma_client: transcribe_audio failed — {type(exc).__name__}: {exc}"
            )
            return ""


def _fallback(region: str) -> AlertOutput:
    """Return an INSUFFICIENT_DATA AlertOutput via validate_output's fallback path."""
    from datetime import datetime

    return validate_output(
        {
            "severity": "INSUFFICIENT_DATA",
            "summary": "Gemma 4 API call failed — safe fallback response.",
            "source_citations": [{"id": "FALLBACK:api-error", "description": "Gemma 4 API call failed"}],
            "region": region,
            "timestamp": datetime.utcnow().isoformat(),
        },
        region,
    )
