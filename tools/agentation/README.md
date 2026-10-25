# Agentation on our pages (laptop only)

[Agentation](https://www.agentation.com) lets you click any element on a page, write what should change, and hand
that to the coding agent with the exact selector. It ships as a React component; our pages are plain HTML served by
Python, so this folder bundles it into one script and `devproxy.py` adds it to any page without changing its code.

```
cd tools/agentation && npm install && npm run build                 # once, needs internet
python tools/agentation/devproxy.py --target 8080 --port 9080      # approval dashboard -> http://127.0.0.1:9080
python tools/agentation/devproxy.py --target 8090 --port 9090      # dispute desk       -> http://127.0.0.1:9090
```

To have notes reach Claude Code directly instead of copying them, register the MCP server once:

```
claude mcp add --scope user agentation -- npx -y agentation-mcp server
```

(on Windows: `... -- cmd /c npx -y agentation-mcp server`), restart Claude Code, then click "Send" in the toolbar.

Never run the proxy on the GB10 for the demo; the box pages stay as they are.
