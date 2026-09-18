from typing import Dict, Type, Optional
from tools.base import BaseTool

class ToolRegistry:
    """
    Registry for managing and dispatching to available tools.
    """
    _tools_by_intent: Dict[str, BaseTool] = {}
    _tools_by_name: Dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool_class: Type[BaseTool]):
        """
        Decorator to register a tool class.
        """
        tool_instance = tool_class()
        cls._tools_by_name[tool_instance.name] = tool_instance
        
        for intent in tool_instance.intent_labels:
            if intent in cls._tools_by_intent:
                # Log a warning in a real app, but for now just overwrite
                pass
            cls._tools_by_intent[intent] = tool_instance
            
        return tool_class

    @classmethod
    def get_tool_for_intent(cls, intent: str) -> Optional[BaseTool]:
        """
        Returns the tool instance registered for the given intent.
        """
        return cls._tools_by_intent.get(intent)
    
    @classmethod
    def get_tool_by_name(cls, name: str) -> Optional[BaseTool]:
        """
        Returns a tool instance by its name.
        """
        return cls._tools_by_name.get(name)

    @classmethod
    def list_tools(cls) -> list[str]:
        return list(cls._tools_by_name.keys())
