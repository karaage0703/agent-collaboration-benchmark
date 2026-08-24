#!/usr/bin/env python3
"""Launch Qwen delegation outside the parent turn and wake it with xangi trigger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class AsyncDelegateError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def default_state_root() -> Path:
    base = os.environ.get("WORKSPACE_PATH") or str(Path.home())
    return Path(base).expanduser().resolve() / ".xangi" / "jobs" / "qwen-delegate"


def generate_job_id(workspace: Path) -> str:
    digest = hashlib.sha256(
        f"{workspace}:{time.time_ns()}".encode()
    ).hexdigest()[:8]
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{digest}"


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise AsyncDelegateError(
            "job-id may contain only letters, digits, dot, underscore, and hyphen"
        )
    return job_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Qwen delegation asynchronously and trigger the parent channel on exit."
    )
    parser.add_argument("--workspace", help="Target workspace directory")
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", help="Task text")
    prompt.add_argument("--prompt-file", help="UTF-8 file containing the task")
    parser.add_argument(
        "--channel", help="xangi channel/thread ID to wake on completion"
    )
    parser.add_argument("--platform", default="discord", help="xangi platform")
    parser.add_argument("--source", help="Unique trigger source")
    parser.add_argument("--job-id", help="Stable job identifier")
    parser.add_argument("--state-root", help="Directory that contains job directories")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--backend", choices=("opencode", "local-llm"), default="opencode"
    )
    parser.add_argument("--model")
    parser.add_argument("--opencode-config")
    parser.add_argument("--opencode-command")
    parser.add_argument(
        "--mode", choices=("read-only", "workspace-write"), default="read-only"
    )
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument(
        "--keep-session",
        action="store_true",
        help="Keep the child session after a successful gate for later follow-up",
    )
    parser.add_argument(
        "--gate-file",
        help="JSON gate spec with argv commands and optional allowed_changes",
    )
    parser.add_argument(
        "--repair-attempts",
        type=int,
        default=1,
        help="Follow-up repair attempts after a failed gate (default: 1)",
    )
    parser.add_argument(
        "--transport-retries",
        type=int,
        default=1,
        help="Fresh-session retries after a transient transport failure (default: 1)",
    )
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--trigger-delay",
        type=float,
        default=5.0,
        help="Seconds to wait after exit.json before triggering (default: 5)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--worker-spec", help=argparse.SUPPRESS)
    return parser.parse_args()


def delegation_command(
    args: argparse.Namespace, result_file: Path, events_prefix: Path | None = None
) -> list[str]:
    script = Path(__file__).resolve().with_name("delegate_qwen.py")
    command = [
        sys.executable,
        str(script),
        "--workspace",
        str(Path(args.workspace).expanduser().resolve()),
        "--mode",
        args.mode,
        "--timeout",
        str(args.timeout),
        "--result-file",
        str(result_file),
        "--backend",
        args.backend,
    ]
    if args.prompt is not None:
        command.extend(["--prompt", args.prompt])
    else:
        command.extend(
            ["--prompt-file", str(Path(args.prompt_file).expanduser().resolve())]
        )
    if args.allow_dirty:
        command.append("--allow-dirty")
    if args.session_id:
        command.extend(["--session-id", args.session_id])
    if args.base_url:
        command.extend(["--base-url", args.base_url])
    if args.model:
        command.extend(["--model", args.model])
    if args.opencode_config:
        command.extend(["--opencode-config", args.opencode_config])
    if args.opencode_command:
        command.extend(["--opencode-command", args.opencode_command])
    if events_prefix:
        command.extend(["--events-prefix", str(events_prefix)])
    return command


def trigger_message(job_id: str, job_dir: Path, summary: dict[str, Any]) -> str:
    return (
        f"Qwen委譲ジョブ {job_id} が終了しました。機械gate結果: "
        f"{json.dumps(summary, ensure_ascii=False, separators=(',', ':'))}。"
        f"証拠は {job_dir}。gate_pass=trueなら追加のread/testをせず、この要約だけで"
        "ユーザーへ結果を報告してください。falseなら証拠を確認して原因を報告してください。"
    )


def load_gate(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    gate = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if "pass" in gate and gate.get("commands") and isinstance(gate["commands"][0], dict):
        raise AsyncDelegateError(
            "gate-file looks like a gate result; pass the original argv-based gate spec"
        )
    commands = gate.get("commands")
    if not isinstance(commands, list) or not commands:
        raise AsyncDelegateError("gate-file commands must be a non-empty list")
    for command in commands:
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise AsyncDelegateError(
                "each gate command must be a non-empty argv string list"
            )
    allowed = gate.get("allowed_changes")
    if allowed is not None and (
        not isinstance(allowed, list)
        or not all(isinstance(item, str) for item in allowed)
    ):
        raise AsyncDelegateError("allowed_changes must be a string list")
    ignored = gate.get("ignored_changes")
    if ignored is not None and (
        not isinstance(ignored, list)
        or not all(isinstance(item, str) for item in ignored)
    ):
        raise AsyncDelegateError("ignored_changes must be a string list")
    return gate


def changed_paths(workspace: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return sorted(
        line[3:].strip() for line in completed.stdout.splitlines() if line.strip()
    )


def git_metadata_snapshot(workspace: Path) -> dict[str, str | None]:
    completed = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    common = Path(completed.stdout.strip())
    if not common.is_absolute():
        common = (workspace / common).resolve()
    snapshot: dict[str, str | None] = {}
    for name in ("config", "info/exclude"):
        path = common / name
        snapshot[str(path)] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
    return snapshot


def changed_git_metadata(baseline: dict[str, str | None]) -> list[str]:
    changed = []
    for raw_path, before in baseline.items():
        path = Path(raw_path)
        after = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if after != before:
            changed.append(raw_path)
    return sorted(changed)


def run_gate(workspace: Path, gate: dict[str, Any] | None) -> dict[str, Any]:
    if gate is None:
        return {"pass": False, "reason": "gate_not_configured", "commands": []}
    results = []
    timeout = int(gate.get("timeout_seconds", 600))
    for argv in gate["commands"]:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            result = {
                "argv": argv,
                "exit_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
        except subprocess.TimeoutExpired as error:
            result = {
                "argv": argv,
                "exit_code": 124,
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": str(error.stdout or "")[-2000:],
                "stderr_tail": str(error.stderr or "")[-2000:],
            }
        results.append(result)
    paths = changed_paths(workspace)
    allowed = gate.get("allowed_changes")
    ignored = set(gate.get("ignored_changes", []))
    evaluated_paths = sorted(set(paths) - ignored)
    unexpected = sorted(set(evaluated_paths) - set(allowed)) if allowed is not None else []
    passed = all(item["exit_code"] == 0 for item in results) and not unexpected
    return {
        "pass": passed,
        "commands": results,
        "changed_paths": paths,
        "ignored_changes": sorted(set(paths) & ignored),
        "unexpected_changes": unexpected,
    }


def repair_command(
    base_command: list[str],
    result_file: Path,
    events_prefix: Path,
    session_id: str,
    gate: dict[str, Any],
) -> list[str]:
    command = list(base_command)
    for option in (
        "--prompt",
        "--prompt-file",
        "--result-file",
        "--events-prefix",
        "--session-id",
    ):
        while option in command:
            index = command.index(option)
            del command[index : index + 2]
    if "--allow-dirty" not in command:
        command.append("--allow-dirty")
    failures = [
        {
            "argv": item["argv"],
            "exit_code": item["exit_code"],
            "stderr_tail": item["stderr_tail"],
        }
        for item in gate["commands"]
        if item["exit_code"] != 0
    ]
    prompt = (
        "独立した機械gateが失敗しました。仕様を読み直し、実装とテストを修正し、"
        "以下の失敗を解消してください。commit/pushはしないでください。\n"
        + json.dumps(
            {"failures": failures, "unexpected_changes": gate["unexpected_changes"]},
            ensure_ascii=False,
        )
    )
    command.extend(
        [
            "--prompt",
            prompt,
            "--session-id",
            session_id,
            "--result-file",
            str(result_file),
            "--events-prefix",
            str(events_prefix),
        ]
    )
    return command


def transient_fresh_retry_eligible(result: dict[str, Any]) -> bool:
    """Retry only a clean Local LLM transport failure that has no resumable session."""
    error = str(result.get("error", "")).lower()
    return (
        result.get("backend") == "local-llm"
        and result.get("status") == "error"
        and not result.get("session_id")
        and not result.get("git_status_after")
        and any(marker in error for marker in ("fetch failed", "connection reset", "timed out"))
    )


def fresh_retry_command(
    base_command: list[str], result_file: Path, events_prefix: Path
) -> list[str]:
    command = list(base_command)
    for option in ("--result-file", "--events-prefix", "--session-id"):
        while option in command:
            index = command.index(option)
            del command[index : index + 2]
    command.extend(["--result-file", str(result_file), "--events-prefix", str(events_prefix)])
    return command


def close_child_session(spec: dict[str, Any], session_id: str) -> dict[str, Any]:
    if spec.get("keep_session"):
        return {"status": "kept", "reason": "keep_session_requested"}
    backend = spec.get("backend", "opencode")
    if backend == "opencode":
        command = [
            spec.get("opencode_command") or "opencode",
            "session",
            "delete",
            session_id,
        ]
        env = os.environ.copy()
        if spec.get("opencode_config"):
            env["OPENCODE_CONFIG"] = str(spec["opencode_config"])
        completed = subprocess.run(
            command,
            cwd=spec["workspace"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return {
            "status": "closed" if completed.returncode == 0 else "failed",
            "backend": backend,
            "exit_code": completed.returncode,
            "stderr_tail": completed.stderr[-1000:],
        }
    base_url = str(spec.get("base_url") or "http://127.0.0.1:18888").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/api/sessions/{urllib.parse.quote(session_id, safe='')}/close",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status_code = response.status
        return {
            "status": "closed" if 200 <= status_code < 300 else "failed",
            "backend": backend,
            "http_status": status_code,
        }
    except urllib.error.URLError as error:
        return {"status": "failed", "backend": backend, "error": str(error)}


def run_worker(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    job_dir = spec_path.parent
    started_at = now_iso()
    workspace = Path(spec["workspace"])
    gate_spec = spec.get("gate")
    command = list(spec["command"])
    attempt_records = []
    final_result: dict[str, Any] = {}
    final_gate: dict[str, Any] = {"pass": False, "reason": "not_run", "commands": []}
    completed = subprocess.CompletedProcess(command, 1)
    repair_attempts_left = int(spec.get("repair_attempts", 0))
    transport_retries_left = int(spec.get("transport_retries", 0))
    max_attempts = 1 + repair_attempts_left + transport_retries_left
    for attempt in range(1, max_attempts + 1):
        result_path = job_dir / f"attempt-{attempt:02d}-result.json"
        stdout_path = job_dir / f"attempt-{attempt:02d}.stdout.log"
        stderr_path = job_dir / f"attempt-{attempt:02d}.stderr.log"
        with (
            stdout_path.open("w", encoding="utf-8") as stdout,
            stderr_path.open("w", encoding="utf-8") as stderr,
        ):
            completed = subprocess.run(
                command, stdout=stdout, stderr=stderr, text=True, check=False
            )
        if result_path.exists():
            final_result = json.loads(result_path.read_text(encoding="utf-8"))
        if gate_spec is None:
            delegate_ok = (
                completed.returncode == 0 and final_result.get("status") == "ok"
            )
            final_gate = {
                "pass": delegate_ok,
                "reason": "delegate_result_only",
                "commands": [],
                "changed_paths": changed_paths(workspace),
                "unexpected_changes": [],
            }
        else:
            final_gate = run_gate(workspace, gate_spec)
            if not final_result.get("session_id"):
                final_gate["pass"] = False
                final_gate["reason"] = "delegate_result_missing_session"
        metadata_changes = changed_git_metadata(spec.get("git_metadata_baseline", {}))
        if metadata_changes:
            final_gate["pass"] = False
            final_gate["reason"] = "git_metadata_changed"
            final_gate["git_metadata_changes"] = metadata_changes
        atomic_write_json(job_dir / f"attempt-{attempt:02d}-gate.json", final_gate)
        attempt_records.append(
            {
                "attempt": attempt,
                "delegate_exit_code": completed.returncode,
                "gate_pass": final_gate["pass"],
            }
        )
        if final_gate["pass"]:
            break
        if final_gate.get("git_metadata_changes"):
            break
        session_id = final_result.get("session_id")
        if transport_retries_left > 0 and transient_fresh_retry_eligible(final_result):
            transport_retries_left -= 1
            command = fresh_retry_command(
                spec["command"],
                job_dir / f"attempt-{attempt + 1:02d}-result.json",
                job_dir / f"attempt-{attempt + 1:02d}.events",
            )
            continue
        if gate_spec is None or attempt >= max_attempts or not session_id or repair_attempts_left <= 0:
            break
        repair_attempts_left -= 1
        command = repair_command(
            spec["command"],
            job_dir / f"attempt-{attempt + 1:02d}-result.json",
            job_dir / f"attempt-{attempt + 1:02d}.events",
            str(session_id),
            final_gate,
        )
    atomic_write_json(job_dir / "gate.json", final_gate)
    final_result["gate"] = final_gate
    final_result["attempts"] = attempt_records
    session_id = final_result.get("session_id")
    session_cleanup = {"status": "not_run", "reason": "gate_not_passed"}
    if final_gate["pass"] and session_id:
        session_cleanup = close_child_session(spec, str(session_id))
    final_result["session_cleanup"] = session_cleanup
    atomic_write_json(job_dir / "result.json", final_result)
    outcome = "success" if final_gate["pass"] else "incomplete"
    summary = {
        "status": outcome,
        "gate_pass": final_gate["pass"],
        "attempts": len(attempt_records),
        "duration_seconds": round(
            (
                datetime.now(timezone.utc) - datetime.fromisoformat(started_at)
            ).total_seconds(),
            3,
        ),
        "changed_paths": final_gate.get("changed_paths", []),
        "failed_commands": [
            " ".join(item["argv"])[:160]
            for item in final_gate.get("commands", [])
            if item["exit_code"] != 0
        ],
        "session_id": final_result.get("session_id"),
        "session_cleanup": session_cleanup,
        "cli_exit_code": final_result.get("cli_exit_code"),
    }
    atomic_write_json(job_dir / "summary.json", summary)
    exit_payload = {
        "job_id": spec["job_id"],
        "status": outcome,
        "exit_code": 0 if final_gate["pass"] else 2,
        "started_at": started_at,
        "finished_at": now_iso(),
        "workspace": spec["workspace"],
        "result_file": str(job_dir / "result.json"),
        "summary_file": str(job_dir / "summary.json"),
        "gate_file": str(job_dir / "gate.json"),
    }
    atomic_write_json(job_dir / "exit.json", exit_payload)

    time.sleep(max(0.0, float(spec["trigger_delay"])))
    trigger = subprocess.run(
        [
            "xangi",
            "tool",
            "trigger",
            "--channel",
            spec["channel"],
            "--message",
            trigger_message(spec["job_id"], job_dir, summary),
            "--source",
            spec["source"],
            "--platform",
            spec["platform"],
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    atomic_write_json(
        job_dir / "trigger.json",
        {
            "attempted_at": now_iso(),
            "exit_code": trigger.returncode,
            "stdout": trigger.stdout,
            "stderr": trigger.stderr,
        },
    )
    return 0 if trigger.returncode == 0 else 1


def validate_launch_args(args: argparse.Namespace) -> None:
    missing = []
    if not args.workspace:
        missing.append("--workspace")
    if args.prompt is None and args.prompt_file is None:
        missing.append("--prompt or --prompt-file")
    if not args.channel:
        missing.append("--channel")
    if missing:
        raise AsyncDelegateError(
            "missing required launch arguments: " + ", ".join(missing)
        )
    if args.timeout <= 0:
        raise AsyncDelegateError("timeout must be a positive integer")
    if args.trigger_delay < 0:
        raise AsyncDelegateError("trigger-delay must be zero or greater")
    if args.repair_attempts < 0:
        raise AsyncDelegateError("repair-attempts must be zero or greater")
    if args.transport_retries < 0:
        raise AsyncDelegateError("transport-retries must be zero or greater")


def launch(args: argparse.Namespace) -> int:
    validate_launch_args(args)
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise AsyncDelegateError(f"workspace directory not found: {workspace}")
    job_id = validate_job_id(args.job_id or generate_job_id(workspace))
    state_root = (
        Path(args.state_root).expanduser().resolve()
        if args.state_root
        else default_state_root()
    )
    job_dir = state_root / job_id
    if job_dir.exists():
        raise AsyncDelegateError(f"job directory already exists: {job_dir}")
    source = args.source or f"qwen-delegate-{job_id}"
    gate = load_gate(args.gate_file)
    spec = {
        "job_id": job_id,
        "workspace": str(workspace),
        "channel": str(args.channel),
        "platform": args.platform,
        "source": source,
        "trigger_delay": args.trigger_delay,
        "repair_attempts": args.repair_attempts,
        "transport_retries": args.transport_retries,
        "gate": gate,
        "git_metadata_baseline": git_metadata_snapshot(workspace),
        "backend": args.backend,
        "base_url": args.base_url,
        "opencode_config": args.opencode_config,
        "opencode_command": args.opencode_command,
        "keep_session": args.keep_session,
        "command": delegation_command(
            args, job_dir / "attempt-01-result.json", job_dir / "attempt-01.events"
        ),
    }
    if args.dry_run:
        print(
            json.dumps(
                {"status": "dry-run", "job_dir": str(job_dir), **spec},
                ensure_ascii=False,
            )
        )
        return 0

    job_dir.mkdir(parents=True)
    spec_path = job_dir / "spec.json"
    atomic_write_json(spec_path, spec)
    worker_log = (job_dir / "worker.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker-spec",
            str(spec_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=worker_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    worker_log.close()
    atomic_write_json(
        job_dir / "pid.json",
        {"pid": process.pid, "started_at": now_iso(), "job_id": job_id},
    )
    time.sleep(0.2)
    if process.poll() is not None:
        raise AsyncDelegateError(
            f"worker exited during startup; inspect {job_dir / 'worker.log'}"
        )
    print(
        json.dumps(
            {
                "status": "started",
                "job_id": job_id,
                "job_dir": str(job_dir),
                "pid": process.pid,
                "source": source,
                "exit_file": str(job_dir / "exit.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        if args.worker_spec:
            return run_worker(Path(args.worker_spec).expanduser().resolve())
        return launch(args)
    except (AsyncDelegateError, OSError, json.JSONDecodeError) as error:
        print(f"async qwen delegation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
