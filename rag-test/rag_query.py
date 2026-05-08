"""RAG query script - runs one MCP query per subprocess to avoid SSE connection limits."""

import json
import os
import re
import subprocess
import sys
import time


MCP_URL = "http://8.147.57.160:15000/mcp"
TOP_K = 5
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "rag_results")

QUESTIONS = {
    "A1": "互斥锁的保护范围中，哪些操作原则上不得放入？",
    "A2": "为什么多把锁时必须建立全局锁顺序？违反后会怎样？",
    "A3": "为什么条件变量必须使用谓词等待？",
    "A4": "ROS2 订阅回调中禁止做哪些长阻塞操作？",
    "A5": "参数动态更新时为什么要用快照语义？",
    "A6": "为什么不要在业务类内直接new外部依赖？",
    "A7": "错误日志必须包含哪些信息才能有效定位问题？",
    "A8": "无上限队列缓存的风险是什么？",
    "A9": "诊断状态与业务状态为什么要分离？",
    "A10": "为什么要把纯逻辑与ROS接口层分离？",
}


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def parse_sse_json(text: str):
    m = re.search(r"data:\s*(\{.*)", text, re.DOTALL)
    if not m:
        return None
    raw = m.group(1).strip()
    depth = 0
    end = 0
    in_str = False
    escape = False
    for i, c in enumerate(raw):
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    decoder = json.JSONDecoder(strict=False)
    d, _ = decoder.raw_decode(raw[:end])
    return d.get("result", d)


def single_query(qid, question):
    """Run a single MCP init+search as a standalone operation."""
    import http.client
    import socket
    import struct
    import urllib.parse

    parsed = urllib.parse.urlparse(MCP_URL)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "close",
    }

    def force_close_conn(conn):
        """Force RST close to avoid TIME_WAIT/ESTABLISHED lingering."""
        try:
            sock = conn.sock
            if sock:
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                sock.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    # Initialize
    t_init_start = time.perf_counter()
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    init_payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": f"rag-{qid}", "version": "1.0.0"},
        },
        "id": 0,
    })
    conn.request("POST", parsed.path, body=init_payload.encode(), headers=headers)
    resp = conn.getresponse()
    session_id = resp.getheader("mcp-session-id", "")
    resp.read()
    force_close_conn(conn)
    t_init = time.perf_counter() - t_init_start

    # Search
    t_query_start = time.perf_counter()
    headers["mcp-session-id"] = session_id
    search_payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {"query": question, "top_k": TOP_K},
        },
        "id": 1,
    })
    conn2 = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    conn2.request("POST", parsed.path, body=search_payload.encode(), headers=headers)
    resp2 = conn2.getresponse()
    body = resp2.read().decode("utf-8", errors="replace")
    force_close_conn(conn2)
    t_query = time.perf_counter() - t_query_start

    result = parse_sse_json(body)
    if result is None:
        # Try parsing as plain JSON-RPC
        try:
            decoder = json.JSONDecoder(strict=False)
            d, _ = decoder.raw_decode(body.strip())
            result = d.get("result", d)
        except (json.JSONDecodeError, TypeError):
            result = None
    top5 = []
    raw_text = ""
    if result and "content" in result:
        for item in result["content"]:
            if item.get("type") == "text":
                raw_text = item["text"]
    if raw_text:
        try:
            decoder = json.JSONDecoder(strict=False)
            parsed_list, _ = decoder.raw_decode(raw_text)
            if isinstance(parsed_list, list):
                for entry in parsed_list[:5]:
                    payload = entry.get("payload", {})
                    top5.append({
                        "score": round(entry.get("score", 0), 4),
                        "sources": entry.get("sources", []),
                        "path": payload.get("path", ""),
                        "text": payload.get("content", ""),
                    })
        except (json.JSONDecodeError, TypeError):
            pass

    in_tok = estimate_tokens(question)
    out_tok = estimate_tokens(raw_text)

    return {
        "question": question,
        "top5": top5,
        "init_time_s": round(t_init, 3),
        "query_time_s": round(t_query, 3),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def run_single(qid):
    """Entry point for subprocess mode."""
    question = QUESTIONS[qid]
    result = single_query(qid, question)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"{qid}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(
        f"{qid}: query={result['query_time_s']:.3f}s, "
        f"hits={len(result['top5'])}, out_tok={result['output_tokens']}",
        flush=True,
    )


def run_all():
    total_start = time.perf_counter()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    all_results = {}
    total_query_time = 0.0
    total_input_tokens = 0
    total_output_tokens = 0

    for qid in QUESTIONS:
        # Run each query in a separate subprocess to avoid connection limit
        proc = subprocess.run(
            [sys.executable, "-u", __file__, "--single", qid],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(__file__) or ".",
        )
        print(proc.stdout.strip(), flush=True)
        if proc.stderr.strip():
            print(f"  stderr: {proc.stderr.strip()}", flush=True)

        # Load result
        result_path = os.path.join(RESULTS_DIR, f"{qid}.json")
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as f:
                r = json.load(f)
            all_results[qid] = r
            total_query_time += r["query_time_s"]
            total_input_tokens += r["input_tokens"]
            total_output_tokens += r["output_tokens"]

    total_elapsed = time.perf_counter() - total_start

    summary = {
        "total_time_s": round(total_elapsed, 3),
        "total_query_time_s": round(total_query_time, 3),
        "avg_query_time_s": round(total_query_time / len(QUESTIONS), 3),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
    }

    output = {"summary": summary, "results": all_results}
    out_path = os.path.join(os.path.dirname(__file__), "rag_query_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSummary: {json.dumps(summary, indent=2)}", flush=True)
    print(f"Results saved to {out_path}", flush=True)


if __name__ == "__main__":
    if "--single" in sys.argv:
        qid = sys.argv[sys.argv.index("--single") + 1]
        run_single(qid)
    else:
        run_all()
