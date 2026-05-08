import json, os

for qid in ['D1','D2','D3','D4','D5','D6','D7','D8','D9','D10']:
    path = os.path.join('rag_results_d', f'{qid}.json')
    d = json.load(open(path, 'r', encoding='utf-8'))
    print(f'=== {qid} ===')
    print(f"init={d['init_time_s']}s query={d['query_time_s']}s total={d['total_time_s']}s in_tok={d['input_tokens']} out_tok={d['output_tokens']}")
    for i, t in enumerate(d['top5']):
        score = t['score']
        p = t['path'][:80]
        txt = t['text'][:120].replace('\n', ' ')
        print(f'  {i+1}. [score={score}] {p} -- {txt}')
    print()
