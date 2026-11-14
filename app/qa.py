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


def retrieve_knowledge(question: str, limit: int = 4) -> list[dict]:
    """按名称匹配检索相关知识点，作为回答的上下文依据。

    🔴 不能用 `question.split()` 切中文 —— 中文句子没有空格，切出来是整句话，
    拿去 LIKE 查询永远命中 0 条，会让"限定学科"的实质约束静默失效。
    这里改为反向匹配：把每个知识点的名称切成片段，看哪些出现在问题里。
    21 个节点全表扫一遍开销可忽略，且无需引入分词库。

    评分：名称整体命中 > 片段命中数量。无任何命中时返回空列表，
    上层据此走"资料库无对应条目"分支并仍受学科范围约束。
    """
    q = question.lower()
    scored: list[tuple[int, dict]] = []

    for k in db.all_knowledge_points():
        name = (k["name"] or "").lower()
        if not name:
            continue

        terms: set[str] = set()
        for seg in name.replace("、", "|").replace("与", "|").replace("和", "|").split("|"):
            s = seg.strip()
            if len(s) >= 2:
                terms.add(s)
            for n in (2, 3, 4):  # 滑窗补子串，让"导数"能命中"导数的概念与几何意义"
                for i in range(max(0, len(s) - n + 1)):
                    sub = s[i : i + n]
                    if len(sub) == n and not sub.isascii():
                        terms.add(sub)

        hits = sum(1 for t in terms if t in q)
        score = 6 if name in q else hits
        if score:
            scored.append((score, k))

    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [k for _, k in scored[:limit]]


def answer(question: str, subject: str, style: str, history: list[dict] | None = None) -> str:
    """回答一个学科问题。

    history: [{"role": "human"/"assistant", "content": ...}]，由界面传入。
    返回 markdown 文本。学科越界时模型按 prompt 要求自行拒绝。
    """
    # 1. 先用数据库把相关知识点捞出来当上下文（这是"限定学科"的实质手段）
    hits = retrieve_knowledge(question)

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
