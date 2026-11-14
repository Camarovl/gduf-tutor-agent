"""M3 掌握度 + M4 针对性练习。

🔴 M3 全部走 SQL，不调模型 —— 结果可复现、成本低、答辩不怕被追问逻辑。
M4 优先从题库抽现成题（可靠），只有题库不够时才让模型生成。
"""

from __future__ import annotations

import json
import pathlib
import random

from app import db, prompts
from app.llm_client import LLMError, chat
from app.schemas import make_practice_validator

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "data" / "question_bank.json"


# ============================================================ M3 掌握度


def mastery() -> "object":
    """返回 DataFrame：知识点/练习数/做对数/正确率/最近练习/状态。"""
    return db.mastery_table()


def summary_text() -> dict:
    """给界面顶部用的几个数字。"""
    df = db.mastery_table()
    practiced = df[df["状态"] != "⚪ 样本不足"]
    weak = list(practiced.sort_values("正确率").head(3)["知识点"])
    return {
        "知识点总数": int(len(df)),
        "已练知识点": int(len(practiced)),
        "未掌握": int((df["状态"] == "🔴 未掌握").sum()),
        "基本掌握": int((df["状态"] == "🟡 基本掌握").sum()),
        "已掌握": int((df["状态"] == "🟢 已掌握").sum()),
        "最薄弱": weak,
        "错题总数": db.wrong_question_count(),
    }


# ============================================================ M4 练习


def load_bank() -> list[dict]:
    if not BANK_PATH.exists():
        return []
    try:
        return json.loads(BANK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _grade_choice(user_answer: str, correct: str) -> bool:
    a, c = (user_answer or "").strip().upper(), (correct or "").strip().upper()
    return a == c or (len(a) == 1 and a in c) or (len(c) == 1 and c in a)


def _grade_blank(user_answer: str, correct: str) -> bool:
    norm = lambda s: "".join(str(s or "").lower().split())  # noqa: E731
    return norm(user_answer) == norm(correct)


def grade(item: dict, user_answer: str) -> bool:
    """客观判分。填空题做空白归一化比较。"""
    if item.get("type") == "single_choice":
        return _grade_choice(user_answer, item.get("answer", ""))
    return _grade_blank(user_answer, item.get("answer", ""))


def pick_from_bank(count: int = 3) -> list[dict]:
    """从薄弱知识点出发在题库里抽题。命中不足时随机补齐。"""
    bank = load_bank()
    if not bank:
        return []
    try:
        weak_ids = {w["id"] for w in db.weak_points(5)}
    except Exception:  # noqa: BLE001 - 统计失败不影响出题主流程
        weak_ids = set()

    targeted = [q for q in bank if q.get("kp_id") in weak_ids]
    random.shuffle(targeted)
    picked = targeted[:count]

    if len(picked) < count:
        rest = [q for q in bank if q not in picked]
        random.shuffle(rest)
        picked += rest[: count - len(picked)]
    return picked


def generate_by_llm(count: int = 3) -> list[dict]:
    """题库不够时让模型生成。输出必须通过"可客观判定"校验。"""
    allowed = db.kp_id_set()
    rows = db.all_knowledge_points()
    weak = db.weak_points(max(1, count))
    focus_ids = {w["id"] for w in weak} if weak else allowed
    lines = [
        f"- {k['id']} | {k['name']}"
        for k in rows
        if k["id"] in focus_ids
    ] or [f"- {k['id']} | {k['name']}" for k in rows[:20]]

    subject = (rows[0]["name"].split("·")[0] if rows else "本学科")
    system = prompts.PRACTICE_SYSTEM.format(
        subject=subject, count=count, candidates="\n".join(lines)
    )
    result = chat(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"请出 {count} 道练习题，重点覆盖薄弱知识点。"
                if weak
                else f"练习记录还少，先出 {count} 道基础题摸清水平。",
            },
        ],
        purpose="gen",
        json_mode=True,
        schema_hint='{"items":[{"kp_id":"...","type":"single_choice","question":"...",'
                    '"options":{"A":"..","B":".."},"answer":"A","explanation":".."}]}',
        validator=make_practice_validator(allowed, expected_min=1),
    )
    start, end = result.text.find("{"), result.text.rfind("}")
    obj = json.loads(result.text[start : end + 1])
    return obj.get("items", [])


def build_practice_session(count: int = 3) -> tuple[list[dict], str]:
    """返回 (题目列表, 来源说明)。优先题库，其次生成，都没有则提示初始化。"""
    from_bank = pick_from_bank(count)
    if len(from_bank) >= max(1, count - 1):
        return from_bank, f"来自本地题库（{len(from_bank)} 道，按薄弱知识点优先抽取）"

    try:
        gen = generate_by_llm(count)
        if gen:
            return gen, f"由模型针对薄弱点生成（{len(gen)} 道，已通过可判分校验）"
    except (LLMError, ValueError, json.JSONDecodeError) as exc:
        if from_bank:
            return from_bank, f"生成失败（{exc}），改用题库现有 {len(from_bank)} 道题"
        return [], f"暂无可用练习题：{exc}。请先运行 `python seed_data.py` 初始化数据。"

    if from_bank:
        return from_bank, f"使用题库现有 {len(from_bank)} 道题"
    return [], "题库为空且生成失败，请先运行 `python seed_data.py`。"


def record_result(items: list[dict], answers: list[str]) -> list[bool]:
    """判分并回写 practice_log。做错的题回流错题本（官方加分项）。"""
    results = []
    for item, ans in zip(items, answers):
        ok = grade(item, ans)
        results.append(ok)
        kp = item.get("kp_id")
        if kp in db.kp_id_set():
            db.log_practice(kp, ok, question_ref=str(item.get("id", item.get("question", "")[:20])))
            if not ok:
                db.insert_wrong_question(
                    {
                        "question_text": item.get("question", ""),
                        "student_answer": ans or "（未作答）",
                        "correct_answer": item.get("answer", ""),
                        "solution_steps": [item.get("explanation", "")] if item.get("explanation") else [],
                        "error_type": "unknown",
                        "source": "generated",
                    },
                    [kp],
                )
    return results
