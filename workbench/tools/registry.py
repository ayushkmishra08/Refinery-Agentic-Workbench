"""Tool registry: name -> Tool. ``default_registry()`` holds the built-ins; others register at start-up."""
from __future__ import annotations

from workbench.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> Tool:
        if tool.name in self._tools and not replace:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict]:
        return [t.describe() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def default_registry() -> ToolRegistry:
    from workbench.tools.calculator_tool import CalculateTool
    from workbench.tools.code_tool import RunPythonTool
    from workbench.tools.document_search_tool import SearchDocumentsTool
    from workbench.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool
    from workbench.tools.spreadsheet_tool import SpreadsheetReadTool, SpreadsheetWriteTool

    reg = ToolRegistry()
    for tool in (ReadFileTool(), WriteFileTool(), ListFilesTool(), RunPythonTool(), SpreadsheetReadTool(),
                 SpreadsheetWriteTool(), SearchDocumentsTool(), CalculateTool()):
        reg.register(tool)
    return reg
