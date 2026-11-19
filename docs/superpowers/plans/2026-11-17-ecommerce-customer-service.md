# E-commerce Customer Service Implementation Plan

> **历史说明（2026-11-17）：** 本文件是早期总计划，Task 1-10 仅保留作历史背景。
> 当前交付已改为严格分步推进，P0 以
> `docs/superpowers/plans/2026-11-17-p0-readonly-after-sales.md` 为准。
> 本阶段只推进 P0；P1、P2、P3 未经下一步确认不得实施。
> 用户确认的移动端专项暂停，且不提交、不推送。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a runnable customer and administrator e-commerce support system with parent-child RAG, SQL-backed dynamic data, LangGraph orchestration, persistence, and Docker Compose deployment.

**Architecture:** FastAPI owns auth, API, orchestration, and persistence. PostgreSQL + PGVector stores relational facts, parent/child knowledge, conversations, traces, and handoffs. A React/Vite application serves the customer chat surface and role-protected administration console.

**Tech Stack:** Python 3.14, FastAPI, LangChain, LangGraph, PostgreSQL 16, PGVector, SQLAlchemy 2, Alembic, psycopg 3, React 19, TypeScript, Vite, React Router, TanStack Query, Lucide React, pytest, Vitest, Playwright, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-17-ecommerce-customer-service-design.md`

## Global Constraints

- Python is 3.14; backend dependencies are pinned after a successful environment install.
- PostgreSQL is 16 or newer; vector extension is PGVector with 1024-dimensional embeddings.
- Chat uses an OpenAI-compatible provider; Embedding and Rerank use DashScope native endpoints.
- `knowledge_chunks.parent_id` is non-null and references a persisted parent.
- Retrieval order is child recall, rerank, parent expansion, source formatting.
- Price, stock, order, item, and logistics facts never enter vector tables.
- Order SQL always includes the authenticated user id.
- No registration, order modification, refund approval, payment, Redis, Milvus, or Celery.
- Frontend uses one Vite app with `/app` customer routes and `/admin` admin routes.
- Normal tests use fakes; live provider tests are opt-in through environment flags.
- Do not commit or push unless the user explicitly requests it.

## Review Focus

1. Cross-user order lookup must never reveal whether another user's order exists.
2. Retrieval must return deduplicated parent context, not child-only snippets.
3. Greeting and chit-chat controls must avoid unnecessary model calls and persist counters.
4. Dynamic values changed in PostgreSQL must appear in the next answer without reindexing.
5. Admin routes and handoff data must reject customer tokens and unauthenticated access.

---

### Task 1: Bootstrap, configuration, database, and health

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.env.example`, `.gitignore`
- Create: `app/main.py`, `app/core/config.py`, `app/core/logging.py`
- Create: `app/db/base.py`, `app/db/session.py`, `app/db/models.py`
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py`
- Test: `tests/unit/test_health.py`, `tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `Settings` loaded from environment and `create_app() -> FastAPI`.
- Produces: SQLAlchemy async session dependency and the complete initial schema.

- [ ] **Step 1: Write failing health and migration tests**

Assert that `GET /health` returns `200` with `status`, `database`, and `models`
fields, and that Alembic upgrade creates all tables in an empty test database.

- [ ] **Step 2: Run the tests and confirm missing application/schema failures**

Run: `pytest tests/unit/test_health.py tests/integration/test_migrations.py -v`

Expected: FAIL because the application and migration do not exist.

- [ ] **Step 3: Implement settings, app factory, SQLAlchemy models, and initial migration**

