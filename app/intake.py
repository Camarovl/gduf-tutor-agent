"""M2 错题录入与自动归类。

拆成三步（对应官方"识别结果允许用户确认或修正后再入库"）：
  A. 抽取   图片/文本 → 结构化字段
  B. 归类   题干 → 知识点 ID（只能从候选集选，禁止模型自由发挥）
  C. 错因   学生答案 vs 正确答案 → 五类枚举之一
校验失败会带原因重试，仍失败则降级为人工填写。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app import db, prompts
from app.llm_client import LLMError, chat, vision
from app.schemas import make_intake_validator

ERROR_LABELS = {
    "concept_confusion": "概念混淆",
    "calculation_error": "计算失误",
    "method_wrong": "方法选错",
    "incomplete": "审题不全",
    "unknown": "无法判定",
}


@dataclass
class IntakeDraft:
    """待用户确认的草稿。界面把它每个字段都渲染成可编辑控件。"""

    question_text: str = ""
    student_answer: str = ""
    correct_answer: str = ""
    solution_steps: list = field(default_factory=list)
    knowledge_ids: list = field(default_factory=list)
    error_type: str = "unknown"
    confidence: float = 0.0
    source: str = "manual"
    degraded: bool = False          # True 表示模型没成功，需人工填
    note: str = ""

    @property
    def error_label(self) -> str:
        return ERROR_LABELS.get(self.error_type, self.error_type)

    def kp_names(self) -> str:
        valid = {k["id"]: k["name"] for k in db.all_knowledge_points()}
        return "、".join(valid.get(i, i) for i in self.knowledge_ids) or "（未归类）"


def _candidate_block() -> str:
    rows = db.all_knowledge_points()
    lines = []
    for k in rows:
        brief = (k.get("explain") or "").replace("\n", " ")[:40]
        lines.append(f"- {k['id']} | {k['name']} | {brief}")
    return "\n".join(lines)


def _to_draft(obj: dict, source: str) -> IntakeDraft:
    steps = obj.get("solution_steps") or []
    return IntakeDraft(
        question_text=str(obj.get("question_text", "")).strip(),
        student_answer=str(obj.get("student_answer", "")).strip(),
        correct_answer=str(obj.get("correct_answer", "")).strip(),
        solution_steps=[str(s) for s in steps],
        knowledge_ids=list(obj.get("knowledge_ids") or []),
        error_type=obj.get("error_type", "unknown"),
        confidence=float(obj.get("confidence", 0.0) or 0.0),
        source=source,
    )


def analyze_text(question_text: str, student_answer: str,
                 correct_answer: str = "") -> IntakeDraft:
    """手输/粘贴题干的识别路径。"""
    allowed = db.kp_id_set()
    system = prompts.INTAKE_SYSTEM.format(candidates=_candidate_block())
    user = prompts.INTAKE_USER_TEXT.format(
        question_text=question_text,
        student_answer=student_answer or "（未提供）",
        correct_answer=correct_answer or "（未提供，请根据题目自行求解）",
    )
    try:
        result = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            purpose="intake",
            json_mode=True,
            schema_hint='{"question_text":"...","knowledge_ids":["..."],"error_type":"..."}',
            validator=make_intake_validator(allowed),
        )
        return _to_draft(json.loads(result.text[result.text.find("{"):result.text.rfind("}") + 1]), "manual")
    except (LLMError, ValueError, json.JSONDecodeError) as exc:
        # 🔴 降级而不是硬失败：让用户手工填也能正常入库，主流程不断
        return IntakeDraft(
            question_text=question_text,
            student_answer=student_answer,
            correct_answer=correct_answer,
            source="manual",
            degraded=True,
            note=f"自动识别失败，请手动补全：{exc}",
        )


def analyze_image(image_bytes: bytes, extra_note: str = "") -> IntakeDraft:
    """拍照/截图识别路径。⚠️ 依赖模型的视觉能力，接入新供应商前先实测。"""
    allowed = db.kp_id_set()
    system = prompts.INTAKE_SYSTEM.format(candidates=_candidate_block())
    prompt_text = prompts.INTAKE_USER_IMAGE + (f"\n补充说明：{extra_note}" if extra_note else "")
    try:
        result = vision(
            prompt_text,
            [image_bytes],
            purpose="intake_photo",
            system=system,
            json_mode=True,
            schema_hint='{"question_text":"...","knowledge_ids":["..."],"error_type":"..."}',
            validator=make_intake_validator(allowed),
        )
        draft = _to_draft(
            json.loads(result.text[result.text.find("{"):result.text.rfind("}") + 1]), "photo"
        )
        if not draft.question_text:
            raise ValueError("未能从图片中识别出题干")
        return draft
    except (LLMError, ValueError, json.JSONDecodeError) as exc:
        return IntakeDraft(
            source="photo",
            degraded=True,
            note=f"图片识别失败，请在下方手动填写题干与答案：{exc}",
        )


def commit(draft: IntakeDraft, image_path: str | None = None) -> int:
    """用户确认后落库。返回错题 id。"""
    return db.insert_wrong_question(
        {
            "question_text": draft.question_text,
            "student_answer": draft.student_answer,
            "correct_answer": draft.correct_answer,
            "solution_steps": draft.solution_steps,
            "error_type": draft.error_type if draft.error_type in ERROR_LABELS else "unknown",
            "source": draft.source,
        },
        draft.knowledge_ids,
        image_path,
    )
