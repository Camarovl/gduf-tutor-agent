"""LLM 输出的结构校验。

这是评分维度「针对场景做约束与调优」最直接落地的地方：
模型返回的 JSON 经常缺字段、多废话、发明不存在的知识点 ID，
必须在这里挡住并触发带原因的重试。
"""

from __future__ import annotations

ERROR_TYPES = {
    "concept_confusion",
    "calculation_error",
    "method_wrong",
    "incomplete",
    "unknown",
}

INTAKE_REQUIRED = (
    "question_text",
    "student_answer",
    "correct_answer",
    "solution_steps",
    "knowledge_ids",
    "error_type",
)


def make_intake_validator(allowed_kp_ids: set[str]):
    """返回一个 validator 函数给 llm_client.chat() 用。

    校验失败返回错误描述字符串（会回喂给模型重试），全部通过返回 None。
    🔴 最关键的一条：knowledge_ids 必须在候选集内。否则会出现
    「二次函数图像」「二次函数的图象」「抛物线性质」三种标签指同一件事，
    M3 的统计直接作废。
    """

    def validator(obj: dict) -> str | None:
        if not isinstance(obj, dict):
            return "输出必须是 JSON 对象"

        missing = [k for k in INTAKE_REQUIRED if k not in obj]
        if missing:
            return f"缺少必填字段：{missing}"

        if not str(obj["question_text"]).strip():
            return "question_text 不能为空"

        steps = obj["solution_steps"]
        if not isinstance(steps, list) or not steps:
            return "solution_steps 必须是非空数组"

        ids = obj["knowledge_ids"]
        if not isinstance(ids, list):
            return "knowledge_ids 必须是数组"
        if len(ids) > 3:
            return "knowledge_ids 最多 3 个"
        bad = [i for i in ids if i not in allowed_kp_ids]
        if bad:
            return (
                f"knowledge_ids 中有不在候选列表里的值：{bad}。"
                "只能使用上面给出的候选 id，不要自己发明名称。"
            )

        if obj["error_type"] not in ERROR_TYPES:
            return f"error_type 只能是 {sorted(ERROR_TYPES)} 之一"

        conf = obj.get("confidence", 1.0)
        try:
            if not 0.0 <= float(conf) <= 1.0:
                return "confidence 必须是 0 到 1 之间的小数"
        except (TypeError, ValueError):
            return "confidence 必须是数字"

        return None

    return validator


def make_practice_validator(allowed_kp_ids: set[str], expected_min: int = 1):
    """校验生成的练习题：必须可客观判定对错。"""

    def validator(obj: dict) -> str | None:
        if not isinstance(obj, dict) or "items" not in obj:
            return "输出必须包含 items 数组"
        items = obj["items"]
        if not isinstance(items, list) or len(items) < expected_min:
            return f"items 至少要有 {expected_min} 道题"

        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                return f"第 {idx + 1} 题不是对象"
            for field in ("kp_id", "type", "question", "answer"):
                if field not in it:
                    return f"第 {idx + 1} 题缺少字段 {field}"
            if it["kp_id"] not in allowed_kp_ids:
                return f"第 {idx + 1} 题的 kp_id={it['kp_id']} 不在候选列表内"
            if it["type"] == "single_choice":
                opts = it.get("options") or {}
                if not isinstance(opts, dict) or len(opts) < 2:
                    return f"第 {idx + 1} 题是单选题但选项不足（至少 2 个）"
                keys = {str(k).strip().upper() for k in opts}
                if str(it["answer"]).strip().upper() not in keys:
                    return (
                        f"第 {idx + 1} 题的标准答案 {it['answer']!r} 不是有效的选项编号，"
                        f"可选编号为 {sorted(keys)}"
                    )
            elif it["type"] != "fill_blank":
                return f"第 {idx + 1} 题的 type 只能是 single_choice 或 fill_blank"
            elif not str(it["answer"]).strip():
                return f"第 {idx + 1} 题填空题的答案不能为空"
        return None

    return validator