Required tables: users, auth_sessions, conversations, chat_messages,
knowledge_sources, knowledge_parents, knowledge_chunks, products, orders,
order_items, logistics_events, agent_runs, tool_call_logs, handoff_tickets.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/unit/test_health.py tests/integration/test_migrations.py -v`

Expected: PASS.

### Task 2: Authentication, roles, and ownership

**Files:**
- Create: `app/core/security.py`, `app/services/auth.py`
- Create: `app/api/deps.py`, `app/api/routes/auth.py`
- Create: `app/db/repositories/auth.py`, `app/db/repositories/chat.py`
- Create: `app/schemas/auth.py`
- Test: `tests/unit/test_security.py`, `tests/integration/test_auth.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str` and `verify_password(password: str, encoded: str) -> bool`.
- Produces: `create_session_token() -> str` and `hash_session_token(token: str) -> str`.
- Produces: `get_current_user(...) -> User` and `require_admin(...) -> User`.
- Consumes: persisted users and auth sessions from Task 1.

- [ ] **Step 1: Write failing tests**

Cover valid and invalid login, expired token, logout, customer rejection from an
admin dependency, and absence of raw token/password in the database.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_security.py tests/integration/test_auth.py -v`

Expected: FAIL because auth functions and routes are absent.

- [ ] **Step 3: Implement scrypt password hashing, opaque token sessions, and route guards**

