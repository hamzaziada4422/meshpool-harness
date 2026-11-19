"""Proof that the pruned Hermes (bundled_tools.hermes_ignore) still runs: start its ACP adapter, create a session and send
a prompt that a local mock OpenAI-compatible server answers (no model calls, no network).

    <python with Hermes' requirements> packaging/verify_hermes_prune.py <the app's vendor/hermes folder>

Exits 0 and prints OK when the adapter answers initialize, session/new and session/prompt with the mock's reply."""
import json, os, subprocess, sys, tempfile, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
hermes = sys.argv[1]
calls = {"n": 0}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        b=json.dumps({"data":[{"id":"x","object":"model"}]}).encode(); self.send_response(200); self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n=int(self.headers.get("content-length",0)); body=json.loads(self.rfile.read(n) or b"{}"); calls["n"]+=1
        msg={"role":"assistant","content":"MOCK-REPLY-OK"}
        if body.get("stream"):
            self.send_response(200); self.send_header("content-type","text/event-stream"); self.end_headers()
            for ch in ({"choices":[{"index":0,"delta":{"role":"assistant","content":"MOCK-REPLY-OK"}}]},{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}):
                ch.update(id="c1",object="chat.completion.chunk",model="x"); self.wfile.write(b"data: "+json.dumps(ch).encode()+b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            b=json.dumps({"id":"c1","object":"chat.completion","model":"x","choices":[{"index":0,"message":msg,"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}).encode()
            self.send_response(200); self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b)
srv=HTTPServer(("127.0.0.1",0),H); port=srv.server_port; threading.Thread(target=srv.serve_forever,daemon=True).start()
home=tempfile.mkdtemp(prefix="hhome-")
env=dict(os.environ,PYTHONPATH=hermes,HERMES_HOME=home,HERMES_BUNDLED_SKILLS=hermes+"/skills",PYTHONUTF8="1",HERMES_DISABLE_LAZY_INSTALLS="1",HERMES_ASK_EVERY_COMMAND="1",PYTHONDONTWRITEBYTECODE="1",NO_PROXY="127.0.0.1",no_proxy="127.0.0.1")
for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY"): env.pop(k,None)
import yaml
open(home+"/config.yaml","w").write(yaml.safe_dump({"model":{"provider":"custom","base_url":f"http://127.0.0.1:{port}/v1","default":"x","api_key":"k"},"tools":{"tool_search":{"enabled":"off"}},"security":{"allow_lazy_installs":False}}))
p=subprocess.Popen([sys.executable,"-m","acp_adapter"],cwd=tempfile.mkdtemp(),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
out=[]
def send(o): p.stdin.write(json.dumps(o)+"\n"); p.stdin.flush()
def recv(i,t=120):
    end=time.time()+t
    while time.time()<end:
        l=p.stdout.readline()
        if not l: return None
        try:m=json.loads(l)
        except Exception: continue
        out.append(m)
        if m.get("id")==i and "method" not in m: return m
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}); r1=recv(1)
send({"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":os.getcwd(),"mcpServers":[]}}); r2=recv(2,90)
sid=(r2 or {}).get("result",{}).get("sessionId")
send({"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":sid,"prompt":[{"type":"text","text":"say hi"}]}}); r3=recv(3,120)
text="".join(m["params"]["update"].get("content",{}).get("text","") for m in out if m.get("method")=="session/update" and m["params"]["update"].get("sessionUpdate")=="agent_message_chunk")
ok=bool(r3 and "result" in r3 and "MOCK-REPLY-OK" in text)
print("prompt result:",json.dumps(r3)[:160],"| text:",text[:60],"| model calls:",calls["n"])
p.stdin.close()
try:p.wait(10)
except Exception:p.kill()
if not ok: print("STDERR:",p.stderr.read()[-1500:])
print("OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
