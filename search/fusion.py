from typing import List, Dict, Any

def reciprocal_rank_fusion(lexical_results: List[Dict[str, Any]], semantic_results: List[Dict[str, Any]], k: int = 60) -> List[Dict[str, Any]]:
    """
    Combines lexical and semantic search results using Reciprocal Rank Fusion (RRF).
    RRF score = 1 / (k + rank)
    """
    fused_scores = {}
    docs_by_id = {}
    
    # Process lexical results
    for rank, result in enumerate(lexical_results):
        doc = result["doc"]
        # Assume docs have an 'id' or we use 'content' as a fallback identifier
        doc_id = doc.get("id", doc.get("content"))
        
        if doc_id not in fused_scores:
            fused_scores[doc_id] = 0.0
            docs_by_id[doc_id] = doc
            
        fused_scores[doc_id] += 1.0 / (k + rank + 1)
        
    # Process semantic results
    for rank, result in enumerate(semantic_results):
        doc = result["doc"]
        doc_id = doc.get("id", doc.get("content"))
        
        if doc_id not in fused_scores:
            fused_scores[doc_id] = 0.0
            docs_by_id[doc_id] = doc
            
        fused_scores[doc_id] += 1.0 / (k + rank + 1)
        
    # Sort by fused score
    fused_results = [
        {"doc": docs_by_id[doc_id], "score": score}
        for doc_id, score in fused_scores.items()
    ]
    
    fused_results.sort(key=lambda x: x["score"], reverse=True)
    return fused_results
