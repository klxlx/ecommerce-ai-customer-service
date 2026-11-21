"""Build small, sanitized evaluation and FAQ assets from local datasets.

This script never copies raw chat logs with phone numbers/addresses into the
derived assets. The multi-turn dataset is intentionally excluded.

Outputs:
    data/eval/ecom_intent_300.jsonl
    data/eval/ecom_tool_200.jsonl
    data/eval/sentinel_100.jsonl
    data/eval/manifest.json
    data/semantic_cache/faq_candidates_100.csv
    data/semantic_cache/faq_candidates_100.json
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_ROOT = PROJECT_ROOT / "生成器"
QA_PATHS = {
    "general": PROJECT_ROOT / "多场景数据集" / "data.jsonl",
    "after_sales": PROJECT_ROOT / "售后维权数据集" / "data.jsonl",
    "logistics": PROJECT_ROOT / "物流查询数据集" / "data.jsonl",
}
KNOWLEDGE_ROOT = PROJECT_ROOT / "data" / "knowledge"

ECO_DOMAINS = {
    "order",
    "logistics",
    "after_sales",
    "refund",
    "invoice",
    "coupon",
    "product",
    "payment",
    "member",
    "complaint",
    "account",
}

INTENT_TARGETS = {
    "order": 80,
    "after_sale": 90,
    "product_knowledge": 40,
    "price_stock": 40,
    "handoff": 50,
}

REMOVED_QA_SCENES = {
    "多轮对话数据集",
}

RISK_PATTERNS = [
    re.compile(
        r"(一定|保证|100%|绝对|肯定).{0,10}"
        r"(退款|退货|换货|换新|赔付|补偿|到账|免费)"
    ),
    re.compile(
        r"(48小时|24小时|3-5天|3天|五天|7天|七天)(内)?"
        r".{0,10}(发货|退款|到账|完成|解决)"
    ),
    re.compile(r"免费.{0,10}(换新|换货|维修|退货|退款)"),
    re.compile(r"(补偿|赔付).{0,10}(元|优惠券|红包|现金)"),
    re.compile(
        r"(立即|马上|立刻).{0,10}(退款|到账|发货|换新|补偿|赔付)"
    ),
]

DYNAMIC_PATTERN = re.compile(
    r"(我的订单|订单号|快递单号|物流到哪|物流进度|到哪了|什么时候到|"
    r"发货了吗|退款进度|售后进度|有货吗|多少钱|价格|库存)"
)
PRODUCT_TERMS = [
    "耳机",
    "充电宝",
    "保温杯",
    "净化器",
    "手机",
    "电池",
    "充电",
    "蓝牙",
    "连接",
    "屏幕",
    "滤网",
    "配件",
    "颜色",
    "型号",
    "容量",
]
POLICY_TERMS = [
    "退货",
    "退款",
    "换货",
    "售后",
    "运费",
    "保修",
    "签收",
    "破损",
    "质量",
    "纠纷",
    "投诉",
    "发货",
    "配送",
    "包邮",
    "发票",
    "支付",
    "优惠券",
]


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError:
                continue


def _task_map() -> dict[str, str]:
    prompts = json.loads(
        (GENERATOR_ROOT / "metadata" / "task_system_prompts.json").read_text(
            encoding="utf-8"
        )
    )
    return {prompt: task for task, prompt in prompts.items()}


DAMAGE_PATTERN = re.compile(
    r"(破损|压坏|损坏|坏了|破了|少件|缺件|错发|漏发|渗漏|质量问题|瑕疵|假货|不一致|不一样)"
)
PRICE_STOCK_PATTERN = re.compile(
    r"(多少钱|价格|售价|降价|有货|库存|现货|缺货|补货|优惠|活动价)"
)
AVAILABILITY_PATTERN = re.compile(
    r"(有没有|有.{0,8}(吗|么)|还有|尺码|码数|颜色|规格|型号)"
)
URGENT_PATTERN = re.compile(
    r"(紧急|加急|急事|马上处理|立刻处理|人身安全|安全问题)"
)
COMPLAINT_PATTERN = re.compile(
    r"(投诉|举报|12315|消协|工商|起诉|报警|欺诈|欺骗|骗子|差评|曝光|人工)"
)
AFTER_SALE_PATTERN = re.compile(r"(退货|退款|退换|换货|维修|售后)")


def _mapped_intent(
    domain: str, intent: str, slots: dict, user: str
) -> str | None:
    """Map the generator's fine-grained labels to this app's router labels."""
    if domain == "order" and intent in {"QUERY", "URGE"}:
        if URGENT_PATTERN.search(user):
            return None
        return "order"
    if domain == "logistics" and intent in {"QUERY", "URGE", "EXCEPTION"}:
        # A damaged parcel is an after-sale fact, not a pure logistics query.
        if DAMAGE_PATTERN.search(user) or URGENT_PATTERN.search(user):
            return None
        return "order"
    if domain == "after_sales" and intent in {
        "APPLY_RETURN",
        "APPLY_EXCHANGE",
        "APPLY_REPAIR",
        "QUERY_PROGRESS",
    }:
        # A complaint/urgent escalation has higher routing priority than a
        # normal after-sale ask.
        if (
            COMPLAINT_PATTERN.search(user)
            or URGENT_PATTERN.search(user)
        ):
            return None
        return "after_sale"
    if (
        domain == "refund"
        and intent in {"APPLY", "QUERY", "URGE"}
        and slots.get("order_id")
    ):
        if (
            COMPLAINT_PATTERN.search(user)
            or URGENT_PATTERN.search(user)
        ):
            return None
        return "after_sale"
    if domain == "product" and intent in {"PRICE", "STOCK"}:
        return "price_stock"
    if domain == "product" and intent == "QUERY":
        # Availability wording ("有42码吗") is a stock check in this app.
        if PRICE_STOCK_PATTERN.search(user) or AVAILABILITY_PATTERN.search(
            user
        ):
            return "price_stock"
        return "product_knowledge"
    if domain == "complaint" and intent == "COMPLAIN":
        # Only keep explicit complaint/handoff wording. A bare "太差了" may
        # legitimately be an after-sale progress question.
        if not COMPLAINT_PATTERN.search(user):
            return None
        return "handoff"
    return None


def _expected_slots(domain: str, intent: str, slots: dict) -> dict[str, str]:
    order_no = str(slots.get("order_id") or slots.get("order_no") or "")
    product = str(slots.get("product") or slots.get("item") or "")
    refund_amount = ""
    if domain == "refund":
        refund_amount = str(slots.get("amount") or "")
    action = ""
    if domain == "after_sales":
        action = "progress" if intent == "QUERY_PROGRESS" else "new_request"
    elif domain == "refund":
        action = "progress" if intent in {"QUERY", "URGE"} else "new_request"
    return {
        "order_no": order_no,
        "query_scope": "",
        "product": product,
        "refund_amount": refund_amount,
        "after_sale_action": action,
    }


def _collect_generator():
    task_map = _task_map()
    intents_by_target: dict[str, list[dict]] = defaultdict(list)
    tool_records: list[dict] = []
    for filename in [
        GENERATOR_ROOT / "data" / "ecom" / "ecom_v2_200m.jsonl",
        GENERATOR_ROOT / "data" / "multi_industry" / "supplement_v2.jsonl",
    ]:
        for _, record in _read_jsonl(filename):
            messages = record.get("messages") or []
            if not messages:
                continue
            task = task_map.get(messages[0].get("content", ""))
            if task == "intent":
                try:
                    gold = json.loads(messages[-1]["content"])
                except (KeyError, json.JSONDecodeError, TypeError):
                    continue
                domain = str(gold.get("domain") or "")
                intent = str(gold.get("intent") or "")
                slots = gold.get("slots") or {}
                user = next(
                    (
                        str(m.get("content") or "")
                        for m in messages
                        if m.get("role") == "user"
                    ),
                    "",
                )
                project_intent = _mapped_intent(
                    domain, intent, slots, user
                )
                if project_intent is None:
                    continue
                if not user:
                    continue
                intents_by_target[project_intent].append(
                    {
                        "user": user,
                        "expected_intent": project_intent,
                        "expected_slots": _expected_slots(
                            domain, intent, slots
                        ),
                        "source_labels": {
                            "domain": domain,
                            "intent": intent,
                            "slots": slots,
                        },
                    }
                )
            elif task == "ecom":
                actions = []
                error_codes = []
                reason_codes = []
                issue_types = []
                for message in messages:
                    content = str(message.get("content") or "")
                    if message.get("role") == "assistant" and content.startswith(
                        "Action:"
                    ):
                        actions.append(
                            content.splitlines()[0].replace("Action:", "").strip()
                        )
                    if message.get("role") == "user" and content.startswith(
                        "工具返回:"
                    ):
                        try:
                            result = json.loads(content.split(":", 1)[1])
                        except (IndexError, json.JSONDecodeError):
                            continue
                        data = result.get("data") or {}
                        if data.get("error_code"):
                            error_codes.append(str(data["error_code"]))
                        if data.get("reason_code"):
                            reason_codes.append(str(data["reason_code"]))
                        if data.get("issue_type"):
                            issue_types.append(str(data["issue_type"]))
                if not actions:
                    continue
                user = next(
                    (
                        str(m.get("content") or "")
                        for m in messages
                        if m.get("role") == "user"
                    ),
                    "",
                )
                tool_records.append(
                    {
                        "messages": messages,
                        "user": user,
                        "actions": actions,
                        "first_action": actions[0],
                        "error_codes": sorted(set(error_codes)),
                        "reason_codes": sorted(set(reason_codes)),
                        "issue_types": sorted(set(issue_types)),
                    }
                )
    return intents_by_target, tool_records


def _sample_intents(intents_by_target, seed: int) -> list[dict]:
    rng = random.Random(seed)
    assets = []
    for target_intent, target_count in INTENT_TARGETS.items():
        candidates = intents_by_target.get(target_intent, [])
        if not candidates:
            continue
        if target_count >= len(candidates):
            chosen = candidates
        else:
            chosen = rng.sample(candidates, target_count)
        for item in chosen:
            assets.append(
                {
                    "id": f"intent-{len(assets) + 1:04d}",
                    "task": "intent",
                    "source": "生成器/ecom_v2_200m.jsonl",
                    "user": item["user"],
                    "expected": {
                        "intent": item["expected_intent"],
                        "slots": item["expected_slots"],
                    },
                    "source_labels": item["source_labels"],
                    "notes": "生成器细粒度标签已映射到本系统意图；歧义标签未纳入",
                }
            )
    return assets


def _sample_tools(tool_records: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    buckets = {
        "create": [
            r
            for r in tool_records
            if "create_after_sales_request" in r["actions"]
        ],
        "policy": [
            r
            for r in tool_records
            if "check_return_policy" in r["actions"]
            and "create_after_sales_request" not in r["actions"]
        ],
        "error": [
            r
            for r in tool_records
            if any(
                code in {"TIMEOUT", "SERVICE_UNAVAILABLE", "ORDER_NOT_FOUND"}
                for code in r["error_codes"]
            )
        ],
        "query": [
            r
            for r in tool_records
            if r["actions"] == ["query_order_status"]
        ],
    }
    quotas = {"create": 50, "policy": 60, "error": 50, "query": 40}
    chosen = []
    seen = set()
    for bucket, quota in quotas.items():
        candidates = buckets[bucket]
        if quota >= len(candidates):
            picked = candidates
        else:
            picked = rng.sample(candidates, quota)
        for item in picked:
            key = item["user"] + "|" + "|".join(item["actions"])
            if key in seen:
                continue
            seen.add(key)
            chosen.append(
                {
                    "id": f"tool-{len(chosen) + 1:04d}",
                    "task": "tool_decision",
                    "source": "生成器/supplement_v2.jsonl",
                    "messages": item["messages"],
                    "user": item["user"],
                    "expected": {
                        "first_action": item["first_action"],
                        "actions": item["actions"],
                    },
                    "meta": {
                        "issue_types": item["issue_types"],
                        "reason_codes": item["reason_codes"],
                        "error_codes": item["error_codes"],
                    },
                    "notes": "工具名来自生成器 synthetic-v2，用于能力评测，未直接接入本系统工具",
                }
            )
    return chosen[:200]


def _load_qa() -> list[dict]:
    records = []
    for name, path in QA_PATHS.items():
        for _, record in _read_jsonl(path):
            user = str(record.get("user") or "").strip()
            bot = str(record.get("bot") or "").strip()
            if not user or not bot:
                continue
            records.append(
                {
                    "dataset": name,
                    "scene": str(record.get("scene") or ""),
                    "tone": str(record.get("tone") or ""),
                    "user": user,
                    "bot": bot,
                }
            )
    return records


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～：:；;\"“”‘’]+", "", text).lower()


def _load_sections() -> list[dict]:
    sections = []
    for path in sorted(KNOWLEDGE_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = next(
            (line.lstrip("# ").strip() for line in lines if line.startswith("# ")),
            path.stem,
        )
        current_heading = "概述"
        current_lines = []
        for line in lines[1:]:
            if line.startswith("## "):
                if current_lines:
                    sections.append(
                        _make_section(
                            path, title, current_heading, current_lines
                        )
                    )
                current_heading = line[3:].strip()
                current_lines = []
            elif line.startswith("# "):
                continue
            else:
                current_lines.append(line)
        if current_lines:
            sections.append(
                _make_section(path, title, current_heading, current_lines)
            )
    return sections


def _make_section(path: Path, title: str, heading: str, lines: list[str]):
    content = "\n".join(lines).strip()
    content = re.sub(r"\n{2,}", "\n", content)
    return {
        "title": title,
        "heading": heading,
        "kb_type": "policy" if "policies" in path.parts else "product",
        "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "content": content,
        "keywords": _normalize(title + heading + content),
    }


def _score_section(question: str, section: dict) -> int:
    q = _normalize(question)
    score = 0
    for term in ["退货", "退款", "换货", "售后", "运费", "保修", "签收", "破损", "质量", "纠纷", "发货", "配送", "包邮"]:
        if term in q and term in section["keywords"]:
            score += 4
    for term in PRODUCT_TERMS:
        if term in q and term in section["keywords"]:
            score += 5
    for term in ["耳机", "充电宝", "保温杯", "净化器"]:
        if term in q and term in section["keywords"]:
            score += 4
    q_bigrams = {q[i : i + 2] for i in range(len(q) - 1)}
    s_bigrams = {
        section["keywords"][i : i + 2]
        for i in range(len(section["keywords"]) - 1)
    }
    score += len(q_bigrams & s_bigrams) / 8
    return int(score)


def _is_candidate_qa(record: dict) -> bool:
    question = record["user"]
    if len(question) > 80 or DYNAMIC_PATTERN.search(question):
        return False
    if not any(term in question for term in PRODUCT_TERMS + POLICY_TERMS):
        return False
    if any(term in record["scene"] for term in ["物流停滞", "丢件", "催发", "差评"]):
        return False
    return True


def _best_dataset_wording(question: str, qa_questions: list[dict]) -> dict | None:
    """Use a synthetic customer wording when it is close enough to mean the same."""
    q = _normalize(question)
    q_bigrams = {q[i : i + 2] for i in range(len(q) - 1)}
    best = None
    best_score = 0.0
    for candidate in qa_questions:
        other = candidate["normalized"]
        if "差评" in other or "曝光" in other or "12315" in other:
            continue
        o_bigrams = {other[i : i + 2] for i in range(len(other) - 1)}
        if not q_bigrams or not o_bigrams:
            continue
        union = q_bigrams | o_bigrams
        score = len(q_bigrams & o_bigrams) / len(union)
        if score > best_score:
            best_score = score
            best = candidate
    if best is None or best_score < 0.62:
        return None
    return best


def _faq_candidates(qa_records: list[dict], sections: list[dict]) -> list[dict]:
    """Curate 100 answerable FAQs from official docs.

    The synthetic QA is used only to borrow natural customer wording.  The
    answer and source always come from an exact official-document section,
    never from the synthetic bot answer.
    """
    section_map = {
        (section["title"], section["heading"]): section for section in sections
    }
    qa_questions = []
    seen_qa = set()
    for record in qa_records:
        if not _is_candidate_qa(record):
            continue
        normalized = _normalize(record["user"])
        if normalized in seen_qa:
            continue
        seen_qa.add(normalized)
        qa_questions.append(
            {
                "question": record["user"],
                "normalized": normalized,
                "dataset": record["dataset"],
                "scene": record["scene"],
            }
        )

    missing = [
        (title, heading)
        for title, heading, _ in FAQ_CATALOG
        if (title, heading) not in section_map
    ]
    if missing:
        raise ValueError(f"FAQ catalog references missing sections: {missing}")

    candidates = []
    for index, (title, heading, catalog_question) in enumerate(
        FAQ_CATALOG, start=1
    ):
        section = section_map[(title, heading)]
        matched = _best_dataset_wording(catalog_question, qa_questions)
        question = matched["question"] if matched else catalog_question
        candidates.append(
            {
                "id": f"faq-{index:04d}",
                "question": question,
                "catalog_question": catalog_question,
                "kb_type": section["kb_type"],
                "source_title": title,
                "source_heading": heading,
                "source_path": section["path"],
                "source_snippet": section["content"][:700],
                "answer_draft": section["content"],
                "origin": (
                    f"dataset:{matched['dataset']}:{matched['scene']}"
                    if matched
                    else "knowledge:catalog"
                ),
                "review_status": "pending",
                "importable": True,
            }
        )
    return candidates


FAQ_CATALOG = [
    ("退换货政策", "退换货时限", "签收后多久可以申请无理由退货？"),
    ("退换货政策", "退换货时限", "质量问题多久内可以换货或维修？"),
    ("退换货政策", "退换货时限", "哪些商品不支持无理由退货？"),
    ("退换货政策", "申请条件", "申请退货对包装配件有什么要求？"),
    ("退换货政策", "申请条件", "质量问题退换货需要提供什么凭证？"),
    ("退换货政策", "申请条件", "售后审核通过后怎么处理？"),
    ("退换货政策", "退款流程", "退货退款多久到账？"),
    ("退换货政策", "退款流程", "退款退回到哪个账户？"),
    ("退换货政策", "退款流程", "使用优惠券的订单退款怎么算？"),
    ("退换货政策", "运费承担", "质量问题退货运费谁承担？"),
    ("退换货政策", "运费承担", "无理由退货运费谁承担？"),
    ("退换货政策", "运费承担", "大件商品上门取件怎么预约？"),
    ("物流与运费规则", "配送范围与时效", "平台支持哪些地区配送？"),
    ("物流与运费规则", "配送范围与时效", "现货商品付款后多久发货？"),
    ("物流与运费规则", "配送范围与时效", "预售商品什么时候发货？"),
    ("物流与运费规则", "运费标准", "运费怎么计算？"),
    ("物流与运费规则", "运费标准", "什么条件下可以包邮？"),
    ("物流与运费规则", "运费标准", "运费在哪里查看？"),
    ("物流与运费规则", "物流查询", "发货后在哪里查看物流进度？"),
    ("物流与运费规则", "物流查询", "没有物流单号怎么查？"),
    ("物流与运费规则", "物流查询", "客服能协助查询物流吗？"),
    ("物流与运费规则", "签收与异常", "签收前要检查什么？"),
    ("物流与运费规则", "签收与异常", "外包装破损可以拒收吗？"),
    ("物流与运费规则", "签收与异常", "物流长时间不更新怎么办？"),
    ("交易纠纷处理规则", "协商解决", "商品有质量问题怎么和商家协商？"),
    ("交易纠纷处理规则", "协商解决", "协商需要提供哪些订单信息？"),
    ("交易纠纷处理规则", "协商解决", "协商一致后怎么处理？"),
    ("交易纠纷处理规则", "平台介入", "协商不一致怎么申请平台介入？"),
    ("交易纠纷处理规则", "平台介入", "平台介入依据哪些材料判定责任？"),
    ("交易纠纷处理规则", "平台介入", "平台多长时间给出处理意见？"),
    ("交易纠纷处理规则", "举证要求", "申请平台介入要提供哪些证据？"),
    ("交易纠纷处理规则", "举证要求", "证据不足会有什么影响？"),
    ("交易纠纷处理规则", "举证要求", "商品照片可以作为证据吗？"),
    ("交易纠纷处理规则", "处理结果", "平台处理结果双方要执行吗？"),
    ("交易纠纷处理规则", "处理结果", "对处理结果不满意怎么办？"),
    ("交易纠纷处理规则", "处理结果", "可以向有关部门投诉或诉讼吗？"),
    ("售后与保修政策", "保修范围", "保修期内哪些故障可以免费维修？"),
    ("售后与保修政策", "保修范围", "哪些部件不在保修范围？"),
    ("售后与保修政策", "保修范围", "保修期限以什么为准？"),
    ("售后与保修政策", "保修流程", "怎么申请保修？"),
    ("售后与保修政策", "保修流程", "保修需要提供什么信息？"),
    ("售后与保修政策", "保修流程", "送修后多久能完成维修？"),
    ("售后与保修政策", "非保修情形", "哪些情况属于非保修？"),
    ("售后与保修政策", "非保修情形", "人为损坏怎么维修？"),
    ("售后与保修政策", "非保修情形", "超过保修期还能维修吗？"),
    ("售后与保修政策", "延保服务", "延保服务怎么购买？"),
    ("售后与保修政策", "延保服务", "延保要在签收后多久内购买？"),
    ("售后与保修政策", "延保服务", "延保期内怎么报修？"),
    ("声动 Pro 无线蓝牙耳机", "概述", "声动 Pro 单次充电能播放多久？"),
    ("声动 Pro 无线蓝牙耳机", "概述", "搭配充电盒总续航多久？"),
    ("声动 Pro 无线蓝牙耳机", "产品参数", "耳机蓝牙版本是多少？"),
    ("声动 Pro 无线蓝牙耳机", "产品参数", "支持哪些蓝牙编码？"),
    ("声动 Pro 无线蓝牙耳机", "产品参数", "耳机防水等级是多少？能游泳吗？"),
    ("声动 Pro 无线蓝牙耳机", "产品参数", "耳机充电接口和快充情况？"),
    ("声动 Pro 无线蓝牙耳机", "使用方法", "首次使用怎么配对？"),
    ("声动 Pro 无线蓝牙耳机", "使用方法", "日常使用怎么自动回连？"),
    ("声动 Pro 无线蓝牙耳机", "使用方法", "耳机首次使用要撕保护膜吗？"),
    ("声动 Pro 无线蓝牙耳机", "注意事项", "耳机怎么清洁？"),
    ("声动 Pro 无线蓝牙耳机", "注意事项", "耳机在什么环境下不适合长时间存放？"),
    ("声动 Pro 无线蓝牙耳机", "保修政策", "耳机保修多久？"),
    ("声动 Pro 无线蓝牙耳机", "保修政策", "耳机哪些损坏不保修？"),
    ("疾充 10000mAh 便携充电宝", "概述", "充电宝容量是多少？"),
    ("疾充 10000mAh 便携充电宝", "概述", "充电宝有哪些接口？"),
    ("疾充 10000mAh 便携充电宝", "产品参数", "充电宝支持多大功率快充？"),
    ("疾充 10000mAh 便携充电宝", "产品参数", "充电宝重量和尺寸是多少？"),
    ("疾充 10000mAh 便携充电宝", "产品参数", "充电宝充满自身要多久？"),
    ("疾充 10000mAh 便携充电宝", "产品参数", "充电宝支持边充边放吗？"),
    ("疾充 10000mAh 便携充电宝", "使用方法", "充电宝怎么给设备充电？"),
    ("疾充 10000mAh 便携充电宝", "使用方法", "小电流模式怎么关闭？"),
    ("疾充 10000mAh 便携充电宝", "使用方法", "充电宝指示灯怎么看电量？"),
    ("疾充 10000mAh 便携充电宝", "注意事项", "充电宝长时间不用怎么保养？"),
    ("疾充 10000mAh 便携充电宝", "注意事项", "充电宝使用时要避免什么？"),
    ("疾充 10000mAh 便携充电宝", "保修政策", "充电宝保修多久？"),
    ("疾充 10000mAh 便携充电宝", "保修政策", "充电宝进水或拆解后还能保修吗？"),
    ("恒温智能保温杯", "概述", "保温杯容量和材质是什么？"),
    ("恒温智能保温杯", "概述", "保温杯保温和保冷时效多久？"),
    ("恒温智能保温杯", "产品参数", "保温杯重量是多少？"),
    ("恒温智能保温杯", "产品参数", "杯盖屏幕怎么查看水温？"),
    ("恒温智能保温杯", "使用方法", "怎么切换摄氏和华氏？"),
    ("恒温智能保温杯", "产品参数", "杯盖电池能用多久？"),
    ("恒温智能保温杯", "使用方法", "保温杯首次使用怎么清洗？"),
    ("恒温智能保温杯", "使用方法", "杯盖电池怎么更换？"),
    ("恒温智能保温杯", "使用方法", "保温杯怎么装饮品？"),
    ("恒温智能保温杯", "注意事项", "保温杯可以装碳酸饮料或牛奶吗？"),
    ("恒温智能保温杯", "注意事项", "保温杯可以放微波炉或洗碗机吗？"),
    ("恒温智能保温杯", "保修政策", "保温杯保修多久？"),
    ("恒温智能保温杯", "保修政策", "保温杯哪些部件不保修？"),
    ("清呼吸 家用空气净化器", "概述", "净化器适用面积多大？"),
    ("清呼吸 家用空气净化器", "概述", "净化器能过滤什么？"),
    ("清呼吸 家用空气净化器", "产品参数", "净化器 CADR 是多少？"),
    ("清呼吸 家用空气净化器", "产品参数", "净化器噪音和功率是多少？"),
    ("清呼吸 家用空气净化器", "产品参数", "滤网多久更换一次？"),
    ("清呼吸 家用空气净化器", "产品参数", "净化器机身尺寸是多少？"),
    ("清呼吸 家用空气净化器", "使用方法", "净化器首次使用要注意什么？"),
    ("清呼吸 家用空气净化器", "使用方法", "净化器自动模式怎么工作？"),
    ("清呼吸 家用空气净化器", "使用方法", "睡眠模式怎么用？"),
    ("清呼吸 家用空气净化器", "注意事项", "净化器外壳怎么清洁？"),
    ("清呼吸 家用空气净化器", "注意事项", "滤网可以水洗吗？"),
    ("清呼吸 家用空气净化器", "保修政策", "净化器保修多久？"),
    ("清呼吸 家用空气净化器", "保修政策", "滤网和核心部件保修吗？"),
]


def _sentinel_cases(qa_records: list[dict], faqs: list[dict]) -> list[dict]:
    negatives = []
    seen = set()
    for record in qa_records:
        if not any(
            pattern.search(record["bot"]) for pattern in RISK_PATTERNS
        ):
            continue
        question = record["user"]
        if question in seen:
            continue
        seen.add(question)
        negatives.append(
            {
                "id": f"sentinel-neg-{len(negatives) + 1:04d}",
                "case": "negative",
                "question": question,
                "answer": record["bot"],
                "intent": (
                    "after_sale"
                    if record["dataset"] in {"after_sales", "logistics"}
                    else "policy_knowledge"
                ),
                "context": "",
                "sources": [],
                "expected": "flag",
                "origin": f"{record['dataset']}:{record['scene']}",
            }
        )
        if len(negatives) == 50:
            break
    positives = []
    for item in faqs:
        if len(positives) == 50:
            break
        positives.append(
            {
                "id": f"sentinel-pos-{len(positives) + 1:04d}",
                "case": "positive",
                "question": item["question"],
                "answer": item["answer_draft"],
                "intent": "policy_knowledge"
                if item["kb_type"] == "policy"
                else "product_knowledge",
                "context": item["source_snippet"],
                "sources": [
                    {
                        "title": item["source_title"],
                        "source_uri": item["source_path"],
                    }
                ],
                "expected": "pass",
                "origin": f"faq:{item['id']}",
            }
        )
    return negatives + positives


PHONE_PATTERN = re.compile(r"(?<![\w-])1[3-9]\d{9}(?![\w-])")
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _redact_text(text: str) -> str:
    text = PHONE_PATTERN.sub("[手机号已脱敏]", text)
    return EMAIL_PATTERN.sub("[邮箱已脱敏]", text)


def _redact_value(value):
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for record in records:
            target.write(
                json.dumps(_redact_value(record), ensure_ascii=False) + "\n"
            )


def _write_faq_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "question",
        "kb_type",
        "source_title",
        "source_heading",
        "source_path",
        "source_snippet",
        "answer_draft",
        "origin",
        "review_status",
        "importable",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261121)
    args = parser.parse_args()

    intents_by_target, tool_records = _collect_generator()
    qa_records = _load_qa()
    sections = _load_sections()
    intents = _sample_intents(intents_by_target, args.seed)
    tools = _sample_tools(tool_records, args.seed)
    faqs = _faq_candidates(qa_records, sections)
    sentinels = _sentinel_cases(qa_records, faqs)

    _write_jsonl(Path("data/eval/ecom_intent_300.jsonl"), intents)
    _write_jsonl(Path("data/eval/ecom_tool_200.jsonl"), tools)
    _write_jsonl(Path("data/eval/sentinel_100.jsonl"), sentinels)
    _write_faq_csv(
        Path("data/semantic_cache/faq_candidates_100.csv"), faqs
    )
    with Path("data/semantic_cache/faq_candidates_100.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as target:
        json.dump(faqs, target, ensure_ascii=False, indent=2)
        target.write("\n")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "sources": {
            "intent": "生成器/data/ecom/ecom_v2_200m.jsonl",
            "tool": "生成器/data/multi_industry/supplement_v2.jsonl",
            "negative": [str(p.relative_to(PROJECT_ROOT)) for p in QA_PATHS.values()],
            "faq": [str(p.relative_to(PROJECT_ROOT)) for p in QA_PATHS.values()]
            + [str(p.relative_to(PROJECT_ROOT)) for p in KNOWLEDGE_ROOT.rglob("*.md")],
        },
        "counts": {
            "intent": len(intents),
            "tool": len(tools),
            "sentinel_negative": sum(1 for s in sentinels if s["case"] == "negative"),
            "sentinel_positive": sum(1 for s in sentinels if s["case"] == "positive"),
            "faq": len(faqs),
            "faq_policy": sum(1 for f in faqs if f["kb_type"] == "policy"),
            "faq_product": sum(1 for f in faqs if f["kb_type"] == "product"),
        },
        "excluded": {
            "多轮对话数据集": "包含手机号、姓名和详细地址；未复制到派生资产",
            "商品回复数据集": "样本小且含承诺性话术；未用于 FAQ 候选",
        },
        "licenses": {
            "生成器": "Apache-2.0（数据），MIT（生成器）",
            "多场景/售后/物流": "Apache-2.0（合成数据）",
            "知识库": "项目自带演示文档",
        },
        "notes": [
            "生成器标签为 synthetic-v2，工具名与本项目工具不同；tool 数据仅用于能力评测。",
            "FAQ answer_draft 只从官方知识库章节摘录，未采用合成客服 bot 回答。",
            "FAQ 均为 pending，未写入 semantic_cache。",
        ],
    }
    Path("data/eval/manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
