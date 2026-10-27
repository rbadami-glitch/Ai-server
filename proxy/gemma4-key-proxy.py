import os
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ["GEMMA_API_KEY"]
DEFAULT_UPSTREAM = os.environ.get("GEMMA_UPSTREAM", "http://127.0.0.1:11435")
OLLAMA_UPSTREAM = "http://127.0.0.1:11434"
CODESTRAL_MODEL = "codestral:22b"
DEVSTRAL_MODEL = "devstral:latest"
OLLAMA_MODELS = {CODESTRAL_MODEL, DEVSTRAL_MODEL}

class Proxy(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            self.send_models()
        else:
            self.forward(DEFAULT_UPSTREAM)

    def do_POST(self):
      length = int(self.headers.get("Content-Length", "0") or "0")
      body = self.rfile.read(length) if length else b""
      upstream = DEFAULT_UPSTREAM

      try:
          payload = json.loads(body.decode("utf-8")) if body else {}
          model = payload.get("model")
          tools = payload.get("tools") or []

          with open(r"C:\AI\service\devstral-debug.jsonl", "a", encoding="utf-8") as f:
              f.write(json.dumps({
                  "model": model,
                  "path": self.path,
                  "has_tools": bool(tools),
                  "tool_count": len(tools),
                  "tool_names": [
                      t.get("function", {}).get("name")
                      for t in tools
                      if t.get("type") == "function"
                  ],
                  "tool_choice": payload.get("tool_choice")
              }) + "\n")

          if model == "gemma4:31b" or model in OLLAMA_MODELS:
              upstream = OLLAMA_UPSTREAM

              if model == DEVSTRAL_MODEL:
                  payload.setdefault("messages", []).insert(0, {
                      "role": "system",
                      "content": (
                          "You are running inside Visual Studio Copilot Agent mode. "
                          "If file tools are available, use them directly. "
                          "When the user asks to create or edit a file, call the appropriate tool. "
                          "Do not say you cannot create files. Do not give manual steps."
                      )
                  })

                  if tools:
                      payload["tool_choice"] = "required"

                  body = json.dumps(payload).encode("utf-8")

      except Exception:
          pass

      self.forward(upstream, body)

    def is_authorized(self):
        return self.headers.get("Authorization", "") == "Bearer " + API_KEY

    def unauthorized(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"unauthorized"}')

    def send_models(self):
        if not self.is_authorized():
            return self.unauthorized()
        try:
            req = urllib.request.Request(DEFAULT_UPSTREAM + "/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            data = {"object": "list", "data": []}
        ids = {item.get("id") for item in data.get("data", [])}
        for model in OLLAMA_MODELS:
            if model not in ids:
               data.setdefault("data", []).append({
                 "id": model,
                 "object": "model",
                 "created": 0,
                 "owned_by": "ollama",
                 "capabilities": ["tools"],
                 "supports_tools": True,
                 "supportsToolCalling": True,
                 "tool_calling": True,
                 "tools": True,
                 "context_length": 131072,
                 "max_tokens": 8192
              })
        out = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def forward(self, upstream, body=None):
        if not self.is_authorized():
            return self.unauthorized()
        if body is None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else None
        req = urllib.request.Request(upstream + self.path, data=body, method=self.command)
        req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                content = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(content)
        except urllib.error.HTTPError as err:
            self.send_response(err.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err.read())
        except Exception as err:
            out = json.dumps({"error": str(err)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

ThreadingHTTPServer(("0.0.0.0", 18002), Proxy).serve_forever()
