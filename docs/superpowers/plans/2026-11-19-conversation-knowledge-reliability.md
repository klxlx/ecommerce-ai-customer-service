# Conversation and Knowledge Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop test data pollution, replace customer-facing conversation IDs with titles, make knowledge re-import reliable, and remove misleading manual metadata and obsolete overview behavior.

**Architecture:** Keep the existing FastAPI, SQLAlchemy, React, and parent-child RAG design. Add narrow helpers at the boundaries: test-run cleanup in the browser/scripts, deterministic metadata extraction in the import service, and preflight/bisect behavior around embedding requests. Preserve history and soft-delete semantics; never reset database sequences or bulk delete data.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy async, PostgreSQL/pgvector, pytest, React 19, TypeScript, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-18-conversation-knowledge-reliability-design.md`

## Global Constraints

- Do not reset database ID sequences.
- Do not bulk-delete existing conversations, knowledge, tests, or tickets.
- Do not fuzzy-delete records by title.
- Test-created data must be identified by an explicit batch marker and cleaned by recorded ID after success, failure, or exception.
- Customer-facing pages must never display the numeric conversation ID.
- A restored knowledge document becomes visible only after a successful version publish.
- Automatic applicability and effective-date extraction must use explicit textual or registry evidence only.
- Remove the service overview rather than restoring it.
- The project is not a Git repository; record completion in this checklist instead of commits.

## Review Focus

- A failed test run or forced browser failure leaves no batch-owned conversations.
- A deleted knowledge item whose replacement embedding fails remains invisible.
- Repeated concurrent uploads of the same content do not create duplicate versions.
- Embedding rejection identifies the offending text without logging sensitive full content.
- A document without explicit metadata shows no guessed values or manual metadata fields.

---

### Task 1: Isolate and Clean Browser Test Data

**Files:**
- Modify: `frontend/tests/e2e/helpers.ts`
- Modify: `frontend/tests/e2e/customer.spec.ts`
- Modify: `frontend/tests/e2e/admin.spec.ts`
- Modify: `frontend/playwright.config.ts`
- Test: `frontend/tests/e2e/customer.spec.ts`

**Interfaces:**
- Produces: `createTestBatch(page): Promise<TestDataTracker>` with `conversationIds`, `knowledgeIds`, `batchId`, and `cleanup()`.
- Produces: `requireTestEnvironment(): void` that rejects an unmarked base URL.

- [ ] **Step 1: Write a failing E2E assertion that a created conversation is registered and removed**

Add a test using `createTestBatch`; assert its `conversationIds` contains the URL ID and cleanup makes the conversation disappear from the rail.

- [ ] **Step 2: Run the focused test and confirm the helper is missing**

Run: `npm run build --prefix frontend`
Expected: TypeScript fails because `createTestBatch` does not exist.

- [ ] **Step 3: Implement batch tracking and cleanup in the E2E helper**

Generate `e2e-<timestamp>-<random>` as the batch ID, record every created resource ID, delete only recorded IDs through the UI/API, and run cleanup from `finally`.
Require `E2E_TEST_MODE=1` or an `E2E_BASE_URL` explicitly marked as test data; otherwise fail before login.

- [ ] **Step 4: Wire every browser-created conversation through the tracker**

Update customer tests to track the URL ID after conversation creation and remove it before the test exits.

- [ ] **Step 5: Verify the frontend build**

Run: `npm run build --prefix frontend`
Expected: exit code 0.

### Task 2: Protect Evaluation Runs from the Runtime Database

**Files:**
- Modify: `scripts/evaluate.py`
- Create: `tests/unit/test_evaluate_guard.py`

**Interfaces:**
- Produces: `assert_evaluation_database(database_url: str) -> None`.
- Produces: `EvaluationRun` cleanup context that records created conversation IDs and deletes them in `finally`.

- [ ] **Step 1: Write failing tests for unsafe database and conversation cleanup**

Assert that a URL without `test`/`eval` in its database name raises `RuntimeError`, and that a run with a created conversation ID deletes that ID on both success and exception.

- [ ] **Step 2: Run the new tests and confirm failure**

Run: `python -m pytest tests/unit/test_evaluate_guard.py -q`
Expected: FAIL because the guard and cleanup API do not exist.

- [ ] **Step 3: Implement the guard and recorded-ID cleanup**

Call the guard before seeding or indexing. Keep explicit request/session data only for the current run and soft-delete only the IDs created by `_run_one`.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/unit/test_evaluate_guard.py -q`
Expected: PASS.

### Task 3: Show Conversation Titles Instead of IDs

