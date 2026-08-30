import urllib.request, json

def call_respond(msg, allow_opt=False, timeout=300):
    payload = {'data': [{'text': msg, 'files': []}, [], '', 0, allow_opt]}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        'http://127.0.0.1:7860/gradio_api/call/respond',
        data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        event_id = json.loads(r.read())['event_id']
    print(f'event_id: {event_id}', flush=True)

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
                                        return c['text'][:800]
    return ''

def call_optimize(timeout=600):
    payload = {'data': [[], '', 0, 'auto']}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        'http://127.0.0.1:7860/gradio_api/call/optimize_once',
        data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        event_id = json.loads(r.read())['event_id']
    print(f'optimize event_id: {event_id}', flush=True)

    url = f'http://127.0.0.1:7860/gradio_api/call/optimize_once/{event_id}'
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
                                        return c['text'][:1000]
    return ''

# Step 1: 建模
print('=== Step 1: 建模 ===', flush=True)
build_result = call_respond('新建一个工程，帮我建一个 5.8GHz 矩形贴片天线，基板用 Rogers 4003C，介电常数3.55，厚度0.8mm', timeout=300)
print('Build result:', build_result[:200], flush=True)

with open('D:/claudecode/cst-agent-workbench/scripts/opt_result.txt', 'w', encoding='utf-8') as f:
    f.write('=== 建模结果 ===\n' + build_result + '\n')

# Step 2: 优化一轮
print('\n=== Step 2: 优化一轮 ===', flush=True)
try:
    opt_result = call_optimize(timeout=600)
    print('Opt result:', opt_result[:200], flush=True)
    with open('D:/claudecode/cst-agent-workbench/scripts/opt_result.txt', 'a', encoding='utf-8') as f:
        f.write('\n=== 优化结果 ===\n' + opt_result + '\n')
except Exception as e:
    print('Optimize error:', e, flush=True)
    with open('D:/claudecode/cst-agent-workbench/scripts/opt_result.txt', 'a', encoding='utf-8') as f:
        f.write(f'\n=== 优化错误 ===\n{e}\n')

print('Done!', flush=True)
