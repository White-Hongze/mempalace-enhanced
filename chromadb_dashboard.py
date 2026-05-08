"""
ChromaDB Web GUI - 使用 Streamlit 构建的可视化界面

运行方式：
cd d:\\econ\\mempalace
streamlit run chromadb_dashboard.py
"""

import os
import streamlit as st
import chromadb
import pandas as pd
from pathlib import Path
import json
import datetime
import uuid
import sys

# ═══════════════════════════════════════════════════════════════
# 回收站 / 记录管理辅助函数
# ═══════════════════════════════════════════════════════════════
TRASH_FILE = os.path.expanduser("~/.mempalace/trash.json")
TRASH_TTL_DAYS = 10


def now_iso() -> str:
    return datetime.datetime.now().isoformat()


def ensure_time_metadata(metadata: dict) -> dict:
    """Ensure both timestamp and filed_at exist for every record metadata."""
    meta = dict(metadata or {})
    ts = meta.get("timestamp")
    filed_at = meta.get("filed_at")
    if not ts and filed_at:
        ts = filed_at
    if not filed_at and ts:
        filed_at = ts
    if not ts and not filed_at:
        ts = filed_at = now_iso()
    meta["timestamp"] = ts
    meta["filed_at"] = filed_at
    return meta


def display_time(metadata: dict) -> str:
    meta = metadata or {}
    return str(meta.get("timestamp") or meta.get("filed_at") or "-")


def time_sort_value(metadata: dict) -> float:
    """Return sortable timestamp (epoch seconds). Missing/invalid values go to the bottom."""
    meta = metadata or {}
    raw = meta.get("timestamp") or meta.get("filed_at")
    if raw is None or raw == "":
        return 0.0

    raw_str = str(raw).strip()
    if not raw_str:
        return 0.0

    # Numeric timestamps are accepted directly.
    try:
        return float(raw_str)
    except ValueError:
        pass

    # Normalize ISO-8601 strings, including trailing Z.
    try:
        dt = datetime.datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.timestamp()
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return 0.0


