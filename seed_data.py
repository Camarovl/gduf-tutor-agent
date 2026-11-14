"""演示数据初始化 —— 让评审无需自行构造就能跑通主流程。

用法：python seed_data.py
可重复执行（先清空再灌），不会报 UNIQUE constraint failed。

数据刻意做了分布设计：两三个知识点正确率落在 30~50%（制造"未掌握"），
另几个较高，这样 M3 的薄弱点排行一眼能看出效果。全是随机数会导致排行榜没有规律。
"""

from __future__ import annotations

import json
import pathlib
import random
import sys

from app import db

# 🔴 Windows 控制台默认 GBK，输出 emoji（🔴🟡✅）会抛 UnicodeEncodeError 直接崩退。
#    评审照 README 执行的第一条命令就是本脚本，必须在这里兜住。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 重配置失败时退回 ASCII 标记
        pass

ROOT = pathlib.Path(__file__).resolve().parent
random.seed(20261113)   # 固定种子，保证每次生成的演示数据一致、可复现

# (知识点id, 总练习次数, 其中做对次数) —— 手工设定分布，不靠随机
PRACTICE_PLAN = [
    ("func_quadratic_graph", 8, 3),     # 🔴 未掌握（37.5%）刻意留作最薄弱
    ("derivative_concept",   6, 2),     # 🔴 未掌握（33.3%）
    ("func_parity",          5, 2),     # 🟡 基本掌握（40.0%，阈值 <40 才算未掌握）
    ("log_function",         7, 4),     # 🟡 基本掌握（57.1%）
    ("trig_formula",         6, 4),     # 🟡 基本掌握（66.7%）
    ("sequence_geo",         5, 4),     # 🟡 基本掌握（80%→🟢）
    ("vector_dot",           6, 5),     # 🟢 已掌握（83.3%）
    ("inequality_basic",     5, 5),     # 🟢 已掌握（100%）
    ("sequence_arith",       4, 3),     # 🟢 已掌握（75%）
    ("set_basic",            2, 1),     # ⚪ 样本不足（验证降权排序逻辑）
    ("derivative_rules",     1, 1),     # ⚪ 样本不足
]

WRONG_SAMPLES = [
    {
        "question_text": "求函数 f(x)=x²-2x+3 在区间 [0,3] 上的最大值。",
        "student_answer": "3",
        "correct_answer": "6",
        "solution_steps": [
            "配方：f(x)=(x-1)²+2，对称轴 x=1",
            "对称轴落在区间内且开口向上，故最小值在顶点 f(1)=2",
            "最大值在离对称轴较远的端点取得：f(0)=3，f(3)=6",
            "比较得最大值为 6",
        ],
        "error_type": "method_wrong",
        "kps": ["func_quadratic_graph", "func_extremum"],
    },
    {
        "question_text": "已知 sin α=3/5 且 α 为第二象限角，求 cos α。",
        "student_answer": "4/5",
        "correct_answer": "-4/5",
        "solution_steps": [
            "由 sin²α+cos²α=1 得 |cos α|=√(1-9/25)=4/5",
            "α 在第二象限，余弦值为负",
            "所以 cos α=-4/5",
        ],
        "error_type": "concept_confusion",
        "kps": ["trig_identity"],
    },
    {
        "question_text": "求曲线 y=x³ 在过点 (1,1) 处的切线方程。",
        "student_answer": "y=3x-2（只写了一条）",
        "correct_answer": "y=3x-2 或 y=(3/4)x+1/4",
        "solution_steps": [
            "'过点'不等于'以该点为切点'，需设切点 (t, t³)",
            "斜率 k=3t²，切线 y-t³=3t²(x-t)",
            "代入 (1,1)：1-t³=3t²(1-t)，化简得 (t-1)²(2t+1)=0",
            "解得 t=1 或 t=-1/2，对应两条切线",
        ],
        "error_type": "incomplete",
        "kps": ["derivative_concept", "derivative_rules"],
    },
    {
        "question_text": "计算 log₂8 + log₃(1/9)。",
        "student_answer": "5",
        "correct_answer": "1",
        "solution_steps": [
            "log₂8=3（因为 2³=8）",
            "log₃(1/9)=log₃(3⁻²)=-2",
            "相加得 3+(-2)=1",
        ],
        "error_type": "calculation_error",
        "kps": ["log_function"],
    },
    {
        "question_text": "判断 f(x)=x²·sin x 的奇偶性。",
        "student_answer": "偶函数",
        "correct_answer": "奇函数",
        "solution_steps": [
            "定义域为 R，关于原点对称（这一步必须先做）",
            "f(-x)=(-x)²·sin(-x)=x²·(-sin x)=-x²sin x",
            "故 f(-x)=-f(x)，是奇函数",
        ],
        "error_type": "concept_confusion",
        "kps": ["func_parity"],
    },
    {
        "question_text": "等差数列中 a₃=7，a₇=15，求 a₁₀。",
        "student_answer": "20",
        "correct_answer": "21",
        "solution_steps": [
            "公差 d=(a₇-a₃)/(7-3)=(15-7)/4=2",
            "a₁₀=a₇+3d=15+6=21",
        ],
        "error_type": "calculation_error",
        "kps": ["sequence_arith"],
    },
]


