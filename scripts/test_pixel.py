import urllib.request, json, sys

payload = {'data': [{'text': '新建一个工程，帮我建一个 5GHz 像素化贴片天线，6x6 网格', 'files': []}, [], '', 0, False]}
data = json.dumps(payload).encode()
req = urllib.request.Request(
    'http://127.0.0.1:7860/gradio_api/call/respond',
    data=data, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=10) as r:
    event_id = json.loads(r.read())['event_id']
print('event_id:', event_id, flush=True)

url = f'http://127.0.0.1:7860/gradio_api/call/respond/{event_id}'
with urllib.request.urlopen(url, timeout=600) as r:
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
                    result_text = ''
                    for msg in d[0]:
                        if isinstance(msg, dict) and msg.get('role') == 'assistant':
                            for c in msg.get('content', []):
                                if isinstance(c, dict) and c.get('type') == 'text':
                                    result_text = c['text'][:1000]
                    if result_text:
                        with open('D:/claudecode/cst-agent-workbench/scripts/pixel_result.txt', 'w', encoding='utf-8') as f:
                            f.write(result_text)
                        print('saved', flush=True)
                        sys.exit(0)
