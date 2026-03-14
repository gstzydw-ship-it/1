from __future__ import annotations

import argparse
import json
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "openclaw-ui"
WSL_DISTRO = "Ubuntu"
AGENT_ID = "video-agent-system"
GATEWAY_PORT = 18789
DASHBOARD_URL = f"http://127.0.0.1:{GATEWAY_PORT}/"


def to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix().split(":/", 1)[1]
    return f"/mnt/{drive}/{tail}"


ROOT_WSL = to_wsl_path(ROOT)
OPENCLAW_WSL_SCRIPT = f"{ROOT_WSL}/scripts/openclaw-wsl.sh"


def run_process(command: list[str], *, cwd: Path | None = None, timeout: int = 120) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": command,
    }


def run_wsl_openclaw(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    arg_text = " ".join(shlex.quote(item) for item in args)
    bash_script = f"cd {shlex.quote(ROOT_WSL)} && {shlex.quote(OPENCLAW_WSL_SCRIPT)} {arg_text}".strip()
    return run_process(["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", bash_script], timeout=timeout)


def extract_last_json_blob(text: str) -> Any | None:
    if not text:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
            return payload
        except json.JSONDecodeError:
            continue
    return None


def gateway_reachable(port: int = GATEWAY_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8):
            return True
    except OSError:
        try:
            with urllib.request.urlopen(DASHBOARD_URL, timeout=1.5) as response:
                return 200 <= response.status < 500
        except OSError:
            probe = run_process(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"try {{ "
                        f"$response = Invoke-WebRequest -UseBasicParsing '{DASHBOARD_URL}' -TimeoutSec 3; "
                        f"[int]$response.StatusCode "
                        f"}} catch {{ "
                        f"if ($_.Exception.Response) {{ [int]$_.Exception.Response.StatusCode.value__ }} else {{ exit 1 }} "
                        f"}}"
                    ),
                ],
                timeout=5,
            )
            try:
                status_code = int((probe["stdout"] or "").strip())
            except ValueError:
                return False
            return 200 <= status_code < 500


def gateway_listening_in_wsl(port: int = GATEWAY_PORT) -> bool:
    probe = run_process(
        [
            "wsl",
            "-d",
            WSL_DISTRO,
            "--",
            "bash",
            "-lc",
            f"ss -ltn | grep '127.0.0.1:{port}'",
        ],
        timeout=10,
    )
    return probe["ok"] and bool((probe["stdout"] or "").strip())


def list_recent_videos(limit: int = 8) -> list[dict[str, Any]]:
    video_root = ROOT / "outputs" / "videos"
    if not video_root.exists():
        return []
    candidates = sorted(
        (
            path
            for path in video_root.iterdir()
            if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    recent: list[dict[str, Any]] = []
    for path in candidates[:limit]:
        stat = path.stat()
        recent.append(
            {
                "name": path.name,
                "path": str(path),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
            }
        )
    return recent


def git_output(*args: str) -> str:
    result = run_process(["git", *args], cwd=ROOT, timeout=30)
    return (result["stdout"] or result["stderr"]).strip()


def get_git_summary() -> dict[str, Any]:
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD")
    status = git_output("status", "--short")
    backup_branch = git_output("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/codex/backup-before-openclaw-20260314")
    return {
        "branch": branch,
        "status": status.splitlines() if status else [],
        "backup_branch": backup_branch or "origin/codex/backup-before-openclaw-20260314",
    }


def get_openclaw_summary() -> dict[str, Any]:
    version_result = run_wsl_openclaw(["--version"], timeout=30)
    version = (version_result["stdout"] or "").strip().splitlines()
    agents_result = run_wsl_openclaw(["agents", "list", "--json"], timeout=60)
    agents_payload = extract_last_json_blob(agents_result["stdout"])
    agents = agents_payload if isinstance(agents_payload, list) else []
    target_agent = next((item for item in agents if item.get("id") == AGENT_ID), None)
    gateway_online = gateway_reachable() or gateway_listening_in_wsl()
    return {
        "version": version[-1] if version else "unknown",
        "installed": version_result["ok"],
        "gateway_reachable": gateway_online,
        "dashboard_url": DASHBOARD_URL,
        "agent_id": AGENT_ID,
        "agent": target_agent,
        "agents": agents,
    }


def build_status() -> dict[str, Any]:
    return {
        "project_root": str(ROOT),
        "workspace_path": str(ROOT / ".openclaw-workspace"),
        "git": get_git_summary(),
        "openclaw": get_openclaw_summary(),
        "recent_videos": list_recent_videos(),
        "commands": [
            'powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 status',
            'powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agent --agent video-agent-system --message "检查当前即梦工作流" --json',
            'powershell -ExecutionPolicy Bypass -File scripts/openclaw-ui.ps1',
            'python -m app.cli doctor',
            'python -m app.cli test-prompt-composer',
        ],
    }


def action_result(title: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": title,
        "ok": result["ok"],
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "command": result["command"],
    }


def run_action(name: str) -> dict[str, Any]:
    if name == "doctor":
        return action_result("项目体检", run_process([sys.executable, "-m", "app.cli", "doctor"], cwd=ROOT))
    if name == "test_asset_planner":
        return action_result(
            "素材规划测试",
            run_process([sys.executable, "-m", "app.cli", "test-asset-planner"], cwd=ROOT),
        )
    if name == "test_prompt_composer":
        return action_result(
            "提示词组装测试",
            run_process([sys.executable, "-m", "app.cli", "test-prompt-composer"], cwd=ROOT),
        )
    if name == "openclaw_status":
        return action_result("OpenClaw 状态", run_wsl_openclaw(["status"], timeout=120))
    if name == "open_outputs":
        subprocess.Popen(["explorer.exe", str(ROOT / "outputs" / "videos")])
        return {
            "title": "打开视频输出目录",
            "ok": True,
            "returncode": 0,
            "stdout": str(ROOT / "outputs" / "videos"),
            "stderr": "",
            "command": ["explorer.exe", str(ROOT / "outputs" / "videos")],
        }
    raise KeyError(name)


def run_agent_prompt(message: str) -> dict[str, Any]:
    result = run_wsl_openclaw(
        ["agent", "--agent", AGENT_ID, "--message", message, "--json"],
        timeout=600,
    )
    payload = extract_last_json_blob(result["stdout"])
    return {
        "title": "OpenClaw Agent 回复",
        "ok": result["ok"],
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "command": result["command"],
        "payload": payload,
    }


class ControlHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._send_json(build_status())
                return
            if parsed.path == "/favicon.ico":
                self.path = "/favicon.svg"
                return super().do_GET()
            if parsed.path == "/":
                self.path = "/index.html"
            return super().do_GET()
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {}

            if parsed.path == "/api/action":
                action = str(payload.get("action") or "").strip()
                if not action:
                    self._send_json({"ok": False, "error": "缺少 action 参数。"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(run_action(action))
                except KeyError:
                    self._send_json({"ok": False, "error": f"未知操作：{action}"}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/agent":
                message = str(payload.get("message") or "").strip()
                if not message:
                    self._send_json({"ok": False, "error": "消息不能为空。"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(run_agent_prompt(message))
                return

            self._send_json({"ok": False, "error": "接口不存在。"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地 OpenClaw 控制台。")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ControlHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"OpenClaw 本地控制台已启动：{url}")

    if args.open_browser:
        time.sleep(0.3)
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
