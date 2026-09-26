"""run_python: execute code in the sandbox; keep what it produced in the workspace."""
from __future__ import annotations

import base64

from workbench.tools.base import Tool, ToolContext, ToolError, ToolResult, confine


class RunPythonTool(Tool):
    name = "run_python"
    description = ("Run Python code in an isolated sandbox: no network, no site-packages, bounded CPU, memory, "
                   "time and disk; a fresh working directory that is destroyed afterwards. Optional tests "
                   "(def test_* functions) decide whether the run is 'verified'. Files the code writes are "
                   "copied into the workspace under sandbox/<run_id>/.")
    parameters = {"type": "object",
                  "properties": {"code": {"type": "string"},
                                 "tests": {"type": "string", "description": "test functions run against the code"},
                                 "inputs": {"type": "object", "description": "filename -> text made available in the sandbox"},
                                 "task_id": {"type": "string"}},
                  "required": ["code"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.sandbox is None:
            raise ToolError("no sandbox is configured for this session")
        inputs = args.get("inputs") or {}
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in inputs.items()):
            raise ToolError("inputs must map file names to text")
        res = ctx.sandbox.run(args["code"], inputs=inputs, tests=args.get("tests"), task_id=args.get("task_id", ""))
        files: list[str] = []
        for rel, b64 in res.artifacts.items():
            try:
                target = confine(ctx.workspace, f"sandbox/{res.run_id}/{rel}")
            except ToolError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(b64))
            files.append(target.relative_to(ctx.workspace.resolve()).as_posix())
        lines = [f"exit code {res.exit_code}" + (f", limit hit: {res.limit_hit}" if res.limit_hit else "")]
        if res.stdout.strip():
            lines.append("stdout:\n" + res.stdout.rstrip())
        if res.stderr.strip():
            lines.append("stderr:\n" + res.stderr.rstrip()[-2000:])
        if res.verification:
            v = res.verification
            lines.append(f"tests: {v.get('tests_passed', 0)}/{v.get('tests_total', 0)} passed; verified={res.verified}")
        if res.error:
            lines.append("error: " + res.error)
        data = res.model_dump(mode="json", exclude={"artifacts"})
        data["workspace_files"] = files
        return ToolResult(tool=self.name, ok=res.ok, output="\n".join(lines), data=data, files=files,
                          error=None if res.ok else (res.error or res.limit_hit or f"exit code {res.exit_code}"))
