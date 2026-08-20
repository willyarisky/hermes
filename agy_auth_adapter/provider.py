"""Antigravity (AGY) Model Provider and Message Translator for Hermes Agent."""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union

from agy_auth_adapter.auth import AGYAuthManager

logger = logging.getLogger("agy_auth_adapter.provider")

# Endpoint URLs
CLOUD_CODE_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}"

# Default Model Aliases
DEFAULT_MODELS = {
    "google-antigravity/gemini-3.7-flash": "gemini-3.7-flash",
    "google-antigravity/gemini-3.1-pro": "gemini-3.1-pro",
    "google-antigravity/claude-3-7-sonnet": "claude-3-7-sonnet",
    "google-antigravity/gemini-2.5-pro": "gemini-2.5-pro",
    "google-antigravity/gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-3.7-flash": "gemini-3.7-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
}


class AGYAPIError(RuntimeError):
    """Upstream Antigravity/Gemini API error, carrying the original status code.

    The proxy re-uses ``status_code`` so callers see 401 for a rejected
    credential instead of a generic 500 that hides what actually went wrong.
    """

    def __init__(self, status_code: int, body: str, url: str = ""):
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"Antigravity API Error ({status_code}): {body}")

    @property
    def is_auth_error(self) -> bool:
        return self.status_code in (401, 403)


def looks_like_api_key(token: str) -> bool:
    """True for a Google AI Studio API key ('AIza...') rather than an OAuth token.

    API keys must go to generativelanguage.googleapis.com as a ``key=`` query
    parameter; sending one as a Bearer token to cloudcode-pa returns
    "Expected OAuth 2 access token".
    """
    return bool(token) and token.startswith("AIza")


class AGYModelProvider:
    """Bridges OpenAI-compatible Chat Completions from Hermes to Google Antigravity / Gemini."""

    def __init__(self, auth_manager: Optional[AGYAuthManager] = None):
        self.auth_manager = auth_manager or AGYAuthManager()

    def map_model_name(self, model_name: str) -> str:
        """Resolves full model identifiers or shortcuts to backend model names."""
        clean_name = model_name.strip()
        return DEFAULT_MODELS.get(clean_name, clean_name.replace("google-antigravity/", ""))

    def format_openai_to_gemini(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Converts OpenAI format messages & tools into Google Gemini / Antigravity payload."""
        system_instruction = None
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                if system_instruction is None:
                    system_instruction = {"parts": [{"text": str(content)}]}
                else:
                    system_instruction["parts"].append({"text": str(content)})
            elif role == "user":
                contents.append({
                    "role": "user",
                    "parts": [{"text": str(content) if content is not None else ""}],
                })
            elif role == "assistant":
                parts: List[Dict[str, Any]] = []
                if content:
                    parts.append({"text": str(content)})
                
                # Check for OpenAI tool calls
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    fn_args = fn.get("arguments", "{}")
                    try:
                        args_dict = json.loads(fn_args) if isinstance(fn_args, str) else fn_args
                    except Exception:
                        args_dict = {}
                    parts.append({
                        "functionCall": {
                            "name": fn_name,
                            "args": args_dict,
                        }
                    })

                if not parts:
                    parts.append({"text": ""})

                contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", "default")
                fn_name = msg.get("name", "function_result")
                try:
                    result_data = json.loads(content) if isinstance(content, str) else {"response": content}
                except Exception:
                    result_data = {"response": content}

                contents.append({
                    "role": "function",
                    "parts": [{
                        "functionResponse": {
                            "name": fn_name,
                            "response": {"name": fn_name, "content": result_data},
                        }
                    }],
                })

        payload: Dict[str, Any] = {"contents": contents}

        if system_instruction:
            payload["systemInstruction"] = system_instruction

        # Convert tool definitions
        if tools:
            function_declarations = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    function_declarations.append({
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
            if function_declarations:
                payload["tools"] = [{"functionDeclarations": function_declarations}]

        # Generation config
        generation_config: Dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
        
        if generation_config:
            payload["generationConfig"] = generation_config

        return payload

    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]:
        """Performs chat completion against Antigravity/Gemini backend."""
        target_model = self.map_model_name(model)
        payload = self.format_openai_to_gemini(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        headers = self.auth_manager.get_auth_headers()
        headers["Content-Type"] = "application/json"

        # Determine endpoint based on the credential shape, not where it came
        # from: an AI Studio API key is routed to generativelanguage regardless
        # of whether it arrived via the environment, 'login --token', or a file.
        creds = self.auth_manager.get_credentials()
        if creds and looks_like_api_key(creds.access_token):
            # Gemini API Key mode
            action = "streamGenerateContent?alt=sse" if stream else "generateContent"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:{action}?key={creds.access_token}"
            req_headers = {"Content-Type": "application/json"}
        else:
            # Google OAuth / Antigravity Cloud Code mode
            payload["model"] = target_model
            url = CLOUD_CODE_ENDPOINT if stream else f"https://cloudcode-pa.googleapis.com/v1internal:generateContent"
            req_headers = headers

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"AGY completion error ({e.code}): {err_body}")
            raise AGYAPIError(e.code, err_body, url) from e

        if stream:
            return self._stream_response(resp, target_model)
        else:
            raw_text = resp.read().decode("utf-8")
            resp_json = json.loads(raw_text)
            return self._format_gemini_to_openai_response(resp_json, target_model)

    def _stream_response(self, resp: Any, model: str) -> Iterator[Dict[str, Any]]:
        """Parses SSE stream chunks from Antigravity and yields OpenAI-compatible chunks."""
        completion_id = f"chatcmpl-agy-{int(time.time()*1000)}"
        created_at = int(time.time())

        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk_json = json.loads(data_str)
                    openai_chunk = self._format_gemini_to_openai_chunk(
                        chunk_json, completion_id, created_at, model
                    )
                    if openai_chunk:
                        yield openai_chunk
                except Exception as e:
                    logger.debug(f"Error parsing SSE chunk: {e}")

    def _format_gemini_to_openai_response(
        self,
        gemini_resp: Dict[str, Any],
        model: str,
    ) -> Dict[str, Any]:
        """Translates single Gemini response into standard OpenAI ChatCompletion schema."""
        candidates = gemini_resp.get("candidates", [])
        if not candidates:
            return {
                "id": f"chatcmpl-agy-{int(time.time()*1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }],
            }

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])

        text_content = ""
        tool_calls = []

        for idx, p in enumerate(parts):
            if "text" in p:
                text_content += p["text"]
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({
                    "id": f"call_{fc.get('name')}_{idx}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name"),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                })

        msg: Dict[str, Any] = {"role": "assistant", "content": text_content if text_content else None}
        if tool_calls:
            msg["tool_calls"] = tool_calls

        return {
            "id": f"chatcmpl-agy-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": msg,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }],
        }

    def _format_gemini_to_openai_chunk(
        self,
        chunk_json: Dict[str, Any],
        completion_id: str,
        created_at: int,
        model: str,
    ) -> Optional[Dict[str, Any]]:
        """Translates a single Gemini stream chunk into an OpenAI stream chunk."""
        candidates = chunk_json.get("candidates", [])
        if not candidates:
            return None

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        finish_reason = candidate.get("finishReason")

        delta: Dict[str, Any] = {}
        for p in parts:
            if "text" in p:
                delta["content"] = p["text"]
            elif "functionCall" in p:
                fc = p["functionCall"]
                delta["tool_calls"] = [{
                    "index": 0,
                    "id": f"call_{fc.get('name')}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name"),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                }]

        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_at,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": "stop" if finish_reason == "STOP" else None,
            }],
        }