def load_trash():
    if not os.path.exists(TRASH_FILE):
        return []
    try:
        with open(TRASH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_trash(items):
    os.makedirs(os.path.dirname(TRASH_FILE), exist_ok=True)
    with open(TRASH_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def cleanup_expired_trash():
    """启动时调用，删除超过 TRASH_TTL_DAYS 的记录。返回清理数量。"""
    items = load_trash()
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=TRASH_TTL_DAYS)
    kept = []
    expired = 0
    for item in items:
        deleted_at = item.get("deleted_at", "")
        try:
            dt = datetime.datetime.fromisoformat(deleted_at)
            if dt >= cutoff:
                kept.append(item)
            else:
                expired += 1
        except Exception:
            kept.append(item)
    if expired > 0:
        save_trash(kept)
    return expired


def soft_delete_record(collection_obj, doc_id, document, metadata, palace_path_, collection_name_):
    """将记录转入回收站，同时从 ChromaDB 删除。"""
    trash = load_trash()
    trash.append({
        "id": doc_id,
        "document": document,
        "metadata": dict(metadata or {}),
        "deleted_at": datetime.datetime.now().isoformat(),
        "palace_path": palace_path_,
        "collection_name": collection_name_,
    })
    save_trash(trash)
    collection_obj.delete(ids=[doc_id])


def restore_from_trash(client_, trash_idx):
    items = load_trash()
    if trash_idx < 0 or trash_idx >= len(items):
        return False, "索引超出范围"
    item = items[trash_idx]
    try:
        coll = client_.get_or_create_collection(item.get("collection_name", "mempalace_drawers"))
        restored_meta = ensure_time_metadata(item.get("metadata", {}))
        coll.add(
            ids=[item["id"]],
            documents=[item["document"]],
            metadatas=[restored_meta],
        )
        items.pop(trash_idx)
        save_trash(items)
        return True, "恢复成功"
    except Exception as e:
        return False, str(e)


def empty_trash():
    save_trash([])


def add_record(collection_obj, content, wing, room, ingest_mode="manual"):
    doc_id = f"manual_{uuid.uuid4().hex[:16]}"
    metadata = ensure_time_metadata({
        "wing": wing,
        "room": room,
        "ingest_mode": ingest_mode,
        "filed_at": now_iso(),
        "added_by": "dashboard",
    })
    collection_obj.add(
        ids=[doc_id],
        documents=[content],
        metadatas=[metadata],
    )
    return doc_id


def _add_imported_record(collection_obj, record):
    content = record.get("content") or record.get("document") or record.get("text")
    if not content:
        raise ValueError("缺少 content/document/text 字段")
    doc_id = record.get("id") or f"imported_{uuid.uuid4().hex[:16]}"
    metadata = {
        "wing": record.get("wing", "imported"),
        "room": record.get("room", "default"),
        "ingest_mode": record.get("ingest_mode", "import"),
        "filed_at": now_iso(),
        "added_by": "dashboard_import",
    }
    for k, v in record.items():
        if k in {"content", "document", "text", "id"}:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            metadata[k] = v
    metadata = ensure_time_metadata(metadata)
    collection_obj.add(
        ids=[doc_id],
        documents=[content],
        metadatas=[metadata],
    )
    return doc_id


def import_records_file(collection_obj, file_bytes, filename):
    """导入 JSON 或 JSONL。返回 (成功数, errors[])。"""
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("utf-8", errors="replace")

    count = 0
    errors = []
    lower_name = filename.lower()

    if lower_name.endswith(".jsonl"):
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                _add_imported_record(collection_obj, record)
                count += 1
            except Exception as e:
                errors.append(f"第 {i} 行: {e}")
    else:
        try:
            data = json.loads(text)
        except Exception as e:
            return 0, [f"JSON 解析失败: {e}"]
        if isinstance(data, list):
            for i, record in enumerate(data, 1):
                try:
                    _add_imported_record(collection_obj, record)
                    count += 1
                except Exception as e:
                    errors.append(f"第 {i} 项: {e}")
        elif isinstance(data, dict):
            try:
                _add_imported_record(collection_obj, data)
                count = 1
            except Exception as e:
                errors.append(str(e))
        else:
            errors.append("JSON 顶层必须是 object 或 array")

    return count, errors

# 页面配置
st.set_page_config(
    page_title="ChromaDB Dashboard",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🏛️ MemPalace ChromaDB Dashboard")
st.markdown("---")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700;800&family=Space+Grotesk:wght@600;700&display=swap');

    :root {
        --bg-0: #f4f7f5;
        --bg-1: #ffffff;
        --ink-0: #13211b;
        --ink-1: #34443d;
        --line: #d7e1db;
        --brand: #0f766e;
        --brand-2: #1d4ed8;
        --warn: #b45309;
        --danger: #b91c1c;
        --shadow: 0 12px 28px rgba(9, 30, 20, 0.08);
    }

    html, body, [class*="css"], [data-testid="stAppViewContainer"] {
        font-family: 'Noto Sans SC', 'Microsoft YaHei', sans-serif;
        color: var(--ink-0);
    }

    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(1200px 360px at -5% -10%, rgba(15, 118, 110, 0.16), rgba(15, 118, 110, 0) 60%),
            radial-gradient(800px 300px at 105% -8%, rgba(29, 78, 216, 0.13), rgba(29, 78, 216, 0) 60%),
            linear-gradient(180deg, #f7fbf9 0%, #edf3ef 100%);
    }

    [data-testid="stHeader"] {
        background: transparent;
    }

    .block-container {
        padding-top: 1.2rem;
        max-width: 1500px;
    }

    h1, h2, h3 {
        font-family: 'Space Grotesk', 'Noto Sans SC', sans-serif;
        letter-spacing: 0.01em;
        color: #0f1f19;
    }

    [data-testid="stMetric"] {
        background: var(--bg-1);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 0.55rem 0.85rem;
        box-shadow: var(--shadow);
    }

    [data-testid="stMetricLabel"] {
        color: #456056;
        font-weight: 600;
    }

    [data-testid="stMetricValue"] {
        color: #11231c;
        font-weight: 800;
    }

    [data-baseweb="tab-list"] {
        gap: 0.55rem;
        padding: 0.15rem 0;
    }

    [data-baseweb="tab"] {
        background: rgba(255, 255, 255, 0.82);
        border: 1px solid var(--line);
        border-radius: 999px;
        color: var(--ink-1);
        padding: 0.36rem 0.95rem;
        transition: all 0.2s ease;
    }

    [aria-selected="true"][data-baseweb="tab"] {
        color: #ffffff;
        border-color: transparent;
        background: linear-gradient(90deg, var(--brand), var(--brand-2));
        box-shadow: 0 6px 16px rgba(15, 118, 110, 0.25);
    }

    [data-testid="stDataFrame"] {
        border-radius: 14px;
        border: 1px solid var(--line);
        overflow: hidden;
        background: #ffffff;
        box-shadow: var(--shadow);
    }

    [data-testid="stTextArea"] textarea,
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stSelectbox"] div[data-baseweb="select"] {
        border-radius: 12px !important;
        border-color: #c8d6ce !important;
        background-color: #ffffff !important;
    }

    [data-testid="stButton"] button,
    [data-testid="baseButton-secondary"] {
        border-radius: 12px !important;
        border: 1px solid #b7c8be !important;
        background: linear-gradient(180deg, #ffffff 0%, #f2f7f4 100%) !important;
        color: #1f352c !important;
        font-weight: 600 !important;
    }

    [data-testid="stButton"] button:hover {
        border-color: #8ea99c !important;
        transform: translateY(-1px);
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #f8fcfa 0%, #eef4f0 100%);
        border-right: 1px solid #d7e1db;
    }

    [data-testid="stSidebar"] .ebot-sidebar-title {
        position: sticky;
        top: 0;
        z-index: 1000;
        padding: 0.45rem 0 0.75rem 0;
        margin-bottom: 0.35rem;
        font-size: 2.05rem;
        line-height: 1.1;
        font-weight: 800;
        letter-spacing: 0.015em;
        font-family: 'Space Grotesk', 'Noto Sans SC', sans-serif;
        background: linear-gradient(90deg, #0f766e, #1d4ed8);
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        border-bottom: 1px solid #d7e1db;
    }

    [data-testid="stCaptionContainer"] {
        color: #4a665a;
    }

    [data-testid="stExpander"] {
        border: 1px solid #d7e1db;
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.78);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 侧边栏配置
with st.sidebar:
    st.markdown('<div class="ebot-sidebar-title">EBOT记忆管理系统</div>', unsafe_allow_html=True)
    st.header("⚙️ 配置")
    
    palace_path = st.text_input(
        "Palace 路径",
        value=os.path.expanduser("~/.mempalace/palace"),
        help="ChromaDB 数据存储路径"
    )

    available_collections = []
    collection_load_error = None
    if os.path.exists(palace_path):
        try:
            list_client = chromadb.PersistentClient(path=palace_path)
            raw_collections = list_client.list_collections()
            for item in raw_collections:
                if isinstance(item, str):
                    available_collections.append(item)
                elif hasattr(item, "name"):
                    available_collections.append(item.name)
                elif isinstance(item, dict) and "name" in item:
                    available_collections.append(item["name"])
            available_collections = sorted(set(available_collections))
        except Exception as e:
            collection_load_error = str(e)

    if available_collections:
        st.caption(f"检测到 {len(available_collections)} 个 collections")
        default_collection = "mempalace_drawers"
        default_index = (
            available_collections.index(default_collection)
            if default_collection in available_collections
            else 0
        )
        selected_collection = st.selectbox(
            "Collection 名称",
            options=available_collections,
            index=default_index,
            help="从数据库中检测到的 collection 里选择"
        )
        use_manual_collection = st.checkbox("手动输入 collection 名称", value=False)
        if use_manual_collection:
            collection_name = st.text_input(
                "手动输入",
                value=selected_collection,
                help="可输入未在列表中的 collection 名称"
            )
        else:
            collection_name = selected_collection
    else:
        collection_name = st.text_input(
            "Collection 名称",
            value="mempalace_drawers",
            help="要查看的 collection"
        )
        if collection_load_error:
            st.warning(f"无法自动读取 collections: {collection_load_error}")
    
    auto_refresh = st.checkbox("自动刷新", value=True, help="每 10 秒刷新一次")
    if auto_refresh:
        st.write("⏱️ 自动刷新已启用")

# 检查路径
if not os.path.exists(palace_path):
    st.error(f"❌ 路径不存在: {palace_path}")
    st.info(f"📁 请检查路径或使用默认路径")
    st.stop()

try:
    # 连接数据库
    client = chromadb.PersistentClient(path=palace_path)
    collection = client.get_collection(collection_name)
except Exception as e:
    st.error(f"❌ 无法连接数据库: {e}")
    st.info(f"💡 确保 palace 路径正确且数据库已初始化")
    st.stop()

# 启动时自动清理过期回收站
expired_count = cleanup_expired_trash()
if expired_count > 0:
    st.toast(f"已自动清理 {expired_count} 条超过 {TRASH_TTL_DAYS} 天的回收站记录")

# 获取统计信息
total_count = collection.count()

# 统计栏
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("📊 总条目数", total_count)
with col2:
    st.metric("📁 存储路径", palace_path.split("\\")[-1])
with col3:
    st.metric("🏷️ Collection", collection_name)

st.markdown("---")

if total_count == 0:
    st.warning("⚠️ 数据库为空，暂无数据可显示")
    st.stop()


def get_selected_row_index(table_event) -> int:
    """Return selected row index from Streamlit dataframe event, defaulting to 0."""
    if not table_event:
        return 0

    selection = getattr(table_event, "selection", None)
    if selection is None:
        return 0

    rows = []
    if isinstance(selection, dict):
        rows = selection.get("rows", [])
    else:
        rows = getattr(selection, "rows", [])

    if rows:
        return int(rows[0])
    return 0


def get_selected_row_indices(table_event) -> list[int]:
    """Return all selected row indices from a multi-row selection dataframe event."""
    if not table_event:
        return []

    selection = getattr(table_event, "selection", None)
    if selection is None:
        return []

    rows = []
    if isinstance(selection, dict):
        rows = selection.get("rows", [])
    else:
        rows = getattr(selection, "rows", [])

    return [int(r) for r in rows]

# 标签页
tab1, tab8, tab9, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "💬 对话内容浏览",
    "📔 Agent日记浏览",
    "🕸️ 知识图谱",
    "📚 全量浏览",
    "🔍 搜索",
    "📊 统计",
    "ℹ️ 详情",
    "➕ 添加记录",
    "🗑️ 回收站",
])

# ═══════════════════════════════════════════════════════════════
# TAB 1: 对话内容浏览（仅 ingest_mode=convos）
# ═══════════════════════════════════════════════════════════════
with tab1:
    st.caption("仅展示通过对话（transcripts）方式录入的内容")
    
    # 获取所有数据进行过滤
    all_results = collection.get(
        limit=total_count,
        include=["documents", "metadatas"]
    )
    
    # 过滤 ingest_mode=convos 的记录，并按时间倒序排序
    convo_records = []
    
    for idx, (doc_id, document, metadata) in enumerate(
        zip(all_results["ids"], all_results["documents"], all_results["metadatas"]), 1
    ):
        if metadata.get("ingest_mode") != "convos":
            continue
        
        convo_records.append((doc_id, document, metadata))

    convo_records.sort(key=lambda x: time_sort_value(x[2]), reverse=True)

    table_data = []
    convo_ids = []
    convo_docs = []
    convo_metas = []
    for doc_id, document, metadata in convo_records:
        convo_ids.append(doc_id)
        convo_docs.append(document)
        convo_metas.append(metadata)

        wing = metadata.get("wing", "—")
        room = metadata.get("room", "—")
        preview = document[:100].replace("\n", " ") if document else "(空)"
        if len(document or "") > 100:
            preview += "..."

        table_data.append({
            "ID": doc_id,
            "Wing": wing,
            "Room": room,
            "Ingest Mode": metadata.get("ingest_mode", "—"),
            "内容预览": preview,
            "长度": len(document or ""),
            "时间": display_time(metadata),
        })
    
    if not table_data:
        st.info("📭 暂无对话内容（ingest_mode=convos）")
        st.stop()
    
    df = pd.DataFrame(table_data)
    convo_count = len(convo_ids)
    st.metric("📱 对话条目数", convo_count)

    # 使用左右布局，避免表格高度变化时下方内容重叠
    table_col1, detail_col1 = st.columns([1.6, 1.4], gap="large")

    selected_row_idx = 0
    with table_col1:
        table_event = st.dataframe(
            df,
            use_container_width=True,
            height=460,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="tab1_browse_table",
        )
        selected_rows_1 = get_selected_row_indices(table_event)
        selected_row_idx = selected_rows_1[0] if selected_rows_1 else 0

        # 批量删除
        if len(selected_rows_1) > 1:
            st.markdown(f"**已选中 {len(selected_rows_1)} 条记录**")
            confirm_batch_1 = st.checkbox("确认批量删除", key="tab1_confirm_batch_del")
            if st.button(
                f"🗑️ 批量删除 {len(selected_rows_1)} 条 (进回收站)",
                key="tab1_batch_del_btn",
                disabled=not confirm_batch_1,
            ):
                ok_count = 0
                for ri in selected_rows_1:
                    try:
                        soft_delete_record(
                            collection, convo_ids[ri], convo_docs[ri],
                            convo_metas[ri], palace_path, collection_name,
                        )
                        ok_count += 1
                    except Exception:
                        pass
                st.success(f"✅ 已将 {ok_count} 条记录移至回收站")

    with detail_col1:
        st.markdown("### 🔎 查看完整内容")
        if len(convo_ids) > 0:
            doc_id = convo_ids[selected_row_idx]
            document = convo_docs[selected_row_idx]
            metadata = convo_metas[selected_row_idx]

            st.subheader(f"条目 #{selected_row_idx + 1}")
            st.write(f"**ID:** `{doc_id}`")
            st.write(f"**Wing:** {metadata.get('wing', '—')}")
            st.write(f"**Room:** {metadata.get('room', '—')}")

            with st.expander("元数据", expanded=False):
                st.json(metadata)

            st.subheader("📄 完整内容")
            st.text_area(
                "内容",
                value=document,
                height=260,
                disabled=True,
                key=f"tab1_full_text_{doc_id}",
            )

            confirm_del_1 = st.checkbox("确认删除", key=f"tab1_confirm_del_{doc_id}")
            if st.button("🗑️ 删除此记录 (进回收站)", key=f"tab1_del_btn_{doc_id}", disabled=not confirm_del_1):
                try:
                    soft_delete_record(collection, doc_id, document, metadata, palace_path, collection_name)
                    st.success(f"已将 {doc_id} 移至回收站")
                except Exception as e:
                    st.error(f"删除失败: {e}")

# ═══════════════════════════════════════════════════════════════
# TAB 9: 知识图谱（实体关系可视化）
# ═══════════════════════════════════════════════════════════════
with tab9:
    st.subheader("🕸️ 知识图谱 — 实体关系可视化")
    st.caption("展示 MemPalace 知识图谱中的实体与关系")

    # 加载知识图谱
    try:
        # 确保 mempalace 模块可导入
        sys.path.insert(0, str(Path(__file__).parent))
        from mempalace.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        kg_stats = kg.stats()

        # 统计概览
        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        col_s1.metric("实体数", kg_stats["entities"])
        col_s2.metric("关系数", kg_stats["triples"])
        col_s3.metric("当前有效", kg_stats["current_facts"])
        col_s4.metric("已失效", kg_stats["expired_facts"])

        if kg_stats["triples"] == 0:
            st.info("知识图谱暂无数据。通过 MCP 工具 `mempalace_kg_add` 添加实体关系后即可在此查看。")
        else:
            # 筛选控件
            st.markdown("---")
            col_f1, col_f2, col_f3 = st.columns([1, 1, 1])

            with col_f1:
                show_expired = st.checkbox("显示已失效关系", value=False, key="kg_show_expired")
            with col_f2:
                # 获取所有实体名用于筛选
                conn = kg._conn()
                all_entities = [
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM entities ORDER BY name"
                    ).fetchall()
                ]
                selected_entity = st.selectbox(
                    "筛选实体",
                    options=["（全部）"] + all_entities,
                    key="kg_filter_entity",
                )
            with col_f3:
                rel_types = kg_stats.get("relationship_types", [])
                selected_rel = st.selectbox(
                    "筛选关系类型",
                    options=["（全部）"] + rel_types,
                    key="kg_filter_rel",
                )

            # 查询三元组
            if selected_entity != "（全部）":
                triples_data = kg.query_entity(selected_entity, direction="both")
            else:
                # 获取所有三元组
                conn = kg._conn()
                query = """
                    SELECT t.*, s.name as sub_name, o.name as obj_name
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    JOIN entities o ON t.object = o.id
                    ORDER BY t.valid_from DESC NULLS LAST
                    LIMIT 500
                """
                rows = conn.execute(query).fetchall()
                triples_data = [
                    {
                        "subject": r["sub_name"],
                        "predicate": r["predicate"],
                        "object": r["obj_name"],
                        "valid_from": r["valid_from"],
                        "valid_to": r["valid_to"],
                        "current": r["valid_to"] is None,
                    }
                    for r in rows
                ]

            # 根据筛选条件过滤
            if not show_expired:
                triples_data = [t for t in triples_data if t.get("current", True)]
            if selected_rel != "（全部）":
                triples_data = [t for t in triples_data if t["predicate"] == selected_rel]

            if not triples_data:
                st.warning("当前筛选条件下无数据。")
            else:
                # ── Graphviz 图谱可视化 ──
                st.markdown("### 关系图谱")

                # 构建 DOT 图
                dot_lines = [
                    "digraph KnowledgeGraph {",
                    '  rankdir=LR;',
                    '  node [shape=box, style="rounded,filled", fillcolor="#E8F4FD", fontname="Microsoft YaHei"];',
                    '  edge [fontname="Microsoft YaHei", fontsize=10];',
                ]

                # 收集节点和边
                nodes = set()
                for t in triples_data:
                    subj = t["subject"]
                    obj = t["object"]
                    pred = t["predicate"]
                    nodes.add(subj)
                    nodes.add(obj)

                    # 边的颜色：当前有效为蓝色，失效为灰色
                    color = "#2196F3" if t.get("current", True) else "#BDBDBD"
                    style = "solid" if t.get("current", True) else "dashed"
                    # 转义引号
                    subj_escaped = subj.replace('"', '\\"')
                    obj_escaped = obj.replace('"', '\\"')
                    pred_escaped = pred.replace('"', '\\"').replace("_", " ")
                    dot_lines.append(
                        f'  "{subj_escaped}" -> "{obj_escaped}" [label="{pred_escaped}", color="{color}", style="{style}"];'
                    )

                # 节点样式
                for node in nodes:
                    node_escaped = node.replace('"', '\\"')
                    dot_lines.append(f'  "{node_escaped}" [label="{node_escaped}"];')

                dot_lines.append("}")
                dot_source = "\n".join(dot_lines)

                try:
                    st.graphviz_chart(dot_source, use_container_width=True)
                except Exception as e:
                    st.warning(f"图谱渲染失败（可能需要安装 graphviz）: {e}")
                    st.code(dot_source, language="dot")

                # ── 关系表格 ──
                st.markdown("### 关系列表")
                table_data = []
                for t in triples_data:
                    table_data.append({
                        "主体": t["subject"],
                        "关系": t["predicate"].replace("_", " "),
                        "客体": t["object"],
                        "起始时间": t.get("valid_from") or "—",
                        "结束时间": t.get("valid_to") or "（当前有效）",
                        "状态": "✅ 有效" if t.get("current", True) else "❌ 已失效",
                    })

                kg_df = pd.DataFrame(table_data)
                st.dataframe(kg_df, use_container_width=True, hide_index=True)

                st.caption(f"共 {len(triples_data)} 条关系 | 涉及 {len(nodes)} 个实体")

    except ImportError as e:
        st.error(f"无法加载知识图谱模块: {e}")
        st.info("请确保 mempalace 已安装: `pip install -e .`")
    except Exception as e:
        st.error(f"知识图谱加载失败: {e}")
        st.info("如果尚未创建知识图谱，请通过 MCP 工具 `mempalace_kg_add` 添加数据。")

