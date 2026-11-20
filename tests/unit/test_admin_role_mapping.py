"""Persisted message roles must be provider-safe before reaching the model."""

from types import SimpleNamespace

from app.services.chat import _llm_history_message


def _message(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


def test_admin_reply_is_mapped_to_assistant_with_label():
    message = _message("admin", "已为您登记补发。")
    mapped = _llm_history_message(message)
    assert mapped == {
        "role": "assistant",
        "content": "[人工客服] 已为您登记补发。",
    }


def test_system_notice_is_mapped_to_assistant():
    message = _message("system", "人工服务已结束，智能体继续为您服务。")
    mapped = _llm_history_message(message)
    assert mapped == {
        "role": "assistant",
        "content": "[系统] 人工服务已结束，智能体继续为您服务。",
    }


def test_customer_and_agent_roles_are_kept():
    assert _llm_history_message(_message("user", "你好")) == {
        "role": "user",
        "content": "你好",
    }
    assert _llm_history_message(_message("assistant", "您好")) == {
        "role": "assistant",
        "content": "您好",
    }
