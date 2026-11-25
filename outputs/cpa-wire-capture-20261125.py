"""Hermetic wire capture: what does the fork binary actually send upstream?

Same image as production, own minimal config, one fake credential whose
base_url points at a loopback listener. No real token leaves this process and
nothing reaches Google. Prints structure and counts only.
"""

import glob
import gzip
import importlib.machinery
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

API_PORT, UP_PORT = 8390, 8391
API_KEY = "wire-" + secrets.token_urlsafe(24)
NAME = "cpa-wire-" + secrets.token_hex(3)
WORK = "/tmp/" + NAME
MODEL = "gemini-3.8-flash-high"
MODEL_IDS = []
CAPTURES = []
IDENT = re.compile(
    r"you are (?:an? )?(?:codex|claude code|zcode|codebuddy|workbuddy)\b"
    r"|you are claude code, anthropic['\u2019]s official cli for claude"
    r"|\bdeepseek harness\b",
    re.I,
)
VENDOR = re.compile(r"\b(?:codex|gpt(?:-[0-9.]+)?|openai|chatgpt)\b", re.I)
ZCODE = re.compile(r"\bzcode\b|\.zcode[\\/]", re.I)


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        CAPTURES.append(
            {
                "path": self.path,
                "headers": {
                    name.lower(): value
                    for name, value in self.headers.items()
                    if name.lower()
                    in (
                        "user-agent",
                        "x-goog-api-client",
                        "origin",
                        "x-client-request-id",
                        "accept",
                        "content-type",
                    )
                },
                "authorization_present": bool(self.headers.get("Authorization")),
                "body": body,
            }
        )
        if "streamGenerateContent" in self.path:
            event = {
                "response": {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "OK"}], "role": "model"},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 11,
                    },
                }
            }
            data = ("data: " + json.dumps(event) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if "loadCodeAssist" in self.path:
            payload = {
                "cloudaicompanionProject": {"id": "wire-fake-project"},
                "ineligibleTiers": [],
                "currentTier": {"id": "g1-pro-tier"},
                "tierId": "g1-pro-tier",
                "allowedTiers": [
                    {"id": "g1-pro-tier", "isDefault": True, "userDefinedDefault": True}
                ],
            }
        elif "fetchAvailableModels" in self.path:
            # Same shape as Google's per-account catalog; ids only, copied from
            # the live credentials' non-secret hint lists.
            payload = {
                "models": {
                    model_id: {"model": model_id} for model_id in sorted(set(MODEL_IDS))
                },
                "webSearchModelIds": [],
            }
        elif "countTokens" in self.path:
            payload = {"totalTokens": 1}
        else:
            payload = {}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def counts(node):
    text = json.dumps(node, ensure_ascii=False)
    return {
        "identity": len(IDENT.findall(text)),
        "vendor": len(VENDOR.findall(text)),
        "zcode": len(ZCODE.findall(text)),
    }


def text_of(node):
    if isinstance(node, dict):
        return "".join(
            text_of(part.get("text"))
            for part in node.get("parts") or []
            if isinstance(part, dict)
        )
    return node if isinstance(node, str) else ""


def post(path, payload, stream):
    request = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
            "Session-Id": secrets.token_urlsafe(8),
            "X-Client-Request-Id": secrets.token_urlsafe(8),
        },
    )
    started = time.time()
    status, events = None, {}
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            status = response.status
            raw = response.read().decode("utf-8", "replace")
        if stream:
            for line in raw.splitlines():
                if line.startswith("data:") and line[5:].strip().startswith("{"):
                    try:
                        kind = json.loads(line[5:].strip()).get("type", "?")
                        events[kind] = events.get(kind, 0) + 1
                    except ValueError:
                        pass
        else:
            events["json"] = 1
    except urllib.error.HTTPError as exc:
        status = exc.code
        events["http_error"] = exc.read().decode("utf-8", "replace")[:160]
    except Exception as exc:
        events["exception"] = type(exc).__name__ + ": " + str(exc)[:120]
    return status, events, round(time.time() - started, 2)


