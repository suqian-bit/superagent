"""
知识助手仪表盘（Streamlit）

单页 8 区块：
  1. KPI 卡片
  2. 收集趋势（最近 14 天，按 tier 堆叠）
  3. tier 分布饼图
  4. 平台分布饼图
  5. Top 高价值内容表（可展开看 md）
  6. 主题热度（categories 柱状 + tags 标签云）
  7. 项目档案
  8. 定时任务 & 系统状态（折叠）

数据源：
  - /www/knowledge/index.db
  - /www/knowledge/items/**/*.md
  - /www/knowledge/projects/*.md
  - /var/log/review_pusher.log
  - /var/log/weekly_report.log
  - systemctl / df / du / crontab 等 shell

部署：
  systemctl --user start dashboard.service
  → 监听 127.0.0.1:8501
  → nginx 反代 https://knowledge.agentforge.com.cn (Basic Auth)
"""
from __future__ import annotations

import subprocess
import sqlite3
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go


# ---- 配置 ----

DB_PATH = Path("/www/knowledge/index.db")
ITEMS_DIR = Path("/www/knowledge/items")
PROJECTS_DIR = Path("/www/knowledge/projects")
REVIEW_LOG = Path("/var/log/review_pusher.log")
WEEKLY_LOG = Path("/var/log/weekly_report.log")
CN_TZ = timezone(timedelta(hours=8))


# ---- 通用 helper ----

@st.cache_data(ttl=10)
def query_all(sql: str, params: tuple = ()):
    """带 10s 缓存的 SQL 查询。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def shell(cmd: str, timeout: int = 5) -> str:
    """跑 shell 命令拿 stdout（合并 stderr），超时返回空。"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                          text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"(error: {e})"


def run_cmd(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    """安全调用 CLI（不用 shell=True），返回 (success, output)"""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + "\n" + r.stderr).strip()
        return (r.returncode == 0, out)
    except subprocess.TimeoutExpired:
        return (False, f"超时 ({timeout}s)")
    except Exception as e:
        return (False, str(e))


