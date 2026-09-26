"""read_file / write_file / list_files, confined to the session workspace."""
from __future__ import annotations

import hashlib
import time

from workbench.tools.base import Tool, ToolContext, ToolError, ToolResult, confine

MAX_READ_BYTES = 2 * 1024 * 1024


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file from the session workspace (max 2 MB). Paths are relative to the workspace."
    parameters = {"type": "object", "properties": {"path": {"type": "string", "description": "workspace-relative path"},
                                                   "max_chars": {"type": "integer", "description": "truncate the returned text"}},
                  "required": ["path"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        target = confine(ctx.workspace, args["path"])
        if not target.exists() or not target.is_file():
            raise ToolError(f"no such file in the workspace: {args['path']!r}")
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"{args['path']!r} is {size} bytes; the limit is {MAX_READ_BYTES}")
        raw = target.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        limit = args.get("max_chars")
        shown = text if not limit else text[: int(limit)]
        return ToolResult(tool=self.name, output=shown,
                          data={"path": args["path"], "bytes": size, "sha256": hashlib.sha256(raw).hexdigest(),
                                "truncated": bool(limit and len(text) > int(limit))})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write (or overwrite) a text file in the session workspace. Returns the file's SHA-256."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                                   "append": {"type": "boolean"}},
                  "required": ["path", "content"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        target = confine(ctx.workspace, args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args.get("append") else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(args["content"])
        raw = target.read_bytes()
        rel = target.relative_to(ctx.workspace.resolve()).as_posix()
        return ToolResult(tool=self.name, output=f"wrote {len(raw)} bytes to {rel}", files=[rel],
                          data={"path": rel, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "appended": mode == "a"})


class ListFilesTool(Tool):
    name = "list_files"
    description = "List the files in the session workspace (name, size, modified time)."
    parameters = {"type": "object", "properties": {"subdir": {"type": "string"}}, "required": []}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        base = confine(ctx.workspace, args["subdir"]) if args.get("subdir") else ctx.workspace.resolve()
        if not base.exists():
            raise ToolError(f"no such directory in the workspace: {args.get('subdir')!r}")
        rows = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                st = p.stat()
                rows.append({"path": p.relative_to(ctx.workspace.resolve()).as_posix(), "bytes": st.st_size,
                             "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))})
        text = "\n".join(f"{r['path']}  ({r['bytes']} bytes, {r['modified']})" for r in rows) or "(workspace is empty)"
        return ToolResult(tool=self.name, output=text, data={"files": rows, "count": len(rows)})