# ═══════════════════════════════════════════════════════════════
# TAB 2: 全量浏览（支持持续加载）
# ═══════════════════════════════════════════════════════════════
with tab2:
    st.subheader("📚 全量浏览")
    st.caption("始终展示当前 collection 的全部记录。")

    loaded_count = total_count
    st.metric("总条数", total_count)

    full_results = collection.get(limit=loaded_count, include=["documents", "metadatas"])
    full_records = list(zip(full_results["ids"], full_results["documents"], full_results["metadatas"]))
    full_records.sort(key=lambda x: time_sort_value(x[2]), reverse=True)

    sorted_full_ids = [x[0] for x in full_records]
    sorted_full_docs = [x[1] for x in full_records]
    sorted_full_metas = [x[2] for x in full_records]

    full_table_data = []
    for idx, (doc_id, document, metadata) in enumerate(full_records, 1):
        preview = document[:120].replace("\n", " ") if document else "(空)"
        if len(document or "") > 120:
            preview += "..."

        full_table_data.append({
            "序号": idx,
            "ID": doc_id,
            "Wing": metadata.get("wing", "—"),
            "Room": metadata.get("room", "—"),
            "Ingest Mode": metadata.get("ingest_mode", "—"),
            "时间": display_time(metadata),
            "内容预览": preview,
            "长度": len(document or "")
        })

    full_df = pd.DataFrame(full_table_data)

    table_col2, detail_col2 = st.columns([1.6, 1.4], gap="large")
    selected_full_row_idx = 0
    with table_col2:
        full_table_event = st.dataframe(
            full_df,
            use_container_width=True,
            height=520,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="tab2_full_table",
        )
        selected_rows_2 = get_selected_row_indices(full_table_event)
        selected_full_row_idx = selected_rows_2[0] if selected_rows_2 else 0

        # 批量删除
        if len(selected_rows_2) > 1:
            st.markdown(f"**已选中 {len(selected_rows_2)} 条记录**")
            confirm_batch_2 = st.checkbox("确认批量删除", key="tab2_confirm_batch_del")
            if st.button(
                f"🗑️ 批量删除 {len(selected_rows_2)} 条 (进回收站)",
                key="tab2_batch_del_btn",
                disabled=not confirm_batch_2,
            ):
                ok_count = 0
                for ri in selected_rows_2:
                    try:
                        soft_delete_record(
                            collection, sorted_full_ids[ri], sorted_full_docs[ri],
                            sorted_full_metas[ri], palace_path, collection_name,
                        )
                        ok_count += 1
                    except Exception:
                        pass
                st.success(f"✅ 已将 {ok_count} 条记录移至回收站")

    with detail_col2:
        st.markdown("### 查看全量浏览中的单条详情")
        if loaded_count > 0:
            selected_full_id = sorted_full_ids[selected_full_row_idx]
            selected_full_doc = sorted_full_docs[selected_full_row_idx]
            selected_full_meta = sorted_full_metas[selected_full_row_idx]
            st.write(f"**ID:** `{selected_full_id}`")
            with st.expander("元数据", expanded=False):
                st.json(selected_full_meta)
            st.text_area(
                "完整内容",
                value=selected_full_doc,
                height=300,
                disabled=True,
                key=f"tab2_full_detail_content_{selected_full_id}",
            )

            confirm_del_2 = st.checkbox("确认删除", key=f"tab2_confirm_del_{selected_full_id}")
            if st.button("🗑️ 删除此记录 (进回收站)", key=f"tab2_del_btn_{selected_full_id}", disabled=not confirm_del_2):
                try:
                    soft_delete_record(collection, selected_full_id, selected_full_doc, selected_full_meta, palace_path, collection_name)
                    st.success(f"已将 {selected_full_id} 移至回收站")
                except Exception as e:
                    st.error(f"删除失败: {e}")