def db_exec(sql: str, params: tuple = ()) -> None:
    """执行写操作（INSERT/UPDATE/DELETE），不缓存。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(sql, params)
    conn.commit()
    conn.close()
    st.cache_data.clear()  # 清缓存，让查询拿到最新数据


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ---- 页面配置 ----

st.set_page_config(
    page_title="知识助手仪表盘",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 顶栏
col_title, col_refresh = st.columns([6, 1])
with col_title:
    st.title("📊 知识助手仪表盘")
    st.caption(f"最后更新：{now_cn().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
with col_refresh:
    st.markdown("&nbsp;")  # 占位
    if st.button("🔄 刷新", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ────────────────────────────────────────
# 工具栏（投喂 + 手动触发）
# ────────────────────────────────────────

with st.expander("🛠 操作工具栏（投喂 / 手动触发 / 跑周报）", expanded=False):
    tab1, tab2, tab3 = st.tabs(["📥 投喂新内容", "📤 手动推送", "📰 跑周报"])

    with tab1:
        st.markdown("把链接或一段文字喂给助理，自动跑 5 步 pipeline 入库。")
        ingest_input = st.text_input(
            "URL 或文本",
            placeholder="https://xhslink.com/o/xxx 或一段笔记...",
            key="ingest_input",
        )
        col_a, col_b = st.columns([1, 6])
        with col_a:
            if st.button("🚀 投喂", type="primary", use_container_width=True):
                if not ingest_input.strip():
                    st.warning("输入不能为空")
                else:
                    st.info("正在跑 ingest pipeline（30-90 秒，长视频更久），请耐心等...")
                    with st.spinner("Step 1 fetch → score → classify → essence → save..."):
                        text = ingest_input.strip()
                        # 判断 URL 类型选 xhs / video，否则当文本走（暂不支持）
                        if "xhslink.com" in text or "xiaohongshu.com" in text:
                            cmd = ["xhs", text, "--transcribe"]
                            tool = "xhs"
                        elif any(d in text for d in ["bilibili.com", "b23.tv", "youtube.com",
                                                     "youtu.be", "douyin.com", "v.douyin.com",
                                                     "kuaishou.com"]):
                            cmd = ["video", text]
                            tool = "video"
                        else:
                            st.error("暂只支持 URL（小红书 / B站 / 抖音 / 快手 / YouTube）。文字内容请通过飞书发给助理。")
                            st.stop()

                        ok, out = run_cmd(cmd, timeout=600)
                        if not ok:
                            st.error(f"Step 1 fetch 失败：\n```\n{out[:1500]}\n```")
                        else:
                            # 找 step_file
                            import json as _json
                            try:
                                # tail 最后一行 json
                                last = [l for l in out.strip().split("\n") if l.startswith("{")][-1]
                                d1 = _json.loads(last)
                                step_file = d1.get("step_file")
                                if d1.get("ok") is False:
                                    st.error(f"{tool} 失败: {d1.get('error', '?')}")
                                    st.stop()
                                # 跑剩下 4 步
                                for step_cmd in ["score", "classify", "essence", "save"]:
                                    ok2, out2 = run_cmd([step_cmd, "--in", step_file], timeout=300)
                                    if not ok2:
                                        st.error(f"{step_cmd} 失败：\n```\n{out2[:1000]}\n```")
                                        st.stop()
                                    last2 = [l for l in out2.strip().split("\n") if l.startswith("{")][-1]
                                    d = _json.loads(last2)
                                    step_file = d.get("step_file")
                                    if step_cmd == "save":
                                        st.success("✅ 入库成功")
                                        st.markdown(d.get("summary_for_agent", ""))
                                        st.cache_data.clear()
                            except Exception as e:
                                st.error(f"解析输出失败：{e}\n\n原始输出:\n```\n{out[-1500:]}\n```")

    with tab2:
        st.markdown("手动触发一次复习推送（cron 之外的临时触发）。")
        col_p1, col_p2 = st.columns([1, 6])
        with col_p1:
            if st.button("📤 立刻推一条", type="primary", use_container_width=True):
                ok, out = run_cmd(["review-push"], timeout=60)
                if ok:
                    st.success("已推送")
                    st.code(out[:2000])
                    st.cache_data.clear()
                else:
                    st.error(f"失败:\n```\n{out[:1500]}\n```")

    with tab3:
        st.markdown("跑一次周报（默认本周已推过会 skip，加 force 强制重发）。")
        col_w1, col_w2, col_w3 = st.columns([1, 1, 5])
        with col_w1:
            if st.button("📰 跑周报", use_container_width=True):
                ok, out = run_cmd(["weekly-report"], timeout=180)
                if ok:
                    st.success("done")
                    st.code(out[:2000])
                    st.cache_data.clear()
                else:
                    st.error(out[:1500])
        with col_w2:
            if st.button("⚡ 强制重发", use_container_width=True):
                ok, out = run_cmd(["weekly-report", "--force"], timeout=180)
                if ok:
                    st.success("已强制重发")
                    st.code(out[:2000])
                    st.cache_data.clear()
                else:
                    st.error(out[:1500])


# ────────────────────────────────────────
# 区块 1: KPI 卡片
# ────────────────────────────────────────

now_iso = now_cn().isoformat(timespec="seconds")
week_ago_iso = (now_cn() - timedelta(days=7)).isoformat(timespec="seconds")
yesterday_iso = (now_cn() - timedelta(hours=24)).isoformat(timespec="seconds")

total = query_all("SELECT COUNT(*) AS n FROM items WHERE archived=0")[0]["n"]
week_new = query_all(
    "SELECT COUNT(*) AS n FROM items WHERE created_at >= ? AND archived=0",
    (week_ago_iso,),
)[0]["n"]
avg_score = query_all(
    "SELECT AVG(score_total) AS a FROM items WHERE archived=0"
)[0]["a"] or 0
due_now = query_all("""
    SELECT COUNT(*) AS n FROM items
    WHERE review_enabled=1 AND archived=0 AND next_review_at <= ?
