"""RAG query script for Part E (E1-E5) - measures timing and token consumption accurately."""

import json
import os
import re
import sys
import time
import http.client
import socket
import struct
import urllib.parse


MCP_URL = "http://8.147.57.160:15000/mcp"
TOP_K = 5
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "rag_results_e")

QUESTIONS = {
    "E1": "设计线程安全数据缓存类，后台线程更新缓存数据，前台线程读取最新快照，多个数据字段之间保证一致性，RAII管理锁，临界区最小化，快照语义，显式返回",
    "E2": "设计ROS2高频图像处理节点，订阅30Hz图像流，回调轻量，处理逻辑与ROS接口分离，依赖注入，配置参数动态更新快照语义",
    "E3": "设计错误处理与诊断函数，记录可定位上下文frame_id request_id，更新诊断状态INIT WAITING RUNNING DEGRADED ERROR，降级策略，诊断可解释性",
    "E4": "设计Producer-Consumer队列与背压机制，队列容量上限，丢旧丢新最新值覆盖策略，统计丢包数，诊断告警，速度失配处理",
    "E5": "设计可测试算法类，构造函数依赖注入，纯逻辑与ROS接口分离，显式返回结果不修改成员变量，单元测试框架GTest样例",
}


def estimate_tokens(text: str) -> int:
    """Estimate token count: CJK ~1.5 chars/token, other ~4 chars/token."""
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


def single_query(qid, question):
    """Run a single MCP init+search and measure timing precisely."""
    parsed = urllib.parse.urlparse(MCP_URL)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Connection": "close",
    }

    # --- Initialize ---
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

    # --- Search ---
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

    content_type = resp2.getheader("content-type", "")
    if "text/event-stream" in content_type:
        body_lines = []
        while True:
            line = resp2.readline().decode("utf-8", errors="replace")
            body_lines.append(line)
            if line.strip() == "" and any(l.startswith("data:") for l in body_lines):
                break
            if not line:
                break
        body = "".join(body_lines)
    else:
        body = resp2.read().decode("utf-8", errors="replace")

    force_close_conn(conn2)
    t_query = time.perf_counter() - t_query_start

    # --- Parse results ---
    t_parse_start = time.perf_counter()
    result = parse_sse_json(body)
    if result is None:
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
                        "text": payload.get("content", "")[:300],
                    })
        except (json.JSONDecodeError, TypeError):
            pass
    t_parse = time.perf_counter() - t_parse_start

    in_tok = estimate_tokens(question)
    out_tok = estimate_tokens(raw_text)

    return {
        "qid": qid,
        "question": question,
        "top5": top5,
        "raw_text": raw_text,
        "raw_text_len": len(raw_text),
        "init_time_s": round(t_init, 4),
        "query_time_s": round(t_query, 4),
        "parse_time_s": round(t_parse, 4),
        "total_time_s": round(t_init + t_query + t_parse, 4),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def run_all():
    total_start = time.perf_counter()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    all_results = {}
    total_init_time = 0.0
    total_query_time = 0.0
    total_parse_time = 0.0
    total_input_tokens = 0
    total_output_tokens = 0

    for qid, question in QUESTIONS.items():
        try:
            result = single_query(qid, question)
            out_path = os.path.join(RESULTS_DIR, f"{qid}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(
                f"{qid}: init={result['init_time_s']:.3f}s, query={result['query_time_s']:.3f}s, "
                f"total={result['total_time_s']:.3f}s, hits={len(result['top5'])}, "
                f"in_tok={result['input_tokens']}, out_tok={result['output_tokens']}",
                flush=True,
            )
            all_results[qid] = result
            total_init_time += result["init_time_s"]
            total_query_time += result["query_time_s"]
            total_parse_time += result["parse_time_s"]
            total_input_tokens += result["input_tokens"]
            total_output_tokens += result["output_tokens"]
        except Exception as e:
            print(f"{qid}: ERROR - {e}", flush=True)

        time.sleep(0.5)
        import gc; gc.collect()

    total_elapsed = time.perf_counter() - total_start
    n = len(QUESTIONS)

    summary = {
        "total_elapsed_s": round(total_elapsed, 3),
        "total_init_time_s": round(total_init_time, 3),
        "total_query_time_s": round(total_query_time, 3),
        "total_parse_time_s": round(total_parse_time, 4),
        "avg_init_time_s": round(total_init_time / n, 3),
        "avg_query_time_s": round(total_query_time / n, 3),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
    }

    output = {"summary": summary, "results": all_results}
    out_path = os.path.join(os.path.dirname(__file__), "rag_query_results_e.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}", flush=True)
    print(f"Summary:", flush=True)
    for k, v in summary.items():
        print(f"  {k}: {v}", flush=True)
    print(f"Results saved to {out_path}", flush=True)


if __name__ == "__main__":
    if "--single" in sys.argv:
        qid = sys.argv[sys.argv.index("--single") + 1]
        question = QUESTIONS[qid]
        result = single_query(qid, question)
        os.makedirs(RESULTS_DIR, exist_ok=True)
        out_path = os.path.join(RESULTS_DIR, f"{qid}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(
            f"{qid}: init={result['init_time_s']:.3f}s, query={result['query_time_s']:.3f}s, "
            f"total={result['total_time_s']:.3f}s, hits={len(result['top5'])}, "
            f"in_tok={result['input_tokens']}, out_tok={result['output_tokens']}",
            flush=True,
        )
    else:
        run_all()
