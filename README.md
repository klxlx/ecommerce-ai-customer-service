# 电商智能客服系统

一套可落地演示的电商场景智能客服系统，以「静态知识检索 + 动态数据查询」双引擎为核心，
采用父子块分层 RAG 架构，基于 LangGraph 实现全链路智能编排。覆盖商品咨询、交易规则查询、
订单进度查询三大核心业务，支持多轮对话记忆、闲聊管控与人工兜底。

- 顾客端：`/app` —— 登录、多轮对话、来源溯源、转人工
- 管理端：`/admin` —— 工单队列（只看状态与流转）、客服回复工作台、知识库、运行日志
- 健康检查：`GET /health`

## 技术栈

- 后端：Python 3.14、FastAPI、LangChain、LangGraph、SQLAlchemy 2、Alembic、psycopg 3
- 数据库：PostgreSQL 16 + PGVector（1024 维向量）
- 模型：OpenAI 兼容对话模型（默认 `qwen-plus`）、DashScope `text-embedding-v3`、`text-rerank-v1`
- 前端：React 19、TypeScript、Vite、React Router、TanStack Query、Lucide React

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env       # 按需填入模型 API Key；无 Key 时默认使用内置 Mock 模型
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

启动后：

- 顾客端：http://127.0.0.1:8000/app
- 管理端：http://127.0.0.1:8000/admin

`MOCK_PROVIDERS=1`（默认）表示使用内置确定性 Mock 模型，无需任何 API Key 即可跑通全流程。
如需接入真实模型，设置 `CHAT_API_KEY`、`DASHSCOPE_API_KEY` 并将 `MOCK_PROVIDERS` 设为 `0` 即可。

### 方式二：本地运行

依赖：Python 3.14、Node 20+、一个 PostgreSQL 16 + PGVector 实例。

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows（Linux/macOS 用 source .venv/bin/activate）
pip install -r requirements.txt
cp .env.example .env              # 修改 DATABASE_URL

alembic upgrade head
MOCK_PROVIDERS=1 python -m scripts.seed_demo
# 只灌业务数据、暂不索引 10 份大 PDF：SEED_INDEX_KNOWLEDGE=0 python -m scripts.seed_demo
python -m scripts.serve --host 127.0.0.1 --port 8000   # 后端 :8000（无需 --reload）

