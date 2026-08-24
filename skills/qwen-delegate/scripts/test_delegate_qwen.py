import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import delegate_qwen
import delegate_qwen_async


class DelegateQwenTest(unittest.TestCase):
    def test_stable_names_depend_on_real_path(self):
        with tempfile.TemporaryDirectory() as directory:
            first = delegate_qwen.stable_names(Path(directory).resolve())
            second = delegate_qwen.stable_names(Path(directory).resolve())
        self.assertEqual(first, second)
        self.assertIn(Path(directory).name, first[1])

    def test_parse_sse_returns_done_payload(self):
        stream = io.BytesIO(
            b'event: text\ndata: {"fullText":"working"}\n\n'
            b'event: done\ndata: {"response":"finished","usage":{"duration_ms":10}}\n\n'
        )
        result = delegate_qwen.parse_sse(stream)
        self.assertEqual(result["response"], "finished")

    def test_parse_sse_raises_for_error(self):
        stream = io.BytesIO(b'event: error\ndata: {"message":"boom"}\n\n')
        with self.assertRaisesRegex(delegate_qwen.DelegateError, "boom"):
            delegate_qwen.parse_sse(stream)

    def test_project_prompt_separates_modes(self):
        self.assertIn("ファイルを変更しない", delegate_qwen.project_prompt("read-only"))
        writable = delegate_qwen.project_prompt("workspace-write")
        self.assertIn("実装", writable)
        self.assertIn("push", writable)

    def test_completion_issues_detect_no_workspace_changes(self):
        self.assertEqual(
            delegate_qwen.completion_issues("workspace-write", "done", []),
            ["no_workspace_changes"],
        )

    def test_completion_issues_ignore_runtime_artifacts(self):
        self.assertEqual(
            delegate_qwen.completion_issues(
                "workspace-write", "done", ["?? logs/", "?? __pycache__/"]
            ),
            ["no_workspace_changes"],
        )

    def test_meaningful_changes_keep_source_files(self):
        self.assertEqual(
            delegate_qwen.meaningful_changes(
                ["?? logs/", "?? __pycache__/", "?? beat.py"]
            ),
            ["?? beat.py"],
        )

    def test_completion_issues_detect_tool_round_limit(self):
        self.assertEqual(
            delegate_qwen.completion_issues(
                "read-only", "Maximum tool rounds reached.", None
            ),
            ["tool_round_limit"],
        )

    def test_emit_result_writes_same_json_as_stdout(self):
        payload = {"status": "ok", "session_id": "session-1"}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "result.json"
            delegate_qwen.emit_result(payload, str(destination))
            self.assertEqual(json.loads(destination.read_text()), payload)

    def test_opencode_nonzero_exit_preserves_events_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "opencode.json"
            config.write_text("{}", encoding="utf-8")
            event = json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses-1",
                    "part": {"text": "finished"},
                }
            )
            completed = __import__("subprocess").CompletedProcess(
                ["opencode"], 1, "", event + "\ntrailing warning\n"
            )
            with mock.patch.object(
                delegate_qwen.subprocess, "run", return_value=completed
            ):
                result = delegate_qwen.run_opencode_cli(
                    root,
                    "task",
                    "model",
                    str(config),
                    "/bin/opencode",
                    None,
                    10,
                    str(root / "attempt.events"),
                )
            self.assertEqual(result["session_id"], "ses-1")
            self.assertEqual(result["response"], "finished")
            self.assertEqual(result["cli_exit_code"], 1)
            self.assertTrue((root / "attempt.stderr.jsonl").exists())


