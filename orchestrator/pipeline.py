from typing import Dict, Any, Optional
from tools.registry import ToolRegistry
import logging
from nlu.intent_classifier import get_intent
from nlu.entity_extractor import extract_entities
from nlu.coref_resolver import resolve_coreferences
from dialogue.state_manager import state_manager
from dialogue.slot_filling import check_missing_slots

logger = logging.getLogger(__name__)

async def process_query(query: str, session_id: str, raw_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main orchestration pipeline for processing a user query.
    
    Phases:
    1. NLU (Intent + Entities + Coref)
    2. Dialogue State Update
    3. Tool Dispatch
    4. Response Building (NLG)
    """
    # Initialize/retrieve managed state
    state = state_manager.get_session(session_id)
    
    # --- PHASE 1: NLU ---
    logger.info(f"Processing raw query: {query}")
    
    # 1.0 Spell Checking
    from nlu.spellchecker import correct_query
    corrected_query = correct_query(query)
    if corrected_query != query:
        logger.info(f"Query after spellcheck: {corrected_query}")
    
    # 1.1 Coreference Resolution
    resolved_query = resolve_coreferences(corrected_query, state)
    
    # 1.2 Intent Classification
    intent_label, confidence = get_intent(resolved_query)
    
    # If confidence is low, and we have an active intent waiting for a slot, reuse it
    if confidence < 0.6 and state.get("pending_clarification") and state.get("active_intent"):
        intent_label = state["active_intent"]
        logger.info(f"Low confidence intent, falling back to active intent: {intent_label}")
    else:
        logger.info(f"Detected intent: {intent_label} (Confidence: {confidence:.2f})")
    
    # 1.3 Entity Extraction
    entities = extract_entities(resolved_query)
    logger.info(f"Extracted entities: {entities}")
    
    # --- PHASE 2/3: Dialogue State & Clarification ---
    state_manager.update_session(session_id, {
        "active_intent": intent_label,
        "slots": entities,
        "history": {"query": query, "intent": intent_label}
    })
    
    # Check for context entity updates for coref
    if entities:
        # Just grab the first extracted entity value for simplistic coref context
        first_ent_val = list(entities.values())[0]
        if isinstance(first_ent_val, list):
            first_ent_val = first_ent_val[0]
        state_manager.update_session(session_id, {"context": {"entity": first_ent_val}})
    
    tool = ToolRegistry.get_tool_for_intent(intent_label)
    if not tool:
        tool = ToolRegistry.get_tool_by_name("fallback_tool")
        
    if tool:
        # Check if tool requires slots that we don't have
        missing_slot = check_missing_slots(tool.required_entities, state["slots"])
        if missing_slot:
            state_manager.update_session(session_id, {"pending_clarification": missing_slot})
            return {
                "direct_answer": f"Could you provide the {missing_slot} for this request?",
                "key_points": [],
                "sources": []
            }
            
        # Slots filled, clear pending
        state_manager.update_session(session_id, {"pending_clarification": None})
        
        # --- PHASE 5: Tool Dispatch ---
        logger.info(f"Dispatching to tool: {tool.name}")
        try:
            tool_result = await tool.execute(state["slots"], state)
        except Exception as e:
            logger.error(f"Error executing tool {tool.name}: {e}")
            tool_result = {"error": str(e)}
    else:
        logger.warning(f"No tool found for intent '{intent_label}' and no fallback available.")
        tool_result = {"direct_answer": "I don't know how to handle that yet."}

    # --- PHASE 4: Response Building ---
    from nlg.response_builder import build_response
    response = build_response(intent_label, tool_result)
    
    return response
