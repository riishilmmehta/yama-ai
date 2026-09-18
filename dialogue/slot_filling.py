from typing import List, Dict, Any, Optional

class SlotFiller:
    """
    Checks if all required entities (slots) for a tool are filled.
    """
    
    def check_slots(self, required_slots: List[str], current_slots: Dict[str, Any]) -> Optional[str]:
        """
        Returns the name of the first missing slot, or None if all are filled.
        """
        for slot in required_slots:
            if slot not in current_slots or current_slots[slot] is None:
                return slot
        return None

slot_filler = SlotFiller()

def check_missing_slots(required_slots: List[str], current_slots: Dict[str, Any]) -> Optional[str]:
    return slot_filler.check_slots(required_slots, current_slots)
