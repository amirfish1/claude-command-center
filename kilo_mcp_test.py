import json

# Read the CCC server.py to find how it handles MCP or check Kilo config schema
with open("/tmp/ccc/server.py") as f:
    content = f.read()

# Search for mcp in the config schema
import re
# Find references to mcp in Kilo docs
for m in re.finditer(r'mcpServers|mcp_servers|mcp\.servers', content):
    start = max(0, m.start() - 100)
    end = min(len(content), m.end() + 100)
    print(f"Found at {m.start()}: ...{content[start:end]}...")
    print("---")
