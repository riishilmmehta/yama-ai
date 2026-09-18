import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

try:
    import spacy
    from spacy.pipeline import EntityRuler
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    logger.warning("spaCy is not installed. Entity extraction will be limited.")

class EntityExtractor:
    def __init__(self, model_name: str = "en_core_web_trf"):
        self.nlp = None
        if SPACY_AVAILABLE:
            try:
                self.nlp = spacy.load(model_name)
                self._add_custom_rules()
            except OSError:
                logger.warning(f"spaCy model {model_name} not found. Try running: python -m spacy download {model_name}")
                # Fallback to a smaller model if the transformer one isn't available
                try:
                    self.nlp = spacy.load("en_core_web_sm")
                    self._add_custom_rules()
                    logger.info("Fell back to en_core_web_sm")
                except OSError:
                    logger.error("No spaCy models found.")

    def _add_custom_rules(self):
        """Adds domain-specific NER rules."""
        if not self.nlp:
            return
            
        # Add EntityRuler if it doesn't exist
        if "entity_ruler" not in self.nlp.pipe_names:
            ruler = self.nlp.add_pipe("entity_ruler", before="ner")
        else:
            ruler = self.nlp.get_pipe("entity_ruler")
            
        # Define some basic domain-specific rules (expand as needed)
        patterns = [
            # Example: Catching basic stock tickers (simplified)
            {"label": "STOCK_TICKER", "pattern": [{"TEXT": {"REGEX": "^[A-Z]{1,5}$"}}]}
            # Math expressions or chemical formulas could have regex patterns here too
        ]
        ruler.add_patterns(patterns)

    def extract(self, text: str) -> Dict[str, Any]:
        """
        Extracts entities from the text.
        Returns a dictionary of entity labels to extracted values.
        """
        if not self.nlp:
            return {}
            
        doc = self.nlp(text)
        entities = {}
        
        for ent in doc.ents:
            # If multiple entities of the same type exist, group them in a list
            if ent.label_ in entities:
                if isinstance(entities[ent.label_], list):
                    entities[ent.label_].append(ent.text)
                else:
                    entities[ent.label_] = [entities[ent.label_], ent.text]
            else:
                entities[ent.label_] = ent.text
                
        return entities

# Singleton instance
extractor = EntityExtractor()

def extract_entities(text: str) -> Dict[str, Any]:
    return extractor.extract(text)