def main() -> None:
    db.init_db()

    kps = json.loads(
        (ROOT / "data" / "knowledge_points.json").read_text(encoding="utf-8")
    )
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM wq_kp")
        conn.execute("DELETE FROM practice_log")
        conn.execute("DELETE FROM wrong_question")
        conn.execute("DELETE FROM knowledge_point")

        conn.executemany(                       # 🔴 executemany 批量插入
            "INSERT OR IGNORE INTO knowledge_point(id,name,parent,explain,common_mistakes)"
            " VALUES (?,?,?,?,?)",
            [
                (
                    k["id"],
                    k["name"],
                    k.get("parent"),
                    k.get("explain"),
                    json.dumps(k.get("common_mistakes", []), ensure_ascii=False),
                )
                for k in kps
            ],
        )

        bank = json.loads(
            (ROOT / "data" / "question_bank.json").read_text(encoding="utf-8")
        )
        by_kp = {}
        for q in bank:
            by_kp.setdefault(q["kp_id"], []).append(q)

        total_practice = 0
        for kp_id, times, correct_times in PRACTICE_PLAN:
            qs = by_kp.get(kp_id, [])
            for i in range(times):
                ref = qs[i % len(qs)]["id"] if qs else f"practice_{kp_id}_{i}"
                is_ok = i < correct_times
                conn.execute(
                    "INSERT INTO practice_log(kp_id, question_ref, is_correct, duration_sec)"
                    " VALUES (?,?,?,?)",
                    (kp_id, ref, 1 if is_ok else 0, random.randint(40, 300)),
                )
                total_practice += 1

        wrong_added = 0
        for w in WRONG_SAMPLES:
            cur = conn.execute(
                "INSERT INTO wrong_question(question_text, student_answer, correct_answer,"
                " solution_steps, error_type, source) VALUES (?,?,?,?,?,?)",
                (
                    w["question_text"],
                    w["student_answer"],
                    w["correct_answer"],
                    json.dumps(w["solution_steps"], ensure_ascii=False),
                    w["error_type"],
                    "manual",
                ),
            )
            for kp in w["kps"]:
                conn.execute(
                    "INSERT OR IGNORE INTO wq_kp(wq_id, kp_id) VALUES (?,?)",
                    (cur.lastrowid, kp),
                )
            wrong_added += 1

        conn.commit()
    finally:
        conn.close()

    print("=" * 56)
    print("演示数据初始化完成")
    print(f"  知识点      : {len(kps)} 个")
    print(f"  题库        : {len(bank)} 道")
    print(f"  错题记录    : {wrong_added} 条")
    print(f"  练习记录    : {total_practice} 条")
    print("-" * 56)
    df = db.mastery_table()
    weak = df[df["状态"] != "⚪ 样本不足"].sort_values("正确率").head(3)
    print("按当前数据算出的最薄弱知识点：")
    for _, r in weak.iterrows():
        print(f"  {r['状态']}  {r['知识点']:<16s} 正确率 {r['正确率']}%（练 {r['练习数']} 次）")
    print("=" * 56)
    print("下一步：配置模型环境变量后运行  streamlit run streamlit_app.py")


if __name__ == "__main__":
    main()
