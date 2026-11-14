# 学习辅导 AI 助教

面向**高中数学**的学习辅导应用。针对某一门课做闭环，而不是做一个什么都能问的通用助手 ——
这是选题 B「限定学科自选」的刻意约束：只有把范围收窄，才能把知识点组织起来、让错题归类合理、
使生成的练习真正落在薄弱点上。

对应考核：[2026 GDUF 人工智能工作室二轮考核 · 方向一 选题 B](https://github.com/gduf-community/2026GDUF-AI-Studio-assessment/blob/main/direction-1-ai-assistant/README.md)

---

## 一、做了什么

四个功能板块（官方 MVP 四条，本项目打通 **M1 + M2 + M3**，并附带实现 M4）：

| 板块 | 页面 | 能力 | 状态 |
|---|---|---|---|
| **M1 知识点问答** | ① 知识点问答 | 概念解释 + 通俗讲解 + 具体例子 + 常见误区；回答严格限定在本课程内，越界问题礼貌拒绝 | ✅ |
| **M2 错题录入与自动归类** | ② 错题录入 | 手输或拍照 → 抽取题干/答案/解题步骤 → 从候选集选知识点 → 判定错因类型 → **用户确认后入库** | ✅ |
| **M3 掌握度记录** | ③ 掌握度分析 | 按知识点统计正确率，四级分级（未掌握/基本掌握/已掌握/样本不足），图表展示薄弱点 | ✅ |
| **M4 针对性练习** | ④ 针对性练习 | 依据薄弱点优先从本地题库抽题，不足时由模型生成；作答后客观判分并回写掌握度，做错回流错题本 | ✅ |

### 核心设计取舍

1. **LLM 只负责两件事**：解释知识点、把非结构化输入转成结构化 JSON。
   统计、归类校验、判分全部是普通 Python + SQL —— 结果可复现、成本低、出错易定位。
2. **知识点 ID 强制取自候选集**。模型不许自己发明标签名称，否则会出现
   「二次函数图像」「二次函数的图象」「抛物线性质」三种标签指同一件事，M3 统计直接作废。
3. **掌握度分级规则写死在 SQL 里**（`schema.sql` / `app/db.py` 的 `MASTERY_SQL`），
   不调用模型，答辩被追问逻辑时能当场指出是哪几个阈值。
4. **「样本不足」单独一档并降权排序**：新知识点练习次数 <3 时不参与"最薄弱"排名，
   否则会永远霸榜并导致 M4 反复推没人练过的题。

---

## 二、技术栈与架构

```
Streamlit 1.57        界面（纯 Python，无前端框架）
SQLite                单文件数据库，裸 SQL，无 ORM
openai SDK 1.109      走 OpenAI 兼容协议，对接任意供应商（地址与密钥由使用者配置）
pandas 2.3            表格与图表数据
Dockerfile            python:3.11-slim 容器化
```

⚠️ **没有使用 LangChain**。本机 langchain 0.2.16 与 langchain-core 0.3.86 混用 pydantic v1/v2，
import 阶段即报 `RuntimeError: no validator found for BaseLanguageModel`。改用 openai SDK 直连后
链路更短，上下文如何拼装也一目了然 —— 详见 [docs/design.md](docs/design.md)。

### 目录结构

```
├── streamlit_app.py        界面入口（只做展示交互，不含业务逻辑）
├── app/
│   ├── llm_client.py       🔴 全项目唯一发网络请求的文件
│   ├── prompts.py          所有提示词集中在此
│   ├── schemas.py          LLM 输出的结构校验（约束与调优的落点）
│   ├── db.py               全项目唯一碰数据库的文件
│   ├── qa.py               M1 知识点问答
│   ├── intake.py           M2 错题录入与归类
│   └── profile.py          M3 掌握度 + M4 练习
├── schema.sql              表结构（5 张表）
├── seed_data.py            演示数据一键灌入
├── data/
│   ├── knowledge_points.json   21 个知识点（含讲解骨架与常见误区）
│   └── question_bank.json      12 道题（选项与标准答案自洽）
├── tests/offline_check.py  离线自检 33 项，不需要模型服务
├── docs/                   方案设计、问题记录、未完成说明
├── .env.example            配置模板（复制为 .env 填写）
├── requirements.txt        版本写死
└── Dockerfile / .dockerignore
```

分层原则：**界面不碰网络、业务不碰 SQL 细节、SQL 不碰界面**。任何人接手都只需读一层。

---

## 三、快速开始

### 步骤 1 · 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
```

要求 Python 3.11 或更高（`python --version` 确认）。

### 步骤 2 · 🔴 配置模型服务（必须填，代码中留空）

```bash
Copy-Item .env.example .env       # PowerShell；Linux/macOS 用 cp .env.example .env
```

编辑 `.env`，填入三项：

```ini
LLM_BASE_URL=                      # ← API 地址
LLM_API_KEY=                       # ← 密钥
LLM_MODEL=                         # ← 模型名
LLM_MAX_TOKENS=4000                # 思考型模型务必保持较大，见下方注意事项
```

`.env` 已被 `.gitignore` 排除，**切勿提交真实密钥**。

也可以不设文件、直接导出环境变量：

```powershell
$env:LLM_BASE_URL="https://api.deepseek.com/v1"   # 以 /v1 结尾
$env:LLM_API_KEY="sk-你的密钥"
$env:LLM_MODEL="deepseek-chat"                    # 与供应商文档写法完全一致
```

三项都是必填，代码中不提供任何默认地址或占位密钥 —— 缺项时应用会拒绝发起请求并列出缺哪几项。
未配置时页面仍可正常打开，侧边栏给出明确提示，不会崩溃。

### 步骤 3 · 初始化演示数据

Windows 中文系统的控制台默认 GBK 编码，而脚本输出含 emoji 状态标记。
本项目已在两个脚本顶部自动重配置为 UTF-8，正常情况下无需额外设置；
若你的环境仍报 `UnicodeEncodeError`，先执行 `$env:PYTHONIOENCODING="utf-8"`。

```bash
python seed_data.py
```

输出示例：

```
演示数据初始化完成
  知识点      : 21 个
  题库        : 12 道
  错题记录    : 6 条
  练习记录    : 55 条
按当前数据算出的最薄弱知识点：
  🔴 未掌握  导数的概念与几何意义   正确率 33.3%（练 6 次）
  🔴 未掌握  二次函数的图像与性质   正确率 37.5%（练 8 次）
  🟡 基本掌握 函数的奇偶性         正确率 40.0%（练 5 次）
```

脚本可重复执行（先清空再灌），不会报 UNIQUE 冲突。

### 步骤 4 · 启动

```bash
streamlit run streamlit_app.py
```

浏览器打开 http://localhost:8501 。

---

## 四、功能验证方式

评审可按以下路径逐项验证，无需自行构造数据：

| 编号 | 操作 | 预期 |
|---|---|---|
| V1 | ① 页提问「什么是导数？举一个例子」 | 返回四段式结构：定义/通俗解释/例子/常见误区 |
| V2 | ① 页提问「帮我写一篇英语作文」 | **礼貌拒绝**并说明只覆盖高中数学 |
| V3 | ② 页手输题干「已知 f(x)=x²-2x+3，求 [0,3] 最大值」+ 错误答案「3」 | 识别出题干、给出正确答案 6、知识点归到「二次函数的图像与性质」、错因判为方法选错 |
| V4 | ② 页修改自动识别的知识点后再入库 | 以修改结果为准（确认环节生效） |
| V5 | ③ 页查看掌握度表 | 21 行全部出现（含从未练过的），薄弱前三有红标 |
| V6 | ④ 页点「生成针对性练习」→ 故意答错一题 → 提交 | 判分正确、该知识点正确率下降、错题本新增一条 |
| V7 | 命令行 `python tests/offline_check.py` | 33 项全 ✅，退出码 0 |

### 离线自检（不需要模型服务）

```bash
python tests/offline_check.py
```

覆盖数据完整性、M3 三条关键 SQL 语义（LEFT JOIN / 除零 / 样本不足降权）、
M2 七种畸形输出是否都被拒、M4 判分逻辑、以及未配置时的优雅降级。

---

## 五、Docker 运行

> ⚠️ **本项目的 Dockerfile 尚未经实机构建验证** —— 开发机未安装 Docker，
> 以下命令按文档编写但从未跑通。提交前必须在装了 Docker 的机器上完整执行一遍。

```powershell
# Windows PowerShell
docker build -t gduf-tutor .
docker run -d -p 8501:8501 -e LLM_BASE_URL="https://..." -e LLM_API_KEY="sk-..." `
           -e LLM_MODEL=qwen3.8-flash -v "${PWD}\data:/app/data" --name tutor gduf-tutor
docker exec tutor python seed_data.py     # 容器内首次需灌演示数据
```

```bash
# Linux / macOS
docker build -t gduf-tutor .
docker run -d -p 8501:8501 -e LLM_BASE_URL="https://..." -e LLM_API_KEY="sk-..." \
           -e LLM_MODEL=qwen3.8-flash -v "$(pwd)/data:/app/data" --name tutor gduf-tutor
docker exec tutor python seed_data.py
```

两种 shell 的当前目录变量写法不同（PowerShell `$PWD`、cmd `%cd%`、bash `$(pwd)`），
照自己用的那个抄，别混用。

| 项 | 值 |
|---|---|
| **端口** | 8501（宿主机 `-p 8501:8501`） |
| **持久化路径** | 容器内 `/app/data/app.db`，通过 `-v` 映射到宿主机 `./data`；不挂载则容器重建后数据丢失 |
| **环境变量** | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 用 `-e` 传入，**镜像内不含任何密钥** |
| **首次进入容器** | 需执行一次 `docker exec <容器名> python seed_data.py` 建库灌数据 |

---

## 六、已知问题与未完成部分

诚实列出，详见 [docs/limitations.md](docs/limitations.md)：

1. **拍照识别准确率依赖模型**。视觉能力弱的模型可能抽错题干，因此保留人工确认环节兜底。
2. **思考型模型的 token 陷阱**：带思维链的模型会把大量 token 花在内部推理上，
   `LLM_MAX_TOKENS` 太小时返回内容为空（`finish_reason=length`）。已在配置注释与界面提示中说明。
3. **M4 生成的题目质量不稳定**，因此优先从本地题库抽题，仅在题库不足时调用模型，且必须通过
   "可客观判定"校验才采用。
4. **单人本地应用，无多用户体系**。刻意不加登录 —— 加了会引入鉴权、数据隔离等与题目无关的工作量。
5. **知识点树目前两层、21 个节点**，覆盖高中数学主干但非全集。扩充只需往 JSON 加条目并重跑 seed。
6. RAG（结合教材课件给出引用出处）**未做**，属于官方加分项而非必做。

---

## 七、团队分工

| 代号 | 职责 | 主要负责文件 |
|---|---|---|
| A（组长） | 定范围、催进度、拍板砍功能、答辩主讲、合并 PR | `docs/` |
| B | 模型调用封装、prompt 设计与调优、输出校验 | `app/llm_client.py`、`app/prompts.py`、`app/schemas.py` |
| C | 表结构、归类规则、统计口径 | `schema.sql`、`app/db.py`、`app/intake.py`、`app/profile.py` |
| D | 页面交互、图片上传、演示数据 | `streamlit_app.py`、`seed_data.py`、`data/*.json` |
| E | 仓库规范、三个 ignore、Dockerfile、部署、README | `Dockerfile`、`README.md`、`.gitignore` |

协作规矩见 [docs/collaboration.md](docs/collaboration.md)：一人一分支、改动走 PR、
由组长合并、全组禁用 `git push --force`。

---

## 八、参考填法速查

`.env.example` 说明了三项必填变量的含义、可选参数默认值，以及三条实测踩坑的排障线索。
