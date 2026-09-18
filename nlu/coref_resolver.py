import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class CorefResolver:
    """
    Resolves coreferences (e.g., pronouns like "it", "they") in a query
    based on the previous conversation context.
    """
    
    def resolve(self, query: str, state: Dict[str, Any]) -> str:
        """
        Takes the current query and the session state.
        Returns the query with pronouns replaced by their referents if found.
        """
        # For MVP, a simple rule-based approach if coreferee is too heavy.
        # Check if the query starts with or contains 'it', 'that', 'those', etc.
        # and we have an 'entity' in the state context.
        
        lower_query = query.lower()
        context = state.get("context", {})
        last_entity = context.get("entity")
        
        if not last_entity:
            return query
            
        # Very simple naive replacement for MVP
        # In a full implementation, you'd use spaCy + coreferee here
        replacements = {
            " it ": f" {last_entity} ",
            " it?": f" {last_entity}?",
            " it.": f" {last_entity}.",
            "what about it": f"what about {last_entity}",
            "how about it": f"how about {last_entity}",
        }
        
        resolved_query = query
        for k, v in replacements.items():
            if k in lower_query:
                # Naive replace (preserves original casing mostly, but simplistic)
                resolved_query = lower_query.replace(k, v)
                logger.info(f"Resolved coreference: '{query}' -> '{resolved_query}'")
                break
                
        return resolved_query

resolver = CorefResolver()

def resolve_coreferences(query: str, state: Dict[str, Any]) -> str:
    return resolver.resolve(query, state)
