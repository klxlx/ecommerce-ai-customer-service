"""Customer-facing tone, empathy and de-escalation helpers.

The wording below is distilled from the curated 多场景/售后维权 service corpus.
Only the tone and safe sentence patterns are reused; concrete compensation,
coupon or delivery-time promises from the raw dataset are deliberately excluded
because they are not backed by this project's business data.
"""

from __future__ import annotations

import re

# A customer threatening a person, including customer-service staff.
_THREAT_PATTERN = re.compile(
    r"(打人|打你|打死|杀人|弄死|揍你|砍死|捅死|炸死|弄残|"
    r"报复你|威胁你|恐吓你|找人收拾你|废了你|卸你)"
)
# Self-harm risk needs a separate, gentler response.
_SELF_HARM_PATTERN = re.compile(
    r"(自杀|轻生|不想活|活不下去|伤害自己|自残|割腕|跳楼|"
    r"我想死|想死了|不如死)"
)
_NEGATIVE_PATTERN = re.compile(
    r"(气死|气炸|气疯|气哭|很生气|太生气|火大|恼火|不爽|委屈|想哭|"
    r"烦死|崩溃|受不了|太失望|失望透顶|太差了|差劲|不满意|"
    r"什么玩意|服了|无语|恶心|"
    r"垃圾(?:客服|东西|产品|服务|平台|系统|你们|公司)|什么垃圾|"
    r"骗子|坑人|什么破|烂货|差评|退钱|赔钱|赔偿|你们等着|"
    r"必须给|马上给|立刻给|滚(?:开|出去|蛋)|闭嘴|去死|"
    r"傻逼|脑残|废物|蠢货|白痴|神经病|有病)"
)
_BUSINESS_TOPIC_PATTERN = re.compile(
    r"(订单|物流|快递|发货|退款|退货|换货|售后|价格|多少钱|库存|"
    r"有货|商品|产品|型号|sku|保修|发票|优惠|运费|支付|付款|"
    r"耳机|蓝牙|手机|电脑|充电|电池|屏幕|尺寸|颜色|配件|"
    r"功能|参数|版本|连接|配对|使用|操作|设置)",
    re.IGNORECASE,
)
_ALREADY_FRIENDLY_PATTERN = re.compile(
    r"^(亲|您好|你好|非常抱歉|很抱歉|抱歉|理解您|您先别着急)"
)
_EMPATHY_PATTERN = re.compile(
    r"^(非常抱歉|很抱歉|抱歉|理解您|您先别着急|"
    r"亲，非常抱歉|亲，您先别着急)"
)

# These examples are shown to the model only as a tone guide. They must never
# be treated as factual policy or as authority for compensation promises.
DATASET_TONE_GUIDE = """【服务语气参考（只学语气，不作为事实依据）】
1. 普通咨询：亲，您好～这个我帮您确认一下。
2. 用户着急或不满：亲，您先别着急，我来帮您处理。
3. 用户失望或投诉：非常抱歉给您带来不好的体验，您反馈的问题我记下了，我来帮您跟进解决。
4. 用户收到破损商品：亲，非常抱歉给您带来困扰，请您先拍照留证，我来帮您处理退款或换货。
5. 结尾可用：感谢您的理解和支持；有进展我会第一时间同步您。
6. 不照抄语料中的优惠券、赔偿金额、保证处理、48小时等具体承诺；这些必须来自当前业务资料。
"""


def emotion_kind(text: str) -> str:
    """Return ``self_harm``, ``threat``, ``anger`` or ``none``."""
    cleaned = " ".join((text or "").split())
    if _SELF_HARM_PATTERN.search(cleaned):
        return "self_harm"
    if _THREAT_PATTERN.search(cleaned):
        return "threat"
    if _NEGATIVE_PATTERN.search(cleaned):
        return "anger"
    return "none"


def is_emotional(text: str) -> bool:
    return emotion_kind(text) != "none"


def has_business_topic(text: str) -> bool:
    return bool(_BUSINESS_TOPIC_PATTERN.search(text or ""))


def is_pure_emotion(text: str) -> bool:
    """True for a message that must go through de-escalation first.

    Threats and self-harm language always take the safety branch, even when
    the message also mentions an order or product.  Bare anger only takes it
    when there is no concrete business topic to answer.
    """
    kind = emotion_kind(text)
    if kind in {"threat", "self_harm"}:
        return True
    return kind == "anger" and not has_business_topic(text)


