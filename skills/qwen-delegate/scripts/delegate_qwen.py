#!/usr/bin/env python3
"""Delegate one task to xangi's selected Qwen backend in a workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:18888"
DEFAULT_BACKEND = "opencode"
DEFAULT_MODEL = "dspark/qwen3.8-27b"


class DelegateError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a child Qwen session through xangi Web API in a selected workspace."
    )
    parser.add_argument(
        "--workspace", required=True, help="Existing workspace directory"
    )
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt", help="Task text")
    prompt.add_argument("--prompt-file", help="UTF-8 file containing the task")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("QWEN_DELEGATE_BASE_URL", DEFAULT_BASE_URL),
        help=f"xangi Web URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--backend",
        choices=("opencode", "local-llm"),
        default=os.environ.get("QWEN_DELEGATE_BACKEND", DEFAULT_BACKEND),
        help=f"xangi agent backend (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("QWEN_DELEGATE_MODEL", DEFAULT_MODEL),
        help=f"Local LLM model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--opencode-config",
        default=os.environ.get("OPENCODE_CONFIG"),
        help="OpenCode config path; when set with --backend opencode, run CLI directly",
    )
    parser.add_argument(
        "--opencode-command",
        default=os.environ.get("OPENCODE_COMMAND"),
        help="OpenCode executable path (default: PATH or ~/.opencode/bin/opencode)",
    )
    parser.add_argument(
        "--mode",
        choices=("read-only", "workspace-write"),
        default="read-only",
        help="Child capability mode (default: read-only)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow workspace-write in a Git worktree with existing changes",
    )
    parser.add_argument(
        "--session-id",
        help="Continue an existing xangi Web session instead of creating a new one",
    )
    parser.add_argument(
        "--result-file",
        help="Also write the final JSON result to this path",
    )
    parser.add_argument(
        "--events-prefix",
        help="Write raw OpenCode stdout/stderr to <prefix>.stdout.jsonl and <prefix>.stderr.jsonl",
    )
    parser.add_argument(
        "--timeout", type=int, default=1800, help="HTTP timeout seconds"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the dispatch plan",
    )
    return parser.parse_args()


def request_json(
    base_url: str, path: str, method: str = "GET", body: Any = None
) -> Any:
    payload = (
        None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    )
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise DelegateError(
            f"{method} {path} failed: HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise DelegateError(
            f"xangi Web API is unavailable at {base_url}: {error.reason}"
        ) from error


def resolve_workspace(raw_path: str) -> Path:
    workspace = Path(raw_path).expanduser().resolve()
    if not workspace.is_dir():
        raise DelegateError(f"workspace directory not found: {workspace}")
    return workspace


def git_status(workspace: Path) -> list[str] | None:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def validate_write_workspace(workspace: Path, allow_dirty: bool) -> None:
    status = git_status(workspace)
    if status is None:
        raise DelegateError("workspace-write requires a Git worktree")
    if status and not allow_dirty:
        preview = "\n".join(status[:20])
        raise DelegateError(
            "workspace has existing changes; use a dedicated clean worktree or pass "
            f"--allow-dirty explicitly:\n{preview}"
        )


def stable_names(workspace: Path) -> tuple[str, str]:
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:8]
    basename = workspace.name or "workspace"
    return f"Delegate {basename} {digest}", f"Qwen Delegate · {basename} · {digest}"


def ensure_workspace(base_url: str, workspace: Path, name: str) -> dict[str, Any]:
    data = request_json(base_url, "/api/workspaces")
    for item in data.get("workspaces", []):
        try:
            registered = Path(str(item.get("path", ""))).resolve()
        except OSError:
            continue
        if registered == workspace:
            return item
    return request_json(
        base_url,
        "/api/workspaces",
        method="POST",
        body={"name": name, "path": str(workspace)},
    )["workspace"]


def project_prompt(mode: str) -> str:
    capability = (
        "調査・読取だけを行い、ファイルを変更しない。"
        if mode == "read-only"
        else "依頼範囲の実装・対象テストまで行ってよい。commit、push、mergeは行わない。"
    )
    return (
        "あなたは親エージェントから委譲された子Qwenです。"
        "現在のworkspaceだけを対象にし、既存変更を尊重してください。"
        f"{capability} "
        "最終回答には、実施内容、変更ファイル、テスト結果、未解決事項を簡潔に含めてください。"
    )


def ensure_project(
    base_url: str,
    name: str,
    workspace_id: str,
    model: str,
    mode: str,
    backend: str,
) -> dict[str, Any]:
    desired = {
        "name": name,
        "prompt": project_prompt(mode),
        "backend": backend,
        "model": model,
        "effort": None,
        "workspaceId": workspace_id,
    }
    projects = request_json(base_url, "/api/projects").get("projects", [])
    existing = next((item for item in projects if item.get("name") == name), None)
    if existing is None:
        return request_json(base_url, "/api/projects", method="POST", body=desired)[
            "project"
        ]
    mismatch = any(
        existing.get(key) != value for key, value in desired.items() if key != "name"
    )
    if mismatch:
        project_id = urllib.parse.quote(str(existing["id"]), safe="")
        return request_json(
            base_url, f"/api/projects/{project_id}", method="PATCH", body=desired
        )["project"]
    return existing


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        text = args.prompt
    else:
        text = Path(args.prompt_file).read_text(encoding="utf-8")
    text = text.strip()
    if not text:
        raise DelegateError("prompt is empty")
    return text


def completion_issues(
    mode: str, response: str, status_after: list[str] | None
) -> list[str]:
    issues: list[str] = []
    normalized = response.strip().lower()
    if "maximum tool rounds reached" in normalized:
        issues.append("tool_round_limit")
    if mode == "workspace-write" and status_after is not None and not meaningful_changes(
        status_after
    ):
        issues.append("no_workspace_changes")
    return issues


def meaningful_changes(status: list[str]) -> list[str]:
    ignored_prefixes = ("logs/", "__pycache__/", ".pytest_cache/", ".xangi/")
    return [
        line
        for line in status
        if not line[3:].strip().startswith(ignored_prefixes)
    ]


def emit_result(payload: dict[str, Any], result_file: str | None) -> None:
    encoded = json.dumps(payload, ensure_ascii=False)
    if result_file:
        destination = Path(result_file).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


def run_opencode_cli(
    workspace: Path,
    prompt: str,
    model: str,
    config: str,
    command_override: str | None,
    session_id: str | None,
    timeout: int,
    events_prefix: str | None = None,
) -> dict[str, Any]:
    executable = command_override or shutil.which("opencode")
    if executable is None:
        candidate = Path.home() / ".opencode" / "bin" / "opencode"
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        raise DelegateError("OpenCode executable not found")
    config_path = Path(config).expanduser().resolve()
    if not config_path.is_file():
        raise DelegateError(f"OpenCode config not found: {config_path}")
    command = [
        executable,
        "run",
        "--format",
        "json",
        "--agent",
        "build",
        "--auto",
        "--dir",
        str(workspace),
        "--model",
        model,
    ]
    if session_id:
        command.extend(["--session", session_id])
    command.append(prompt)
    environment = os.environ.copy()
    environment["OPENCODE_CONFIG"] = str(config_path)
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise DelegateError(f"OpenCode timed out after {timeout}s") from error

    if events_prefix:
        prefix = Path(events_prefix).expanduser().resolve()
        prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix(".stdout.jsonl").write_text(completed.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.jsonl").write_text(completed.stderr, encoding="utf-8")

    response_parts: list[str] = []
    resolved_session_id = session_id or ""
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    event_count = 0
    parse_errors = 0
    provider_errors: list[str] = []
    for line in (completed.stdout + "\n" + completed.stderr).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                parse_errors += 1
            continue
        event_count += 1
        resolved_session_id = str(
            event.get("sessionID") or event.get("part", {}).get("sessionID") or resolved_session_id
        )
        part = event.get("part") or {}
        if event.get("type") == "error":
            provider_errors.append(
                str(
                    event.get("error", {}).get("data", {}).get("message")
                    or event.get("error")
                    or "OpenCode provider error"
                )
            )
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            response_parts.append(part["text"])
        if event.get("type") == "step_finish" and isinstance(part.get("tokens"), dict):
            tokens = part["tokens"]
            usage["input_tokens"] += int(tokens.get("input") or 0)
            usage["output_tokens"] += int(tokens.get("output") or 0)
            cache = tokens.get("cache") or {}
            usage["cached_input_tokens"] += int(cache.get("read") or 0)
    if not resolved_session_id:
        detail = completed.stderr.strip()[-2000:] or completed.stdout[-2000:].strip()
        raise DelegateError(
            f"OpenCode stream ended without a session ID (exit {completed.returncode}): {detail}"
        )
    return {
        "response": "".join(response_parts),
        "session_id": resolved_session_id,
        "usage": usage,
        "event_count": event_count,
        "parse_errors": parse_errors,
        "cli_exit_code": completed.returncode,
        "cli_stderr_tail": completed.stderr.strip()[-2000:],
        "provider_errors": provider_errors,
    }


def parse_sse(response: Any) -> dict[str, Any]:
    event_name = "message"
    data_lines: list[str] = []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if not data_lines:
                event_name = "message"
                continue
            payload = json.loads("\n".join(data_lines))
            if event_name == "done":
                return payload
            if event_name == "error":
                raise DelegateError(str(payload.get("message") or payload))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    raise DelegateError("xangi chat stream ended without a done event")


def run_chat(
    base_url: str, session_id: str, prompt: str, mode: str, timeout: int
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "appSessionId": session_id,
            "message": prompt,
            "skipPermissions": mode == "workspace-write",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_sse(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise DelegateError(
            f"POST /api/chat failed: HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise DelegateError(
            f"Qwen delegation transport failed: {error.reason}"
        ) from error


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    try:
        if args.timeout <= 0:
            raise DelegateError("timeout must be a positive integer")
        workspace = resolve_workspace(args.workspace)
        prompt = read_prompt(args)
        if args.mode == "workspace-write":
            validate_write_workspace(workspace, args.allow_dirty)
        status_before = git_status(workspace)
        workspace_name, project_name = stable_names(workspace)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "dry-run",
                        "workspace": str(workspace),
                        "project_name": project_name,
                        "backend": args.backend,
                        "model": args.model,
                        "mode": args.mode,
                        "prompt_chars": len(prompt),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        registered = None
        project = None
        session_id = args.session_id
        if args.backend == "opencode" and args.opencode_config:
            result = run_opencode_cli(
                workspace,
                prompt,
                args.model,
                args.opencode_config,
                args.opencode_command,
                session_id,
                args.timeout,
                args.events_prefix,
            )
            session_id = str(result["session_id"])
        else:
            registered = ensure_workspace(args.base_url, workspace, workspace_name)
            project = ensure_project(
                args.base_url,
                project_name,
                str(registered["id"]),
                args.model,
                args.mode,
                args.backend,
            )
            if session_id is None:
                session = request_json(
                    args.base_url,
                    "/api/sessions",
                    method="POST",
                    body={"projectId": project["id"]},
                )
                session_id = str(session["sessionId"])
            result = run_chat(
                args.base_url, session_id, prompt, args.mode, args.timeout
            )
        response = str(result.get("response", ""))
        status_after = git_status(workspace)
        issues = completion_issues(args.mode, response, status_after)
        if result.get("cli_exit_code") not in (None, 0):
            issues.append("opencode_nonzero_exit")
        if result.get("provider_errors"):
            issues.append("provider_error")
        payload = {
            "status": "incomplete" if issues else "ok",
            "workspace": str(workspace),
            "workspace_id": registered["id"] if registered else None,
            "project_id": project["id"] if project else None,
            "session_id": session_id,
            "continued": args.session_id is not None,
            "model": args.model,
            "backend": args.backend,
            "mode": args.mode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "response": response,
            "usage": result.get("usage"),
            "cli_exit_code": result.get("cli_exit_code"),
            "cli_stderr_tail": result.get("cli_stderr_tail"),
            "event_count": result.get("event_count"),
            "parse_errors": result.get("parse_errors"),
            "provider_errors": result.get("provider_errors"),
            "completion_issues": issues,
            "git_status_before": status_before,
            "git_status_after": status_after,
            "meaningful_changes": (
                meaningful_changes(status_after) if status_after is not None else None
            ),
        }
        emit_result(payload, args.result_file)
        return 2 if issues else 0
    except (DelegateError, OSError, json.JSONDecodeError) as error:
        if args.result_file:
            emit_result(
                {
                    "status": "error",
                    "workspace": str(Path(args.workspace).expanduser().resolve()),
                    "backend": args.backend,
                    "model": args.model,
                    "mode": args.mode,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error": str(error),
                    "git_status_after": git_status(Path(args.workspace).expanduser().resolve()),
                },
                args.result_file,
            )
        print(f"qwen delegation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
