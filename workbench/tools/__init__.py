"""Named local tools the agent can call, and the loop that calls them.

    read_file / write_file / list_files   a per-session workspace, path-confined
    run_python                             the sandbox (workbench.sandbox)
    spreadsheet_read / spreadsheet_write   openpyxl over workspace files, formulas kept
    search_documents                       the guarded knowledge service (access enforced below the tool)
    calculate                              deterministic arithmetic that shows every step

Every call is validated against the tool's parameter schema, timed, and appended to a
hash-chained log. The LLM never does arithmetic and never touches a file directly: it chooses a
tool and arguments, the tool does the work, and the result comes back as data with evidence.
"""
from workbench.tools.base import Tool, ToolContext, ToolResult
from workbench.tools.registry import ToolRegistry, default_registry

__all__ = ["Tool", "ToolContext", "ToolResult", "ToolRegistry", "default_registry"]