""", (now_iso,))[0]["n"]
unanswered = query_all("""
    SELECT COUNT(*) AS n FROM push_history
    WHERE response_quality IS NULL AND mode != 'weekly' AND mode != 'heartbeat'
      AND pushed_at >= ?
""", (yesterday_iso,))[0]["n"]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("📥 总收录", total)
c2.metric("📈 本周新增", week_new)
c3.metric("⭐ 平均分", f"{avg_score:.1f}/100")
c4.metric("⏰ 待复习", due_now)
c5.metric("📨 未答推送", unanswered, delta=("⚠️ 有积压" if unanswered > 0 else "✅ 已清"))


# ────────────────────────────────────────
# 区块 2: 收集趋势（最近 14 天）
# ────────────────────────────────────────

st.divider()
st.subheader("📈 收集趋势（最近 14 天）")

trend_start = (now_cn() - timedelta(days=14)).isoformat(timespec="seconds")
trend_rows = query_all("""
    SELECT substr(created_at, 1, 10) AS d, tier, COUNT(*) AS n
    FROM items WHERE created_at >= ? AND archived=0
    GROUP BY d, tier
    ORDER BY d
""", (trend_start,))

if trend_rows:
    import pandas as pd
    df_trend = pd.DataFrame(trend_rows)
    # 补全日期
    all_days = [(now_cn() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    # 透视
    pivot = df_trend.pivot_table(index="d", columns="tier", values="n", fill_value=0)
    pivot = pivot.reindex(all_days, fill_value=0)
    pivot = pivot[[c for c in ["A", "B", "C"] if c in pivot.columns]]

    fig = go.Figure()
    colors = {"A": "#16a34a", "B": "#3b82f6", "C": "#9ca3af"}
    for tier in pivot.columns:
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[tier],
            name=f"{tier} 级",
            marker_color=colors.get(tier, "#888"),
        ))
    fig.update_layout(
        barmode="stack",
        height=320,
        margin=dict(l=10, r=10, t=10, b=60),
        xaxis_title=None, yaxis_title="条数",
        # 把图例放到底部，避免和 mode bar (右上) 重叠
        legend=dict(orientation="h", yanchor="top", y=-0.18,
                    xanchor="center", x=0.5),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displaylogo": False})
else:
    st.info("最近 14 天没有新收录。")


# ────────────────────────────────────────
# 区块 3 & 4: tier 分布 + 平台分布
# ────────────────────────────────────────

st.divider()
col_tier, col_plat = st.columns(2)

with col_tier:
    st.subheader("🎯 tier 分布")
    tier_rows = query_all(
        "SELECT tier, COUNT(*) AS n FROM items WHERE archived=0 GROUP BY tier"
    )
    if tier_rows:
        fig = px.pie(
            tier_rows, values="n", names="tier", hole=0.4,
            color="tier",
            color_discrete_map={"A": "#16a34a", "B": "#3b82f6", "C": "#9ca3af"},
        )
        fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("无数据")

with col_plat:
    st.subheader("🌐 平台分布")
    plat_rows = query_all("""
        SELECT COALESCE(NULLIF(platform, ''), '未知') AS platform,
               COUNT(*) AS n
        FROM items WHERE archived=0 GROUP BY platform
    """)
    if plat_rows:
        fig = px.pie(plat_rows, values="n", names="platform", hole=0.4)
        fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("无数据")


# ────────────────────────────────────────
# 区块 5: Top 高价值内容表
# ────────────────────────────────────────

st.divider()
st.subheader("🏆 Top 高价值内容（按 weight 排）")

top_rows = query_all("""
    SELECT id, title, uploader, platform, tier, weight, score_total,
           summary_one_line, created_at, file_path,
           review_enabled, review_count, mastery, next_review_at
    FROM items WHERE archived=0
    ORDER BY weight DESC, score_total DESC, id DESC
    LIMIT 20
