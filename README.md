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
