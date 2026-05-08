#!/usr/bin/env python3
"""
MemPalace MCP 服务器 — 为 Claude Code 提供记忆宫殿的读写访问
================================================================
安装: claude mcp add mempalace -- python -m mempalace.mcp_server [--palace /path/to/palace]

工具 (读取):
  mempalace_status          — 总抽屉数、翼/房间分布
  mempalace_list_wings      — 所有翼及其抽屉数
  mempalace_list_rooms      — 翼内的房间列表
  mempalace_get_taxonomy    — 完整的 翼 → 房间 → 数量 树
  mempalace_search          — 语义搜索，可按翼/房间过滤
  mempalace_check_duplicate — 归档前检查内容是否已存在

工具 (写入):
  mempalace_add_drawer      — 将原文内容归档到翼/房间
  mempalace_delete_drawer   — 按 ID 删除抽屉
"""

import argparse
import os
import sys
import json
import logging
import hashlib
from datetime import datetime
from pathlib import Path

from .config import MempalaceConfig, sanitize_name, sanitize_content
from .version import __version__
from .searcher import search_memories
from .palace_graph import traverse, find_tunnels, graph_stats
import chromadb

from .knowledge_graph import KnowledgeGraph

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger("mempalace_mcp")


def _parse_args():
    parser = argparse.ArgumentParser(description="MemPalace MCP 服务器")
    parser.add_argument(
        "--palace",
        metavar="PATH",
        help="宫殿目录路径（覆盖配置文件和环境变量）",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        logger.debug("Ignoring unknown args: %s", unknown)
    return args


_args = _parse_args()

if _args.palace:
    os.environ["MEMPALACE_PALACE_PATH"] = os.path.abspath(_args.palace)

_config = MempalaceConfig()
if _args.palace:
    _kg = KnowledgeGraph(db_path=os.path.join(_config.palace_path, "knowledge_graph.sqlite3"))
else:
    _kg = KnowledgeGraph()


_client_cache = None
_collection_cache = None


# ==================== 预写日志 (WAL) ====================
# 每个写操作在执行前都会记录到 JSONL 文件中。
# 这提供了审计跟踪，用于检测记忆投毒，
# 并支持对来自外部或不可信来源的写操作进行审查/回滚。

_WAL_DIR = Path(os.path.expanduser("~/.mempalace/wal"))
_WAL_DIR.mkdir(parents=True, exist_ok=True)
try:
    _WAL_DIR.chmod(0o700)
except (OSError, NotImplementedError):
    pass
_WAL_FILE = _WAL_DIR / "write_log.jsonl"


def _wal_log(operation: str, params: dict, result: dict = None):
    """将写操作追加到预写日志。"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "params": params,
        "result": result,
    }
    try:
        with open(_WAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        try:
            _WAL_FILE.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
    except Exception as e:
        logger.error(f"WAL write failed: {e}")


_client_cache = None
_collection_cache = None


def _get_client():
    """返回单例 ChromaDB PersistentClient。"""
    global _client_cache
    if _client_cache is None:
        _client_cache = chromadb.PersistentClient(path=_config.palace_path)
    return _client_cache


def _get_collection(create=False):
    """返回 ChromaDB collection，在调用之间缓存客户端。"""
    global _collection_cache
    try:
        client = _get_client()
        if create:
            _collection_cache = client.get_or_create_collection(_config.collection_name)
        elif _collection_cache is None:
            _collection_cache = client.get_collection(_config.collection_name)
        return _collection_cache
    except Exception:
        return None


def _no_palace():
    return {
        "error": "No palace found",
        "hint": "Run: mempalace init <dir> && mempalace mine <dir>",
    }


# ==================== 读取工具 ====================


def tool_status():
    col = _get_collection()
    if not col:
        return _no_palace()
    count = col.count()
    wings = {}
    rooms = {}
    try:
        all_meta = col.get(include=["metadatas"], limit=10000)["metadatas"]
        for m in all_meta:
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            wings[w] = wings.get(w, 0) + 1
            rooms[r] = rooms.get(r, 0) + 1
    except Exception:
        pass
    return {
        "total_drawers": count,
        "wings": wings,
        "rooms": rooms,
        "palace_path": _config.palace_path,
        "protocol": PALACE_PROTOCOL,
    }


# ── 宫殿协议 ─────────────────────────────────────────────────────────────
# 包含在 status 响应中，以便 AI 在首次唤醒时学习。

PALACE_PROTOCOL = """重要 — MemPalace 记忆协议：
1. 唤醒时：调用 mempalace_status 加载宫殿概览。
2. 回答关于任何人物、项目或过往事件的问题前：先调用 mempalace_kg_query 或 mempalace_search 查证。绝不猜测——务必核实。
3. 对某个事实不确定时（姓名、性别、年龄、关系）：说"让我查一下"，然后查询宫殿。说错比说慢更糟糕。
4. 每次会话结束后：调用 mempalace_diary_write 用自然语言中文记录发生了什么、学到了什么、什么是重要的。
5. 每次会话开始前：调用 mempalace_diary_write 用自然语言中文记录上一个会话发生了什么、学到了什么、什么是重要的。
6. 事实发生变化时：先调用 mempalace_kg_invalidate 标记旧事实失效，再调用 mempalace_kg_add 添加新事实。

此协议确保 AI 先知后言。存储不等于记忆——但存储 + 此协议 = 记忆。"""


def tool_list_wings():
    col = _get_collection()
    if not col:
        return _no_palace()
    wings = {}
    try:
        all_meta = col.get(include=["metadatas"], limit=10000)["metadatas"]
        for m in all_meta:
            w = m.get("wing", "unknown")
            wings[w] = wings.get(w, 0) + 1
    except Exception:
        pass
    return {"wings": wings}


def tool_list_rooms(wing: str = None):
    col = _get_collection()
    if not col:
        return _no_palace()
    rooms = {}
    try:
        kwargs = {"include": ["metadatas"], "limit": 10000}
        if wing:
            kwargs["where"] = {"wing": wing}
        all_meta = col.get(**kwargs)["metadatas"]
        for m in all_meta:
            r = m.get("room", "unknown")
            rooms[r] = rooms.get(r, 0) + 1
    except Exception:
        pass
    return {"wing": wing or "all", "rooms": rooms}


def tool_get_taxonomy():
    col = _get_collection()
    if not col:
        return _no_palace()
    taxonomy = {}
    try:
        all_meta = col.get(include=["metadatas"], limit=10000)["metadatas"]
        for m in all_meta:
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            if w not in taxonomy:
                taxonomy[w] = {}
            taxonomy[w][r] = taxonomy[w].get(r, 0) + 1
    except Exception:
        pass
    return {"taxonomy": taxonomy}


def tool_search(query: str, limit: int = 5, wing: str = None, room: str = None):
    result = search_memories(
        query,
        palace_path=_config.palace_path,
        wing=wing,
        room=room,
        n_results=limit,
    )
    if "error" in result:
        return result

    # 召回规则：超过 7 天的命中结果，自动加载当天全部 diary 作为补充上下文
    from datetime import timedelta

    now = datetime.now()
    cutoff = now - timedelta(days=7)
    old_dates = set()

    for hit in result.get("results", []):
        filed_at = hit.get("filed_at", "")
        if filed_at:
            try:
                hit_time = datetime.fromisoformat(filed_at)
                if hit_time < cutoff:
                    date_str = hit.get("date", "")
                    if date_str:
                        old_dates.add(date_str)
            except (ValueError, TypeError):
                pass

    if old_dates:
        col = _get_collection()
        if col:
            expanded_context = []
            for date_str in sorted(old_dates):
                try:
                    day_results = col.get(
                        where={"$and": [{"room": "diary"}, {"date": date_str}]},
                        include=["documents", "metadatas"],
                    )
                    if day_results["ids"]:
                        for doc, meta in zip(
                            day_results["documents"], day_results["metadatas"]
                        ):
                            expanded_context.append(
                                {
                                    "text": doc,
                                    "wing": meta.get("wing", "unknown"),
                                    "room": "diary",
                                    "topic": meta.get("topic", ""),
                                    "date": date_str,
                                    "source": "expanded_recall",
                                }
                            )
                except Exception:
                    pass
            if expanded_context:
                result["expanded_diary_context"] = expanded_context
                result["recall_note"] = (
                    f"命中结果中有 {len(old_dates)} 天超过 7 天前的内容，"
                    f"已自动加载这些天的全部 diary 条目（共 {len(expanded_context)} 条）作为补充上下文。"
                )

    return result


def tool_check_duplicate(content: str, threshold: float = 0.9):
    """检查内容是否已存在于宫殿中。"""
    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        results = col.query(
            query_texts=[content],
            n_results=5,
            include=["metadatas", "documents", "distances"],
        )
        duplicates = []
        if results["ids"] and results["ids"][0]:
            for i, drawer_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                similarity = round(1 - dist, 3)
                if similarity >= threshold:
                    meta = results["metadatas"][0][i]
                    doc = results["documents"][0][i]
                    duplicates.append(
                        {
                            "id": drawer_id,
                            "wing": meta.get("wing", "?"),
                            "room": meta.get("room", "?"),
                            "similarity": similarity,
                            "content": doc[:200] + "..." if len(doc) > 200 else doc,
                        }
                    )
        return {
            "is_duplicate": len(duplicates) > 0,
            "matches": duplicates,
        }
    except Exception as e:
        return {"error": str(e)}



def tool_traverse_graph(start_room: str, max_hops: int = 2):
    """从一个房间出发遍历宫殿图。发现跨翼的关联想法。"""
    col = _get_collection()
    if not col:
        return _no_palace()
    return traverse(start_room, col=col, max_hops=max_hops)


def tool_find_tunnels(wing_a: str = None, wing_b: str = None):
    """查找连接两个翼的房间——连通不同领域的走廊。"""
    col = _get_collection()
    if not col:
        return _no_palace()
    return find_tunnels(wing_a, wing_b, col=col)


def tool_graph_stats():
    """宫殿图概览：节点、隧道、边、连通性。"""
    col = _get_collection()
    if not col:
        return _no_palace()
    return graph_stats(col=col)


# ==================== 写入工具 ====================


def tool_add_drawer(
    wing: str, room: str, content: str, source_file: str = None, added_by: str = "mcp"
):
    """将原文内容归档到翼/房间。会先检查重复。"""
    try:
        wing = sanitize_name(wing, "wing")
        room = sanitize_name(room, "room")
        content = sanitize_content(content)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    drawer_id = f"drawer_{wing}_{room}_{hashlib.sha256((wing + room + content[:100]).encode()).hexdigest()[:24]}"

    _wal_log(
        "add_drawer",
        {
            "drawer_id": drawer_id,
            "wing": wing,
            "room": room,
            "added_by": added_by,
            "content_length": len(content),
            "content_preview": content[:200],
        },
    )

    # 幂等性：如果确定性 ID 已存在，直接返回成功（无操作）。
    try:
        existing = col.get(ids=[drawer_id])
        if existing and existing["ids"]:
            return {"success": True, "reason": "already_exists", "drawer_id": drawer_id}
    except Exception:
        pass

    try:
        col.upsert(
            ids=[drawer_id],
            documents=[content],
            metadatas=[
                {
                    "wing": wing,
                    "room": room,
                    "source_file": source_file or "",
                    "chunk_index": 0,
                    "added_by": added_by,
                    "filed_at": datetime.now().isoformat(),
                }
            ],
        )
        logger.info(f"Filed drawer: {drawer_id} → {wing}/{room}")
        return {"success": True, "drawer_id": drawer_id, "wing": wing, "room": room}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_delete_drawer(drawer_id: str):
    """按 ID 删除单个抽屉。"""
    col = _get_collection()
    if not col:
        return _no_palace()
    existing = col.get(ids=[drawer_id])
    if not existing["ids"]:
        return {"success": False, "error": f"Drawer not found: {drawer_id}"}

    # 记录删除操作及被删内容，用于审计跟踪
    deleted_content = existing.get("documents", [""])[0] if existing.get("documents") else ""
    deleted_meta = existing.get("metadatas", [{}])[0] if existing.get("metadatas") else {}
    _wal_log(
        "delete_drawer",
        {
            "drawer_id": drawer_id,
            "deleted_meta": deleted_meta,
            "content_preview": deleted_content[:200],
        },
    )

    try:
        col.delete(ids=[drawer_id])
        logger.info(f"Deleted drawer: {drawer_id}")
        return {"success": True, "drawer_id": drawer_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== 知识图谱 ====================


def tool_kg_query(entity: str, as_of: str = None, direction: str = "both"):
    """查询知识图谱中实体的关系。"""
    results = _kg.query_entity(entity, as_of=as_of, direction=direction)
    return {"entity": entity, "as_of": as_of, "facts": results, "count": len(results)}


def tool_kg_add(
    subject: str, predicate: str, object: str, valid_from: str = None, source_closet: str = None
):
    """向知识图谱添加关系。"""
    try:
        subject = sanitize_name(subject, "subject")
        predicate = sanitize_name(predicate, "predicate")
        object = sanitize_name(object, "object")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    _wal_log(
        "kg_add",
        {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "valid_from": valid_from,
            "source_closet": source_closet,
        },
    )
    triple_id = _kg.add_triple(
        subject, predicate, object, valid_from=valid_from, source_closet=source_closet
    )
    return {"success": True, "triple_id": triple_id, "fact": f"{subject} → {predicate} → {object}"}


def tool_kg_invalidate(subject: str, predicate: str, object: str, ended: str = None):
    """将事实标记为不再有效（设置结束日期）。"""
    _wal_log(
        "kg_invalidate",
        {"subject": subject, "predicate": predicate, "object": object, "ended": ended},
    )
    _kg.invalidate(subject, predicate, object, ended=ended)
    return {
        "success": True,
        "fact": f"{subject} → {predicate} → {object}",
        "ended": ended or "today",
    }


def tool_kg_timeline(entity: str = None):
    """获取事实的时间线，可选择指定实体。"""
    results = _kg.timeline(entity)
    return {"entity": entity or "all", "timeline": results, "count": len(results)}


def tool_kg_stats():
    """知识图谱概览：实体、三元组、关系类型。"""
    return _kg.stats()


# ==================== Agent 日记 ====================


def tool_diary_write(agent_name: str, entry: str, topic: str = "general", index: int = None, session_id: str = None):
    """
    为此 agent 写入日记条目。每个 agent 拥有自己的翼和日记房间。
    条目带有时间戳，随时间累积。

    这是 agent 的个人日志——观察、想法、工作内容、注意到的事物、认为重要的事。
    """
    try:
        agent_name = sanitize_name(agent_name, "agent_name")
        entry = sanitize_content(entry)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    wing = f"wing_{agent_name.lower().replace(' ', '_')}"
    room = "diary"
    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    now = datetime.now()
    entry_id = f"diary_{wing}_{now.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha256(entry[:50].encode()).hexdigest()[:12]}"

    _wal_log(
        "diary_write",
        {
            "agent_name": agent_name,
            "topic": topic,
            "entry_id": entry_id,
            "entry_preview": entry[:200],
        },
    )

    try:
        metadata = {
            "wing": wing,
            "room": room,
            "hall": "hall_diary",
            "topic": topic,
            "type": "diary_entry",
            "agent": agent_name,
            "filed_at": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
        }
        if index is not None:
            metadata["index"] = index
        if session_id:
            metadata["session_id"] = session_id

        col.add(
            ids=[entry_id],
            documents=[entry],
            metadatas=[metadata],
        )
        logger.info(f"Diary entry: {entry_id} → {wing}/diary/{topic}")
        result = {
            "success": True,
            "entry_id": entry_id,
            "agent": agent_name,
            "topic": topic,
            "timestamp": now.isoformat(),
        }
        if index is not None:
            result["index"] = index
        if session_id:
            result["session_id"] = session_id
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_extract_session(session_log_path: str):
    """
    从 Copilot 会话的 JSONL 日志中提取用户问题和 agent 回复。
    返回结构化的对话内容，供总结使用。
    """
    from pathlib import Path

    log_path = Path(session_log_path)
    if not log_path.exists():
        return {"success": False, "error": f"Session log not found: {session_log_path}"}
    if not log_path.suffix == ".jsonl":
        return {"success": False, "error": "Expected a .jsonl file"}
    # 安全检查：限制文件大小（防止读取过大文件）
    file_size = log_path.stat().st_size
    if file_size > 10 * 1024 * 1024:  # 10MB
        return {"success": False, "error": "Session log too large (>10MB)"}

    try:
        messages = []
        session_id = log_path.stem
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                msg_type = obj.get("type", "")
                data = obj.get("data", {})

                if msg_type == "user.message":
                    content = data.get("content", "")
                    if content:
                        messages.append({"role": "user", "content": content})
                elif msg_type == "assistant.message":
                    content = data.get("content", "")
                    if content:
                        messages.append({"role": "assistant", "content": content})

        return {
            "success": True,
            "session_id": session_id,
            "message_count": len(messages),
            "messages": messages,
        }
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_ingest_session(session_log_path: str, agent_name: str = "copilot"):
    """
    将会话的 JSONL 日志按 Q&A 对逐条入库到 ChromaDB。
    每个 Q&A 对（用户提问 + agent 回复）为一条独立记录，
    同一 session 的记录通过 session_id 关联，通过 index 区分顺序。
    用于 hook 在新 session 开启时自动入库上一个 session 的对话内容。
    """
    from pathlib import Path

    log_path = Path(session_log_path)
    if not log_path.exists():
        return {"success": False, "error": f"Session log not found: {session_log_path}"}
    if not log_path.suffix == ".jsonl":
        return {"success": False, "error": "Expected a .jsonl file"}
    file_size = log_path.stat().st_size
    if file_size > 10 * 1024 * 1024:  # 10MB
        return {"success": False, "error": "Session log too large (>10MB)"}

    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    try:
        session_id = log_path.stem
        wing = f"wing_{agent_name.lower().replace(' ', '_')}"
        room = "conversation"
        now = datetime.now()

        # 解析 JSONL，组装 Q&A 对
        qa_pairs = []
        current_question = None
        current_answers = []

        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                msg_type = obj.get("type", "")
                data = obj.get("data", {})

                if msg_type == "user.message":
                    # 遇到新问题时，先保存上一个 Q&A 对
                    if current_question is not None:
                        answer_text = "\n".join(current_answers) if current_answers else ""
                        qa_pairs.append(
                            {"question": current_question, "answer": answer_text}
                        )
                    current_question = data.get("content", "")
                    current_answers = []
                elif msg_type == "assistant.message":
                    content = data.get("content", "")
                    if content:
                        current_answers.append(content)

        # 保存最后一个 Q&A 对
        if current_question is not None:
            answer_text = "\n".join(current_answers) if current_answers else ""
            qa_pairs.append({"question": current_question, "answer": answer_text})

        if not qa_pairs:
            return {"success": True, "session_id": session_id, "ingested": 0, "message": "无有效 Q&A 对"}

        # 逐条入库
        ingested = 0
        for idx, qa in enumerate(qa_pairs, start=1):
            content = f"Q: {qa['question']}\nA: {qa['answer']}"
            entry_id = (
                f"convo_{session_id}_{idx:03d}_"
                f"{hashlib.sha256(qa['question'][:50].encode()).hexdigest()[:12]}"
            )

            metadata = {
                "wing": wing,
                "room": room,
                "type": "conversation",
                "agent": agent_name,
                "session_id": session_id,
                "index": idx,
                "filed_at": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
            }

            col.add(ids=[entry_id], documents=[content], metadatas=[metadata])
            ingested += 1

        _wal_log(
            "ingest_session",
            {
                "session_id": session_id,
                "agent_name": agent_name,
                "qa_count": ingested,
            },
        )

        return {
            "success": True,
            "session_id": session_id,
            "ingested": ingested,
            "wing": wing,
            "room": room,
        }
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_diary_read(agent_name: str, last_n: int = 10):
    """
    读取 agent 的最近日记条目。按时间顺序返回最近 N 条。
    """
    wing = f"wing_{agent_name.lower().replace(' ', '_')}"
    col = _get_collection()
    if not col:
        return _no_palace()

    try:
        results = col.get(
            where={"$and": [{"wing": wing}, {"room": "diary"}]},
            include=["documents", "metadatas"],
            limit=10000,
        )

        if not results["ids"]:
            return {"agent": agent_name, "entries": [], "message": "暂无日记条目。"}

        # 合并并按时间戳排序
        entries = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            entries.append(
                {
                    "date": meta.get("date", ""),
                    "timestamp": meta.get("filed_at", ""),
                    "topic": meta.get("topic", ""),
                    "content": doc,
                }
            )

        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        entries = entries[:last_n]

        return {
            "agent": agent_name,
            "entries": entries,
            "total": len(results["ids"]),
            "showing": len(entries),
        }
    except Exception as e:
        return {"error": str(e)}


# ==================== MCP 协议 ====================

TOOLS = {
    "mempalace_status": {
        "description": "宫殿概览 — drawer 总数、wing 和 room 数量",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_status,
    },
    "mempalace_list_wings": {
        "description": "列出所有 wing 及其 drawer 数量",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_list_wings,
    },
    "mempalace_list_rooms": {
        "description": "列出某 wing 内的 room（不指定 wing 则列出所有 room）",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "要列出 room 的 wing（可选）"},
            },
        },
        "handler": tool_list_rooms,
    },
    "mempalace_get_taxonomy": {
        "description": "完整分类：wing → room → drawer 数量",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_get_taxonomy,
    },
    "mempalace_kg_query": {
        "description": "查询知识图谱中某实体的关系。返回带时间有效性的类型化事实。例如 'Max' → child_of Alice, loves chess, does swimming。使用 as_of 按日期过滤，查看某时间点的有效事实。",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "要查询的实体（如 'Max'、'MyProject'、'Alice'）",
                },
                "as_of": {
                    "type": "string",
                    "description": "日期过滤 — 仅返回该日期有效的事实（YYYY-MM-DD，可选）",
                },
                "direction": {
                    "type": "string",
                    "description": "outgoing（实体→?）、incoming（?→实体）或 both（默认：both）",
                },
            },
            "required": ["entity"],
        },
        "handler": tool_kg_query,
    },
    "mempalace_kg_add": {
        "description": "向知识图谱添加事实。主体 → 谓词 → 客体，可附带时间窗口。例如 ('Max', 'started_school', 'Year 7', valid_from='2026-09-01')。",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "执行动作或具有属性的实体"},
                "predicate": {
                    "type": "string",
                    "description": "关系类型（如 'loves'、'works_on'、'daughter_of'）",
                },
                "object": {"type": "string", "description": "被关联的实体"},
                "valid_from": {
                    "type": "string",
                    "description": "事实生效日期（YYYY-MM-DD，可选）",
                },
                "source_closet": {
                    "type": "string",
                    "description": "该事实出处的 drawer ID（可选）",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_add,
    },
    "mempalace_kg_invalidate": {
        "description": "将事实标记为不再有效。E.g. ankle injury resolved, job ended, moved house.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "实体"},
                "predicate": {"type": "string", "description": "关系"},
                "object": {"type": "string", "description": "被关联的实体"},
                "ended": {
                    "type": "string",
                    "description": "失效日期（YYYY-MM-DD，默认：今天）",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_invalidate,
    },
    "mempalace_kg_timeline": {
        "description": "按时间线展示事实。按时间顺序展示某实体（或全部）的故事。",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "要查看时间线的实体（可选 — 不填则展示全部）",
                },
            },
        },
        "handler": tool_kg_timeline,
    },
    "mempalace_kg_stats": {
        "description": "知识图谱概览：实体数、triple 数、当前/已失效事实、关系类型。",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_kg_stats,
    },
    "mempalace_traverse": {
        "description": "从某 room 出发遍历宫殿图。展示跨 wing 的关联想法 — 即隧道。如同在宫殿中循线而行：从 wing_code 的 'chromadb-setup' 出发，发现它连接到 wing_myproject（规划）和 wing_user（感受）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_room": {
                    "type": "string",
                    "description": "起始 room（如 'chromadb-setup'、'riley-school'）",
                },
                "max_hops": {
                    "type": "integer",
                    "description": "跟随连接的跳数（默认：2）",
                },
            },
            "required": ["start_room"],
        },
        "handler": tool_traverse_graph,
    },
    "mempalace_find_tunnels": {
        "description": "查找连接两个 wing 的 room — 连通不同领域的走廊。例如哪些主题连接了 wing_code 和 wing_team？",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "第一个 wing（可选）"},
                "wing_b": {"type": "string", "description": "第二个 wing（可选）"},
            },
        },
        "handler": tool_find_tunnels,
    },
    "mempalace_graph_stats": {
        "description": "宫殿图概览：room 总数、隧道连接、wing 间边数。",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_graph_stats,
    },
    "mempalace_search": {
        "description": "语义搜索。返回 drawer 原文内容及相似度分数。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索内容"},
                "limit": {"type": "integer", "description": "最大返回数量（默认 5）"},
                "wing": {"type": "string", "description": "按 wing 过滤（可选）"},
                "room": {"type": "string", "description": "按 room 过滤（可选）"},
            },
            "required": ["query"],
        },
        "handler": tool_search,
    },
    "mempalace_check_duplicate": {
        "description": "归档前检查内容是否已存在于宫殿中",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要检查的内容"},
                "threshold": {
                    "type": "number",
                    "description": "相似度阈值 0-1（默认 0.9）",
                },
            },
            "required": ["content"],
        },
        "handler": tool_check_duplicate,
    },
    "mempalace_add_drawer": {
        "description": "将原文内容归档到宫殿中。会先检查是否重复（添加 drawer）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "wing（项目名称）"},
                "room": {
                    "type": "string",
                    "description": "room（方面：backend、decisions、meetings...）",
                },
                "content": {
                    "type": "string",
                    "description": "要存储的原文内容 — 原话原文，不做总结",
                },
                "source_file": {"type": "string", "description": "内容来源（可选）"},
                "added_by": {"type": "string", "description": "归档人（默认：mcp）"},
            },
            "required": ["wing", "room", "content"],
        },
        "handler": tool_add_drawer,
    },
    "mempalace_delete_drawer": {
        "description": "按 ID 删除 drawer。不可撤销。",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "要删除的 drawer ID"},
            },
            "required": ["drawer_id"],
        },
        "handler": tool_delete_drawer,
    },
    "mempalace_diary_write": {
        "description": "写入个人 agent 日记。每条 diary 只覆盖一个主题，同一会话按 index 编号拆分为多条。topic 为该条的关键词（不超过两个词）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "agent 名称 — 每个 agent 有独立的日记 wing",
                },
                "entry": {
                    "type": "string",
                    "description": "日记内容，自然语言中文，只覆盖一个主题",
                },
                "topic": {
                    "type": "string",
                    "description": "该条日记的关键词标签，不超过两个词（如 'MCP工具' 或 '日记机制'）",
                },
                "index": {
                    "type": "integer",
                    "description": "同一会话中该条 diary 的序号（从 1 开始）",
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID，用于标识同一次会话的多条 diary",
                },
            },
            "required": ["agent_name", "entry"],
        },
        "handler": tool_diary_write,
    },
    "mempalace_diary_read": {
        "description": "读取最近的日记条目。查看过去的自己记录了什么 — 跨会话的个人日志。",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "你的名称 — 每个 agent 有独立的日记 wing",
                },
                "last_n": {
                    "type": "integer",
                    "description": "读取最近条目的数量（默认：10）",
                },
            },
            "required": ["agent_name"],
        },
        "handler": tool_diary_read,
    },
    "mempalace_extract_session": {
        "description": "从当前会话的 JSONL 日志中提取用户问题和 agent 回复。用于会话结束时获取全量对话内容以便总结。",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_log_path": {
                    "type": "string",
                    "description": "会话 JSONL 日志文件的完整路径",
                },
            },
            "required": ["session_log_path"],
        },
        "handler": tool_extract_session,
    },
    "mempalace_ingest_session": {
        "description": "将会话 JSONL 日志按 Q&A 对逐条入库。每个 Q&A 为一条独立记录，同一 session 通过 session_id 关联、index 区分顺序。用于 hook 在新 session 开启时自动入库上一个 session 的对话。",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_log_path": {
                    "type": "string",
                    "description": "上一个会话的 JSONL 日志文件完整路径",
                },
                "agent_name": {
                    "type": "string",
                    "description": "agent 名称（默认：copilot）",
                },
            },
            "required": ["session_log_path"],
        },
        "handler": tool_ingest_session,
    },
}


SUPPORTED_PROTOCOL_VERSIONS = [
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]


def handle_request(request):
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        client_version = params.get("protocolVersion", SUPPORTED_PROTOCOL_VERSIONS[-1])
        negotiated = (
            client_version
            if client_version in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mempalace", "version": __version__},
            },
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {"name": n, "description": t["description"], "inputSchema": t["input_schema"]}
                    for n, t in TOOLS.items()
                ]
            },
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        # 根据 input_schema 强制转换参数类型。
        # MCP JSON 传输可能将整数作为浮点数或字符串传递；
        # ChromaDB 和 Python 切片需要原生 int。
        schema_props = TOOLS[tool_name]["input_schema"].get("properties", {})
        for key, value in list(tool_args.items()):
            prop_schema = schema_props.get(key, {})
            declared_type = prop_schema.get("type")
            if declared_type == "integer" and not isinstance(value, int):
                tool_args[key] = int(value)
            elif declared_type == "number" and not isinstance(value, (int, float)):
                tool_args[key] = float(value)
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            }
        except Exception:
            logger.exception(f"Tool error in {tool_name}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": "Internal tool error"},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    # Ensure UTF-8 for stdio on Windows (prevents encoding errors with CJK text)
    if sys.platform == "win32":
        for stream in ("stdin", "stdout", "stderr"):
            s = getattr(sys, stream)
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8")
    logger.info("MemPalace MCP Server starting...")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Server error: {e}")


if __name__ == "__main__":
    main()
