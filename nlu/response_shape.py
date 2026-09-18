import re

def classify_shape(query: str) -> str:
    """
    Determine the desired visual shape of the response based on the query.
    Returns one of: 'text', 'chart', 'table', 'image'
    """
    query_lower = query.lower()
    
    # 1. Image
    if re.search(r'\b(image|picture|photo|show me a picture of|visual of)\b', query_lower):
        return "image"
        
    # 2. Chart / Graph
    if re.search(r'\b(chart|graph|plot|trend|visualize)\b', query_lower):
        return "chart"
        
    # 3. Table / Grid
    if re.search(r'\b(table|grid|spreadsheet|columns and rows)\b', query_lower):
        return "table"
        
    # Default is text
    return "text"
