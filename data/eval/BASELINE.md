# 数据集评测基线（qwen-plus 历史版）

> 当前可用基线见 `artifacts/eval-dataset-qwen-max-flash.md`：router=qwen-max，意图 295/300=98.3%；chat/sentinel=qwen-flash，工具首动作 100%、动作集合 45%、哨兵负样本拦截 70%。本文件保留 qwen-plus 配额耗尽前的历史结果，便于对比。

- 生成时间：2026-11-21T06:24:32+00:00
- 模型：router=qwen-plus，chat=qwen-plus，sentinel=qwen-plus
- 数据版本：seed=20261121
- 数据量：intent 300、tool 200、sentinel 50 负 + 50 正、FAQ 100

> 备注：本报告来自 qwen-plus 配额耗尽前的完整运行。之后复跑时 DashScope 聊天模型返回 HTTP 403 `Free quota exhausted`，因此没有用失败数据覆盖本报告。失败日志保留在 `artifacts/eval-dataset-provider-quota-error.json`。

## 意图识别

- 总数 300，通过 294，准确率 **0.980**
- 售后动作槽位：82/90
- 商品槽位：195/195
- 主要剩余失败：订单号查询返回 clarification；已签收但未收到货被判 after_sale；带订单号的“能保修吗”被判 product_knowledge。

## 工具决策

- 首动作准确率：**1.000**（200/200）
- 动作集合准确率：**0.325**（65/200）
- 错误分支停止率：**1.000**（67/67）
- 说明：首动作全部是 `query_order_status`，1.0 只代表顺序基线；动作集合低分主要因为模型在查完政策后经常多调用一次 `create_after_sales_request`，而生成器 gold 只要求查询。

## 回答哨兵

- 负样本拦截率：**0.780**（39/50）
- 正样本误杀率：**0.000**（0/50）
- 未完成检查：0
- 漏检集中在“立即重新发货 / 补偿方案 / 一定会处理”等承诺型话术；空上下文下哨兵没有判定为无依据承诺。

## 失败样例

### 意图

- `intent-0036` 期望 `order`，实际 `clarification`：帮我查一下订单 EC-55E750F060504E4C 到哪了
- `intent-0068` 期望 `order`，实际 `after_sale`：物流显示已签收，可是我没收到精华水
- `intent-0088` 期望 `after_sale`，实际 `product_knowledge`：路由器用了两个月就坏了，能保修吗（带订单号）
- `intent-0146` 期望 `after_sale`，实际 `product_knowledge`：电饭煲用了两个月就坏了，能保修吗（带订单号）
- `intent-0150` 期望 `after_sale`，实际 `product_knowledge`：蓝牙耳机用了两个月就坏了，能保修吗（带订单号）

### 工具

- `tool-0051` 期望 `query_order_status, check_return_policy`，实际多出 `create_after_sales_request`
- `tool-0052` 同上
- `tool-0055` 同上
- `tool-0056` 同上
- `tool-0057` 同上

### 哨兵

- `sentinel-neg-0006`：回答“一定会重视……可以申请退货退款”，未拦截
- `sentinel-neg-0008`：回答“立即处理并为您重新发货”，未拦截
- `sentinel-neg-0013`：回答“立即处理并补偿相应损失”，未拦截
- `sentinel-neg-0016`：回答“立即处理换货或者退款”，未拦截
- `sentinel-neg-0017`：回答“立即处理并补偿方案”，未拦截

## 数据边界

- 原始生成器和三套合成 QA 只读，未复制进 git；原始目录已加入 `.gitignore`。
- `多轮对话数据集` 含手机号、姓名、详细地址，未用于任何派生资产。
- 工具评测使用生成器的工具名（`query_order_status` 等），只代表模型工具决策能力，不代表本项目实际工具协议的端到端准确率。
- FAQ 100 条已全部 `review_status=approved`，并写入 `semantic_cache`（policy 48、product 52）；详见 `data/semantic_cache/FAQ_REVIEW.md`。
