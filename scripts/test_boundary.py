"""全面边界测试 v2 — 每次测试前清空 session，避免 400 错误传播。"""
import urllib.request, json, time

RESULTS = []

def clear_session():
    """清空 agent 历史记录。"""
    try:
        payload = {'data': [[], '', 0]}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            'http://127.0.0.1:7860/gradio_api/call/clear_all',
            data=data, headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=5) as r:
            event_id = json.loads(r.read())['event_id']
        url = f'http://127.0.0.1:7860/gradio_api/call/clear_all/{event_id}'
        with urllib.request.urlopen(url, timeout=10) as r:
            r.read()
    except Exception:
        pass

def call_respond(msg, allow_opt=False, timeout=90):
    clear_session()
    time.sleep(0.5)
    payload = {'data': [{'text': msg, 'files': []}, [], '', 0, allow_opt]}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        'http://127.0.0.1:7860/gradio_api/call/respond',
        data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as r:
        event_id = json.loads(r.read())['event_id']

    url = f'http://127.0.0.1:7860/gradio_api/call/respond/{event_id}'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        buf = b''
        while True:
            chunk = r.read(2048)
            if not chunk:
                break
            buf += chunk
            lines = buf.split(b'\n')
            buf = lines[-1]
            for line in lines[:-1]:
                line = line.decode('utf-8', errors='replace').strip()
                if line.startswith('data:') and line != 'data: null':
                    d = json.loads(line[5:])
                    if isinstance(d, list) and d:
                        for m in d[0]:
                            if isinstance(m, dict) and m.get('role') == 'assistant':
                                for c in m.get('content', []):
                                    if isinstance(c, dict) and c.get('type') == 'text':
                                        return c['text']
    return ''

def call_direct(endpoint, payload_data, timeout=60):
    """调用非 respond 的直接 API 端点。"""
    payload = {'data': payload_data}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f'http://127.0.0.1:7860/gradio_api/call/{endpoint}',
        data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as r:
        event_id = json.loads(r.read())['event_id']
    url = f'http://127.0.0.1:7860/gradio_api/call/{endpoint}/{event_id}'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        buf = b''
        while True:
            chunk = r.read(2048)
            if not chunk:
                break
            buf += chunk
            lines = buf.split(b'\n')
            buf = lines[-1]
            for line in lines[:-1]:
                line = line.decode('utf-8', errors='replace').strip()
                if line.startswith('data:') and line != 'data: null':
                    return line[5:]
    return '{}'

def test(name, fn, expect_keywords=None):
    print(f'\n=== {name} ===', flush=True)
    try:
        result = fn()
        result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        ok = True
        if expect_keywords:
            keywords = expect_keywords if isinstance(expect_keywords, list) else [expect_keywords]
            ok = any(kw.lower() in result_str.lower() for kw in keywords)
        status = 'PASS' if ok else 'WARN'
        preview = result_str[:200].replace('\n', ' ')
        print(f'[{status}] {preview}', flush=True)
        RESULTS.append({'name': name, 'status': status, 'preview': preview})
        return result_str
    except Exception as e:
        err = str(e)[:150]
        print(f'[FAIL] {err}', flush=True)
        RESULTS.append({'name': name, 'status': 'FAIL', 'preview': err})
        return ''

# ── 1. 基础对话 ──
test('普通问候', lambda: call_respond('你好'), ['CST', 'agent', '助手', '建模'])

# ── 2. CST 状态 ──
test('CST连接状态', lambda: call_respond('CST 当前连接状态如何'), ['连接', '在线', 'online', 'offline', '离线'])

# ── 3. RAG 知识检索 ──
test('RAG-Pozar公式', lambda: call_respond('矩形贴片天线的谐振频率计算公式'), ['频率', 'GHz', 'patch', '公式', 'εr', 'Pozar'])
test('RAG-S参数', lambda: call_respond('S11 参数代表什么'), ['S11', '反射', '匹配', '回波'])
test('RAG-优化算法', lambda: call_respond('贝叶斯优化和粒子群优化有什么区别'), ['贝叶斯', 'PSO', '粒子群', '优化'])

# ── 4. 参数操作 ──
test('store_parameter', lambda: call_respond('定义参数 bnd_w = 40'), ['参数', 'bnd_w', 'store', '40'])
test('store_parameter表达式', lambda: call_respond('定义参数 patch_area = patch_L * patch_W'), ['参数', 'patch_area', '表达式'])

# ── 5. 直接 API 端点 ──
test('refresh_results_direct', lambda: call_direct('refresh_results_direct', [[], '', 0]), ['data', '[]', 'null', 'result'])
test('read_s11_direct', lambda: call_direct('read_s11_direct', [[], '', 0]), ['data', 'null', 's11', 'error', 'success'])

# ── 6. 边界：不明确/不支持请求 ──
test('不明确请求', lambda: call_respond('帮我做某个东西'), ['什么', '请', '天线', '描述', '告诉'])
test('无效工具名', lambda: call_respond('调用 delete_everything 工具'), ['无法', '不支持', '找不到', '未知', 'tool'])
test('非天线领域', lambda: call_respond('帮我写一个 Python 排序算法'), ['天线', 'CST', '仿真', 'Python', '排序'])

# ── 7. 连续对话（不清 session）──
clear_session()
call_respond('我们来建一个天线。先告诉我你支持哪些天线类型？')
test('连续对话-第二轮', lambda: call_respond('矩形贴片天线需要哪些参数？'), ['频率', '基板', '介电', '参数', 'patch', 'GHz'])

# ── 8. VBA 执行 ──
test('VBA简单脚本', lambda: call_respond('执行 VBA 脚本打印消息: Sub Test()\nDim x As Double\nx = 3.14\nEnd Sub'), ['VBA', 'vba', '执行', 'script'])

# 写入报告
with open('D:/claudecode/cst-agent-workbench/scripts/boundary_report.txt', 'w', encoding='utf-8') as f:
    pass_count = sum(1 for r in RESULTS if r['status'] == 'PASS')
    warn_count = sum(1 for r in RESULTS if r['status'] == 'WARN')
    fail_count = sum(1 for r in RESULTS if r['status'] == 'FAIL')
    f.write(f'# 边界测试报告\n总计: {len(RESULTS)} | PASS:{pass_count} | WARN:{warn_count} | FAIL:{fail_count}\n\n')
    for r in RESULTS:
        f.write(f"[{r['status']}] {r['name']}\n  {r['preview']}\n\n")

print(f'\n=== 完成 {len(RESULTS)} 项 | PASS:{pass_count} WARN:{warn_count} FAIL:{fail_count} ===', flush=True)
