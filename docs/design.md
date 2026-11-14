# 方案设计说明

## 一、为什么不用 LangChain / Dify

### LangChain：pydantic v1/v2 混用（已实测复现）

早期原型使用 LangChain，撞上版本兼容问题 —— `langchain` 0.2.x 内部依赖 pydantic v1，
而 `langchain-core` 0.3.x 起改用 pydantic v2，两者混装时：

```
RuntimeError: no validator found for <class 'langchain_core.language_models.base.BaseLanguageModel'>
UserWarning: Mixing V1 and V2 models is not supported. `BaseLanguageModel` is a V2 model.
```

炸点在 `langchain/memory/__init__.py` 第 48 行导入的 `ConversationSummaryMemory` ——
我们根本没用它，但 `from langchain.memory import X` 会执行整个包的 `__init__`，被连坐。

同一条链上的 `langchain_openai` 0.1.x 甚至无法完成 import：

```
PydanticUserError: The `__modify_schema__` method is not supported in Pydantic v2.
                   Use `__get_pydantic_json_schema__` instead in class `SecretStr`.
```

**结论**：不是绕开 `langchain.memory` 就行 —— 只要 langchain 与 langchain-core 跨了 pydantic 主版本，整套都不可用。
最终改用 `openai` SDK 直连（OpenAI 兼容协议），依赖链最短。

附带的好处是：上下文如何拼装全部显式写在代码里，读代码就能看懂 prompt 是怎么组织的，
不必去翻框架的封装层。

### Dify 等低代码平台：技术栈错位

评分维度里有两条明确指向自建：

- 「有没有针对场景做**约束与调优**」—— 平台节点是别人封装好的能力，无从体现
- 「怎么用 **AI 辅助编码**……遇到模型能力边界时怎么绕过去」—— 出题人预期的就是 vibe coding

更实际的问题：本题四条 MVP 里有两条（M2 错题归类落库、M3 掌握度统计）的核心是
**数据落到关系型数据库再查出来**，而 Dify 的知识库是私有向量库，写外部 SQLite 反而要绕 HTTP 节点。
另外工作流存在平台数据库里，导出 `.yml` 后评审看不到任何 Python 工程结构，
与「分多次提交」「能否看出真实开发过程」直接冲突。

---

## 二、关键设计决策

### 1. LLM 的职责边界

只做两件事：① 解释知识点 ② 非结构化 → 结构化 JSON。

统计、判分、分级规则全部是 SQL/Python。理由：LLM 参与的环节越多，结果越不可复现，
评审点两次得到不同答案就会质疑有效性；同时 token 成本也会失控。

### 2. 知识点 ID 强制取自候选集 🔴

这是整个数据层最关键的一条约束。若允许模型自由命名标签，会出现
「二次函数图像」「二次函数的图象」「抛物线性质」三种写法指同一件事，
M3 的 `GROUP BY` 会把它们算成三个知识点，掌握度统计彻底作废。

实现方式：把 `knowledge_points.json` 的全部 id/name 注入 prompt，
再用 `schemas.make_intake_validator(allowed)` 检查返回的每个 id 都在集合内，
越界即判定失败并**把错误原因回喂给模型重试**（比盲目重试成功率高得多）。

### 3. 三级容错策略

```
校验通过 → 采用
校验失败 → 带原因重试（最多 2 次）
仍失败   → 降级为人工填写，主流程不中断
```

官方原话是「识别结果允许用户确认或修正后再入库」，所以降级路径本身就是合规设计而非妥协。

### 4. 掌握度分级写死在 SQL

四个阈值（<40% 未掌握、40~70% 基本掌握、≥70% 已掌握、练习<3 样本不足）
以 `CASE WHEN` 形式固化在 `MASTERY_SQL` 里。好处：可复现、零成本、被追问时能当场指出是哪几行。

「样本不足」还额外做了降权排序 —— 否则新知识点正确率为 NULL 会永远占据"最薄弱前三"，
导致 M4 反复推没人练过的题。这个坑在 `tests/offline_check.py` 里有专门一项验证。

### 5. M4 优先用题库而不是生成

生成的题目需要额外校验"能不能客观判定对错"，成本高且不稳定。
本地题库的题已经过人工确认，抽题逻辑只是按薄弱点加权随机。
只有题库覆盖不足时才调用模型，且必须通过 `make_practice_validator` 才采用。

### 6. 开场白不进对话历史

多数模型服务用的 jinja chat template 不接受以 assistant 开头的消息序列：

```
Error rendering prompt with jinja template: "No user query found in messages."
```

Streamlit 每次交互都会重跑整个脚本，如果开场白存在 `session_state["messages"][0]`，
首轮请求就可能带上它触发报错。现在的做法是开场白单独渲染、不写入历史，
保证发给模型的 payload 恒为 `[system, user, assistant, ..., user]`。

---

## 三、分层架构

| 层 | 文件 | 唯一职责 | 硬性规矩 |
|---|---|---|---|
| 界面 | `streamlit_app.py` | 展示与交互 | 🔴 不含业务逻辑，不发网络请求 |
| 业务 | `qa.py` / `intake.py` / `profile.py` | 功能编排 | 不写 SQL、不拼原始 prompt |
| 能力 | `llm_client.py` | 模型调用 | 🔴 全项目唯一发网络请求处 |
| 约束 | `prompts.py` / `schemas.py` | 提示词与校验 | 所有 prompt 集中管理 |
| 数据 | `db.py` / `schema.sql` | 持久化 | 🔴 全项目唯一碰数据库处，SQL 一律参数化 |

这套分层的价值在换模型供应商时立刻显现：只改 `.env` 三个变量，其余一行不动。
