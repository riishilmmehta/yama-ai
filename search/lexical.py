import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    logger.warning("rank_bm25 is not installed. Lexical search will fallback to simple keyword matching.")

class LexicalSearch:
    """
    Handles BM25 lexical search over documents.
    """
    def __init__(self):
        self.corpus: List[Dict[str, Any]] = []
        self.bm25 = None
        self.tokenized_corpus = []

    def build_index(self, documents: List[Dict[str, Any]]):
        """
        Builds the BM25 index.
        Expects documents like [{"id": "1", "content": "...", "metadata": {...}}]
        """
        self.corpus = documents
        if not BM25_AVAILABLE:
            return
            
        self.tokenized_corpus = [doc["content"].lower().split() for doc in documents]
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        logger.info(f"Built BM25 index with {len(documents)} documents.")

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Searches the corpus and returns the top_k results.
        Returns a list of dicts with 'doc' and 'score'.
        """
        if not BM25_AVAILABLE or not self.bm25:
            # Fallback to a very naive keyword match if no BM25
            results = []
            q_terms = set(query.lower().split())
            for doc in self.corpus:
                doc_terms = set(doc["content"].lower().split())
                score = len(q_terms.intersection(doc_terms))
                if score > 0:
                    results.append({"doc": doc, "score": score})
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]

        tokenized_query = query.lower().split()
        doc_scores = self.bm25.get_scores(tokenized_query)
        
        # Get top K indices
        import numpy as np
        top_indices = np.argsort(doc_scores)[::-1][:top_k]
        
        results = []
        for idx in top_indices:
            score = doc_scores[idx]
            if score > 0:
                results.append({
                    "doc": self.corpus[idx],
                    "score": float(score)
                })
                
        return results

lexical_searcher = LexicalSearch()
