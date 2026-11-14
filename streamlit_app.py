"""学习辅导 AI 助教 · Streamlit 界面

只做展示与交互，业务逻辑在 app/qa.py、app/intake.py、app/profile.py。

运行：streamlit run streamlit_app.py
首次使用请先配置模型服务环境变量（见 README「快速开始」）。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# 若存在 .env 则自动载入（未安装 python-dotenv 时静默跳过，不影响运行）
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import pandas as pd
import streamlit as st

from app import db, intake, llm_client, profile, qa

st.set_page_config(page_title="学习辅导 AI 助教", page_icon="🎓", layout="wide")
db.init_db()

SUBJECT = "高中数学"

# ---------------------------------------------------------------- 侧边栏
with st.sidebar:
    st.header("⚙️ 设置")
    style = st.radio("讲解风格", ["简洁", "详细"], horizontal=True)

    if not llm_client.is_configured():
        missing = "、".join(f"`{k}`" for k in llm_client.missing_config_keys())
        st.error(
            f"❌ **模型服务未配置**（缺少 {missing}）\n\n"
            "两种方式任选其一：\n\n"
            "**① 复制配置模板**（推荐，改动不会被提交）\n"
            "```powershell\n"
            "Copy-Item .env.example .env   # 然后编辑 .env 填入三项\n"
            "```\n\n"
            "**② 直接设环境变量后重启本页面**\n"
            "```powershell\n"
            '$env:LLM_BASE_URL="https://api.deepseek.com/v1"\n'
            '$env:LLM_API_KEY="sk-你的密钥"\n'
            '$env:LLM_MODEL="deepseek-chat"\n'
            "```\n"
            "> 三项均为必填，代码中不提供任何默认地址或占位密钥。\n"
        )
    else:
        st.success(f"已连接 `{llm_client._config()['model']}`")

    cost = db.cost_summary()
    st.caption(
        f"累计调用 {cost['calls']} 次 · "
        f"输入 {cost['prompt_tokens']} / 输出 {cost['completion_tokens']} tokens"
        f"（重试 {cost['retried']} 次）"
    )
    if st.button("清空当前对话"):
        st.session_state["messages"] = []


st.title("🎓 学习辅导 AI 助教")
st.caption(f"本课程限定学科：**{SUBJECT}**　·　超出范围的问题会被礼貌拒绝")

tab_qa, tab_intake, tab_mastery, tab_practice = st.tabs(
    ["① 知识点问答", "② 错题录入", "③ 掌握度分析", "④ 针对性练习"]
)

# ================================================================ ① M1 知识点问答
with tab_qa:
    if "messages" not in st.session_state:
        # 🔴 开场白不进历史：保证发给模型的序列以 user 开头，
        #    否则多数模型的 chat template 会报 "No user query found in messages"
        st.session_state["messages"] = []

    topics = qa.list_subject_topics()
    if topics:
        st.markdown("**覆盖的知识点**：" + "、".join(topics[:10]) + (" …" if len(topics) > 10 else ""))
    else:
        st.info("知识库为空，请先运行 `python seed_data.py` 初始化演示数据。")

    for m in st.session_state["messages"]:
        st.chat_message(m["role"]).write(m["content"])

    question = st.chat_input("提出一个本课问题，例如：什么是导数？")
    if question:
        if not llm_client.is_configured():
            st.warning("请先按左侧提示配置模型服务。")
            st.stop()

        st.chat_message("user").write(question)
        st.session_state["messages"].append({"role": "user", "content": question})

        with st.spinner("正在思考…"):
            try:
                answer = qa.answer(
                    question, SUBJECT, style, history=st.session_state["messages"][:-1]
                )
            except llm_client.LLMError as exc:
                answer = None
                st.error(f"调用失败：{exc}")

        if answer:
            st.chat_message("assistant").write(answer)
            st.session_state["messages"].append({"role": "assistant", "content": answer})

# ================================================================ ② M2 错题录入
with tab_intake:
    st.markdown("录入一道做错的题 → 自动识别知识点与错因 → **你确认后**才入库。")

    mode = st.radio("录入方式", ["手输题干", "拍照 / 上传截图"], horizontal=True)

    draft_key = "intake_draft"
    if mode == "手输题干":
        with st.form("text_intake", clear_on_submit=False):
            q_text = st.text_area("题干", height=110, placeholder="已知 f(x)=x²-2x+3，求 f(x) 在 [0,3] 上的最大值")
            s_ans = st.text_input("我的错误答案", value="3")
            c_ans = st.text_input("参考答案（可留空，让系统自行求解）")
            go = st.form_submit_button("🔍 识别并归类")
        if go:
            if not q_text.strip():
                st.warning("题干不能为空。")
            elif not llm_client.is_configured():
                st.warning("请先配置模型服务。")
            else:
                with st.spinner("识别中…"):
                    st.session_state[draft_key] = intake.analyze_text(q_text, s_ans, c_ans)
    else:
        up = st.file_uploader("上传题目照片或截图", type=["jpg", "jpeg", "png"])
        note = st.text_input("补充说明（可选）", placeholder="第 3 小题做错了")
        if st.button("🔍 识别并归类", disabled=up is None):
            if not llm_client.is_configured():
                st.warning("请先配置模型服务。")
            else:
                with st.spinner("识别中…"):
                    st.session_state[draft_key] = intake.analyze_image(up.getvalue(), note)

    draft: intake.IntakeDraft | None = st.session_state.get(draft_key)
    if draft:
        st.divider()
        if draft.degraded:
            st.warning(draft.note or "自动识别未完成，请手动补全下列字段。")
        else:
            st.caption(
                f"识别置信度 **{draft.confidence:.2f}**"
                + (f"（本次经过 {draft.source} 路径）" if draft.source else "")
            )

        with st.form("confirm_intake"):
            st.markdown("#### 确认识别结果（可直接修改）")
            n_question = st.text_area("题干", draft.question_text, height=110)
            col1, col2 = st.columns(2)
            with col1:
                n_student = st.text_input("我的答案", draft.student_answer)
                n_correct = st.text_input("正确答案", draft.correct_answer)
            with col2:
                all_kps = db.all_knowledge_points()
                labels = {k["name"]: k["id"] for k in all_kps}
                default_names = [
                    next((n for n, i in labels.items() if i == kid), None)
                    for kid in draft.knowledge_ids
                ]
                n_kps = st.multiselect(
                    "涉及知识点（只能从本课程列表选择）",
                    options=list(labels),
                    default=[n for n in default_names if n],
                )
                n_error = st.selectbox(
                    "错因类型",
                    list(intake.ERROR_LABELS.values()),
                    index=list(intake.ERROR_LABELS.values()).index(
                        intake.ERROR_LABELS.get(draft.error_type, "无法判定")
                    ),
                )
            steps_text = st.text_area(
                "解题步骤（每行一步）", "\n".join(draft.solution_steps), height=120
            )
            saved_name = st.session_state.get("uploaded_image_name")
            submit = st.form_submit_button("✅ 确认入库")

        if submit:
            rev = {v: k for k, v in labels.items()}
            final = intake.IntakeDraft(
                question_text=n_question,
                student_answer=n_student,
                correct_answer=n_correct,
                solution_steps=[s.strip() for s in steps_text.splitlines() if s.strip()],
                knowledge_ids=[rev[n] for n in n_kps if n in rev],
                error_type=next(
                    (k for k, v in intake.ERROR_LABELS.items() if v == n_error), "unknown"
                ),
                source=draft.source,
            )
            if not final.question_text.strip():
                st.error("题干为空，无法入库。")
            else:
                new_id = intake.commit(final)
                st.success(f"已入库，错题编号 #{new_id}，关联知识点 {len(final.knowledge_ids)} 个。")
                st.session_state.pop(draft_key, None)
                st.rerun()

    st.divider()
    st.markdown("#### 最近录入的错题")
    rows = db.recent_wrong_questions(10)
    if rows:
        df = pd.DataFrame(rows)[["id", "question_text", "kp_names", "error_type", "created_at"]]
        df.columns = ["编号", "题干", "知识点", "错因", "录入时间"]
        df["题干"] = df["题干"].str.slice(0, 40) + "…"
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("还没有错题记录。")

# ================================================================ ③ M3 掌握度
with tab_mastery:
    s = profile.summary_text()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("错题总数", s["错题总数"])
    c2.metric("🔴 未掌握", s["未掌握"])
    c3.metric("🟡 基本掌握", s["基本掌握"])
    c4.metric("🟢 已掌握", s["已掌握"])

    if s["最薄弱"]:
        st.markdown("**当前最薄弱的知识点**：" + "、".join(s["最薄弱"]))

    df = profile.mastery()
    st.markdown("#### 各知识点掌握情况")
    st.dataframe(df, hide_index=True, use_container_width=True)

    chart_df = df[df["状态"] != "⚪ 样本不足"][["知识点", "正确率"]].dropna()
    if not chart_df.empty:
        st.markdown("#### 正确率对比（越低越该练）")
        st.bar_chart(chart_df.set_index("知识点")["正确率"])
    else:
        st.info("练习记录还不足（每个知识点需 ≥3 次），先去做几组练习吧。")

    dist = db.kp_error_distribution()
    if dist:
        st.markdown("#### 错题分布与主要错因")
        st.dataframe(pd.DataFrame(dist), hide_index=True, use_container_width=True)

# ================================================================ ④ M4 针对性练习
with tab_practice:
    st.markdown("依据错题与练习分布出题（优先本地题库，不足时由模型生成），作答后自动判分并回写掌握度。")

    count = st.slider("题目数量", 1, 5, 3)
    if st.button("🎯 生成针对性练习"):
        if not llm_client.is_configured() and not profile.load_bank():
            st.warning("请先配置模型服务，或运行 `python seed_data.py` 载入题库。")
        else:
            with st.spinner("按薄弱点选题中…"):
                items, origin = profile.build_practice_session(count)
            if items:
                st.caption(f"来源：{origin}")
                st.session_state["practice_items"] = items
            else:
                st.error(origin)

    items = st.session_state.get("practice_items", [])
    if items:
        answers: list[str] = []
        for i, it in enumerate(items, 1):
            st.markdown(f"**第 {i} 题**　`{it.get('type','')}`")
            st.write(it.get("question", ""))
            if it.get("type") == "single_choice" and it.get("options"):
                ans = st.radio(
                    "你的选择",
                    list(it["options"].items()),
                    key=f"q{i}",
                    format_func=lambda kv: f"{kv[0]}. {kv[1]}",
                )[0]
            else:
                ans = st.text_input("你的答案", key=f"q{i}_blank")
            answers.append(ans)

        if st.button("提交并判分", type="primary"):
            results = profile.record_result(items, answers)
            ok = sum(results)
            st.success(f"得分 **{ok}/{len(items)}**，已写入练习记录；做错的题已回流错题本。")
            for i, (it, r, ans) in enumerate(zip(items, results, answers), 1):
                with st.expander(
                    f"第 {i} 题 {'✅ 正确' if r else '❌ 错误'}　你的答案：{ans or '（空）'}"
                ):
                    st.write(f"标准答案：**{it.get('answer','')}**")
                    if it.get("explanation"):
                        st.write(it["explanation"])
            st.session_state.pop("practice_items", None)
            st.button("刷新查看新的掌握度")
