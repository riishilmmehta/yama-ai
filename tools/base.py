from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseTool(ABC):
    """
    Abstract base class for all tools in the Yama AI ecosystem.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The internal name of the tool."""
        pass
        
    @property
    @abstractmethod
    def intent_labels(self) -> List[str]:
        """A list of intents that should route to this tool."""
        pass
        
    @property
    def required_entities(self) -> List[str]:
        """A list of entities required for this tool to execute."""
        return []
        
    @abstractmethod
    async def execute(self, entities: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the tool's logic.
        
        Args:
            entities: Extracted entities from the user's query.
            state: The current session state.
            
        Returns:
            A dictionary containing the results of the execution.
            Typically structured with 'direct_answer', 'key_points', 'sources', etc.
        """
        pass
