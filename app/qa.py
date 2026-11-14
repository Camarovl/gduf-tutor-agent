"""M1 知识点问答。

链路：问题 → 检索相关知识点 → 拼上下文 → 模型作答（带学科边界约束）
"""

from __future__ import annotations

import json

from app import db, prompts
from app.llm_client import LLMError, chat


def _format_context(kps: list[dict]) -> str:
    parts = []
    for k in kps:
        try:
            mistakes = json.loads(k.get("common_mistakes") or "[]")
        except json.JSONDecodeError:
            mistakes = []
        block = f"### {k['name']}\n{k.get('explain') or ''}"
        if mistakes:
            block += "\n常见误区：" + "；".join(mistakes)
        parts.append(block)
    return "\n\n".join(parts)


def detect_off_topic(text: str) -> bool:
    """粗判模型是否已经在拒绝。用于界面提示，不是硬约束。"""
    keys = ("超出", "不在", "无法回答", "抱歉", "范围", "本课程", "这门课")
    return any(k in text for k in keys)


def answer(question: str, subject: str, style: str, history: list[dict] | None = None) -> str:
    """回答一个学科问题。

    history: [{"role": "human"/"assistant", "content": ...}]，由界面传入。
    返回 markdown 文本。学科越界时模型按 prompt 要求自行拒绝。
    """
    # 1. 先用数据库把相关知识点捞出来当上下文（这是"限定学科"的实质手段）
    keywords = [question[:12]]
    for seg in question.split():
        if len(seg) >= 2 and seg not in keywords:
            keywords.append(seg[:8])

    hits: list[dict] = []
    for kw in keywords[:6]:
        for row in db.find_relevant_kps(kw, limit=3):
            if row["id"] not in {h["id"] for h in hits}:
                hits.append(row)
        if len(hits) >= 4:
            break

    context_block = (
        prompts.CONTEXT_TEMPLATE.format(blocks=_format_context(hits))
        if hits
        else "\n注意：本课程资料库暂无与该问题直接对应的条目，请在本课程范围内作答。\n"
    )

    system = prompts.QA_SYSTEM.format(
        subject=subject,
        style_block=prompts.STYLE_CONCISE if style == "简洁" else prompts.STYLE_DETAILED,
        context_block=context_block,
    )

    messages = [{"role": "system", "content": system}]
    for m in (history or []):
        role = "assistant" if m["role"] == "assistant" else "user"
        messages.append({"role": role, "content": m["content"]})
    messages.append({"role": "user", "content": question})

    result = chat(messages, purpose="qa")
    return result.text


def list_subject_topics() -> list[str]:
    """给界面展示"本系统覆盖哪些知识点"，也方便评审验证学科边界。"""
    return [k["name"] for k in db.all_knowledge_points()]
