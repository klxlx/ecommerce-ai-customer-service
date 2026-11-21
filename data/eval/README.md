# 数据集评测资产

本目录只存放从本地原始数据派生的小型、脱敏评测文件，不复制原始 JSONL。

## 文件

- `ecom_intent_300.jsonl`：300 条意图样本，order 80、after_sale 90、product_knowledge 40、price_stock 40、handoff 50。
- `ecom_tool_200.jsonl`：200 条工具决策样本，按 create/policy/error/query 分层；保留生成器完整消息和 gold 动作序列。
- `sentinel_100.jsonl`：50 条高风险负样本 + 50 条官方知识正样本。
- `manifest.json`：来源、计数、排除项和许可证。
- `BASELINE.md`：qwen-plus 完整运行基线。

## 复现

```powershell
.\.venv\Scripts\python.exe -m scripts.build_dataset_assets
.\.venv\Scripts\python.exe -m scripts.eval_agent_dataset
```

评测脚本默认调用 `.env` 中的真实模型；如果多数调用出现 `ProviderAuthenticationError`，脚本会拒绝覆盖上一次报告。

## 边界

- 生成器工具名与本项目实际工具不同，`tool` 只用于模型工具决策能力基线。
- 原始数据目录已在 `.gitignore` 中排除。
- `多轮对话数据集` 含手机号、姓名、详细地址，未使用。
