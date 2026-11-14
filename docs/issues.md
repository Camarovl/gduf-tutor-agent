# 遇到的问题与解决方式

按发生顺序记录，每条都附**实际报错原文**和最终处理方式。答辩被问"你们踩过什么坑"时直接翻这份。

---

## 1. LangChain 全家桶不可用（pydantic v1/v2 混装）

**现象**

```
RuntimeError: no validator found for <class 'langchain_core.language_models.base.BaseLanguageModel'>,
see `arbitrary_types_allowed` in Config
```

炸在 `from langchain.memory import ConversationBufferMemory` 这一行，
但调用栈显示真正失败的是同包内我们**完全没用到**的 `ConversationSummaryMemory`。

**原因**：`langchain` 0.2.x 内部用 pydantic v1，`langchain-core` 0.3.x 起用 v2；两者跨主版本混装就会冲突。
Python 执行 `from package import X` 会跑完整个包的 `__init__.py`，因此被连坐。

**尝试过的方案**

| 方案 | 结果 |
|---|---|
| 只绕开 `langchain.memory`，改用 `langchain_openai.ChatOpenAI` | ❌ 换成 `PydanticUserError: __modify_schema__ is not supported in Pydantic v2 ... in class SecretStr`，连 import 都过不去 |
| `pip install -U langchain langchain-openai` | ⚠️ 理论可行，但网络不稳且会导致五台机器版本漂移，与「requirements.txt 版本写死」冲突 |
| **改用 openai SDK 直连** | ✅ 采用。依赖链最短，上下文拼装全可见 |

**教训**：依赖版本要一次性对齐并写死，别让每个人的环境各自演化。

---

## 2. 模型返回空内容（思考型模型的 token 陷阱）

**现象**：请求成功、无异常，但界面什么都不显示。打印完整响应才看清：

```
finish_reason: length
completion_tokens: 800
reasoning_tokens: 799      ← 全部预算花在内部推理上，可见输出为零
```

**原因**：所用的是带思维链（chain-of-thought）的模型，会先在内部推理很久再输出可见内容。
我们把 `max_tokens` 设成 800，它想完就没额度了。

**处理**：默认值提到 `LLM_MAX_TOKENS=4000`；同时在 `llm_client.chat()` 里显式检测这个症状，
抛出可读的错误信息而不是静默返回空串：

```python
if not result.text:
    last_err = (f"模型返回空内容（finish_reason={...}，reasoning_tokens={...}）。"
                "若是思考型模型，请调大 LLM_MAX_TOKENS 或改用非思考模型。")
```

`.env.example` 和侧边栏提示里也都写了这条注意事项 —— 这是最容易让新人以为"代码坏了"的坑。

---

## 3. jinja 模板拒绝消息序列

**现象**：**只有第一次提问失败**，之后重试就正常了。

```
Error code: 400 - {'error': 'Error rendering prompt with jinja template:
"No user query found in messages."'}
```

**定位过程**：把不同消息结构逐个发给本地服务对比——

| payload | 结果 |
|---|---|
| `[system, user]` | ✅ OK |
| `[system, assistant, user]` | ❌ 400 |
| `[user]` | ✅ OK |

结论：模板不接受以 assistant 开头的对话轮。而我们的开场白是硬编码的第一条消息：

```python
st.session_state["messages"] = [{"role": "assistant", "content": "你好！我是学习助手…"}]
```

Streamlit 每次交互都会从头重跑脚本，首轮请求就可能把它带上。

**处理**：开场白只做界面渲染、不写入历史，保证 payload 恒为 `[system, user, ...]`。
这样从根上消除了这类报错，也让"为什么第一轮特殊"这个问题不再存在。

---

## 4. 校验器自身的 bug（被自己的测试抓到）

**现象**：写 `make_practice_validator` 后跑单测，发现一道合法题被判非法。

题目是四选一、答案 `"C"`，选项有 A/B/C/D 四个 —— 本该通过。问题出在这行：

```python
if str(it["answer"]) not in opts:     # 检查的是 key 还是 value？
```

`opts` 是 `{"A": "...", "B": "..."}`，`"C" not in opts` 检查的是键，看似正确；
但大小写不一致时（模型返回 `"c"`）就会误拒。而且错误信息写成"选项不足"，指错了方向。

**修正**：

```python
keys = {str(k).strip().upper() for k in opts}
if str(it["answer"]).strip().upper() not in keys:
    return f"标准答案 {it['answer']!r} 不是有效的选项编号，可选编号为 {sorted(keys)}"
```

**教训**：校验层自己也需要测试。现在 `tests/offline_check.py` 里专门有四项覆盖这个分支。

---

## 5. SQLite 外键静默失效

**风险点**：SQLite 的 `PRAGMA foreign_keys = ON` **是按连接的，不是按数据库的**。
建库时设过一次，之后每个新连接仍然是关闭状态。

后果很隐蔽：可以插入指向不存在知识点的错题，程序不报错，直到统计时才发现数据脏了。

**处理**：收口到 `db.get_conn()` 一处，每个连接都执行一遍；同时界面层的知识点下拉框
只从数据库读取现有 id，双向保证不会出现越界引用。

---

## 6. 演示数据的排行榜没有规律

**现象**：第一版 `seed_data.py` 用纯随机生成练习记录，结果掌握度排行全是 50% 上下，
看不出"哪个知识点薄弱"，演示效果等于零。

**处理**：改成手工设定分布 —— 明确指定三个知识点落在 33%~40%（🔴 未掌握）、
三个在 57%~67%（🟡）、三个 ≥75%（🟢），另留两个练习次数 <3 用来验证「样本不足」降权逻辑。
并且固定随机种子 `random.seed(20261113)`，保证任何人跑出来的结果一致。

---

## 7. 除零与 NULL 传播

**现象**：从没练过的知识点参与计算时，正确率列出现 NULL，后续排序行为不确定。

**处理**：SQL 里两处防护

```sql
ROUND(100.0 * SUM(p.is_correct) / NULLIF(COUNT(p.id), 0), 1)   -- 防除零
...
ORDER BY 正确率 ASC NULLS LAST                                  -- NULL 排最后
```

SQLite 除零返回 NULL 而不报错，所以必须主动处理，否则会一路污染到前端。

---

## 8. PowerShell 传中文导致 commit message 乱码

**现象**：仓库历史里出现过一条

```
docs: ?? main ????????? PR ???? (#2)
```

不是终端显示问题 —— 乱码真的写进了 commit 对象。

**原因**：PowerShell 向原生程序传中文参数时会发生编码转换失败，实测字节数从 24 降到 8
（每个汉字变成一个 `?`）。

**处理**：涉及中文的提交信息一律先用 UTF-8 无 BOM 写入临时文件，再 `git commit -F <file>`，
并用 .NET 进程以 UTF-8 显式解码回读验证。本仓库的所有 commit 均按此方式产生。

---

## 附：一条通用经验

上述问题里有五条（1、2、3、4、7）**都不是靠读文档发现的，而是靠把最小输入喂进去看真实返回**。
所以这个项目从一开始就保留了 `tests/offline_check.py` 和 `.env.example` 里的踩坑注释 ——
遇到 AI 能力边界时，"怎么绕过去"的答案通常就在原始报错文本里。