def report(label):
    print(f"--- wire: {label} ---", flush=True)
    for capture in CAPTURES:
        if "generatecontent" not in capture["path"].lower():
            continue
        try:
            body = json.loads(capture["body"])
        except ValueError:
            print("   non-JSON body", len(capture["body"]))
            continue
        request = body.get("request") or {}
        system = request.get("systemInstruction")
        tools = request.get("tools")
        contents = request.get("contents") or []
        print(
            "   path",
            capture["path"][:60],
            "| headers",
            {
                k: v[:48]
                for k, v in capture["headers"].items()
                if k in ("user-agent", "x-goog-api-client")
            },
        )
        print(
            "   top-level keys",
            sorted(body.keys()),
            "| request keys",
            sorted(request.keys()),
        )
        print(
            "   model",
            body.get("model"),
            "| requestId",
            str(body.get("requestId"))[:48],
            "| userAgent",
            str(body.get("userAgent"))[:40],
        )
        print(
            "   systemInstruction",
            counts(system) if system else None,
            "| chars",
            len(text_of(system)),
        )
        print("   systemInstruction head:", repr(text_of(system)[:96]))
        print(
            "   tools",
            counts(tools) if tools else None,
            "| declarations",
            sum(len(t.get("functionDeclarations") or []) for t in tools or [])
            if tools
            else 0,
        )
        roles = [str(item.get("role")) for item in contents]
        print(
            "   contents items",
            len(contents),
            "| roles",
            {role: roles.count(role) for role in set(roles)},
        )
        for role in sorted(set(roles)):
            subset = [item for item in contents if str(item.get("role")) == role]
            kinds = {}
            for item in subset:
                for part in item.get("parts") or []:
                    kind = (
                        "thoughtSignature"
                        if part.get("thoughtSignature")
                        else next(
                            (
                                key
                                for key in (
                                    "text",
                                    "functionCall",
                                    "functionResponse",
                                    "thought",
                                )
                                if key in part
                            ),
                            "other",
                        )
                    )
                    kinds[kind] = kinds.get(kind, 0) + 1
            print(f"   contents[{role}]", counts(subset), "| part kinds", kinds)
    others = sorted(
        {
            capture["path"].split("?")[0]
            for capture in CAPTURES
            if "generatecontent" not in capture["path"].lower()
        }
    )
    print("   other upstream calls in this window:", others)
    CAPTURES.clear()