def deescalation_reply(text: str) -> str:
    """A safe, non-judgemental reply for threats, insults and distress."""
    kind = emotion_kind(text)
    if kind == "self_harm":
        return (
            "非常抱歉让您这么难受。您的安全最重要，请先照顾好自己，"
            "不要伤害自己。如果方便，可以告诉我具体发生了什么，我来帮您处理；"
            "也可以回复“转人工”，我为您转接人工客服。"
        )
    if kind == "threat":
        return (
            "非常抱歉让您有这样的情绪。我非常理解您现在很生气，"
            "您的安全和他人的安全都很重要，请先冷静一下，"
            "不要伤害自己或他人。您可以把遇到的具体问题告诉我，"
            "我会优先帮您核实；也可以回复“转人工”，我为您转接人工客服。"
        )
    return (
        "非常抱歉给您带来不好的体验，您先别着急，我来帮您处理。"
        "请告诉我具体是订单、商品还是售后问题，我会优先为您核实。"
    )


def handoff_reply(text: str) -> str:
    """Empathy before the fixed handoff acknowledgement."""
    kind = emotion_kind(text)
    if kind == "none" and re.search(
        r"(投诉|举报|12315|差评|曝光)", text or ""
    ):
        kind = "anger"
    if kind in {"threat", "self_harm"}:
        if kind == "self_harm":
            opener = (
                "非常抱歉让您这么难受。您的安全最重要，请先照顾好自己，"
                "不要伤害自己。"
            )
        else:
            opener = (
                "非常抱歉让您有这样的情绪。我非常理解您现在很生气，"
                "您的安全和他人的安全都很重要，请先冷静一下，"
                "不要伤害自己或他人。"
            )
        return f"{opener}已为您转接人工客服，客服会尽快与您联系。"
    if kind == "anger":
        return (
            "非常抱歉给您带来不好的体验，您先别着急。"
            "已为您转接人工客服，客服会尽快与您联系并帮您处理。"
        )
    return "已为您转接人工客服，客服会尽快与您联系。"


def active_handoff_reply(text: str) -> str:
    """Acknowledge emotion even when a human ticket is already active."""
    kind = emotion_kind(text)
    if kind == "none" and re.search(
        r"(投诉|举报|12315|差评|曝光)", text or ""
    ):
        kind = "anger"
    if kind == "self_harm":
        return (
            "非常抱歉让您这么难受。您的安全最重要，请先照顾好自己，"
            "不要伤害自己。这条消息已同步给人工客服，请稍候。"
        )
    if kind == "threat":
        return (
            "非常抱歉让您有这样的情绪。您的安全和他人的安全都很重要，"
            "请先冷静一下，不要伤害自己或他人。这条消息已同步给人工客服，请稍候。"
        )
    if kind == "anger":
        return (
            "非常抱歉给您带来不好的体验，您先别着急。"
            "这条消息已同步给人工客服，客服会尽快回复您。"
        )
    return "消息已发送给人工客服，请稍候。"


def style_cached_answer(answer: str, kb_type: str, question: str = "") -> str:
    """Add a dataset-style friendly opener around a curated FAQ answer.

    The stored FAQ stays in its formal, reviewed form; this only changes the
    customer-facing presentation so the curated answer is both visible and
    approachable.
    """
    cleaned = (answer or "").strip()
    if not cleaned:
        return cleaned
    kind = emotion_kind(question)
    if kind in {"threat", "self_harm"}:
        opener = (
            "非常抱歉让您有这样的情绪，请先冷静一下，不要伤害自己或他人。"
        )
    elif kind == "anger":
        opener = "非常抱歉给您带来不好的体验，您先别着急，我帮您确认一下～"
    elif _ALREADY_FRIENDLY_PATTERN.search(cleaned):
        return cleaned
    elif kb_type == "policy" and any(
        term in (question or "")
        for term in ("退款", "退货", "退换", "售后", "破损", "投诉")
    ):
        opener = "亲，您先别着急，我帮您确认一下～"
    else:
        opener = "亲，您好～"
    if _ALREADY_FRIENDLY_PATTERN.search(cleaned):
        return cleaned
    return f"{opener}{cleaned}"


def empathy_prefix(text: str) -> str:
    """Return an empathy opener for an upset customer, otherwise ``""``."""
    kind = emotion_kind(text)
    if kind == "self_harm":
        return (
            "非常抱歉让您这么难受。您的安全最重要，请先照顾好自己，"
            "不要伤害自己。"
        )
    if kind == "threat":
        return (
            "非常抱歉让您有这样的情绪。我非常理解您现在很生气，"
            "您的安全和他人的安全都很重要，请先冷静一下，"
            "不要伤害自己或他人。"
        )
    if kind == "anger":
        return "非常抱歉给您带来不好的体验，您先别着急。"
    return ""


def ensure_empathy(text: str, answer: str) -> str:
    """Prefix empathy when the customer is upset and the reply lacks it."""
    cleaned = (answer or "").strip()
    if not cleaned:
        return cleaned
    kind = emotion_kind(text)
    if kind == "none" or _EMPATHY_PATTERN.search(cleaned):
        return cleaned
    return f"{empathy_prefix(text)}{cleaned}"
