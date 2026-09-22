# Local MCP Host Configuration

Torii is a local stdio MCP server. Install `uv`, then configure the host with an
absolute path to the plugin's PEP 723 runner. The adjacent script lock supplies
the Python dependencies. The host does not need a separate Python interpreter or
`PYTHONPATH` setting.

Example host configuration:

```json
{
  "mcpServers": {
    "torii-sumo": {
      "command": "uv",
      "args": [
        "run",
        "--isolated",
        "--frozen",
        "--script",
        "C:\\path\\to\\torii-sumo\\scripts\\run_torii_sumo.py"
      ],
      "env": {
        "SUMO_HOME": "C:\\Program Files\\Eclipse\\sumo"
      }
    }
  }
}
```

If the package is installed in the host's Python environment, the installed
`torii-sumo` console script is also available.

The server communicates with the MCP host through stdio. SUMO subprocess stdout and stderr are captured by the tool code and returned to the host as structured fields.