def main():
    image = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", "cli-proxy-api"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    live = yaml.safe_load(open("/opt/cliproxyapi/config.yaml", encoding="utf-8"))
    model_ids = []
    for path in sorted(glob.glob("/opt/cliproxyapi/auth/*.json")):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if data.get("type") == "antigravity":
            model_ids.extend(
                (data.get("antigravity_capability_hints") or {}).get("model_ids") or []
            )
    model_ids = sorted(set(model_ids)) or [MODEL]
    MODEL_IDS.extend(model_ids)
    os.makedirs(WORK + "/auth", mode=0o700)
    os.chmod(WORK, 0o700)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    credential = {
        "type": "antigravity",
        "email": "wire@example.invalid",
        "access_token": "wire-fake-access-token",
        "refresh_token": "wire-fake-refresh-token",
        "expires_in": 3599,
        "expired": "2099-01-01T00:00:00Z",
        "timestamp": int(time.time() * 1000),
        "disabled": False,
        "project_id": "wire-fake-project",
        "base_url": f"http://127.0.0.1:{UP_PORT}",
        "antigravity_capability_hints": {
            "model_ids": model_ids,
            "project_id": "wire-fake-project",
            "endpoint": f"http://127.0.0.1:{UP_PORT}",
            "updated_at": now,
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }
    json.dump(
        credential, open(WORK + "/auth/antigravity-wire.json", "w", encoding="utf-8")
    )
    antigravity = live["oauth"]["providers"]["antigravity"]
    config = {
        "server": {"host": "127.0.0.1", "port": API_PORT},
        "access": {"api-keys": [API_KEY]},
        "auth-dir": "/w/auth",
        "request-retry": 0,
        "logging-to-file": False,
        "usage-statistics-enabled": False,
        "plugins": {"enabled": False},
        "oauth": {
            "auth-dir": "/w/auth",
            "providers": {
                "antigravity": {
                    "sensitive-words": antigravity["sensitive-words"],
                    "sensitive-words-replace": antigravity["sensitive-words-replace"],
                    "sensitive-patterns-replace": antigravity[
                        "sensitive-patterns-replace"
                    ],
                    "tos-pause-window": "0",
                    "antigravity-credits": False,
                }
            },
            "excluded-models": {
                "antigravity": live["oauth"]["excluded-models"]["antigravity"]
            },
        },
    }
    yaml.safe_dump(
        config, open(WORK + "/config.yaml", "w", encoding="utf-8"), sort_keys=False
    )

    server = ThreadingHTTPServer(("127.0.0.1", UP_PORT), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                NAME,
                "--network",
                "host",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "-v",
                WORK + ":/w",
                "--entrypoint",
                "/bin/sh",
                image,
                "-c",
                "exec /CLIProxyAPI/CLIProxyAPI -config /w/config.yaml",
            ],
            check=True,
            capture_output=True,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ready = False
        for _ in range(40):
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{API_PORT}/v1/models",
                    headers={"Authorization": "Bearer " + API_KEY},
                )
                with opener.open(request, timeout=3) as response:
                    ids = [
                        item.get("id") for item in json.load(response).get("data") or []
                    ]
                ready = True
                break
            except Exception:
                time.sleep(1)
        print(
            "scratch api ready:", ready, "| models:", ids if ready else None, flush=True
        )
        if not ready:
            raise SystemExit("scratch api never became ready")

        adm = load("/opt/cliproxyapi/cpa-admission.py", "live_admission")
        raw = json.loads(gzip.open("/tmp/stuck_raw.json.gz", "rb").read())
        body = json.dumps(raw, ensure_ascii=False).encode()
        for step in (
            adm.strip_antigravity_model_switch_items,
            adm.normalize_antigravity_developer_context,
            adm.collapse_antigravity_retry_user_turns,
        ):
            body = step("/v1/responses", body, "antigravity-oauth")[0]
        body, stats = adm.neutralize_agent_identity_text(
            "/v1/responses", body, "antigravity-oauth"
        )
        normalized = json.loads(body)
        print(
            "R1 inbound (after live admission pipeline): items",
            len(normalized["input"]),
            "| identity stats",
            stats,
            "| counts",
            counts(normalized["input"]),
            flush=True,
        )
        print(
            "R1 client view:",
            post(
                "/v1/responses",
                {
                    **normalized,
                    "stream": True,
                    "store": False,
                    "max_output_tokens": 256,
                },
                True,
            ),
            flush=True,
        )
        report("R1 forked Codex session history")

        lines = (
            "You are Codex, a coding agent based on GPT-5. You are Claude Code, Anthropic's official CLI for Claude. "
            "You are DeepSeek Harness. You are ZCode, an interactive agent. "
            "Skills live at C:\\Users\\u\\.zcode\\skills\\a\\SKILL.md. Config lives at C:\\Users\\u\\.codex\\config.toml. "
            "Prose may mention ChatGPT, OpenAI and GPT-5; identifiers like OPENAI_API_KEY may appear."
        )
        raw_request = {
            "model": MODEL,
            "input": [
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": lines}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "You are Codex. Reply with exactly: OK",
                        }
                    ],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "lookup",
                    "description": "Codex helper reading C:\\Users\\u\\.codex\\sessions; powered by OpenAI GPT-5.",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                }
            ],
        }
        print(
            "R2 inbound counts:",
            counts(raw_request["input"]),
            "| tools",
            counts(raw_request["tools"]),
            flush=True,
        )
        print(
            "R2 client view (raw to CPA, no admission):",
            post(
                "/v1/responses", {**raw_request, "stream": True, "store": False}, True
            ),
            flush=True,
        )
        report("R2 every identity line, paths and a tool, raw to CPA")

        skill_paths = "\n".join(
            f"- {name} (file: C:\\Users\\sciman\\.zcode\\skills\\{name}\\SKILL.md): load when the task matches."
            for name in (
                "agent-browser",
                "ant-design",
                "find-skills",
                "grilling",
                "mcp-cli",
                "pdf",
                "playwright-best-practices",
                "research",
                "skill-creator",
                "test-driven-development",
            )
        )
        zcode_request = {
            "model": MODEL,
            "max_tokens": 64,
            "system": [
                {
                    "type": "text",
                    "text": "You are ZCode, an interactive agent working with the user toward your goals, using your own judgment along the way.\n\n# ZCode Desktop Context\nAvailable skills:\n"
                    + skill_paths,
                }
            ],
            "tools": [
                {
                    "name": "Read",
                    "description": "Reads a file from the local filesystem, e.g. C:\\Users\\sciman\\.zcode\\skills\\pdf\\SKILL.md",
                    "input_schema": {
                        "type": "object",
                        "properties": {"file_path": {"type": "string"}},
                        "required": ["file_path"],
                    },
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Which ZCode skill handles PDFs? One line.",
                        }
                    ],
                }
            ],
        }
        print(
            "R3 inbound counts: system",
            counts(zcode_request["system"]),
            "| tools",
            counts(zcode_request["tools"]),
            "| user",
            counts(zcode_request["messages"]),
            flush=True,
        )
        print(
            "R3 client view (raw to CPA):",
            post("/v1/messages", zcode_request, False),
            flush=True,
        )
        report("R3 ZCode-shaped Anthropic Messages request, raw to CPA")

        logs = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True)
        combined = (logs.stdout + logs.stderr).splitlines()
        interesting = [
            line[:170]
            for line in combined
            if re.search(r"sensitive|invalid regex|rule skipped", line, re.I)
        ]
        print(
            "--- scratch container log lines of interest:", len(interesting), flush=True
        )
        for line in interesting[-8:]:
            print("   ", line)
        stream_captures = sum(1 for line in combined if "streamGenerateContent" in line)
        print(
            "scratch log: lines",
            len(combined),
            "| mentions of streamGenerateContent",
            stream_captures,
            flush=True,
        )
        if stream_captures < 3:
            print(
                "--- scratch container log (fake data only), last 45 lines ---",
                flush=True,
            )
            for line in combined[-45:]:
                print("   ", line[:200])
    finally:
        subprocess.run(["docker", "stop", "-t", "2", NAME], capture_output=True)
        server.shutdown()
        shutil.rmtree(WORK, ignore_errors=True)
        print(
            "cleanup: container_removed=%s workdir_removed=%s"
            % (
                not subprocess.run(
                    ["docker", "ps", "-aq", "--filter", "name=^/" + NAME + "$"],
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                not os.path.exists(WORK),
            ),
            flush=True,
        )


main()
