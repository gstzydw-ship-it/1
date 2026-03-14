from __future__ import annotations

import argparse
import json
import shlex
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
OPENCLAW_PS1 = ROOT / "scripts" / "openclaw.ps1"
TASK_HISTORY_PATH = ROOT / "data" / "openclaw_task_history.jsonl"


def clean_process_output(text: str) -> str:
    if not text:
        return ""

    normalized = text.replace("\x00", "")
    kept: list[str] = []
    for line in normalized.splitlines():
        lowered = line.strip().lower()
        if "localhost" in lowered and ("wsl" in lowered or "nat" in lowered):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


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
        "stdout": clean_process_output(completed.stdout),
        "stderr": clean_process_output(completed.stderr),
        "command": command,
    }


def run_wsl_openclaw(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    arg_text = " ".join(shlex.quote(item) for item in args)
    bash_script = f"cd {shlex.quote(ROOT_WSL)} && {shlex.quote(OPENCLAW_WSL_SCRIPT)} {arg_text}".strip()
    return run_process(["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", bash_script], timeout=timeout)


def run_windows_openclaw(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    result = run_process(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(OPENCLAW_PS1), *args],
        cwd=ROOT,
        timeout=timeout,
    )
    lowered_stderr = (result["stderr"] or "").lower()
    if result["ok"] and "localhost" in lowered_stderr and "wsl" in lowered_stderr:
        result["stderr"] = ""
    return result


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


def extract_agent_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    payloads = payload.get("payloads")
    if not isinstance(payloads, list):
        return ""

    texts: list[str] = []
    for item in payloads:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n\n".join(texts)


def summarize_text(text: str, *, limit: int = 180) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1].rstrip()}…"


def append_task_history(entry: dict[str, Any]) -> None:
    TASK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TASK_HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False))
        handle.write("\n")


def read_task_history(limit: int = 10) -> list[dict[str, Any]]:
    if not TASK_HISTORY_PATH.exists():
        return []

    items: list[dict[str, Any]] = []
    for line in TASK_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return list(reversed(items[-limit:]))


def dashboard_direct_accessible() -> bool:
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
            timeout=8,
        )
        if not probe["ok"]:
            return False
        try:
            status_code = int((probe["stdout"] or "").strip())
        except ValueError:
            return False
        return 200 <= status_code < 500


def gateway_running_in_wsl(port: int = GATEWAY_PORT) -> bool:
    service_probe = run_process(
        [
            "wsl",
            "-d",
            WSL_DISTRO,
            "--",
            "bash",
            "-lc",
            "systemctl --user is-active openclaw-gateway",
        ],
        timeout=10,
    )
    if service_probe["ok"] and (service_probe["stdout"] or "").strip() == "active":
        return True

    port_probe = run_process(
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
    return port_probe["ok"] and bool((port_probe["stdout"] or "").strip())


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
    backup_branch = git_output(
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/remotes/origin/codex/backup-before-openclaw-20260314",
    )
    return {
        "branch": branch,
        "status": status.splitlines() if status else [],
        "backup_branch": backup_branch or "origin/codex/backup-before-openclaw-20260314",
    }


def get_openclaw_summary() -> dict[str, Any]:
    version_result = run_wsl_openclaw(["--version"], timeout=30)
    version_lines = (version_result["stdout"] or "").strip().splitlines()

    agents_result = run_wsl_openclaw(["agents", "list", "--json"], timeout=60)
    agents_payload = extract_last_json_blob(agents_result["stdout"])
    agents = agents_payload if isinstance(agents_payload, list) else []
    target_agent = next((item for item in agents if item.get("id") == AGENT_ID), None)

    return {
        "version": version_lines[-1] if version_lines else "unknown",
        "installed": version_result["ok"],
        "gateway_running": gateway_running_in_wsl(),
        "dashboard_direct_accessible": dashboard_direct_accessible(),
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
        "recent_tasks": read_task_history(),
        "recent_videos": list_recent_videos(),
        "commands": [
            "powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 status",
            "powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 dashboard --no-open",
            'powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agent --local --agent video-agent-system --message "\u68c0\u67e5\u5f53\u524d\u5373\u68a6\u5de5\u4f5c\u6d41" --json',
            "powershell -ExecutionPolicy Bypass -File scripts/openclaw-ui.ps1",
            "python -m app.cli doctor",
            "python -m app.cli test-prompt-composer",
        ],
    }


def action_result(action: str, result: dict[str, Any], *, message: str = "") -> dict[str, Any]:
    return {
        "action": action,
        "ok": result["ok"],
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "command": result["command"],
        "message": message,
    }


def run_action(name: str) -> dict[str, Any]:
    if name == "doctor":
        return action_result(name, run_process([sys.executable, "-m", "app.cli", "doctor"], cwd=ROOT))

    if name == "test_asset_planner":
        return action_result(
            name,
            run_process([sys.executable, "-m", "app.cli", "test-asset-planner"], cwd=ROOT),
        )

    if name == "test_prompt_composer":
        return action_result(
            name,
            run_process([sys.executable, "-m", "app.cli", "test-prompt-composer"], cwd=ROOT),
        )

    if name == "openclaw_status":
        return action_result(name, run_wsl_openclaw(["status"], timeout=120))

    if name == "open_outputs":
        target = str(ROOT / "outputs" / "videos")
        subprocess.Popen(["explorer.exe", target])
        return {
            "action": name,
            "ok": True,
            "returncode": 0,
            "stdout": target,
            "stderr": "",
            "command": ["explorer.exe", target],
            "message": "Video output folder opened in Explorer.",
        }

    if name == "copy_dashboard_url":
        return action_result(
            name,
            run_windows_openclaw(["dashboard", "--no-open"], timeout=120),
            message=(
                "Copied the official Dashboard URL with the current token to the clipboard. "
                "If the page says token mismatch, reopen it with the fresh link or paste the token in Control UI settings."
            ),
        )

    if name == "open_dashboard":
        return action_result(
            name,
            run_windows_openclaw(["dashboard"], timeout=120),
            message=(
                "Tried to open the official Dashboard with the current token and copied the link to the clipboard. "
                "If the page still shows token mismatch, reopen it from the copied link."
            ),
        )

    raise KeyError(name)


def run_agent_prompt(message: str) -> dict[str, Any]:
    result = run_wsl_openclaw(
        ["agent", "--local", "--agent", AGENT_ID, "--message", message, "--json"],
        timeout=600,
    )
    payload = extract_last_json_blob(result["stdout"])
    reply_text = extract_agent_text(payload)
    history_entry = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
        "ok": result["ok"],
        "returncode": result["returncode"],
        "summary": summarize_text(reply_text or result["stderr"] or result["stdout"] or "No reply captured."),
        "reply_preview": summarize_text(reply_text, limit=320),
    }
    append_task_history(history_entry)
    return {
        "action": "agent_prompt",
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
                    self._send_json({"ok": False, "error": "Missing action."}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(run_action(action))
                except KeyError:
                    self._send_json(
                        {"ok": False, "error": f"Unknown action: {action}"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                return

            if parsed.path == "/api/agent":
                message = str(payload.get("message") or "").strip()
                if not message:
                    self._send_json({"ok": False, "error": "Message is required."}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(run_agent_prompt(message))
                return

            self._send_json({"ok": False, "error": "Not found."}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def end_headers(self) -> None:
        # Keep the local console UI fresh so browser tabs do not stick to stale JS/CSS.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local OpenClaw control console.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ControlHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"OpenClaw local console ready at {url}")

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
