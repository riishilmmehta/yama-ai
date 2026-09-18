from typing import Dict, Any, Optional

class StateManager:
    """
    Manages multi-turn conversation state.
    For MVP, uses an in-memory dictionary. Production should use Redis.
    """
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieves or creates a session state."""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "active_intent": None,
                "slots": {},
                "history": [],
                "pending_clarification": None,
                "context": {} # For coref resolution
            }
        return self._sessions[session_id]

    def update_session(self, session_id: str, updates: Dict[str, Any]):
        """Updates the session state."""
        session = self.get_session(session_id)
        
        if "active_intent" in updates:
            session["active_intent"] = updates["active_intent"]
            
        if "slots" in updates:
            # Merge slots
            session["slots"].update(updates["slots"])
            
        if "history" in updates:
            # Append to history, keep last N
            session["history"].append(updates["history"])
            if len(session["history"]) > 10:
                session["history"] = session["history"][-10:]
                
        if "pending_clarification" in updates:
            session["pending_clarification"] = updates["pending_clarification"]
            
        if "context" in updates:
            session["context"].update(updates["context"])

    def clear_session(self, session_id: str):
        """Clears a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]

state_manager = StateManager()
