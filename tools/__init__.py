from tools.registry import ToolRegistry
from tools.legacy_wrapper import LegacyWrapperTool

# Register legacy wrapper as fallback
ToolRegistry.register(LegacyWrapperTool)