""")

if not top_rows:
    st.info("库里还是空的")
else:
    for r in top_rows:
        tier_color = {"A": "🟢", "B": "🔵", "C": "⚪"}.get(r["tier"], "⚪")
        review_badge = ""
        if r["review_enabled"]:
            review_badge = f" · 🧠 复习{r['review_count']}次 · 掌握{r['mastery']}/100"

        created_short = (r["created_at"] or "")[:16].replace("T", " ")

        with st.expander(
            f"{tier_color} [{r['tier']}·{r['weight']}] id={r['id']}  "
            f"《{r['title']}》"
            f" · {r['uploader']} · {r['platform']}"
            f" · {created_short}{review_badge}"
        ):
            cols = st.columns([3, 1])
            with cols[0]:
                if r["summary_one_line"]:
                    st.markdown(f"**📌 一句话核心**：{r['summary_one_line']}")
            with cols[1]:
                if r["next_review_at"]:
                    st.caption(f"下次复习：{r['next_review_at'][:16]}")

            # ─── 操作按钮 ───
            iid = r["id"]
            tier_now = r["tier"]
            act_cols = st.columns([1, 1, 1, 1, 1, 3])

            with act_cols[0]:
                if tier_now != "A":
                    if st.button("⭐ 提到 A", key=f"to_a_{iid}", use_container_width=True):
                        ok, out = run_cmd(["digest", "promote", str(iid)], timeout=15)
                        if ok:
                            st.success(f"id={iid} → A 级")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(out[:300])
            with act_cols[1]:
                if tier_now != "B":
                    if st.button("🔵 → B", key=f"to_b_{iid}", use_container_width=True):
                        sub = "promote" if tier_now == "C" else "demote"
                        ok, out = run_cmd(["digest", sub, str(iid), "--tier", "B"], timeout=15)
                        if ok:
                            st.success(f"id={iid} → B 级")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(out[:300])
            with act_cols[2]:
                if tier_now != "C":
                    if st.button("⚪ → C", key=f"to_c_{iid}", use_container_width=True):
                        ok, out = run_cmd(["digest", "demote", str(iid)], timeout=15)
                        if ok:
                            st.success(f"id={iid} → C 级")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(out[:300])
            with act_cols[3]:
                if st.button("📦 归档", key=f"arch_{iid}", use_container_width=True):
                    ok, out = run_cmd(["digest", "demote", str(iid), "--archive"], timeout=15)
                    if ok:
                        st.success(f"id={iid} 已归档")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(out[:300])
            with act_cols[4]:
                if st.button("🔁 立即复习", key=f"rev_now_{iid}", use_container_width=True):
                    # 把 next_review_at 改成 1 分钟前，下次 cron / 手动推就会挑到
                    one_min_ago = (now_cn() - timedelta(minutes=1)).isoformat(timespec="seconds")
                    try:
                        db_exec(
                            "UPDATE items SET next_review_at=?, review_enabled=1 WHERE id=?",
                            (one_min_ago, iid),
                        )
                        st.success(f"id={iid} 标记为待复习")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

            # 读 md 文件展示
            file_path = r.get("file_path")
            if file_path and Path(file_path).exists():
                md_content = Path(file_path).read_text(encoding="utf-8")
                # 把 frontmatter 截掉，只显示正文
                if md_content.startswith("---"):
                    parts = md_content.split("---", 2)
                    body = parts[2] if len(parts) >= 3 else md_content
                else:
                    body = md_content
                st.markdown(body)
            else:
                st.warning(f"md 文件不存在：{file_path}")


# ────────────────────────────────────────
# 区块 6: 主题热度
# ────────────────────────────────────────

st.divider()
st.subheader("🔥 主题热度")

col_cat, col_tag = st.columns([1, 1])

with col_cat:
    st.markdown("**📂 大类分布**")
    cat_rows = query_all("""
        SELECT name, count FROM categories ORDER BY count DESC LIMIT 12
    """)
    if cat_rows:
        df_cat = [{"name": c["name"], "count": c["count"]} for c in cat_rows]
        fig = px.bar(df_cat, x="count", y="name", orientation="h", text="count")
        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(categoryorder="total ascending"),
            xaxis_title=None, yaxis_title=None,
        )
        fig.update_traces(textposition="outside", marker_color="#3b82f6")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("还没有分类")

with col_tag:
    st.markdown("**🏷 标签云（Top 30）**")
    tag_rows = query_all("""
        SELECT name, count FROM tags ORDER BY count DESC LIMIT 30
    """)
    if tag_rows:
        # 简单标签云：用 markdown + 字体大小映射
        max_c = max(t["count"] for t in tag_rows)
        html = "<div style='line-height:2; padding:10px;'>"
        for t in tag_rows:
            size = 12 + int((t["count"] / max_c) * 14)  # 12-26 px
            html += (
                f"<span style='font-size:{size}px; margin:4px; "
                f"padding:3px 8px; background:#e5e7eb; border-radius:6px; "
                f"display:inline-block;'>"
                f"#{t['name']} <span style='color:#6b7280; font-size:10px;'>({t['count']})</span>"
                f"</span>"
            )
        html += "</div>"
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.info("还没有标签")


# ────────────────────────────────────────
# 区块 7: 项目档案
# ────────────────────────────────────────

st.divider()
st.subheader("📋 项目档案")

proj_rows = query_all("""
    SELECT p.id, p.name, p.display_name, p.status, p.file_path,
           p.created_at, p.updated_at,
           COUNT(ip.item_id) AS linked_count,
           AVG(ip.relevance) AS avg_relevance
    FROM projects p
    LEFT JOIN item_projects ip ON p.id = ip.project_id
    WHERE p.archived=0
    GROUP BY p.id
    ORDER BY p.updated_at DESC
