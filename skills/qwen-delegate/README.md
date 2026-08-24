# qwen-delegate

Local LLMのQwenへ実装を非同期委譲し、独立gateと構造化usageを返すベンチマーク用スキルです。

親GPT Solは起動turnと完了turnだけを担当します。gate合格後に成果物を読み直さないことで、委譲による親token削減を測定できます。

詳細は [SKILL.md](SKILL.md) を参照してください。スクリプトはxangi Web APIと`xangi tool trigger`を利用します。

## 出典

borot workspaceの`xs-qwen-delegate`（commit `800555fd60daf5d01609623e0c79ffc96354cb57`）を基に、ベンチマーク専用の短い監督契約へ調整しています。