**Files:**
- Modify: `frontend/src/routes/customer/CustomerApp.tsx`
- Modify: `frontend/src/routes/customer/CustomerApp.test.tsx`

**Interfaces:**
- Consumes: `listConversations(): Promise<ConversationSummary[]>`.
- Produces: header text `activeConversation?.title || "智能客服"`.

- [ ] **Step 1: Write failing customer-app tests**

Assert an active conversation with title `耳机咨询` renders that title and does not render `会话 #7`; assert a missing title renders `智能客服`.

- [ ] **Step 2: Run the focused frontend tests and confirm failure**

Run: `npm test --prefix frontend -- CustomerApp.test.tsx`
Expected: FAIL because the header still renders `会话 #7`.

- [ ] **Step 3: Share the conversation list query and render the active title**

Use the same `["conversations"]` query key in the workspace, pass the active summary into `ChatPanel`, and remove every customer-facing numeric ID rendering.

- [ ] **Step 4: Run the focused frontend tests**

Run: `npm test --prefix frontend -- CustomerApp.test.tsx`
Expected: PASS.

### Task 4: Restore Deleted Knowledge and Preserve Published Versions

**Files:**
- Modify: `app/services/knowledge_admin.py`
- Modify: `app/rag/indexer.py`
- Modify: `tests/integration/test_admin_knowledge_api.py`
- Test: `tests/integration/test_rag_repository.py`

**Interfaces:**
- Produces: delete-then-same-content upsert returns `status="unchanged"` and clears `deleted_at`.
- Produces: delete-then-different-content upsert publishes the new version before clearing `deleted_at`.
- Produces: an equal checksum for a deleted document is not skipped.

- [ ] **Step 1: Replace the deleted-document conflict test with failing recovery tests**

Test same-content recovery, different-content recovery, and failed replacement remaining deleted.

- [ ] **Step 2: Run the focused integration tests and confirm failure**

Run: `python -m pytest tests/integration/test_admin_knowledge_api.py -q`
Expected: the recovery tests fail with the current 409 conflict.

- [ ] **Step 3: Implement restore semantics in the admin service and indexer**

Allow deleted manual documents to pass the manual-version guard. For an existing checksum, treat recovery as an idempotent publish of the already-published version, then clear `deleted_at`. For new content, leave `deleted_at` set until `_publish_version` succeeds, then clear it there.

- [ ] **Step 4: Avoid rewriting already-published parent/chunk rows**

Do not reset a published or retired version to draft when it is reused. Draft/failed retries may still reset their own incomplete rows.

- [ ] **Step 5: Run the focused integration tests**

Run: `python -m pytest tests/integration/test_admin_knowledge_api.py tests/integration/test_rag_repository.py -q`
Expected: PASS.

### Task 5: Make Embedding Ingestion Defensive and Diagnosable

**Files:**
- Modify: `app/rag/embeddings.py`
- Create: `tests/unit/test_embedding_inputs.py`

**Interfaces:**
- Produces: `normalize_embedding_text(text: str) -> str`.
- Produces: `bisect_invalid_embedding_batch(texts: list[str]) -> int`.
- Preserves: `EmbeddingProvider.embed_documents(texts) -> list[list[float]]`.

- [ ] **Step 1: Write failing embedding tests**

Cover zero-width/control-character removal, empty-after-normalization rejection, over-limit text splitting, a 400 batch failure bisected to the offending item, and preservation of a safe provider error detail.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python -m pytest tests/unit/test_embedding_inputs.py -q`
Expected: FAIL because normalization and bisect behavior do not exist.

- [ ] **Step 3: Implement normalization and bounded batching**

Remove invisible characters, normalize whitespace, split oversized text at safe boundaries, keep batches under the provider character limit, and never silently truncate content.

- [ ] **Step 4: Implement invalid-request diagnosis**

Catch `ProviderInvalidRequestError`, split a failing batch until a single item is isolated, and raise a safe error naming the item position and character count. Include the provider's sanitized one-line message when available.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/unit/test_embedding_inputs.py -q`
Expected: PASS.

### Task 6: Auto-Detect Applicability and Effective Date

**Files:**
- Create: `app/services/knowledge_metadata.py`
- Modify: `app/services/knowledge_import.py`
- Modify: `app/schemas/knowledge.py`
- Modify: `app/api/routes/admin_knowledge.py`
- Modify: `frontend/src/api/admin.ts`
- Modify: `frontend/src/routes/admin/KnowledgePage.tsx`
- Create: `tests/unit/test_knowledge_metadata.py`