""")

if not proj_rows:
    st.info("还没有项目档案。在飞书说「我最近在研究 XX」可引导建一个。")
else:
    cols = st.columns(min(3, len(proj_rows)))
    for i, p in enumerate(proj_rows):
        with cols[i % len(cols)]:
            status_emoji = {
                "in-progress": "🚧",
                "planning": "💭",
                "paused": "⏸️",
                "done": "✅",
            }.get(p["status"], "📋")
            avg_rel = p["avg_relevance"] or 0
            updated = (p["updated_at"] or "")[:16].replace("T", " ")

            with st.container(border=True):
                st.markdown(
                    f"### {status_emoji} {p['display_name'] or p['name']}\n"
                    f"`{p['name']}` · {p['status']}"
                )
                st.metric("关联内容", f"{p['linked_count']} 条",
                         delta=(f"avg relevance {avg_rel:.0f}" if p["linked_count"] else None))
                st.caption(f"更新于 {updated}")

                if st.button(f"📖 看档案", key=f"view_proj_{p['id']}"):
                    st.session_state[f"show_proj_{p['id']}"] = True

                if st.session_state.get(f"show_proj_{p['id']}"):
                    file_path = p.get("file_path")
                    if file_path and Path(file_path).exists():
                        md_content = Path(file_path).read_text(encoding="utf-8")
                        if md_content.startswith("---"):
                            parts = md_content.split("---", 2)
                            body = parts[2] if len(parts) >= 3 else md_content
                        else:
                            body = md_content
                        st.markdown("---")
                        st.markdown(body)

                        # ── 快速更新表单 ──
                        st.markdown("---")
                        st.markdown("**✏️ 快速更新**")
                        section_label = {
                            "pain_points": "⚠️ 卡点 / 想搞懂的",
                            "progress": "📊 进度",
                            "goal": "🎯 目标",
                            "notes": "📌 备注",
                            "tags": "🏷 标签",
                            "code_location": "📂 代码位置",
                            "cadence": "🕒 节奏",
                        }
                        section = st.selectbox(
                            "选段落",
                            options=list(section_label.keys()),
                            format_func=lambda x: section_label[x],
                            key=f"sec_{p['id']}",
                        )
                        update_text = st.text_area(
                            "要补充的内容",
                            key=f"text_{p['id']}",
                            placeholder="比如：新发现一个卡点，xxx",
                            height=80,
                        )
                        upd_cols = st.columns([1, 1, 5])
                        with upd_cols[0]:
                            if st.button("➕ 追加", key=f"app_{p['id']}",
                                         use_container_width=True):
                                if not update_text.strip():
                                    st.warning("内容为空")
                                else:
                                    ok, out = run_cmd([
                                        "project", "update", p["name"],
                                        "--section", section,
                                        "--text", update_text.strip(),
                                        "--mode", "append",
                                    ], timeout=15)
                                    if ok:
                                        st.success("已追加")
                                        st.cache_data.clear()
                                        st.rerun()
                                    else:
                                        st.error(out[:400])
                        with upd_cols[1]:
                            if st.button("🔁 替换", key=f"rep_{p['id']}",
                                         use_container_width=True):
                                if not update_text.strip():
                                    st.warning("内容为空")
                                else:
                                    ok, out = run_cmd([
                                        "project", "update", p["name"],
                                        "--section", section,
                                        "--text", update_text.strip(),
                                        "--mode", "replace",
                                    ], timeout=15)
                                    if ok:
                                        st.success("已替换")
                                        st.cache_data.clear()
                                        st.rerun()
                                    else:
                                        st.error(out[:400])

                        # 关联的 items
                        linked = query_all("""
                            SELECT i.id, i.title, i.tier, i.weight,
                                   ip.relevance, ip.reason
                            FROM items i
                            JOIN item_projects ip ON i.id = ip.item_id
                            WHERE ip.project_id = ? AND i.archived = 0
                            ORDER BY ip.relevance DESC, i.weight DESC
                        """, (p["id"],))
                        if linked:
                            st.markdown("---")
                            st.markdown("**🔗 关联内容**")
                            for li in linked:
                                st.markdown(
                                    f"- **[{li['relevance']}]** "
                                    f"`{li['tier']}·{li['weight']}` "
                                    f"id={li['id']} {li['title']} — _{li['reason']}_"
                                )


# ────────────────────────────────────────
# 区块 8: 定时任务 & 系统状态
# ────────────────────────────────────────

st.divider()
st.subheader("⚙️ 定时任务 & 系统状态")

# === 8a 复习推送历史 + 手动打分 ===
with st.expander("📤 复习推送（最近 10 次，未答的可手动打分）", expanded=False):
    push_rows = query_all("""
        SELECT p.id, p.item_id, p.mode, p.pushed_at,
               p.response_quality, p.response_at,
               i.title, i.tier
        FROM push_history p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.mode IN ('soul', 'associate', 'apply', 'drop')
        ORDER BY p.id DESC LIMIT 10
    """)
    if push_rows:
        for p in push_rows:
            quality = p["response_quality"]
            pushed = (p["pushed_at"] or "")[:16].replace("T", " ")
            title = (p["title"] or "(unknown)")[:30]

            if quality is None:
                # 未答 → 显示手动打分按钮
                st.markdown(
                    f"- `{pushed}` · **{p['mode']}** · `{p['tier']}` "
                    f"《{title}》(id={p['item_id']}) · ⏳ 未答"
                )
                rate_cols = st.columns([1, 1, 1, 1, 1, 4])
                for i, q in enumerate([1, 2, 3, 4, 5]):
                    emoji = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🟢", 5: "💚"}[q]
                    with rate_cols[i]:
                        if st.button(f"{emoji} {q}", key=f"rate_{p['id']}_{q}",
                                     use_container_width=True):
                            ok, out = run_cmd(
                                ["digest", "review", str(p["item_id"]), str(q)],
                                timeout=15,
                            )
                            if ok:
                                st.success(f"已打分 {q}/5")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(out[:400])
            else:
                emoji = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🟢", 5: "💚"}.get(quality, "?")
                resp = f"{emoji} {quality}/5"
                st.markdown(
                    f"- `{pushed}` · **{p['mode']}** · `{p['tier']}` "
                    f"《{title}》(id={p['item_id']}) · {resp}"
                )
    else:
        st.info("还没有复习推送")

# === 8b 周报历史 ===
with st.expander("📰 周报推送（最近 4 次）", expanded=False):
    weekly_rows = query_all("""
        SELECT id, pushed_at, length(question) AS chars
        FROM push_history
        WHERE mode='weekly'
        ORDER BY id DESC LIMIT 4
    """)
    if weekly_rows:
        for w in weekly_rows:
            pushed = (w["pushed_at"] or "")[:16].replace("T", " ")
            st.markdown(f"- `{pushed}` · {w['chars']} 字")
    else:
        st.info("还没发过周报")

# === 8c 心跳推送 ===
with st.expander("☀️ 心跳推送（最近 7 次）", expanded=False):
    hb_rows = query_all("""
        SELECT id, pushed_at
        FROM push_history
        WHERE mode='heartbeat'
        ORDER BY id DESC LIMIT 7
    """)
    if hb_rows:
        for h in hb_rows:
            pushed = (h["pushed_at"] or "")[:16].replace("T", " ")
            st.markdown(f"- `{pushed}`")
    else:
        st.info("还没发过心跳")

# === 8d 失败日志 ===
with st.expander("❌ 失败日志（review_pusher / weekly_report）", expanded=False):
    errors = []
    for log in [REVIEW_LOG, WEEKLY_LOG]:
        if log.exists():
            content = log.read_text(encoding="utf-8", errors="ignore")[-20000:]  # 最后 20KB
            for line in content.split("\n"):
                if re.search(r"ERROR|Failed|Exception|Traceback|error", line, re.I):
                    errors.append((log.name, line))

    if not errors:
        st.success("✅ 最近无错误日志")
    else:
        for fname, line in errors[-20:]:  # 最多 20 条
            st.code(f"[{fname}] {line}", language=None)

# === 8e systemd 状态 ===
with st.expander("🛠 systemd / cron / 磁盘", expanded=False):
    cols = st.columns(2)

    with cols[0]:
        st.markdown("**Systemd 服务**")
        for svc in ["hermes-gateway", "hermes-gateway-knowledge", "dashboard"]:
            status = shell(f"systemctl --user is-active {svc}")
            color = "🟢" if status == "active" else "🔴"
            st.markdown(f"- {color} `{svc}` — {status}")

        st.markdown("")
        st.markdown("**Crontab**")
        cron_out = shell("crontab -l 2>/dev/null | grep -vE '^#|^$'")
        if cron_out:
            st.code(cron_out, language="cron")
        else:
            st.info("（无 root crontab）")

    with cols[1]:
        st.markdown("**磁盘**")
        df_out = shell("df -h / /www | head -3")
        st.code(df_out)

        st.markdown("**数据库大小**")
        db_out = shell(f"du -sh {DB_PATH}")
        st.code(db_out)

        st.markdown("**知识库总大小**")
        kb_out = shell("du -sh /www/knowledge/")
        st.code(kb_out)

        st.markdown("**md 文件数**")
        md_count = shell("find /www/knowledge/items -name '*.md' | wc -l")
        st.code(f"items/*.md  {md_count}")


# ---- 底部 ----

st.divider()
st.caption(
    "💡 这是只读面板。想改库存请走飞书或 SSH。\n\n"
    "改字段：`digest promote/demote/archive`、`project update`、`digest review`\n\n"
    f"PRD：`obsidian_knowledge/superagent/docs/13-可视化面板.md`"
)
