#!/usr/bin/env python3
"""
查看 ChromaDB 内容的工具
"""

import os
import chromadb
from pathlib import Path

def view_chromadb(palace_path: str = None, collection_name: str = "mempalace_drawers", limit: int = 10):
    """查看 ChromaDB 中的数据"""
    
    # 使用默认路径或用户指定路径
    if palace_path is None:
        palace_path = os.path.expanduser("~/.mempalace/palace")
    
    # 检查路径是否存在
    if not os.path.exists(palace_path):
        print(f"❌ 数据库路径不存在: {palace_path}")
        print(f"📁 完整路径: {os.path.abspath(palace_path)}")
        return
    
    print(f"📁 ChromaDB 路径: {palace_path}")
    print(f"📊 Collection: {collection_name}\n")
    
    # 连接数据库
    try:
        client = chromadb.PersistentClient(path=palace_path)
        collection = client.get_collection(collection_name)
    except Exception as e:
        print(f"❌ 无法打开数据库: {e}")
        return
    
    # 获取集合信息
    count = collection.count()
    print(f"📈 总条目数: {count}\n")
    
    if count == 0:
        print("(数据库为空)")
        return
    
    # 获取数据
    print(f"📄 前 {min(limit, count)} 条记录:\n")
    print("-" * 80)
    
    results = collection.get(limit=limit, include=["documents", "metadatas", "embeddings"])
    
    for i, (doc_id, document, metadata) in enumerate(
        zip(results["ids"], results["documents"], results["metadatas"]), 1
    ):
        print(f"\n#{i}")
        print(f"  ID: {doc_id}")
        print(f"  内容 ({len(document)} 字):")
        # 显示内容预览（最多 200 字）
        preview = document[:200] + "..." if len(document) > 200 else document
        print(f"    {preview}")
        print(f"  元数据: {metadata}")
    
    print("\n" + "-" * 80)
    print(f"\n✅ 成功读取 {min(limit, count)} 条记录 (共 {count} 条)")


if __name__ == "__main__":
    import sys
    
    palace_path = sys.argv[1] if len(sys.argv) > 1 else None
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    view_chromadb(palace_path, limit=limit)