# ═══════════════════════════════════════════════════════════════
# TAB 3: 语义搜索
# ═══════════════════════════════════════════════════════════════
with tab3:
    st.subheader("🔍 语义搜索")
    
    search_query = st.text_input(
        "搜索关键词",
        placeholder="输入要搜索的文本...",
        help="使用语义相似度搜索"
    )
    
    search_limit = st.slider("返回结果数", min_value=1, max_value=20, value=5)
    
    if search_query:
        try:
            search_results = collection.query(
                query_texts=[search_query],
                n_results=search_limit,
                include=["documents", "metadatas", "distances"]
            )
            
            st.success(f"✅ 找到 {len(search_results['ids'][0])} 个相关结果")
            st.markdown("---")
            
            for i, (doc_id, document, metadata, distance) in enumerate(
                zip(
                    search_results["ids"][0],
                    search_results["documents"][0],
                    search_results["metadatas"][0],
                    search_results["distances"][0]
                ),
                1
            ):
                with st.container():
                    col1, col2, col3 = st.columns([3, 1, 1])
                    
                    with col1:
                        st.write(f"**#{i}** - {metadata.get('wing', '—')} / {metadata.get('room', '—')}")
                        preview = document[:150].replace("\n", " ")
                        if len(document) > 150:
                            preview += "..."
                        st.write(preview)
                    
                    with col2:
                        # 显示相似度 (distance 越小越相似)
                        similarity = 1 / (1 + distance)
                        st.metric("相似度", f"{similarity:.1%}")
                    
                    with col3:
                        st.write(f"**ID:** `{doc_id[:8]}...`")
                    
                    if st.checkbox(f"查看完整内容 #{i}", key=f"expand_{i}"):
                        st.text_area(
                            f"完整内容 #{i}",
                            value=document,
                            height=150,
                            disabled=True,
                            key=f"textarea_{i}"
                        )
                
                st.markdown("---")
        
        except Exception as e:
            st.error(f"❌ 搜索失败: {e}")
    
    else:
        st.info("💡 输入关键词来搜索相似内容")

