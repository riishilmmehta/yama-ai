import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer
    import faiss
    import numpy as np
    SEMANTIC_AVAILABLE = True
except ImportError:
    SEMANTIC_AVAILABLE = False
    logger.warning("sentence-transformers or faiss is not installed. Semantic search will be disabled.")

class SemanticSearch:
    """
    Handles dense vector retrieval using sentence-transformers and FAISS.
    """
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.model = None
        self.index = None
        self.corpus: List[Dict[str, Any]] = []
        
        if SEMANTIC_AVAILABLE:
            try:
                self.model = SentenceTransformer(self.model_name)
            except Exception as e:
                logger.error(f"Failed to load SentenceTransformer {self.model_name}: {e}")

    def build_index(self, documents: List[Dict[str, Any]]):
        """
        Embeds documents and builds the FAISS index.
        """
        self.corpus = documents
        if not SEMANTIC_AVAILABLE or not self.model:
            return
            
        texts = [doc["content"] for doc in documents]
        if not texts:
            return
            
        logger.info(f"Embedding {len(texts)} documents for semantic search...")
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        
        # Determine embedding dimension
        dim = embeddings.shape[1]
        
        # Build FAISS index (using L2 distance)
        self.index = faiss.IndexFlatL2(dim)
        self.index.add(embeddings)
        logger.info(f"Built FAISS index with {len(documents)} documents.")

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Embeds the query and searches the FAISS index.
        """
        if not SEMANTIC_AVAILABLE or not self.model or not self.index:
            return []
            
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        
        # faiss returns (distances, indices)
        distances, indices = self.index.search(query_embedding, top_k)
        
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1:  # -1 means not enough results
                # Invert distance to act like a similarity score for fusion later
                # (Lower distance = higher similarity, so we can use 1 / (1 + dist))
                score = 1.0 / (1.0 + float(dist))
                results.append({
                    "doc": self.corpus[idx],
                    "score": score
                })
                
        return results

semantic_searcher = SemanticSearch()