Login returns a bearer token and user role. Logout revokes the current token.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_security.py tests/integration/test_auth.py -v`

Expected: PASS.

### Task 3: Parent-child ingestion and durable knowledge model

**Files:**
- Create: `app/rag/chunking.py`, `app/rag/embeddings.py`, `app/rag/indexer.py`
- Create: `app/db/repositories/rag.py`
- Create: `data/knowledge/manifest.json`
- Create: `data/knowledge/products/*.md`, `data/knowledge/policies/*.md`
- Create: `scripts/index_knowledge.py`
- Test: `tests/unit/test_rag_ingestion.py`, `tests/integration/test_rag_repository.py`

**Interfaces:**
- Produces: `EmbeddingProvider.embed_documents(texts: list[str]) -> list[list[float]]`.
- Produces: `index_manifest(session, provider, manifest_path) -> IndexResult`.
- Guarantees: every child row has a valid `parent_id`; source checksum makes reindex idempotent.

- [ ] **Step 1: Write failing ingestion tests**

Assert stable parent identities, non-null child parent ids, source metadata,
1024-dimension validation, idempotent reindexing, and rejection of chunks
declaring dynamic price or stock fields.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_rag_ingestion.py tests/integration/test_rag_repository.py -v`

Expected: FAIL because indexing is not implemented.

- [ ] **Step 3: Implement manifest parsing, deterministic parent/chunk identities, batching, and persistence**

Use repository-owned knowledge content. Batch in groups accepted by the native
embedding API and preserve the original order.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_rag_ingestion.py tests/integration/test_rag_repository.py -v`

Expected: PASS.

### Task 4: Reranked child recall with parent expansion

**Files:**
- Create: `app/rag/rerank.py`, `app/rag/retriever.py`
- Create: `app/schemas/knowledge.py`
- Test: `tests/unit/test_parent_child_retrieval.py`, `tests/integration/test_rag_retrieval.py`

**Interfaces:**
- Produces: `RerankProvider.rerank(query: str, documents: list[str], top_n: int) -> list[RerankHit]`.
- Produces: `retrieve_parent_context(session, query, kb_type, providers, recall_k=12, rerank_k=5) -> RetrievalResult`.
- Each source contains `title`, `source_uri`, `parent_id`, `chunk_id`, and `score`.

- [ ] **Step 1: Write failing tests**

Use two parents with multiple matching children and a fake reranker. Assert that
children determine ranking, each parent is returned once, full parent text is
used, filters respect `kb_type`, and empty results are explicit.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_parent_child_retrieval.py tests/integration/test_rag_retrieval.py -v`

Expected: FAIL because retriever behavior is absent.

- [ ] **Step 3: Implement vector recall, rerank, parent load, deduplication, and citations**

Do not generate an answer from child rows alone.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_parent_child_retrieval.py tests/integration/test_rag_retrieval.py -v`

Expected: PASS.

### Task 5: Dynamic catalog and authorized order tools

**Files:**
- Create: `app/tools/catalog.py`, `app/tools/orders.py`
- Create: `app/db/repositories/catalog.py`, `app/db/repositories/orders.py`
- Create: `app/schemas/catalog.py`, `app/schemas/orders.py`
- Test: `tests/integration/test_dynamic_data.py`, `tests/integration/test_order_authorization.py`

**Interfaces:**
- Produces: `get_product_price_stock(session, sku_or_name) -> ProductFact | AmbiguousProduct`.
- Produces: `get_order_and_logistics(session, user_id: int, order_no: str) -> OrderFact | None`.
- Order repository never accepts a user id from model-generated tool arguments.

- [ ] **Step 1: Write failing tests**

Change price and stock in the database and assert new results without reindexing.
Assert owner A gets the order and owner B receives the same result as a missing
order. Assert ambiguous products require clarification.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/integration/test_dynamic_data.py tests/integration/test_order_authorization.py -v`

Expected: FAIL because tools are absent.

- [ ] **Step 3: Implement SQL-scoped repositories and typed tools**

Return stable domain objects; API-facing text is assembled outside repositories.

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_dynamic_data.py tests/integration/test_order_authorization.py -v`

Expected: PASS.

### Task 6: LangGraph orchestration

**Files:**
- Create: `app/providers/chat.py`, `app/providers/dashscope.py`
- Create: `app/graph/state.py`, `app/graph/router.py`, `app/graph/nodes.py`, `app/graph/builder.py`
- Create: `app/prompts/router.py`, `app/prompts/answer.py`
- Create: `app/tools/knowledge.py`, `app/tools/handoff.py`
- Test: `tests/unit/test_graph_routing.py`, `tests/unit/test_graph_guards.py`

**Interfaces:**
- Produces: `ChatProvider.complete(...)` and `ChatProvider.structured(...)`.
- Produces: `build_graph(container) -> CompiledStateGraph`.
- Produces: `AgentState` fields for messages, intent, slots, user identity,
  sources, tool evidence, chit-chat streak, clarification attempts, answer, and
  handoff request.

- [ ] **Step 1: Write failing graph tests**

Cover fixed greeting without LLM, every router branch, malformed router output,
missing order slot, product ambiguity, two chit-chat turns then guidance,
retrieval failure offering handoff, and repeated clarification handoff.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_graph_routing.py tests/unit/test_graph_guards.py -v`

Expected: FAIL because the graph is absent.

- [ ] **Step 3: Implement guarded routing and branch nodes**

Use explicit conditional edges and deterministic thresholds. Force all generated
answers to include only supplied sources and tool evidence.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_graph_routing.py tests/unit/test_graph_guards.py -v`

Expected: PASS.

### Task 7: Chat API, persistence, handoff, and admin APIs

**Files:**
- Create: `app/services/chat.py`, `app/services/handoff.py`
- Create: `app/api/routes/chat.py`, `app/api/routes/handoffs.py`, `app/api/routes/health.py`
- Create: `app/schemas/chat.py`, `app/schemas/handoff.py`
- Test: `tests/integration/test_chat_api.py`, `tests/integration/test_admin_api.py`

**Interfaces:**
- Produces: `POST /api/conversations`, `GET /api/conversations`,
  `GET /api/conversations/{id}/messages`, `POST /api/conversations/{id}/messages`.
- Produces: `GET /api/admin/overview`,
  `GET /api/admin/handoffs`, `GET /api/admin/handoffs/{id}`,
  `PATCH /api/admin/handoffs/{id}`.
- Chat responses include `answer`, `intent`, `sources`, `trace_id`, and optional
  `handoff`.

- [ ] **Step 1: Write failing API tests**

Cover conversation ownership, multi-turn context, replay after new app instance,
persisted trace/source evidence, handoff creation, admin filtering, status
update, and customer-token rejection from admin APIs.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/integration/test_chat_api.py tests/integration/test_admin_api.py -v`

Expected: FAIL because routes and services are absent.

- [ ] **Step 3: Implement services and routes**

Persist user and assistant messages transactionally with run evidence. Do not
return another user's handoff or conversation.

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_chat_api.py tests/integration/test_admin_api.py -v`

Expected: PASS.

### Task 8: Customer React application

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Create: `frontend/src/api/`, `frontend/src/auth/`, `frontend/src/routes/customer/`
- Create: `frontend/src/styles/tokens.css`, `frontend/src/styles/app.css`
- Test: `frontend/src/**/*.test.tsx`, `tests/e2e/customer.spec.ts`

**Interfaces:**
- Consumes: auth and chat APIs from Tasks 2 and 7.
- Produces: `/login` and `/app` customer routes with persistent conversation,
  source display, handoff indicator, responsive layout, and accessible states.

- [ ] **Step 1: Write failing frontend tests**

Cover login validation, unauthorized redirect, message send, loading state,
source rendering, history selection, handoff status, empty state, and mobile
layout without overlapping text.

- [ ] **Step 2: Run tests and confirm failure**

Run: `npm test -- --run`

Expected: FAIL because the frontend does not exist.

- [ ] **Step 3: Implement the customer routes and visual system**

Use the approved ink/canvas/cobalt/service-orange/green tokens. Build a working
conversation workspace, not a marketing page. Use Lucide icons and stable
dimensions for toolbars and message controls.

- [ ] **Step 4: Run unit and Playwright customer tests**

Run: `npm test -- --run`

Run: `npx playwright test tests/e2e/customer.spec.ts`

Expected: PASS with desktop and mobile screenshots.

### Task 9: Administrator React application

**Files:**
- Create: `frontend/src/routes/admin/`
- Create: `frontend/src/api/admin.ts`, `frontend/src/auth/requireAdmin.tsx`
- Test: `frontend/src/routes/admin/**/*.test.tsx`, `tests/e2e/admin.spec.ts`

**Interfaces:**
- Consumes: admin APIs from Task 7.
- Produces: `/admin`, `/admin/handoffs`, and `/admin/handoffs/:id` with overview,
  filters, detail evidence, and status mutation.

- [ ] **Step 1: Write failing admin tests**

Cover customer-token rejection, unauthenticated redirect, overview metrics,
queue filters, detail evidence, status update, empty/error states, and mobile
operation.

- [ ] **Step 2: Run tests and confirm failure**

Run: `npm test -- --run`

Expected: FAIL because admin routes are absent.

- [ ] **Step 3: Implement the administrator console**

Use dense full-width working areas, restrained dividers, and one clear evidence
panel. Do not nest cards or expose raw secrets.

- [ ] **Step 4: Run admin tests**

Run: `npm test -- --run`

Run: `npx playwright test tests/e2e/admin.spec.ts`

Expected: PASS with desktop and mobile screenshots.

### Task 10: Seed data, evaluation, containers, and demo documentation

**Files:**
- Create: `scripts/seed_demo.py`, `scripts/evaluate.py`
- Create: `Dockerfile`, `compose.yaml`, `.dockerignore`, `README.md`
- Create: `docs/architecture.md`, `docs/demo-guide.md`
- Test: `tests/e2e/test_demo_flow.py`, `tests/e2e/test_compose_smoke.py`

**Interfaces:**
- Produces: idempotent demo customer/admin/product/order/logistics data.
- Produces: one command for the full stack and a runnable scripted evaluation
  report covering all business branches and security cases.

- [ ] **Step 1: Write failing end-to-end and compose contract tests**

Assert seeded credentials, inference with fake providers, browser flows,
Compose health, migration/seed/index completion, and the documented health URL.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/e2e/test_demo_flow.py tests/e2e/test_compose_smoke.py -v`

Expected: FAIL because deployment and seed scripts are absent.

- [ ] **Step 3: Implement multi-stage frontend/backend image, Compose services, seeds, evaluation, and docs**

The app image builds the Vite application and serves its output. Startup waits
for PostgreSQL, applies Alembic, runs idempotent seed/index steps, then starts
Uvicorn.

- [ ] **Step 4: Run complete verification**

Run: `pytest -v`

Run: `npm test -- --run`

Run: `npm run build`

Run: `npx playwright test`

Run: `docker compose up --build -d --wait`

Run: `docker compose ps`

Run: `curl http://127.0.0.1:8000/health`

Expected: all commands pass; `/app` and `/admin` load nonblank; handoff and chat
flows are usable; no overlapping UI at desktop or mobile viewport sizes.