# ═══════════════════════════════════════════════════════════════
# TAB 4: 统计信息
# ═══════════════════════════════════════════════════════════════
with tab4:
    st.subheader("📊 统计信息")
    
    # 获取所有数据用于统计
    all_results = collection.get(
        limit=total_count,
        include=["documents", "metadatas"]
    )
    
    # Wing 统计
    wings = {}
    rooms = {}
    content_lengths = []
    
    for document, metadata in zip(all_results["documents"], all_results["metadatas"]):
        wing = metadata.get("wing", "Unknown")
        room = metadata.get("room", "Unknown")
        
        wings[wing] = wings.get(wing, 0) + 1
        rooms[room] = rooms.get(room, 0) + 1
        content_lengths.append(len(document or ""))
    
    # 显示统计
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("📁 Wing 数量", len(wings))
    with col2:
        st.metric("🏷️ Room 数量", len(rooms))
    with col3:
        avg_length = sum(content_lengths) / len(content_lengths) if content_lengths else 0
        st.metric("📄 平均内容长度", f"{avg_length:.0f} 字")
    with col4:
        total_chars = sum(content_lengths)
        st.metric("📊 总字数", f"{total_chars:,}")
    
    st.markdown("---")
    
    # Wing 分布
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🏛️ Wing 分布")
        wing_df = pd.DataFrame(sorted(wings.items()), columns=["Wing", "条目数"])
        st.bar_chart(wing_df.set_index("Wing"))
    
    with col2:
        st.subheader("🏷️ Room 分布 (Top 10)")
        room_df = pd.DataFrame(sorted(rooms.items(), key=lambda x: x[1], reverse=True)[:10], columns=["Room", "条目数"])
        st.bar_chart(room_df.set_index("Room"))
    
    # 内容长度分布
    st.subheader("📏 内容长度分布")
    st.bar_chart(pd.Series(content_lengths).value_counts().sort_index(), height=300)