**Interfaces:**
- Produces: `detect_knowledge_metadata(content: str, *, registry_metadata: dict | None = None) -> MetadataDetection`.
- `MetadataDetection` exposes `applicability`, `effective_date`, and `evidence` for each detected field.
- Extends `KnowledgeFilePreviewOut` with read-only detection fields.

- [ ] **Step 1: Write failing deterministic metadata tests**

Cover `适用范围：...`, `仅适用于...`, `生效日期：2026年11月1日`, `自2026年11月1日起施行`, publication-date-only text, registry precedence, and absence of evidence.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python -m pytest tests/unit/test_knowledge_metadata.py -q`
Expected: FAIL because the metadata module does not exist.

- [ ] **Step 3: Implement explicit-evidence extraction**

Use conservative regexes and section parsing. Date-only publication text must not become an effective date unless the same context says it is effective from publication.

- [ ] **Step 4: Return detected metadata in previews and re-detect on final upsert**

Populate preview values/evidence and ignore stale front-end metadata when the final Markdown is saved.

- [ ] **Step 5: Remove the two manual input controls**

Display read-only auto-detected labels with evidence. Hide the metadata region when neither field has evidence.

- [ ] **Step 6: Run backend and frontend focused tests**

Run: `python -m pytest tests/unit/test_knowledge_metadata.py -q`
Run: `npm run build --prefix frontend`
Expected: both pass.

### Task 7: Remove Service Overview Residue

**Files:**
- Modify: `frontend/src/routes/admin/AdminApp.tsx`
- Modify: `frontend/tests/e2e/admin.spec.ts`
- Search: `frontend/src`

**Interfaces:**
- Produces: `/admin/overview` contains no overview page, overview navigation, or conversation ID.

- [ ] **Step 1: Add a failing browser assertion for the retired URL**

Visit `/admin/overview` as an admin and assert the ticket queue is shown without `服务概览` or `会话 #`.

- [ ] **Step 2: Run the focused browser test and confirm any residual mismatch**

Run: `npx playwright test tests/e2e/admin.spec.ts --project=desktop --grep "overview"`
Expected: FAIL only if stale overview content remains.

- [ ] **Step 3: Remove stale routes, labels, and links**

Keep unknown admin routes safely landing on the ticket queue; do not add an overview redirect page.

- [ ] **Step 4: Re-run the focused test**

Run: `npx playwright test tests/e2e/admin.spec.ts --project=desktop --grep "overview"`
Expected: PASS and all created test data is cleaned.

### Task 8: Verify Customer Reply Fallbacks and Cleanup

**Files:**
- Inspect: `app/graph/`, `app/services/chat.py`, `app/prompts/answer.py`
- Test: `tests/integration/test_chat_api.py`
- Test: `tests/unit/test_graph_guards.py`

**Interfaces:**
- Preserves: knowledge miss returns a truthful limitation with handoff guidance.
- Preserves: tool errors and high-risk refunds do not fabricate success.

- [ ] **Step 1: Run reply-focused tests to identify actual gaps**

Run: `python -m pytest tests/unit/test_graph_routing.py tests/unit/test_graph_guards.py tests/integration/test_chat_api.py tests/integration/test_after_sales_authorization.py -q`
Expected: existing passing cases pass; observed failures identify required reply changes.

- [ ] **Step 2: Add only missing failing reply tests**

Cover greeting, product price, policy knowledge, order logistics, no after-sale records, normal refund, over-threshold refund, knowledge miss, tool failure, explicit handoff, and slot retention.

- [ ] **Step 3: Implement the smallest reply-rule changes needed**

Do not add a new orchestration layer. Keep the existing graph and tools, tightening prompts/guards where tests demonstrate a gap.

- [ ] **Step 4: Run the reply-focused suite**

Run: `python -m pytest tests/unit/test_graph_routing.py tests/unit/test_graph_guards.py tests/integration/test_chat_api.py tests/integration/test_after_sales_authorization.py -q`
Expected: PASS.

### Task 9: Full Verification and Session Cleanup

**Files:**
- Verify all changed files.

**Interfaces:**
- Consumes: all prior task interfaces.

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 2: Run frontend tests and build**

Run: `npm test --prefix frontend`
Run: `npm run build --prefix frontend`
Expected: both exit 0.

- [ ] **Step 3: Run focused Playwright tests against explicit test mode**

Run with the project's test server: `$env:E2E_TEST_MODE="1"; npx playwright test --config frontend/playwright.config.ts`
Expected: 0 failures; each test cleans recorded conversation IDs.

- [ ] **Step 4: Confirm no test-created sessions remain**

List the IDs created by this verification batch and verify each soft-deleted/absent. Report the count as zero or give the exact remaining IDs.

