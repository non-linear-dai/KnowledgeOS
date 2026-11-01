"""KnowledgeOS MCP adapter.

A separate process that lets MCP hosts (Claude Code, Claude Desktop, Cursor,
Kimi Code, Codex …) use KnowledgeOS as shared, governed memory. It never calls
a model and never imports the kernel: every tool call goes to the public
KnowledgeOS HTTP API with the calling participant's own credential, so the
kernel's permissions, ChangeSet governance and audit apply unchanged.
"""

__version__ = "0.1.0"
