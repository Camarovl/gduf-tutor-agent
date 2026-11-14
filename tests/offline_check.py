"""离线自检 —— 不联网、不需要模型服务，验证数据层与校验逻辑。

用法：python tests/offline_check.py
全绿即表示除"模型调用"之外的所有链路都正常，可放心接 API。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import db, profile, schemas  # noqa: E402

PASS, FAIL = "✅", "❌"
results: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    results.append((name, cond))


# ---------------------------------------------------------------- 1. 数据完整性
db.init_db()
kps = db.all_knowledge_points()
check(f"知识点已载入（{len(kps)} 个）", len(kps) >= 20)
check("每个知识点都有 explain", all(k.get("explain") for k in kps))

bank = profile.load_bank()
check(f"题库已载入（{len(bank)} 道）", len(bank) >= 10)
allowed = db.kp_id_set()
orphan = [q["id"] for q in bank if q.get("kp_id") not in allowed]
check(f"题库无越界 kp_id{'' if not orphan else ' -> ' + str(orphan)}", not orphan)

bad_choice = [
    q["id"] for q in bank
    if q["type"] == "single_choice" and str(q["answer"]).upper() not in {k.upper() for k in (q.get("options") or {})}
]
check(f"单选题答案都在选项中{'' if not bad_choice else ' -> ' + str(bad_choice)}", not bad_choice)

# ---------------------------------------------------------------- 2. M3 掌握度 SQL
df = db.mastery_table()
if len(df) == 0:
    print("\n 🔴 数据库为空。请先运行  python seed_data.py  再执行本自检。\n")
    sys.exit(1)

check(f"掌握度表行数 == 知识点数（LEFT JOIN 生效）({len(df)})", len(df) == len(kps))
check("存在「样本不足」条目", (df["状态"] == "⚪ 样本不足").any())
first = df.iloc[0]["状态"]
check(f"首位不是样本不足而是未掌握（降权排序生效）[{first}]", first == "🔴 未掌握")
weak = db.weak_points(3)
check("weak_points 排除了样本不足", all(len(str(w["练习数"])) > 0 and w["练习数"] >= 3 for w in weak))

# ---------------------------------------------------------------- 3. M2 输出校验器
v = schemas.make_intake_validator(allowed)
base = {
    "question_text": "q", "student_answer": "a", "correct_answer": "b",
    "solution_steps": ["s"], "knowledge_ids": ["func_parity"],
    "error_type": "concept_confusion", "confidence": 0.8,
}
check("合法 JSON 通过校验", v(base) is None)
check("发明知识点被拒", v({**base, "knowledge_ids": ["made_up"]}) is not None)
check("缺少必填字段被拒", v({k: x for k, x in base.items() if k != "error_type"}) is not None)
check("错因不在枚举被拒", v({**base, "error_type": "stupid"}) is not None)
check("解题步骤为空被拒", v({**base, "solution_steps": []}) is not None)
check("confidence 越界被拒", v({**base, "confidence": 1.5}) is not None)
check("知识点超过 3 个被拒", v({**base, "knowledge_ids": list(allowed)[:4]}) is not None)

# ---------------------------------------------------------------- 4. M4 练习题校验器
pv = schemas.make_practice_validator(allowed, 1)
ok_item = {"kp_id": "func_parity", "type": "single_choice", "question": "q",
           "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "answer": "C"}
check("合法练习题通过", pv({"items": [ok_item]}) is None)
check("答案指向不存在选项被拒", pv({"items": [{**ok_item, "answer": "E"}]}) is not None)
check("选项不足被拒", pv({"items": [{**ok_item, "options": {"A": "1"}}]}) is not None)
check("填空题空答案被拒", pv({"items": [{"kp_id": "func_parity", "type": "fill_blank",
                                        "question": "q", "answer": ""}]}) is not None)

# ---------------------------------------------------------------- 5. 判分函数
c_item = {"type": "single_choice", "answer": "B"}
f_item = {"type": "fill_blank", "answer": "x = 3"}
check("选对判正确", profile.grade(c_item, "B") is True)
check("大小写宽容", profile.grade(c_item, "b") is True)
check("选错判错误", profile.grade(c_item, "A") is False)
check("填空忽略空格差异", profile.grade(f_item, "x=3") is True)
check("填空内容不同判错", profile.grade(f_item, "x=4") is False)

# ---------------------------------------------------------------- 6. 未配置时优雅降级
import os  # noqa: E402

saved = {k: os.environ.pop(k, None) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
try:
    from app import llm_client

    check("缺配置时 is_configured() 为 False", llm_client.is_configured() is False)
    try:
        llm_client.chat([{"role": "user", "content": "hi"}])
        check("缺配置时抛 LLMError", False)
    except llm_client.LLMError:
        check("缺配置时抛 LLMError（不会裸崩）", True)
finally:
    for k, val in saved.items():
        if val is not None:
            os.environ[k] = val

# ---------------------------------------------------------------- 输出
width = max(len(n) for n, _ in results)
failed = [n for n, ok in results if not ok]
print("=" * (width + 10))
for name, ok in results:
    print(f" {PASS if ok else FAIL}  {name}")
print("-" * (width + 10))
print(f" 共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
if failed:
    print("\n 🔴 失败项：")
    for f in failed:
        print(f"    - {f}")
print("=" * (width + 10))
sys.exit(1 if failed else 0)
