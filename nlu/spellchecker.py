import re
import os
import logging
from symspellpy import SymSpell, Verbosity
import pkg_resources

logger = logging.getLogger(__name__)

class YamaSpellChecker:
    def __init__(self):
        self.sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        dictionary_path = pkg_resources.resource_filename(
            "symspellpy", "frequency_dictionary_en_82_765.txt"
        )
        if os.path.exists(dictionary_path):
            self.sym_spell.load_dictionary(dictionary_path, term_index=0, count_index=1)
        
        self.whitelist = {
            "aapl", "msft", "tsla", "goog", "googl", "amzn", "meta", "nflx", "nvda",
            "yama", "ai", "llm", "api", "ui", "ux", "json", "html", "css", "js", "nlp",
            "spacy", "fastapi", "chartjs"
        }
        
    def should_skip(self, token: str) -> bool:
        """Protect domain vocabulary from spellcheck."""
        if token.lower() in self.whitelist:
            return True
        if token.isupper():
            return True
        if re.search(r'\d', token) and re.search(r'[a-zA-Z]', token):
            return True
        if re.match(r'^([A-Z][a-z]?\d*)+$', token):
            return True
        return False

    def correct(self, text: str) -> str:
        tokens = re.findall(r'\b\w+\b', text)
        corrected_text = text
        
        for token in tokens:
            if not token.isalnum():
                continue
            
            if self.should_skip(token):
                continue
                
            suggestions = self.sym_spell.lookup(
                token, Verbosity.CLOSEST, max_edit_distance=2, include_unknown=True
            )
            
            if suggestions:
                best_suggestion = suggestions[0].term
                if token.istitle():
                    best_suggestion = best_suggestion.capitalize()
                elif token.isupper():
                    best_suggestion = best_suggestion.upper()
                
                if best_suggestion.lower() != token.lower():
                    logger.info(f"SPELLCHECK: Corrected '{token}' -> '{best_suggestion}'")
                    # Replace only as a whole word
                    corrected_text = re.sub(rf'\b{re.escape(token)}\b', best_suggestion, corrected_text, count=1)
                    
        return corrected_text

_checker = YamaSpellChecker()

def correct_query(query: str) -> str:
    return _checker.correct(query)
