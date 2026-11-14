# 协作规矩

本项目 5 人各用自己的电脑，靠 GitHub 同步。以下规矩是**硬要求**，不是建议。

---

## 一、日常动作

| 时机 | 做什么 |
|---|---|
| 开工第一件事 | `git pull`（在自己的分支上先同步 main） |
| 做完一小件事 | `git add <文件>` → `git commit -m "说清改了什么"` |
| 收工最后一件事 | `git push` 到自己的分支，然后在群里报一声 |
| 心里没底时 | `git status` |

**每天同步一次 main** 的理由：你和别人各憋两周不合，最后合并时冲突多到没法收拾。
小步同步 = 每次只处理一点点冲突。这是团队协作里最值钱的习惯。

```bash
git switch feature/login
git pull origin main        # 把主干最新进展并进来
```

---

## 二、谁能做什么 🔴

| 动作 | 权限归属 |
|---|---|
| 在自己分支上 commit / push | ✅ 每个人 |
| **创建 Pull Request** | **只有组长 A** |
| **点 Merge** | **只有组长 A** |
| 直推 `main` | ❌ 机器已拦截（分支保护），谁都推不动 |
| `git push --force` / `git reset --hard` 后推送 | 🚫 **全组禁用** |

组员职责链到 push 就结束：

```bash
git switch -c feature/xxx
# ...干活...
git add <文件> && git commit -m "feat: ..."
git push -u origin feature/xxx
# 然后群里报一句「feature/xxx 推好了，本地跑通了」，等组长合并
```

⚠️ GitHub 网页上那个绿色的 **Merge pull request** 按钮对协作者也是可点的（Write 权限自带），
拦你们靠的是这条约定而不是机器。**手滑点了就是事故。** 同理别点 Close pull request。

也别让自己的 DSH 替你合并 —— 它有你的凭据，完全有能力绕过流程。

---

## 三、文件所有权（防冲突第一手段）

一个文件只有一个负责人，别人要改先在群里说一声。

| 文件 | 负责人 |
|---|---|
| `streamlit_app.py`、`seed_data.py`、`data/*.json` | D |
| `app/llm_client.py`、`app/prompts.py`、`app/schemas.py` | B |
| `schema.sql`、`app/db.py`、`app/intake.py`、`app/profile.py`、`app/qa.py` | C |
| `Dockerfile`、`.dockerignore`、`requirements.txt`、`README.md`、三个 ignore | E |
| `docs/` | A |

🔴 **不要用格式化工具重排整个文件** —— 会产生几百行无关 diff，冲突时几乎无法手工合并。

---

## 四、提交规范

按 Angular 风格，type 取值：

```
feat     新功能          fix      修 bug
docs     文档            refactor 重构（不改行为）
test     测试            chore    构建/辅助变动
```

写清楚的最低标准：**别人看 `git log --oneline` 就知道你干了什么**。

```bash
✅ git commit -m "feat(m2): 错题归类加知识点ID越界校验与带原因重试"
✅ git commit -m "fix(sql): 掌握度查询改用 NULLIF 防止除零返回 NULL"
❌ git commit -m "update"
❌ git commit -m "111"
❌ git commit -m "改了点东西"
```

每周每人至少 **3 个真实功能 commit**。官方原话：「我们会直接克隆你的仓库并尝试运行，
仓库本身就是评分的一部分」—— 最后一把梭上传是最容易失分的行为。

---

## 五、绝对不提交的东西

```
.env                    ← 真实密钥
data/*.db               ← 评审跑 seed_data.py 会重新生成
assets/uploads/         ← 用户上传的图片
__pycache__/ .venv/     ← 缓存与环境
.idea/ .vscode/         ← IDE 配置
原始数据集 / 模型权重      ← 体积大且官方明确禁止
```

提交前扫一遍历史确认没有密钥泄漏：

```bash
git log -p | Select-String -Pattern "sk-[a-zA-Z0-9]{20,}"
```

---

## 六、卡住了怎么办

| 情况 | 处理 |
|---|---|
| 遇到 merge 冲突 | 让 AI **列出双方内容**，人来决定取舍。**不要让 AI 替你选** |
| 报错看不懂 | 整段红字原样粘给 DSH，问「这是什么意思，该怎么办」—— 是问方案，不是让它直接改 |
| 卡住超过 90 分钟 | 必须求助，不许闷头耗 |
| 命令行连不上 GitHub | 浏览器能打开网页 ≠ 命令行能 push。检查代理软件是否常驻运行 |
| 需要撤销已推送的改动 | 用 `git revert <commit>`（生成新提交，不改历史），绝不用 force push |

---

## 七、AI 助手的使用约定

提问时默认**只要方案，不动代码**；确定要改了才说"按这个执行"。

```text
第一步：只分析，给方案，不改代码
第二步：我选定方案后你再执行
第三步：执行完先跑一遍验证，把结果给我看
第四步：确认没问题我才让你 commit
```

碎改动留在本地 commit 或 `git stash`，**攒够了、跑通了才 push**。
一句话：**commit 可以频繁，push 要有分寸。**

🔴 任何人不许让 AI 把 API 调用直接写进 `streamlit_app.py`，也不许在界面层写 SQL。
分层规矩见 [design.md](design.md) 第三节。

仓库根目录的 `AGENTS.md` 会被自动注入到每个成员的 AI 助手上下文，
它会默认把使用者当作普通组员并拒绝越界请求 —— 但那是软约束，别当保险。
