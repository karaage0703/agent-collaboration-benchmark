---
name: qwen-delegate
description: GPT Solの監督turnを増やさず、実装をLocal LLMのQwenへ非同期委譲し、独立gateと構造化usageを完了triggerで返す。Qwenに実装させる、委譲armを測る時に使う。
---

# Qwen Delegate

実装をLocal LLMへ委譲し、親GPT Solのtoken、子Qwenのtoken、品質、時間を分離して測る。

## 入力

- cleanな専用worktree
- `benchmark/task.md`
- `benchmark/gate.json`
- 現在のDiscord channelまたはthread ID

## 手順

1. worktreeがbaseline commitかつcleanであることを確認する。
2. 下記コマンドを1回だけ起動する。
3. `pid.json`、生存PID、`worker.log`、`exit.json`の予定pathを確認して親turnを終了する。
4. 完了triggerでは`summary.json`だけを読み、gate成功時は実装・diff・ログ・testを再読しない。
5. 親usage、子usage、wall time、gate、changed paths、cleanupを別々に報告する。

```bash
uv run [SKILL_DIR]/scripts/delegate_qwen_async.py \
  --workspace <fresh-worktree> \
  --prompt-file <workspace>/benchmark/task.md \
  --mode workspace-write \
  --backend local-llm \
  --model qwen3.8-27b \
  --gate-file <workspace>/benchmark/gate.json \
  --repair-attempts 1 \
  --channel <thread-id> \
  --platform discord \
  --timeout 1800
```

## 判定

- `summary.status=success`
- `gate_pass=true`
- `attempts=1`を理想値として記録する
- `session_cleanup.status=closed`
- trigger receiptが`delivered`でmessage IDを持つ

gate失敗時だけjob directoryを調査する。失敗調査を測定turnへ混ぜず、そのrunを無効として原因を修正後にfresh sessionとfresh worktreeで再測定する。

## 出力

```text
Delegated arm:
- parent input/cached/output: ...
- child input/cached/output: ...
- wall time: ...
- gate: pass/fail
- attempts: ...
- session cleanup: ...
- changed paths: ...
```

## 使用例

```text
Qwenにbenchmark/task.mdを実装させて
委譲armをfresh条件で測定して
```
