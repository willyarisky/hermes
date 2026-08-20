"""OpenAI-compatible HTTP Proxy Server bridging Hermes to Antigravity (AGY)."""

import http.server
import json
import logging
import time
from typing import Any, Dict, Optional

from agy_auth_adapter.auth import AGYAuthManager
from agy_auth_adapter.provider import DEFAULT_MODELS, AGYModelProvider

logger = logging.getLogger("agy_auth_adapter.proxy")


class AGYProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler implementing OpenAI v1 API endpoints."""

    auth_manager: AGYAuthManager = AGYAuthManager()
    provider: AGYModelProvider = AGYModelProvider(auth_manager)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info(f"{self.address_string()} - {format % args}")

    def do_OPTIONS(self) -> None:
        """Handles CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/v1/models" or self.path == "/models":
            self._handle_models()
        elif self.path == "/health" or self.path == "/status":
            self._handle_health()
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/v1/chat/completions" or self.path == "/chat/completions":
            self._handle_chat_completions()
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

    def _handle_models(self) -> None:
        model_list = [
            {
                "id": model_id,
                "object": "model",
                "created": 1700000000,
                "owned_by": "google-antigravity",
                "permission": [],
                "root": model_id,
                "parent": None,
            }
            for model_id in DEFAULT_MODELS.keys()
        ]
        self._send_json({"object": "list", "data": model_list})

    def _handle_health(self) -> None:
        status = self.auth_manager.get_status()
        self._send_json({
            "service": "Hermes Antigravity Auth Adapter Proxy",
            "status": "healthy" if status["authenticated"] else "unauthenticated",
            "auth": status,
        })

    def _handle_chat_completions(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._send_json({"error": {"message": "Empty request body"}}, status=400)
            return

        body_raw = self.rfile.read(content_length)
        try:
            body = json.loads(body_raw.decode("utf-8"))
        except Exception as e:
            self._send_json({"error": {"message": f"Invalid JSON: {e}"}}, status=400)
            return

        model = body.get("model", "google-antigravity/gemini-3.7-flash")
        messages = body.get("messages", [])
        tools = body.get("tools")
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens")
        stream = body.get("stream", False)

        try:
            result = self.provider.chat_completion(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
            )

            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                for chunk in result:
                    chunk_str = f"data: {json.dumps(chunk)}\n\n"
                    self.wfile.write(chunk_str.encode("utf-8"))
                    self.wfile.flush()

                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self._send_json(result)

        except Exception as e:
            logger.error(f"Error handling chat completion: {e}", exc_info=True)
            self._send_json(
                {"error": {"message": str(e), "type": "antigravity_error"}},
                status=500,
            )

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)


def run_proxy_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    auth_manager: Optional[AGYAuthManager] = None,
) -> None:
    """Starts the OpenAI-compatible HTTP proxy server."""
    mgr = auth_manager or AGYAuthManager()
    AGYProxyHandler.auth_manager = mgr
    AGYProxyHandler.provider = AGYModelProvider(mgr)

    server = http.server.ThreadingHTTPServer((host, port), AGYProxyHandler)
    logger.info(f"Antigravity OpenAI Bridge Proxy listening on http://{host}:{port}/v1")
    print(f"\n[AGY Proxy] Running on http://{host}:{port}")
    print(f"[AGY Proxy] OpenAI endpoint: http://{host}:{port}/v1/chat/completions")
    print(f"[AGY Proxy] Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping AGY Proxy Server...")
    finally:
        server.server_close()