cd frontend
npm install
npm run dev                       # 前端 :5173（已配置 /api 代理到 :8000）
```

`scripts.serve` 启动时显式使用 selector 事件循环（Windows 下 Uvicorn 默认的
Proactor 事件循环与异步 psycopg 不兼容）。不需要 `--reload`；修改代码后重新启动即可。

天气 MCP 默认由后端同进程提供在 `/mcp/weather`，数据来自只读 Open-Meteo；
本地时间由闲聊 Agent 的内置工具生成，不调用模型。若部署端口不是 `8000`，请同步设置
`WEATHER_MCP_URL`。

## 演示账号

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `customer` | `password123` | 顾客（张伟） |
| `customer2` | `password123` | 顾客（林小雨） |
| `customer3` | `password123` | 顾客（赵子航） |
| `admin` | `password123` | 管理员（王敏） |
| `admin2` | `password123` | 管理员（陈晨） |

管理端「客服回复」是独立模块，位置在「工单队列」与「知识库」之间：顾客转人工后会话出现在
待接入列表，点击接入即独占该会话，右侧原样加载顾客会话窗口（气泡、来源、售后卡片与顾客端一致）。
回复不会结束会话，可连续发送；客服确认问题解决后点右上角「退出会话」，必须填写处理结论，
提交后工单转为已解决、结论回写会话槽位，会话交还智能体。「工单队列」只负责查看状态和标记状态。
流程图见 `docs/客服回复流程图.png`，可编辑的 Mermaid 源文件为 `docs/客服回复流程图.md`。

知识库单个文件上限为 30 MB。扫描版 PDF 或图片会自动走 RapidOCR，转换结果需人工核对后入库。

新会话首屏显示「智能客服小天」欢迎语，智能体回复通过 SSE 流式输出。知识库资料不足时创建
`knowledge_gap` 工单，只进入工单队列学习，不进入客服回复、不阻断会话；顾客会收到固定提示，
可换说法继续追问。

同一用户跨会话按“归一化问题 + 语义相似度”去重：同一问题在未解决期间只保留一个工单，
重复提问复用旧工单并累计次数。每个用户最多同时存在 2 个未解决工单（`human` 与
`knowledge_gap` 都计入）；超限时不再建单，返回带两个工单摘要的提示。投诉、紧急升级和
系统故障例外。语义阈值默认 `0.93`，可用 `HANDOFF_DEDUPE_SIMILARITY` 调整；型号、
订单号、退款/退货类型和否定词会先做冲突守卫，避免把不同问题误合并。

流式回复在 Provider 空闲超时或整轮预算超时后转人工兜底；如果最终答案与已发送 token
不一致，SSE 会发送 `replace` 事件让前端整体替换气泡，而不是追加。

## MVP：SSE 心跳、哨兵、拍照退换货与语义缓存

### SSE 心跳与网关超时

- 应用在流式响应开始时立即发送 `: connected` 注释帧，之后每 `SSE_HEARTBEAT_SECONDS`
  秒（默认 15 秒）发送 `: heartbeat`。注释帧不进入前端正文，只用于保持连接。
- 网关读超时应大于 `CHAT_TOTAL_BUDGET_SECONDS=60` 加心跳间隔，示例值 `300s`；
  同时关闭 `proxy_buffering`、`gzip`，并设置 `X-Accel-Buffering: no`。
- 示例配置见 `docs/nginx-sse.conf.example`。当前项目没有真实网关，未修改本机网络配置。

### 异步哨兵

- 位置：回答生成之后、写入会话之前；独立于 LangGraph 主流程。
- 模型：默认 `ROUTER_MODEL`（与意图模型同档、同阿里云 Key），可用
  `SENTINEL_MODEL` 单独覆盖。
- 只判断两件事：回答中的事实性结论是否有检索资料/工具结果支持；是否做出无依据的
 价格、退款、退货、赔付、时效或政策承诺。不做通用内容审核。
- 超时（`SENTINEL_TIMEOUT_SECONDS`，默认 8 秒）、模型异常或 JSON 解析失败时 fail-open，
  不阻断回答；判定违规时用固定保守文案替换答案，流式场景通过 `replace` 事件下发。
- 哨兵结果写入 `agent_runs.trace.sentinel`，指标包括 `sentinel.check.total`、
  `sentinel.flagged.total`。

### 拍照退换货

1. 顾客说“退货/换货/退换货”时，助手消息携带 `actions` 卡片，顾客点击后打开弹窗。
2. 弹窗上传照片；后端保存去 EXIF 的 JPEG，调用 `VISION_PROVIDER` 配置的视觉分析器。
3. 当前默认使用预训练 `yolov8n.onnx`（COCO）。它只能识别“手机/杯子/键盘”等通用商品
   主体，不能识别型号、颜色、配件齐全度和破损类型；这些字段由顾客在弹窗内核对或选择。
4. 识别置信度低于 `YOLO_CONFIDENCE_THRESHOLD`、未识别到商品主体、或视觉模型不可用时，
   `passed=false`，**禁止建单**，必须重拍。提交时还会再次校验置信度，并比对订单商品与
   照片检测类别，防止用无关照片发起退换货。
5. 顾客填写订单号后，只查询当前用户自己的订单；多商品订单选择其中一件，自动带出商品
   名称、型号、颜色。商品数据新增了 `products.model/color` 与 `order_items.model/color`。
6. 顾客填写破损类型、破损程度、退换原因、顾客取件地址和联系电话，勾选“情况属实”后提交。
7. 创建 `kind=human`、`category=return_exchange` 工单，`evidence.return_request` 保存
   图片、识别结果、订单商品和表单；退换货工单不占用“每人最多 2 个未解决工单”的名额。
8. 同一订单 + 同一 SKU + 同一破损类型 + 同一原因，未解决期间按精确业务键去重；不同订单
   不会因语义相似被合并。
9. 创建成功后助手回复“已联系管理员，请稍候。”，会话进入人工接待。

接口：

- `POST /api/returns/analyze`（multipart：`conversation_id`、`file`）
- `GET /api/returns/orders/{order_no}`
- `POST /api/returns`
- `GET /api/admin/handoffs/{id}/return-image`（管理员读取原图）

视觉模型可替换：`VISION_PROVIDER=yolo` 当前对应 `YoloOnnxVisionAnalyzer`，后续加
Qwen-VL 或自训 YOLO 时实现相同的 `VisionAnalyzer.analyze()` 接口并改配置即可，API、
数据库和前端表单无需改动。

### 语义缓存（Postgres + pgvector）

- 表 `semantic_cache` 保存人工导入的高频问题、答案、来源、embedding、命中次数和过期时间。
- 只做种子导入，不自动缓存模型生成的回答；订单/物流/售后进度、价格、库存等动态问题
  直接跳过缓存。
- 默认阈值 `SEMANTIC_CACHE_SIMILARITY=0.95`，最多取 3 条候选。
- 缓存问题可以使用独立的 embedding 模型：`SEMANTIC_CACHE_EMBEDDING_MODEL` 留空时跟随
  `EMBEDDING_MODEL`；如果主模型的免费额度耗尽，可单独设为 `text-embedding-v4`（仍为
  1024 维），不影响知识库已有向量。更换缓存 embedding 模型后应 `--clear` 并重新导入。
- 知识库新增/删除知识时自动清空缓存，避免旧政策答案继续命中。
- 导入方式：

```bash
# 先按 data/semantic_cache_seed.example.json 准备 50–100 条
python -m scripts.seed_semantic_cache --file data/semantic_cache_seed.json
python -m scripts.seed_semantic_cache --file data/semantic_cache_seed.csv
python -m scripts.seed_semantic_cache --clear
```

JSON 字段：`question`、`answer`、`kb_type`（`policy`/`product`）、`sources`（数组）、
可选 `expires_at`（ISO 时间）。CSV 列同名，`sources` 填 JSON 字符串。

### 视觉模型文件

仓库不提交模型权重。首次部署需把 `yolov8n.onnx` 放到 `models/yolov8n.onnx`，或通过
`YOLO_MODEL_PATH` 指向已有文件。未配置模型时拍照识别接口会返回 `passed=false`，不会
放行建单。

## 测试

```bash
pytest -v                         # 后端单元 + 集成 + 端到端测试
cd frontend && npm test           # 前端组件测试
cd frontend && npm run build      # 前端类型检查 + 构建
```

端到端浏览器测试（Playwright）需先启动后端（Mock 模式）与前端；首次运行先执行
`npx playwright install chromium webkit` 安装浏览器，再运行 `npx playwright test`。

## 目录结构

```
app/             FastAPI 应用（core/db/api/services/rag/graph/tools/providers）
migrations/      Alembic 迁移
data/knowledge/  知识库清单与商品/规则文档
scripts/         索引、演示数据、评测脚本
frontend/        React 前端（顾客端 + 管理端）
tests/           后端单元/集成/端到端测试
docs/            架构与演示指南
```

详见 `docs/architecture.md` 与 `docs/demo-guide.md`。
