#!/bin/bash
# Start MCP server in background, then start main API
uvicorn mcp_server:app --host 0.0.0.0 --port 8001 &
uvicorn main:app --host 0.0.0.0 --port 8000
