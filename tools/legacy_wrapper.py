from tools.base import BaseTool
from typing import Dict, Any

class LegacyWrapperTool(BaseTool):
    """
    Wraps the legacy get_response function from main.py as a fallback tool.
    This allows gradual migration of tools.
    """
    
    @property
    def name(self) -> str:
        return "fallback_tool"
        
    @property
    def intent_labels(self) -> list[str]:
        return ["fallback"]
        
    async def execute(self, entities: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        # Avoid circular imports by importing here
        import main
        
        # We need the original query to pass to get_response
        history = state.get("history", [])
        if history:
            original_query = history[-1].get("query", "")
        else:
            original_query = ""
            
        email = state.get("session_id", "anon")
        
        # Call the synchronous legacy function
        result = main.get_response(original_query, email)
        
        # Convert legacy result to standard format if needed
        if isinstance(result, dict):
            return {
                "direct_answer": result.get("text", str(result)),
                "key_points": [],
                "sources": [],
                "image": result.get("image")
            }
        else:
            return {
                "direct_answer": str(result),
                "key_points": [],
                "sources": []
            }
