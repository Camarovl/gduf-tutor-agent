-- 学习辅导 AI 助教 · 表结构（SQLite）
-- 由 app/db.py 的 init_db() 读取执行；seed_data.py 负责灌演示数据。

PRAGMA foreign_keys = ON;   -- 🔴 SQLite 默认关闭外键，必须在最前面显式打开

-- 1. 知识点树：M1 问答的边界依据、M2 归类的候选集、M3 统计的维度
CREATE TABLE IF NOT EXISTS knowledge_point (
  id              TEXT PRIMARY KEY,           -- 'func_quadratic_graph'
  name            TEXT NOT NULL UNIQUE,       -- '二次函数的图像与性质'
  parent          TEXT,                       -- 父节点 id，构成树
  explain         TEXT,                       -- 讲解骨架，作为 M1 的上下文
  common_mistakes TEXT                        -- JSON 数组字符串，常见误区
);

-- 2. 错题本（M2 的落库目标）
CREATE TABLE IF NOT EXISTS wrong_question (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question_text  TEXT NOT NULL,
  student_answer TEXT,
  correct_answer TEXT,
  solution_steps TEXT,                        -- JSON 数组
  error_type     TEXT CHECK(error_type IN
                   ('concept_confusion','calculation_error','method_wrong',
                    'incomplete','unknown')),
  source         TEXT DEFAULT 'manual',       -- manual / photo / generated
  image_path     TEXT,                        -- 只存相对路径，不存二进制
  created_at     TEXT DEFAULT (datetime('now','localtime'))
);

-- 3. 错题 ↔ 知识点（一道题可涉及多个知识点）
CREATE TABLE IF NOT EXISTS wq_kp (
  wq_id INTEGER NOT NULL REFERENCES wrong_question(id) ON DELETE CASCADE,
  kp_id TEXT    NOT NULL REFERENCES knowledge_point(id),
  PRIMARY KEY (wq_id, kp_id)
);

-- 4. 练习记录：M3 掌握度的数据源，也是 M4 推题后的回写目标
CREATE TABLE IF NOT EXISTS practice_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kp_id        TEXT NOT NULL REFERENCES knowledge_point(id),
  question_ref TEXT,                          -- 题库题号或错题本 id
  is_correct   BOOLEAN NOT NULL,
  duration_sec INTEGER,
  created_at   TEXT DEFAULT (datetime('now','localtime'))
);

-- 5. 模型调用日志：答辩回答"成本多少"就靠这张表
CREATE TABLE IF NOT EXISTS llm_log (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  purpose           TEXT,                     -- qa / intake / gen
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  retried           BOOLEAN DEFAULT 0,
  success           BOOLEAN,
  created_at        TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_log_kp   ON practice_log(kp_id);
CREATE INDEX IF NOT EXISTS idx_log_time ON practice_log(created_at);
CREATE INDEX IF NOT EXISTS idx_wq_time  ON wrong_question(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_time ON llm_log(created_at);
