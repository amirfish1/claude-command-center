import subprocess, json, sys, time, select

proc = subprocess.Popen(
    ["python3", "/tmp/ccc/ccc_acp.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

init_msg = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
        "clientInfo": {"name": "hermes-test", "version": "1.0"}
    }
})

session_msg = json.dumps({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "session/new",
    "params": {}
})

# Send both messages
proc.stdin.write(init_msg + "\n")
proc.stdin.flush()
time.sleep(1)

proc.stdin.write(session_msg + "\n")
proc.stdin.flush()
time.sleep(2)

# Close stdin and wait for process
proc.stdin.close()
proc.wait(timeout=5)

stdout = proc.stdout.read()
stderr = proc.stderr.read()

print("=== STDOUT ===")
print(repr(stdout))
print("\n=== STDERR ===")
print(stderr[:3000])
print("\n=== PARSED LINES ===")
for line in stdout.strip().split("\n"):
    if line.strip():
        try:
            obj = json.loads(line)
            print(json.dumps(obj, indent=2))
        except:
            print("RAW:", line[:200])
