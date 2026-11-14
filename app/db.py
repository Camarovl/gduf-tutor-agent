"""数据访问层 —— 全项目唯一碰数据库的模块。

设计要点：
1. get_conn() 每次都会开启外键约束（SQLite 的 PRAGMA 是按连接的，不是按库的）
2. row_factory = sqlite3.Row，查询结果可用 row["name"] 取值
3. 所有 SQL 一律参数化（? 占位），禁止字符串拼接
4. 掌握度分级规则写死在 SQL 里，不调用模型 —— 保证可复现、答辩讲得清
"""

from __future__ import annotations

import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"
SCHEMA_PATH = ROOT / "schema.sql"


def get_conn() -> sqlite3.Connection:
    """返回一个已开启外键、支持字典式取值的连接。调用方负责 close()。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # 🔴 每个连接都要设，建库时设一次没用
    return conn


def init_db() -> None:
    """按 schema.sql 建表。幂等，可反复执行。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_conn()
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 知识点


def all_knowledge_points() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, parent, explain, common_mistakes "
            "FROM knowledge_point ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def kp_id_set() -> set[str]:
    """给 llm_client 的 validator 用：模型返回的知识点 ID 必须落在这个集合内。"""
    return {k["id"] for k in all_knowledge_points()}


def find_relevant_kps(query: str, limit: int = 5) -> list[dict]:
    """按名称模糊匹配出与问题相关的知识点，作为 M1 回答的上下文。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, explain, common_mistakes FROM knowledge_point "
            "WHERE name LIKE ? OR explain LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- 错题本


def insert_wrong_question(data: dict, kp_ids: list[str], image_path: str | None = None) -> int:
    """写入一条错题及其知识点关联，返回新记录 id。"""
    import json

    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO wrong_question(question_text, student_answer, correct_answer,"
            " solution_steps, error_type, source, image_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                data["question_text"],
                data.get("student_answer"),
                data.get("correct_answer"),
                json.dumps(data.get("solution_steps", []), ensure_ascii=False),
                data.get("error_type", "unknown"),
                data.get("source", "manual"),
                image_path,
            ),
        )
        wq_id = cur.lastrowid
        valid = kp_id_set()
        for kp in kp_ids:
            if kp in valid:                      # 越界 ID 直接丢弃，避免脏数据
                conn.execute(
                    "INSERT OR IGNORE INTO wq_kp(wq_id, kp_id) VALUES (?,?)",
                    (wq_id, kp),
                )
        conn.commit()
        return int(wq_id)
    finally:
        conn.close()


def recent_wrong_questions(limit: int = 20) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT wq.*, COALESCE(GROUP_CONCAT(kp.name, '、'), '') AS kp_names "
            "FROM wrong_question wq "
            "LEFT JOIN wq_kp x ON x.wq_id = wq.id "
            "LEFT JOIN knowledge_point kp ON kp.id = x.kp_id "
            "GROUP BY wq.id ORDER BY wq.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def wrong_question_count() -> int:
    conn = get_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM wrong_question").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------- 练习记录


def log_practice(kp_id: str, is_correct: bool, question_ref: str | None = None,
                 duration_sec: int | None = None) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO practice_log(kp_id, question_ref, is_correct, duration_sec)"
            " VALUES (?,?,?,?)",
            (kp_id, question_ref, 1 if is_correct else 0, duration_sec),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- M3 掌握度

# 🔴 LEFT JOIN：没练过的知识点也要出现，否则界面像"数据丢了"
# 🔴 NULLIF：防除零（SQLite 除零返回 NULL 而非报错，会污染后续计算）
# 🔴 「样本不足」单独一档并降权排序：否则新知识点正确率为 NULL/0，
#    永远霸占"最薄弱前三"，M4 就会一直推没人练过的题
MASTERY_SQL = """
WITH m AS (
  SELECT
    kp.id,
    kp.name                                                        AS 知识点,
    COUNT(p.id)                                                    AS 练习数,
    COALESCE(SUM(p.is_correct), 0)                                 AS 做对数,
    ROUND(100.0 * SUM(p.is_correct) / NULLIF(COUNT(p.id), 0), 1)   AS 正确率,
    MAX(p.created_at)                                              AS 最近练习
  FROM knowledge_point kp
  LEFT JOIN practice_log p ON p.kp_id = kp.id
  GROUP BY kp.id
)
SELECT *,
  CASE
    WHEN 练习数 IS NULL OR 练习数 < 3 THEN '⚪ 样本不足'
    WHEN 正确率 < 40                  THEN '🔴 未掌握'
    WHEN 正确率 < 70                  THEN '🟡 基本掌握'
    ELSE                                   '🟢 已掌握'
  END AS 状态
FROM m
ORDER BY
  CASE
    WHEN 练习数 IS NULL OR 练习数 < 3 THEN 1
    WHEN 正确率 < 40 THEN 0
    WHEN 正确率 < 70 THEN 2
    ELSE 3
  END,
  正确率 ASC NULLS LAST;
"""


def mastery_table():
    """返回 pandas.DataFrame：知识点 / 练习数 / 做对数 / 正确率 / 最近练习 / 状态。"""
    import pandas as pd

    conn = get_conn()
    try:
        return pd.read_sql_query(MASTERY_SQL, conn)
    finally:
        conn.close()


def weak_points(top_n: int = 3) -> list[dict]:
    """M4 抽题依据：排除「样本不足」后正确率最低的若干知识点。"""
    df = mastery_table()
    real = df[df["状态"] != "⚪ 样本不足"].sort_values("正确率")
    return real.head(top_n).to_dict("records")


def kp_error_distribution() -> list[dict]:
    """错题按知识点分布 + 主要错因，用于检查 M2 归类质量。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT kp.name AS 知识点, COUNT(*) AS 错题数,"
            " GROUP_CONCAT(DISTINCT wq.error_type) AS 主要错因 "
            "FROM wrong_question wq "
            "JOIN wq_kp x ON x.wq_id = wq.id "
            "JOIN knowledge_point kp ON kp.id = x.kp_id "
            "GROUP BY kp.id ORDER BY 错题数 DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- 调用日志


def insert_llm_log(purpose: str, prompt_tokens: int, completion_tokens: int,
                   retried: bool, success: bool) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO llm_log(purpose, prompt_tokens, completion_tokens, retried, success)"
            " VALUES (?,?,?,?,?)",
            (purpose, prompt_tokens, completion_tokens, 1 if retried else 0, 1 if success else 0),
        )
        conn.commit()
    finally:
        conn.close()


def cost_summary() -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS p,"
            " COALESCE(SUM(completion_tokens),0) AS c, COALESCE(SUM(retried),0) AS r"
            " FROM llm_log"
        ).fetchone()
        return {
            "calls": row["n"],
            "prompt_tokens": row["p"],
            "completion_tokens": row["c"],
            "retried": row["r"],
        }
    finally:
        conn.close()