# ═══════════════════════════════════════════════════════════════
# TAB 5: 详细信息
# ═══════════════════════════════════════════════════════════════
with tab5:
    st.subheader("ℹ️ 数据库信息")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**路径信息**")
        st.code(palace_path)
        st.write(f"**磁盘使用**")
        try:
            import shutil
            disk_usage = shutil.disk_usage(palace_path)
            st.write(f"- 已用: {disk_usage.used / (1024**3):.2f} GB")
            st.write(f"- 空闲: {disk_usage.free / (1024**3):.2f} GB")
        except:
            st.write("无法获取磁盘信息")
    
    with col2:
        st.write("**Collection 信息**")
        st.write(f"- 名称: `{collection_name}`")
        st.write(f"- 总条目: {total_count}")
        st.write(f"- 嵌入维度: (自动检测)")
    
    st.markdown("---")
    
    st.write("**元数据样本** (前 5 项)")
    sample_results = collection.get(limit=5, include=["metadatas"])
    for i, meta in enumerate(sample_results["metadatas"], 1):
        with st.expander(f"📋 元数据样本 #{i}"):
            st.json(meta)

# ═══════════════════════════════════════════════════════════════
# TAB 6: 添加记录（手工输入 + JSON/JSONL 导入）
# ═══════════════════════════════════════════════════════════════
with tab6:
    st.subheader("➕ 添加记录")

    add_mode = st.radio(
        "添加方式",
        ["✍️ 手工输入", "📂 文件导入 (JSON / JSONL)"],
        horizontal=True,
        key="add_mode_radio",
    )

    if add_mode == "✍️ 手工输入":
        with st.form("manual_add_form", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            with col_a:
                manual_wing = st.text_input("Wing", value="manual", help="所属 Wing")
            with col_b:
                manual_room = st.text_input("Room", value="default", help="所属 Room")

            manual_ingest = st.text_input("Ingest Mode", value="manual", help="录入方式标识")
            manual_content = st.text_area(
                "内容",
                placeholder="粘贴或输入要保存的文本...",
                height=240,
            )

            submitted = st.form_submit_button("✅ 添加到数据库")
            if submitted:
                if not manual_content.strip():
                    st.error("❌ 内容不能为空")
                elif not manual_wing.strip() or not manual_room.strip():
                    st.error("❌ Wing/Room 不能为空")
                else:
                    try:
                        new_id = add_record(
                            collection,
                            manual_content.strip(),
                            manual_wing.strip(),
                            manual_room.strip(),
                            ingest_mode=manual_ingest.strip() or "manual",
                        )
                        st.success(f"✅ 已添加: `{new_id}`")
                    except Exception as e:
                        st.error(f"❌ 添加失败: {e}")

    else:
        st.caption(
            "支持 `.json`（数组或单对象）与 `.jsonl`（每行一条）。"
            "每条至少包含 `content`/`document`/`text` 字段，"
            "可选 `id`、`wing`、`room`、`ingest_mode` 等。"
        )

        uploaded_files = st.file_uploader(
            "选择 JSON 或 JSONL 文件",
            type=["json", "jsonl"],
            accept_multiple_files=True,
            key="import_files",
        )

        if uploaded_files:
            if st.button("🚀 开始导入", key="import_start_btn"):
                total_ok = 0
                all_errors = []
                for uf in uploaded_files:
                    try:
                        data = uf.read()
                        ok, errs = import_records_file(collection, data, uf.name)
                        total_ok += ok
                        for e in errs:
                            all_errors.append(f"[{uf.name}] {e}")
                    except Exception as e:
                        all_errors.append(f"[{uf.name}] 读取失败: {e}")

                if total_ok > 0:
                    st.success(f"✅ 成功导入 {total_ok} 条")
                if all_errors:
                    st.error(f"⚠️ 出现 {len(all_errors)} 个错误")
                    with st.expander("查看错误详情"):
                        for err in all_errors:
                            st.write(f"- {err}")

# ═══════════════════════════════════════════════════════════════
# TAB 7: 回收站
# ═══════════════════════════════════════════════════════════════
with tab7:
    st.subheader("🗑️ 回收站")
    st.caption(f"被删除的记录会保留 {TRASH_TTL_DAYS} 天，过期自动彻底删除。")

    trash_items = load_trash()
    now_dt = datetime.datetime.now()

    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        st.metric("📦 当前条数", len(trash_items))
    with col_t2:
        st.metric("⏳ 保留天数", TRASH_TTL_DAYS)
    with col_t3:
        st.metric("📁 回收站文件", os.path.basename(TRASH_FILE))

    st.code(TRASH_FILE)

    if not trash_items:
        st.info("📭 回收站为空")
    else:
        # 表格
        trash_table = []
        for idx, item in enumerate(trash_items):
            deleted_at_str = item.get("deleted_at", "")
            try:
                deleted_dt = datetime.datetime.fromisoformat(deleted_at_str)
                age_days = (now_dt - deleted_dt).days
                remaining = max(0, TRASH_TTL_DAYS - age_days)
            except Exception:
                remaining = "?"

            doc = item.get("document", "")
            preview = doc[:80].replace("\n", " ") if doc else "(空)"
            if len(doc or "") > 80:
                preview += "..."

            meta = item.get("metadata", {}) or {}
            trash_table.append({
                "序号": idx + 1,
                "ID": item.get("id", "—"),
                "Wing": meta.get("wing", "—"),
                "Room": meta.get("room", "—"),
                "Ingest Mode": meta.get("ingest_mode", "—"),
                "删除时间": deleted_at_str,
                "剩余天数": remaining,
                "内容预览": preview,
            })

        trash_df = pd.DataFrame(trash_table)
        trash_event = st.dataframe(
            trash_df,
            use_container_width=True,
            height=360,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="trash_table",
        )
        trash_sel_idx = get_selected_row_index(trash_event)

        if 0 <= trash_sel_idx < len(trash_items):
            sel_item = trash_items[trash_sel_idx]
            st.markdown("### 🔎 回收站记录详情")
            st.write(f"**ID:** `{sel_item.get('id', '—')}`")
            st.write(f"**删除时间:** {sel_item.get('deleted_at', '—')}")
            st.write(f"**Collection:** {sel_item.get('collection_name', '—')}")
            st.json(sel_item.get("metadata", {}))
            st.text_area(
                "完整内容",
                value=sel_item.get("document", ""),
                height=200,
                disabled=True,
                key=f"trash_detail_{trash_sel_idx}_{sel_item.get('id', '')}",
            )

            col_r1, col_r2 = st.columns([1, 1])
            with col_r1:
                if st.button("♻️ 恢复此记录", key=f"trash_restore_{trash_sel_idx}"):
                    ok, msg = restore_from_trash(client, trash_sel_idx)
                    if ok:
                        st.success(f"✅ {msg}")
                    else:
                        st.error(f"❌ {msg}")
            with col_r2:
                if st.button("🔥 立即彻底删除此条", key=f"trash_purge_{trash_sel_idx}"):
                    items = load_trash()
                    if 0 <= trash_sel_idx < len(items):
                        items.pop(trash_sel_idx)
                        save_trash(items)
                        st.success("✅ 已彻底删除")

        st.markdown("---")
        st.markdown("#### ⚠️ 危险操作")
        confirm_empty = st.checkbox("我确认要彻底清空回收站", key="confirm_empty_trash")
        if st.button("🔥 立即清空回收站", disabled=not confirm_empty, key="empty_trash_btn"):
            empty_trash()
            st.success("✅ 回收站已清空")

# ═══════════════════════════════════════════════════════════════
# TAB 8: Agent日记浏览（仅 room=diary）
# ═══════════════════════════════════════════════════════════════
with tab8:
    st.subheader("📔 Agent日记浏览")
    st.caption("仅展示 room=diary 的记录")
    
    # 获取所有数据进行过滤
    all_results = collection.get(
        limit=total_count,
        include=["documents", "metadatas"]
    )
    
    # 过滤 room=diary 的记录，并按时间倒序排序
    diary_records = []
    
    for idx, (doc_id, document, metadata) in enumerate(
        zip(all_results["ids"], all_results["documents"], all_results["metadatas"]), 1
    ):
        if metadata.get("room") != "diary":
            continue
        
        diary_records.append((doc_id, document, metadata))
    
    # 按时间倒序排列（最新的在前）
    diary_records.sort(key=lambda x: time_sort_value(x[2]), reverse=True)
    
    if not diary_records:
        st.info("📭 暂无日志记录")
    else:
        st.write(f"📊 **共 {len(diary_records)} 条日志**")
        st.markdown("---")
        
        # 表格显示日志列表
        diary_table = []
        for idx, (doc_id, document, metadata) in enumerate(diary_records, 1):
            wing = metadata.get("wing", "—")
            content_preview = document[:100].replace("\n", " ") if document else "(空)"
            if document and len(document) > 100:
                content_preview += "..."
            
            diary_table.append({
                "序号": idx,
                "Wing": wing,
                "时间": display_time(metadata),
                "内容预览": content_preview,
            })
        
        diary_df = pd.DataFrame(diary_table)
        diary_event = st.dataframe(
            diary_df,
            use_container_width=True,
            height=400,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="diary_table",
        )
        
        selected_rows_8 = get_selected_row_indices(diary_event)
        diary_sel_idx = selected_rows_8[0] if selected_rows_8 else 0

        # 批量删除
        if len(selected_rows_8) > 1:
            st.markdown(f"**已选中 {len(selected_rows_8)} 条日记**")
            confirm_batch_8 = st.checkbox("确认批量删除", key="tab8_confirm_batch_del")
            if st.button(
                f"🗑️ 批量删除 {len(selected_rows_8)} 条日记 (进回收站)",
                key="tab8_batch_del_btn",
                disabled=not confirm_batch_8,
            ):
                ok_count = 0
                for ri in selected_rows_8:
                    try:
                        did, ddoc, dmeta = diary_records[ri]
                        soft_delete_record(
                            collection, did, ddoc, dmeta, palace_path, collection_name,
                        )
                        ok_count += 1
                    except Exception:
                        pass
                st.success(f"✅ 已将 {ok_count} 条日记移至回收站")
        
        if 0 <= diary_sel_idx < len(diary_records):
            doc_id, document, metadata = diary_records[diary_sel_idx]
            
            st.markdown("---")
            st.markdown("### 🔎 日志详情")
            
            col_d1, col_d2 = st.columns([1.6, 1.4])
            
            with col_d1:
                st.markdown(f"**ID:** `{doc_id}`")
                st.markdown(f"**时间:** {display_time(metadata)}")
                st.markdown(f"**Wing:** {metadata.get('wing', '—')}")
                
                st.markdown("**完整内容**")
                st.text_area(
                    "日志内容",
                    value=document,
                    height=300,
                    disabled=True,
                    key=f"tab8_diary_text_{diary_sel_idx}_{doc_id}",
                )
            
            with col_d2:
                st.markdown("**元数据**")
                st.json(metadata)
                
                st.markdown("---")
                if st.button("🗑️ 删除此条日记", key=f"tab8_delete_{diary_sel_idx}"):
                    soft_delete_record(collection, doc_id, document, metadata, palace_path, collection_name)
                    st.success("✅ 日记已删除")

st.markdown("---")
st.caption(f"🏛️ MemPalace ChromaDB Dashboard | 更新于: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