class AsyncDelegateQwenTest(unittest.TestCase):
    def test_delegation_command_forwards_opencode_backend(self):
        args = SimpleNamespace(
            workspace="/tmp/workspace",
            mode="workspace-write",
            timeout=1800,
            prompt="implement it",
            prompt_file=None,
            allow_dirty=False,
            session_id=None,
            base_url=None,
            backend="opencode",
            model="dspark/qwen3.8-27b",
            opencode_config="/tmp/opencode.json",
            opencode_command="/tmp/opencode",
            gate_file=None,
            repair_attempts=1,
        )
        command = delegate_qwen_async.delegation_command(args, Path("/tmp/result.json"))
        self.assertEqual(command[command.index("--backend") + 1], "opencode")
        self.assertEqual(command[command.index("--model") + 1], "dspark/qwen3.8-27b")
        self.assertEqual(
            command[command.index("--opencode-config") + 1], "/tmp/opencode.json"
        )

    def test_validate_job_id(self):
        self.assertEqual(
            delegate_qwen_async.validate_job_id("job-20260823_1"),
            "job-20260823_1",
        )
        with self.assertRaises(delegate_qwen_async.AsyncDelegateError):
            delegate_qwen_async.validate_job_id("../escape")

    def test_trigger_message_is_compact_and_points_to_exit_file(self):
        message = delegate_qwen_async.trigger_message(
            "job-1", Path("/tmp/job-1"), {"gate_pass": True, "changed_paths": ["a.py"]}
        )
        self.assertIn("job-1", message)
        self.assertIn('"gate_pass":true', message)
        self.assertNotIn("response", message)

    def test_load_gate_rejects_shell_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            path.write_text(json.dumps({"commands": ["npm test"]}), encoding="utf-8")
            with self.assertRaises(delegate_qwen_async.AsyncDelegateError):
                delegate_qwen_async.load_gate(str(path))

    def test_load_gate_rejects_previous_gate_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            path.write_text(
                json.dumps({"pass": True, "commands": [{"argv": ["npm", "test"]}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                delegate_qwen_async.AsyncDelegateError, "looks like a gate result"
            ):
                delegate_qwen_async.load_gate(str(path))

    def test_run_gate_checks_commands_and_allowed_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with mock.patch.object(
                delegate_qwen_async.subprocess,
                "run",
                side_effect=[
                    __import__("subprocess").CompletedProcess(["test"], 0, "ok", ""),
                    __import__("subprocess").CompletedProcess(
                        ["git", "status", "--short"], 0, "?? src/a.py\n", ""
                    ),
                ],
            ):
                result = delegate_qwen_async.run_gate(
                    workspace,
                    {"commands": [["test"]], "allowed_changes": ["src/a.py"]},
                )
            self.assertTrue(result["pass"])
            self.assertEqual(result["changed_paths"], ["src/a.py"])

    def test_run_gate_ignores_declared_runtime_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with mock.patch.object(
                delegate_qwen_async.subprocess,
                "run",
                side_effect=[
                    __import__("subprocess").CompletedProcess(["test"], 0, "ok", ""),
                    __import__("subprocess").CompletedProcess(
                        ["git", "status", "--short"],
                        0,
                        "?? src/a.py\n?? logs/\n",
                        "",
                    ),
                ],
            ):
                result = delegate_qwen_async.run_gate(
                    workspace,
                    {
                        "commands": [["test"]],
                        "allowed_changes": ["src/a.py"],
                        "ignored_changes": ["logs/"],
                    },
                )
            self.assertTrue(result["pass"])
            self.assertEqual(result["ignored_changes"], ["logs/"])

    def test_git_metadata_snapshot_detects_exclude_change(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            __import__("subprocess").run(
                ["git", "init", "-q"], cwd=workspace, check=True
            )
            baseline = delegate_qwen_async.git_metadata_snapshot(workspace)
            common = Path(
                __import__("subprocess")
                .run(
                    ["git", "rev-parse", "--git-common-dir"],
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                .stdout.strip()
            )
            if not common.is_absolute():
                common = workspace / common
            exclude = common / "info" / "exclude"
            exclude.write_text(exclude.read_text(encoding="utf-8") + "logs/\n", encoding="utf-8")
            self.assertEqual(
                delegate_qwen_async.changed_git_metadata(baseline), [str(exclude.resolve())]
            )

    def test_repair_command_reuses_session_and_replaces_prompt(self):
        command = delegate_qwen_async.repair_command(
            [
                "python",
                "delegate_qwen.py",
                "--workspace",
                "/tmp/workspace",
                "--prompt-file",
                "/tmp/task.md",
                "--result-file",
                "/tmp/old.json",
            ],
            Path("/tmp/new.json"),
            Path("/tmp/new.events"),
            "ses-1",
            {
                "commands": [
                    {"argv": ["npm", "test"], "exit_code": 1, "stderr_tail": "failed"}
                ],
                "unexpected_changes": [],
            },
        )
        self.assertNotIn("--prompt-file", command)
        self.assertIn("--allow-dirty", command)
        self.assertEqual(command[command.index("--session-id") + 1], "ses-1")
        self.assertEqual(command[command.index("--result-file") + 1], "/tmp/new.json")

    def test_atomic_write_json_replaces_complete_document(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "exit.json"
            delegate_qwen_async.atomic_write_json(destination, {"status": "success"})
            self.assertEqual(json.loads(destination.read_text()), {"status": "success"})

    def test_close_opencode_session_uses_config_and_workspace(self):
        completed = __import__("subprocess").CompletedProcess(
            ["opencode"], 0, "deleted", ""
        )
        with mock.patch.object(
            delegate_qwen_async.subprocess, "run", return_value=completed
        ) as run:
            result = delegate_qwen_async.close_child_session(
                {
                    "workspace": "/tmp/workspace",
                    "backend": "opencode",
                    "opencode_command": "/tmp/opencode",
                    "opencode_config": "/tmp/opencode.json",
                    "keep_session": False,
                },
                "ses-1",
            )
        self.assertEqual(result["status"], "closed")
        self.assertEqual(
            run.call_args.args[0],
            ["/tmp/opencode", "session", "delete", "ses-1"],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], "/tmp/workspace")
        self.assertEqual(
            run.call_args.kwargs["env"]["OPENCODE_CONFIG"], "/tmp/opencode.json"
        )

    def test_close_child_session_can_be_kept(self):
        with mock.patch.object(delegate_qwen_async.subprocess, "run") as run:
            result = delegate_qwen_async.close_child_session(
                {"keep_session": True}, "ses-1"
            )
        self.assertEqual(result["status"], "kept")
        run.assert_not_called()

    def test_worker_saves_exit_before_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            spec = {
                "job_id": "job-1",
                "workspace": "/tmp/workspace",
                "channel": "123",
                "platform": "discord",
                "source": "qwen-delegate-job-1",
                "trigger_delay": 0,
                "command": ["delegate-command"],
                "repair_attempts": 0,
                "gate": {"commands": [["gate-command"]]},
                "backend": "opencode",
                "keep_session": False,
            }
            spec_path = job_dir / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                if command == ["delegate-command"]:
                    (job_dir / "attempt-01-result.json").write_text(
                        json.dumps({"session_id": "s1", "cli_exit_code": 0}),
                        encoding="utf-8",
                    )
                    return __import__("subprocess").CompletedProcess(command, 0, "", "")
                if command == ["gate-command"]:
                    return __import__("subprocess").CompletedProcess(
                        command, 0, "ok", ""
                    )
                if command[:3] == ["git", "status", "--short"]:
                    return __import__("subprocess").CompletedProcess(
                        command, 0, "?? src/a.py\n", ""
                    )
                if command[:3] == ["opencode", "session", "delete"]:
                    return __import__("subprocess").CompletedProcess(
                        command, 0, "deleted", ""
                    )
                if command[0] == "xangi":
                    self.assertTrue((job_dir / "exit.json").exists())
                    return __import__("subprocess").CompletedProcess(
                        command, 0, "ok", ""
                    )
                raise AssertionError(command)

            with (
                mock.patch.object(
                    delegate_qwen_async.subprocess, "run", side_effect=fake_run
                ),
                mock.patch.object(delegate_qwen_async.time, "sleep"),
            ):
                self.assertEqual(delegate_qwen_async.run_worker(spec_path), 0)

            self.assertEqual(calls[0], ["delegate-command"])
            self.assertEqual(calls[-1][:3], ["xangi", "tool", "trigger"])
            self.assertEqual(
                json.loads((job_dir / "exit.json").read_text())["status"], "success"
            )
            self.assertEqual(
                json.loads((job_dir / "trigger.json").read_text())["exit_code"], 0
            )
            self.assertEqual(
                json.loads((job_dir / "summary.json").read_text())["session_cleanup"][
                    "status"
                ],
                "closed",
            )

    def test_worker_triggers_after_delegate_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            spec_path = job_dir / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "job_id": "job-failed",
                        "workspace": "/tmp/workspace",
                        "channel": "123",
                        "platform": "discord",
                        "source": "qwen-delegate-job-failed",
                        "trigger_delay": 0,
                        "command": ["delegate-command"],
                        "repair_attempts": 0,
                        "gate": {"commands": [["gate-command"]]},
                    }
                ),
                encoding="utf-8",
            )

            def fake_run(command, **kwargs):
                if command == ["delegate-command"]:
                    return __import__("subprocess").CompletedProcess(
                        command, 1, "", "failed"
                    )
                if command == ["gate-command"]:
                    return __import__("subprocess").CompletedProcess(
                        command, 1, "", "failed"
                    )
                if command[:3] == ["git", "status", "--short"]:
                    return __import__("subprocess").CompletedProcess(command, 0, "", "")
                if command[0] == "xangi":
                    return __import__("subprocess").CompletedProcess(
                        command, 0, "ok", ""
                    )
                raise AssertionError(command)

            with (
                mock.patch.object(
                    delegate_qwen_async.subprocess, "run", side_effect=fake_run
                ),
                mock.patch.object(delegate_qwen_async.time, "sleep"),
            ):
                self.assertEqual(delegate_qwen_async.run_worker(spec_path), 0)

            exit_payload = json.loads((job_dir / "exit.json").read_text())
            self.assertEqual(exit_payload["status"], "incomplete")
            self.assertEqual(exit_payload["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
