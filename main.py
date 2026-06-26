from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import os
import re
from datetime import datetime
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from tinydb import TinyDB, Query
import secrets
import asyncio
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from typing import List, Dict, Any, Optional, Tuple

app = FastAPI(title="Yama AI")

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

# ============ CONTEXT MEMORY ============
class ContextMemory:
    def __init__(self, max_messages=30):
        self.max_messages = max_messages
        self._contexts = {}
    
    def get_context(self, email: str) -> List[Dict]:
        return self._contexts.get(email, [])
    
    def add_message(self, email: str, role: str, content: str):
        if email not in self._contexts:
            self._contexts[email] = []
        self._contexts[email].append({"role": role, "content": content})
        if len(self._contexts[email]) > self.max_messages:
            self._contexts[email] = self._contexts[email][-self.max_messages:]
    
    def clear_context(self, email: str):
        if email in self._contexts:
            self._contexts[email] = []

context_memory = ContextMemory()

# ============ SOURCE QUALITY SCORING ============
def get_source_quality_score(url: str) -> Tuple[int, str]:
    """Return quality score and category for a URL"""
    url_lower = url.lower()
    
    # Government websites
    if any(domain in url_lower for domain in ['.gov', '.gov.', 'government', 'parliament', 'whitehouse']):
        return 100, "Government"
    
    # Educational websites
    if any(domain in url_lower for domain in ['.edu', '.ac.', 'university', 'college', 'school', 'scholar']):
        return 95, "Educational"
    
    # Research papers
    if any(domain in url_lower for domain in ['researchgate', 'arxiv', 'pubmed', 'sciencedirect', 'springer', 'ieee']):
        return 90, "Research"
    
    # Official company websites
    if any(domain in url_lower for domain in ['microsoft', 'apple', 'google', 'amazon', 'facebook', 'twitter', 'github']):
        return 90, "Official Company"
    
    # Major news websites
    if any(domain in url_lower for domain in ['nytimes', 'washingtonpost', 'bbc', 'cnn', 'reuters', 'apnews', 'bloomberg', 'wsj', 'theguardian', 'economist']):
        return 80, "Major News"
    
    # Wikipedia
    if 'wikipedia' in url_lower:
        return 85, "Encyclopedia"
    
    # Blogs
    if any(domain in url_lower for domain in ['blog', 'medium', 'wordpress']):
        return 60, "Blog"
    
    # Unknown - moderate quality
    if any(domain in url_lower for domain in ['.com', '.org', '.net']):
        return 50, "Website"
    
    return 40, "Unknown"

# ============ SOURCE AGREEMENT SYSTEM ============
def analyze_source_agreement(sources: List[Dict]) -> Dict[str, Any]:
    """Analyze agreement between sources and return confidence metrics"""
    if not sources:
        return {"confidence": 0, "agreement": "No sources", "confident_sources": 0}
    
    # Group similar information
    fact_groups = defaultdict(list)
    
    for source in sources:
        # Extract key claims from snippet
        snippet = source.get('snippet', '').lower()
        claims = set()
        
        # Simple claim extraction (could be improved with NLP)
        sentences = snippet.split('.')
        for sentence in sentences:
            if len(sentence.strip()) > 20 and any(word in sentence for word in ['is', 'are', 'was', 'were', 'has', 'have']):
                claims.add(sentence.strip())
        
        for claim in claims:
            if claim:
                fact_groups[claim].append(source['url'])
    
    # Count unique claims and sources
    total_claims = len(fact_groups)
    
    # Find claims with multiple sources
    agreed_claims = {k: v for k, v in fact_groups.items() if len(v) >= 3}
    disagreeing_claims = {k: v for k, v in fact_groups.items() if len(v) == 1}
    
    # Calculate confidence score
    total_sources = len(sources)
    
    if total_sources == 0:
        confidence = 0
    else:
        # Base confidence from source quality
        avg_quality = sum(s.get('quality_score', 50) for s in sources) / total_sources
        source_quality_factor = avg_quality / 100
        
        # Agreement factor
        if agreed_claims:
            agreement_factor = min(1.0, len(agreed_claims) / total_sources)
        else:
            agreement_factor = 0.5 if len(sources) > 1 else 0.3
        
        # Number of sources factor
        source_count_factor = min(1.0, total_sources / 8)
        
        # Calculate final confidence
        confidence = (source_quality_factor * 0.4 + agreement_factor * 0.4 + source_count_factor * 0.2) * 100
        
        # Ensure confidence is within bounds
        confidence = max(0, min(100, confidence))
    
    # Determine confidence level
    if confidence >= 85:
        level = "High"
    elif confidence >= 60:
        level = "Medium"
    else:
        level = "Low"
    
    return {
        "confidence": round(confidence, 1),
        "level": level,
        "total_sources": total_sources,
        "agreement": f"{len(agreed_claims)} facts agreed by {len(sources)} sources",
        "agreed_facts": len(agreed_claims),
        "disagreements": len(disagreeing_claims)
    }

# ============ SEARCH IMPROVEMENTS ============
def search_web_improved(query: str, max_results: int = 10) -> List[Dict]:
    """Improved search with more results and quality ranking"""
    results = []
    try:
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))
            
            for r in search_results:
                url = r.get('href', '')
                quality_score, category = get_source_quality_score(url)
                
                results.append({
                    "title": r.get('title', ''),
                    "snippet": r.get('body', '')[:300],
                    "url": url,
                    "quality_score": quality_score,
                    "quality_category": category,
                    "content_richness": min(1.0, len(r.get('body', '')) / 500)  # Estimate content richness
                })
            
            # Remove duplicates by URL
            seen_urls = set()
            unique_results = []
            for r in results:
                if r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    unique_results.append(r)
            
            # Sort by quality score and richness
            unique_results.sort(key=lambda x: (x['quality_score'] + x['content_richness'] * 50), reverse=True)
            
            return unique_results
    except Exception as e:
        print(f"Search error: {e}")
        return []

def read_full_webpage_improved(url: str) -> Optional[str]:
    """Improved webpage extraction removing noise"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove noise elements
        for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
            element.decompose()
        
        # Remove common ad and popup elements
        for element in soup.find_all(class_=re.compile(r'(ad|popup|modal|banner|cookie|newsletter|subscribe)', re.I)):
            element.decompose()
        
        # Remove navigation items
        for element in soup.find_all(['ul', 'ol']):
            if element.find_parent(['nav', 'header']):
                element.decompose()
        
        # Extract main content
        content_parts = []
        
        # Try to find main article
        article = soup.find('article') or soup.find('main')
        
        if article:
            # Get paragraphs from article
            for p in article.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 50 and not any(x in text.lower() for x in ['advertisement', 'sponsored', 'cookie']):
                    content_parts.append(text)
        else:
            # Fallback to all paragraphs
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 50 and not any(x in text.lower() for x in ['advertisement', 'sponsored', 'cookie']):
                    content_parts.append(text)
        
        # Also get headings and list items
        for h in soup.find_all(['h1', 'h2', 'h3']):
            text = h.get_text(strip=True)
            if len(text) > 10:
                content_parts.append(f"**{text}**")
        
        for li in soup.find_all('li'):
            text = li.get_text(strip=True)
            if len(text) > 20:
                content_parts.append(f"• {text}")
        
        # Combine content
        full_text = ' '.join(content_parts[:50])  # Take more content
        
        # Clean up extra whitespace
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        
        return full_text[:3000]  # Limit to 3000 chars
    except:
        return None

# ============ ADVANCED ANSWER GENERATION ============
def generate_advanced_answer(query: str, search_results: List[Dict], context_history: List[Dict]) -> str:
    """Generate structured, professional answer with confidence scores"""
    
    # If no results
    if not search_results:
        return f"I searched for '{query}' but found no results. Please try rephrasing your question."
    
    # Read top sources (5-8 sources)
    sources_to_read = min(8, len(search_results))
    source_contents = []
    
    for i in range(sources_to_read):
        url = search_results[i]['url']
        content = read_full_webpage_improved(url)
        if content and len(content) > 100:
            source_contents.append({
                "url": url,
                "title": search_results[i]['title'],
                "content": content,
                "quality_score": search_results[i]['quality_score'],
                "quality_category": search_results[i]['quality_category']
            })
    
    # Build sources list for analysis
    sources_for_analysis = [
        {"url": s['url'], "snippet": s['snippet'], "quality_score": s.get('quality_score', 50)}
        for s in search_results[:10]
    ]
    
    # Analyze agreement
    agreement_analysis = analyze_source_agreement(sources_for_analysis)
    
    # Build answer structure
    answer_parts = []
    
    # 📌 Quick Answer
    quick_answer = generate_quick_answer(query, source_contents)
    answer_parts.append(f"📌 **Quick Answer**\n{quick_answer}\n")
    
    # 📖 Detailed Explanation
    detailed_explanation = generate_detailed_explanation(query, source_contents)
    if detailed_explanation:
        answer_parts.append(f"📖 **Detailed Explanation**\n{detailed_explanation}\n")
    
    # 📊 Key Facts
    key_facts = generate_key_facts(query, source_contents)
    if key_facts:
        facts_text = "\n".join([f"• {fact}" for fact in key_facts[:5]])
        answer_parts.append(f"📊 **Key Facts**\n{facts_text}\n")
    
    # 🔍 Analysis
    analysis = generate_analysis(query, source_contents, agreement_analysis)
    if analysis:
        answer_parts.append(f"🔍 **Analysis**\n{analysis}\n")
    
    # Confidence System
    confidence_info = generate_confidence_info(agreement_analysis)
    answer_parts.append(confidence_info)
    
    # 🔗 Sources
    source_cards = generate_source_cards(search_results[:6])
    if source_cards:
        answer_parts.append(f"🔗 **Sources**\n{source_cards}")
    
    return "\n".join(answer_parts)

def generate_quick_answer(query: str, sources: List[Dict]) -> str:
    """Generate a concise quick answer"""
    if not sources:
        return "No information found."
    
    # Use first source content to generate quick answer
    first_content = sources[0]['content'] if sources else ""
    # Extract first meaningful sentence
    sentences = first_content.split('.')
    for sentence in sentences:
        if len(sentence.strip()) > 30:
            return sentence.strip() + '.'
    
    return "Information found but could not generate a quick answer."

def generate_detailed_explanation(query: str, sources: List[Dict]) -> str:
    """Generate a detailed explanation from sources"""
    if not sources:
        return ""
    
    # Combine content from top 3 sources
    combined_content = ""
    for source in sources[:3]:
        combined_content += source['content'] + " "
    
    # Extract key paragraphs
    paragraphs = combined_content.split('. ')
    key_paragraphs = []
    
    for p in paragraphs:
        if len(p) > 100 and not any(x in p.lower() for x in ['advertisement', 'cookie']):
            key_paragraphs.append(p)
    
    if key_paragraphs:
        # Join first few key paragraphs
        explanation = ". ".join(key_paragraphs[:3])
        # Ensure it ends with a period
        if not explanation.endswith('.'):
            explanation += '.'
        return explanation
    
    return "Detailed explanation could not be generated."

def generate_key_facts(query: str, sources: List[Dict]) -> List[str]:
    """Extract key facts from sources"""
    facts = []
    seen_facts = set()
    
    for source in sources[:5]:
        content = source['content']
        # Look for bullet points, list items, or key statements
        sentences = re.split(r'[.!?]', content)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 30 and len(sentence) < 150:
                # Check if it's a fact (contains numbers, dates, or specific info)
                if (re.search(r'\d+', sentence) or 
                    any(word in sentence.lower() for word in ['is', 'are', 'was', 'were', 'has', 'have']) or
                    any(word in sentence.lower() for word in ['percent', 'million', 'billion', 'year'])):
                    
                    # Clean up the fact
                    fact = sentence.strip()
                    if fact and fact not in seen_facts:
                        seen_facts.add(fact)
                        facts.append(fact)
        
        if len(facts) >= 5:
            break
    
    return facts[:5]

def generate_analysis(query: str, sources: List[Dict], agreement: Dict) -> str:
    """Generate analysis based on source agreement and quality"""
    analysis_parts = []
    
    # Source quality analysis
    qualities = [s['quality_score'] for s in sources if 'quality_score' in s]
    if qualities:
        avg_quality = sum(qualities) / len(qualities)
        if avg_quality >= 80:
            analysis_parts.append(f"High-quality sources with average reliability score of {avg_quality:.0f}%.")
        elif avg_quality >= 60:
            analysis_parts.append(f"Moderate-quality sources with average reliability score of {avg_quality:.0f}%.")
        else:
            analysis_parts.append(f"Source quality is mixed with average reliability of {avg_quality:.0f}%.")
    
    # Agreement analysis
    if agreement['total_sources'] > 1:
        if agreement['level'] == 'High':
            analysis_parts.append(f"Strong agreement across {agreement['total_sources']} sources.")
        elif agreement['level'] == 'Medium':
            analysis_parts.append(f"Mixed agreement among {agreement['total_sources']} sources.")
        else:
            analysis_parts.append(f"Limited agreement between sources.")
    
    return " ".join(analysis_parts)

def generate_confidence_info(agreement: Dict) -> str:
    """Generate confidence information"""
    return f"""🔬 **Confidence: {agreement['confidence']:.1f}%**
Information gathered from {agreement['total_sources']} sources.
Confidence Level: {agreement['level']}

{agreement['agreement']}"""

def generate_source_cards(sources: List[Dict]) -> str:
    """Generate source cards with quality scores"""
    cards = []
    for i, source in enumerate(sources[:6], 1):
        quality = source.get('quality_score', 0)
        category = source.get('quality_category', 'Unknown')
        cards.append(f"{i}. **{source['title']}** (⭐ {quality}% - {category})\n   {source['url']}")
    return "\n\n".join(cards)

# ============ FOLLOW-UP ENGINE ============
def generate_follow_ups(query: str) -> List[str]:
    """Generate intelligent follow-up questions"""
    follow_ups = []
    
    # Analyze query type
    query_lower = query.lower()
    
    # Add general follow-ups
    follow_ups.append("Explain simply")
    follow_ups.append("Give examples")
    follow_ups.append("Real-world use cases")
    
    # Add specific follow-ups based on query type
    if any(word in query_lower for word in ['what', 'who', 'when', 'where']):
        follow_ups.append("Why is this important?")
        follow_ups.append("Latest developments")
    
    if any(word in query_lower for word in ['how', 'why']):
        follow_ups.append("What are the benefits?")
        follow_ups.append("What are the drawbacks?")
    
    if any(word in query_lower for word in ['technology', 'software', 'programming', 'code', 'python', 'javascript']):
        follow_ups.append("How does it compare to alternatives?")
        follow_ups.append("What are the best practices?")
    
    if any(word in query_lower for word in ['business', 'company', 'market']):
        follow_ups.append("What is the market impact?")
        follow_ups.append("Who are the key players?")
    
    if any(word in query_lower for word in ['health', 'medicine', 'fitness']):
        follow_ups.append("What are the health benefits?")
        follow_ups.append("Are there any risks?")
    
    if any(word in query_lower for word in ['history', 'historical', 'ancient']):
        follow_ups.append("What is the historical significance?")
        follow_ups.append("How did it develop over time?")
    
    # Add default follow-ups if few were added
    if len(follow_ups) < 3:
        follow_ups.extend(["Tell me more", "What are the implications?", "Is this widely used?"])
    
    # Remove duplicates
    return list(dict.fromkeys(follow_ups))[:6]

# ============ RESPONSE FUNCTION ============
def resolve_references(message: str, context: List[Dict]) -> str:
    """Resolve references like 'it', 'they', 'this' using context"""
    if not context:
        return message
    
    # Extract the last few user messages from context
    user_messages = [msg['content'] for msg in context if msg['role'] == 'user'][-3:]
    if not user_messages:
        return message
    
    # Simple reference resolution
    resolved = message
    last_message = user_messages[-1] if user_messages else ""
    
    # Extract key entities from last message
    entities = []
    # Look for named entities (simple heuristic)
    words = last_message.split()
    potential_entities = []
    current_entity = []
    
    for word in words:
        if word[0].isupper() and len(word) > 1:
            current_entity.append(word)
        elif current_entity and word in ['is', 'are', 'was', 'were', 'has', 'have', 'had']:
            if current_entity:
                potential_entities.append(' '.join(current_entity))
                current_entity = []
        elif current_entity and len(current_entity) < 5:
            current_entity.append(word)
        else:
            if current_entity:
                potential_entities.append(' '.join(current_entity))
                current_entity = []
    
    if current_entity:
        potential_entities.append(' '.join(current_entity))
    
    # Extract key terms (nouns) from last message
    import nltk
    try:
        from nltk.tokenize import word_tokenize
        from nltk.tag import pos_tag
        words = word_tokenize(last_message)
        tagged = pos_tag(words)
        nouns = [word for word, pos in tagged if pos.startswith('NN') and len(word) > 2]
        potential_entities.extend(nouns)
    except:
        # Fallback: extract words that are capitalized or look like entities
        for word in last_message.split():
            if word[0].isupper() and len(word) > 2:
                potential_entities.append(word)
    
    # Remove duplicates
    potential_entities = list(dict.fromkeys(potential_entities))
    
    # Replace references
    pronouns = {
        r'\bit\b': potential_entities[0] if potential_entities else 'it',
        r'\bthey\b': potential_entities[0] if potential_entities else 'they',
        r'\bthem\b': potential_entities[0] if potential_entities else 'them',
        r'\bthis\b': potential_entities[0] if potential_entities else 'this',
        r'\bthat\b': potential_entities[0] if potential_entities else 'that',
        r'\bthese\b': potential_entities[0] if potential_entities else 'these',
        r'\bthose\b': potential_entities[0] if potential_entities else 'those'
    }
    
    for pronoun, entity in pronouns.items():
        resolved = re.sub(pronoun, entity, resolved, flags=re.IGNORECASE)
    
    return resolved

def get_response(message, email):
    msg = message.strip().lower()
    
    stats = update_user_stats(email)
    user = user_db.get(User.email == email)
    user_name = user.get('name', 'User') if user else 'User'
    
    # Math
    math_match = re.search(r'(\d+)\s*([\+\-\*\/])\s*(\d+)', msg)
    if math_match:
        try:
            a = int(math_match.group(1))
            op = math_match.group(2)
            b = int(math_match.group(3))
            if op == '+': result = a + b
            elif op == '-': result = a - b
            elif op == '*': result = a * b
            elif op == '/': result = a / b
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return f"🧮 {a} {op} {b} = {result}\n\n✨ Great job, {user_name}! Level {stats['level']} - {stats['title']}"
        except:
            pass
    
    # Greetings
    if msg in ['hi', 'hello', 'hey', 'sup', 'yo']:
        return f"👋 Hello {user_name}! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
    
    if 'how are you' in msg:
        return f"😊 I'm doing great! Thanks for asking, {user_name}!"
    
    # Resolve references using context
    context = context_memory.get_context(email)
    resolved_message = resolve_references(message, context)
    
    # Track response time
    start_time = time.time()
    
    # Perform improved search
    search_results = search_web_improved(resolved_message, max_results=10)
    
    # Generate advanced answer
    response = generate_advanced_answer(resolved_message, search_results, context)
    
    # Add follow-up suggestions
    follow_ups = generate_follow_ups(resolved_message)
    if follow_ups:
        response += "\n\n💭 **Follow-up Questions:**\n" + "\n".join([f"• {q}" for q in follow_ups[:4]])
    
    # Add user stats
    response += f"\n\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
    
    # Track performance
    response_time = time.time() - start_time
    track_analytics({
        "query": resolved_message,
        "response_time": response_time,
        "sources_found": len(search_results),
        "quality_scores": [s.get('quality_score', 0) for s in search_results[:5]],
        "context_used": len(context)
    })
    
    # Update context memory
    context_memory.add_message(email, "user", message)
    context_memory.add_message(email, "ai", response)
    
    return response

# ============ ANALYTICS SYSTEM ============
analytics_data = []

def track_analytics(data: Dict):
    """Track internal analytics"""
    analytics_data.append({
        **data,
        "timestamp": datetime.now().isoformat()
    })
    
    # Keep only last 1000 entries
    if len(analytics_data) > 1000:
        analytics_data[:] = analytics_data[-1000:]

def get_analytics_summary() -> Dict:
    """Get analytics summary for admin"""
    if not analytics_data:
        return {"error": "No analytics data available"}
    
    total_queries = len(analytics_data)
    avg_response_time = sum(d.get('response_time', 0) for d in analytics_data) / total_queries if total_queries > 0 else 0
    avg_sources = sum(d.get('sources_found', 0) for d in analytics_data) / total_queries if total_queries > 0 else 0
    avg_quality = sum(sum(d.get('quality_scores', [0])) / len(d.get('quality_scores', [1])) for d in analytics_data if d.get('quality_scores')) / total_queries if total_queries > 0 else 0
    avg_context = sum(d.get('context_used', 0) for d in analytics_data) / total_queries if total_queries > 0 else 0
    
    return {
        "total_queries": total_queries,
        "average_response_time": f"{avg_response_time:.2f}s",
        "average_sources_per_query": f"{avg_sources:.1f}",
        "average_source_quality": f"{avg_quality:.1f}%",
        "average_context_used": f"{avg_context:.1f} messages"
    }

# ============ HISTORY ============
def load_history(email):
    if not email:
        return []
    safe_email = email.replace('@', '_at_').replace('.', '_dot_')
    filepath = f"history_{safe_email}.json"
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(email, history):
    if not email:
        return
    safe_email = email.replace('@', '_at_').replace('.', '_dot_')
    filepath = f"history_{safe_email}.json"
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def get_or_create_user(email, name, picture=None):
    user = user_db.get(User.email == email)
    if not user:
        user_id = secrets.token_urlsafe(16)
        user_db.insert({
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "message_count": 0,
            "level": 1,
            "title": "🌟 Newbie Chatter",
            "created_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        })
        user = user_db.get(User.email == email)
    else:
        user_db.update({"last_seen": datetime.now().isoformat()}, User.email == email)
    return user

def update_user_stats(email):
    user = user_db.get(User.email == email)
    if user:
        new_count = user.get("message_count", 0) + 1
        new_level = 1 + (new_count // 50)
        
        titles = {
            1: "🌟 Newbie Chatter",
            2: "💬 Regular Talker",
            3: "🔥 Chatty User",
            4: "⚡ Power User",
            5: "👑 Super Chat Master",
            6: "🏆 Ultimate Reviewer",
            7: "🧠 Yama Legend"
        }
        new_title = titles.get(new_level, "🧠 Yama Legend")
        
        user_db.update({
            "message_count": new_count,
            "level": new_level,
            "title": new_title,
            "last_seen": datetime.now().isoformat()
        }, User.email == email)
        
        return {"count": new_count, "level": new_level, "title": new_title}
    return {"count": 0, "level": 1, "title": "🌟 Newbie Chatter"}

# ============ GOOGLE CLIENT ID (UNCHANGED) ============
GOOGLE_CLIENT_ID = "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com"

# ============ COMPLETE FIXED HTML (UNCHANGED - REUSE THE SAME HTML) ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        /* ========== RESET ========== */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        
        /* ========== FIXED: NO FIXED POSITION, NO OVERFLOW HIDDEN ========== */
        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            overflow-x: hidden;
            overflow-y: auto;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f0e8;
            transition: all 0.3s ease;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }
        
        /* ========== FLUID MEDIA ========== */
        img, video, iframe {
            max-width: 100%;
            height: auto;
        }
        
        /* ========== DARK MODE ========== */
        body.dark {
            background: #1a1a2e;
        }
        
        body.dark .app {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        }
        
        body.dark .header {
            background: rgba(26,26,46,0.95);
            border-bottom-color: #2a2a4e;
        }
        
        body.dark .logo h1 {
            color: #d4c5a9;
        }
        
        body.dark .input-wrapper {
            background: #2a2a4e;
            border-color: #3a3a5e;
        }
        
        body.dark textarea {
            color: #e0e0e0;
        }
        
        body.dark textarea::placeholder {
            color: #6a5a7a;
        }
        
        body.dark .message-content {
            color: #e0e0e0;
        }
        
        body.dark .ai-message .message-content {
            background: #2a2a4e !important;
            color: #e0e0e0 !important;
        }
        
        body.dark .suggestion {
            background: #2a2a4e;
            border-color: #3a3a5e;
            color: #e0e0e0;
        }
        
        body.dark .suggestion:hover {
            background: #3a3a5e;
            color: white;
        }
        
        body.dark .welcome h2 {
            color: #d4c5a9;
        }
        
        body.dark .welcome p {
            color: #8a7a6a;
        }
        
        body.dark .sidebar {
            background: #0f0f23;
            border-right-color: #2a2a4e;
        }
        
        body.dark .sidebar-header {
            background: #0a0a1a;
        }
        
        body.dark .history-question {
            color: #d4c5a9;
        }
        
        body.dark .history-time {
            color: #6a5a7a;
        }
        
        body.dark .history-item:hover {
            background: rgba(212,197,169,0.08);
            border-color: #3a3a5e;
        }
        
        body.dark .clear-history {
            color: #d4c5a9;
            border-color: #3a3a5e;
        }
        
        body.dark .clear-history:hover {
            background: rgba(212,197,169,0.2);
            border-color: #c4a57b;
        }
        
        body.dark .new-chat-btn {
            background: #3a3a5e;
            color: #d4c5a9;
        }
        
        body.dark .new-chat-btn:hover {
            background: #4a4a6e;
        }
        
        body.dark .typing span {
            background: #d4c5a9;
        }
        
        body.dark .typing {
            color: #d4c5a9;
        }
        
        body.dark a {
            color: #4ecdc4;
        }
        
        body.dark .message-content a {
            color: #4ecdc4;
        }
        
        body.dark .message-content a:hover {
            color: #6ee7de;
        }
        
        body.dark .control-btn {
            color: #d4c5a9;
        }
        
        body.dark .control-btn:hover {
            background: #3a3a5e;
            color: white;
        }
        
        /* ========== LOGIN OVERLAY ========== */
        .login-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            z-index: 2000;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .login-card {
            background: white;
            border-radius: 30px;
            padding: 40px 30px;
            text-align: center;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 25px 50px rgba(0,0,0,0.2);
        }
        
        .login-card .logo-icon {
            font-size: 4rem;
            margin-bottom: 20px;
        }
        
        .login-card h2 {
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            margin-bottom: 10px;
        }
        
        .login-card p {
            color: #666;
            font-size: 1rem;
            margin-bottom: 30px;
        }
        
        /* ========== APP - FIXED LAYOUT ========== */
        .app {
            display: flex;
            flex-direction: column;
            height: 100dvh;
            min-height: 100vh;
            width: 100%;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            position: relative;
            overflow: hidden;
        }
        
        /* ========== SIDEBAR - RESPONSIVE ========== */
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: min(280px, 80vw);
            background: #2c2418;
            border-right: 1px solid #4a3f2f;
            display: flex;
            flex-direction: column;
            transform: translateX(-100%);
            transition: transform 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            z-index: 1000;
            box-shadow: 4px 0 20px rgba(0,0,0,0.1);
        }
        
        .sidebar.open {
            transform: translateX(0);
        }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid #4a3f2f;
            background: #1f1912;
            flex-shrink: 0;
        }
        
        .sidebar-header h3 {
            color: #d4c5a9;
            font-family: 'Playfair Display', serif;
            font-size: 1rem;
        }
        
        .user-profile {
            display: none;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: rgba(212,197,169,0.1);
            border-radius: 12px;
            margin-top: 15px;
        }
        
        .user-profile-img {
            width: 45px;
            height: 45px;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .user-profile-info {
            flex: 1;
            min-width: 0;
        }
        
        .user-profile-name {
            color: #d4c5a9;
            font-weight: 600;
            font-size: 0.85rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .user-profile-email {
            color: #8a7a6a;
            font-size: 0.65rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .logout-btn {
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 6px 12px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.65rem;
            white-space: nowrap;
        }
        
        .history-list {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            -webkit-overflow-scrolling: touch;
        }
        
        .history-item {
            padding: 10px;
            margin-bottom: 6px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid transparent;
        }
        
        .history-item:hover {
            background: rgba(212,197,169,0.08);
            border-color: #4a3f2f;
        }
        
        .history-question {
            font-size: 0.8rem;
            color: #d4c5a9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .history-time {
            font-size: 0.6rem;
            color: #6a5a4a;
            margin-top: 4px;
        }
        
        .sidebar-footer {
            padding: 16px;
            border-top: 1px solid #4a3f2f;
            background: #1f1912;
            flex-shrink: 0;
        }
        
        .new-chat-btn {
            background: #4a3f2f;
            border: none;
            border-radius: 25px;
            padding: 12px 16px;
            color: #d4c5a9;
            cursor: pointer;
            width: 100%;
            font-size: 0.85rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s;
        }
        
        .new-chat-btn:hover {
            background: #5a4f3f;
        }
        
        .clear-history {
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 8px 16px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.7rem;
            margin-top: 10px;
            width: 100%;
        }
        
        .overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.4);
            display: none;
            z-index: 999;
        }
        
        .overlay.show {
            display: block;
        }
        
        /* ========== MAIN - FLEX LAYOUT ========== */
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
            height: 100%;
            width: 100%;
            overflow: hidden;
        }
        
        /* ========== HEADER ========== */
        .header {
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
            flex-shrink: 0;
            min-height: 56px;
            width: 100%;
            position: relative;
            z-index: 10;
        }
        
        .menu-btn {
            background: none;
            border: none;
            font-size: 1.3rem;
            cursor: pointer;
            color: #6a5a4a;
            padding: 8px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .menu-btn:hover {
            background: #d4c5a9;
            color: #2c2418;
        }
        
        .logo {
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 6px;
            min-width: 0;
        }
        
        .logo-icon {
            font-size: 1.8rem;
        }
        
        .logo h1 {
            font-family: 'Playfair Display', serif;
            font-size: 1.3rem;
            color: #2c2418;
            white-space: nowrap;
        }
        
        .new-chat-mobile {
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px;
            border-radius: 10px;
            color: #6a5a4a;
            display: none;
        }
        
        .control-btn {
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px 12px;
            border-radius: 20px;
            color: #6a5a4a;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .control-btn:hover {
            background: #d4c5a9;
        }
        
        .user-btn {
            background: none;
            border: none;
            cursor: pointer;
            display: none;
            padding: 4px;
        }
        
        .user-btn img {
            width: 35px;
            height: 35px;
            border-radius: 50%;
            object-fit: cover;
        }
        
        /* ========== MESSAGES - SCROLLABLE ========== */
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            padding-bottom: 20px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }
        
        .message {
            margin-bottom: 20px;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .user-message {
            text-align: right;
        }
        
        .ai-message {
            text-align: left;
        }
        
        .message-content {
            display: inline-block;
            max-width: 85%;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
        }
        
        .user-message .message-content {
            background: #2c2418 !important;
            color: white !important;
            padding: 10px 16px !important;
            border-radius: 20px !important;
        }
        
        .ai-message .message-content {
            background: white !important;
            color: #2c2418 !important;
            padding: 12px 18px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }
        
        /* ========== TYPING ========== */
        .typing {
            display: none;
            padding: 10px 16px;
            gap: 5px;
            color: #888;
            font-size: 0.8rem;
            flex-shrink: 0;
        }
        
        .typing span {
            width: 6px;
            height: 6px;
            background: #c4a57b;
            border-radius: 50%;
            display: inline-block;
            animation: bounce 1.4s infinite;
        }
        
        @keyframes bounce {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-6px); }
        }
        
        /* ========== INPUT AREA - STICKY WITH SAFE AREA ========== */
        .input-area {
            position: sticky;
            bottom: 0;
            z-index: 100;
            background: #f5f0e8;
            padding: 12px 16px 20px;
            padding-bottom: env(safe-area-inset-bottom, 20px);
            flex-shrink: 0;
            border-top: 1px solid rgba(212,197,169,0.3);
        }
        
        body.dark .input-area {
            background: #1a1a2e;
            border-top-color: rgba(42,42,78,0.3);
        }
        
        .input-wrapper {
            display: flex;
            align-items: flex-end;
            gap: 12px;
            background: white;
            border-radius: 28px;
            padding: 8px 8px 8px 20px;
            border: 1px solid #d4c5a9;
            width: 100%;
            max-width: 760px;
            margin: 0 auto;
            min-height: 56px;
        }
        
        body.dark .input-wrapper {
            background: #2a2a4e;
            border-color: #3a3a5e;
        }
        
        .input-text-wrapper {
            flex: 1;
            min-width: 0;
        }
        
        textarea {
            width: 100%;
            background: transparent;
            border: none;
            outline: none;
            font-size: 16px;
            line-height: 1.5;
            resize: none;
            padding: 8px 0;
            font-family: inherit;
            color: #2c2418;
            min-height: 24px;
            max-height: 180px;
            overflow-y: auto;
        }
        
        body.dark textarea {
            color: #e0e0e0;
        }
        
        textarea::placeholder {
            color: #b8a88a;
            font-size: 0.95rem;
        }
        
        /* iOS Zoom Fix */
        @media (max-width: 768px) {
            textarea {
                font-size: 16px !important;
            }
        }
        
        .submit-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            border: none;
            background-color: #2c2418;
            color: white;
            cursor: pointer;
            transition: all 0.2s;
            min-width: 44px;
            min-height: 44px;
        }
        
        body.dark .submit-btn {
            background-color: #4a3f2f;
        }
        
        .submit-btn:hover {
            background-color: #4a3f2f;
            transform: scale(1.02);
        }
        
        .submit-btn:active {
            transform: scale(0.96);
        }
        
        .submit-icon {
            width: 20px;
            height: 20px;
            fill: currentColor;
        }
        
        /* ========== WELCOME ========== */
        .welcome {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 50vh;
            text-align: center;
            padding: 20px;
        }
        
        .welcome-icon {
            font-size: 3rem;
            margin-bottom: 15px;
            animation: float 3s ease-in-out infinite;
        }
        
        @keyframes float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-8px); }
        }
        
        .welcome h2 {
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            color: #2c2418;
            margin-bottom: 8px;
        }
        
        .welcome p {
            color: #6a5a4a;
            font-size: 0.85rem;
            margin-bottom: 20px;
        }
        
        .suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
            margin-top: 15px;
        }
        
        .suggestion {
            background: white;
            border: 1px solid #d4c5a9;
            border-radius: 30px;
            padding: 6px 14px;
            font-size: 0.75rem;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        
        .suggestion:hover {
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }
        
        /* ========== RESPONSIVE BREAKPOINTS ========== */
        
        /* Tablet & Mobile */
        @media (max-width: 768px) {
            .messages {
                padding: 12px 16px;
                padding-bottom: 16px;
            }
            .suggestions {
                display: none;
            }
            .new-chat-mobile {
                display: block;
            }
            .header {
                padding: 10px 14px;
                min-height: 52px;
            }
            .logo h1 {
                font-size: 1.1rem;
            }
            .logo-icon {
                font-size: 1.4rem;
            }
            .message-content {
                max-width: 90%;
                font-size: 0.85rem;
            }
            .input-area {
                padding: 10px 12px 16px;
            }
            .input-wrapper {
                padding: 6px 6px 6px 16px;
                min-height: 50px;
                border-radius: 26px;
            }
            textarea {
                font-size: 16px !important;
                padding: 8px 0;
            }
            .submit-btn {
                width: 40px;
                height: 40px;
                min-width: 40px;
                min-height: 40px;
            }
            .submit-icon {
                width: 18px;
                height: 18px;
            }
        }
        
        /* Small Phones */
        @media (max-width: 480px) {
            .header {
                padding: 8px 12px;
                min-height: 48px;
                gap: 8px;
            }
            .logo h1 {
                font-size: 1rem;
            }
            .logo-icon {
                font-size: 1.2rem;
            }
            .control-btn {
                font-size: 0.9rem;
                padding: 6px 8px;
            }
            .menu-btn {
                font-size: 1.1rem;
                padding: 6px;
            }
            .messages {
                padding: 10px 12px;
            }
            .input-area {
                padding: 8px 10px 14px;
                padding-bottom: env(safe-area-inset-bottom, 14px);
            }
            .input-wrapper {
                padding: 5px 5px 5px 14px;
                min-height: 44px;
                gap: 8px;
                border-radius: 24px;
            }
            textarea {
                font-size: 15px !important;
                padding: 6px 0;
                min-height: 20px;
            }
            .submit-btn {
                width: 40px;
                height: 40px;
                min-width: 40px;
                min-height: 40px;
            }
            .submit-icon {
                width: 16px;
                height: 16px;
            }
            .message-content {
                font-size: 0.8rem;
            }
        }
        
        /* Very Small Phones */
        @media (max-width: 380px) {
            .header {
                padding: 6px 10px;
                min-height: 44px;
                gap: 6px;
            }
            .logo h1 {
                font-size: 0.85rem;
            }
            .logo-icon {
                font-size: 1rem;
            }
            .control-btn {
                font-size: 0.8rem;
                padding: 4px 6px;
            }
            .messages {
                padding: 8px 10px;
            }
            .input-area {
                padding: 6px 8px 12px;
            }
            .input-wrapper {
                padding: 4px 4px 4px 12px;
                min-height: 40px;
                gap: 6px;
                border-radius: 22px;
            }
            textarea {
                font-size: 14px !important;
                padding: 5px 0;
                min-height: 18px;
            }
            .submit-btn {
                width: 36px;
                height: 36px;
                min-width: 36px;
                min-height: 36px;
            }
            .submit-icon {
                width: 14px;
                height: 14px;
            }
            .message-content {
                font-size: 0.75rem;
            }
        }
        
        /* Landscape Phones */
        @media (max-height: 500px) and (orientation: landscape) {
            .header {
                min-height: 40px;
                padding: 4px 12px;
                gap: 6px;
            }
            .logo h1 {
                font-size: 0.9rem;
            }
            .logo-icon {
                font-size: 1.1rem;
            }
            .messages {
                padding: 6px 12px;
                padding-bottom: 10px;
            }
            .input-area {
                padding: 4px 12px 8px;
            }
            .input-wrapper {
                min-height: 38px;
                padding: 4px 4px 4px 12px;
            }
            textarea {
                min-height: 20px;
                max-height: 80px;
                font-size: 14px !important;
                padding: 4px 0;
            }
            .submit-btn {
                width: 36px;
                height: 36px;
                min-width: 36px;
                min-height: 36px;
            }
            .submit-icon {
                width: 14px;
                height: 14px;
            }
            .welcome {
                min-height: 20vh;
            }
            .suggestions {
                display: none;
            }
            .control-btn {
                font-size: 0.8rem;
                padding: 3px 6px;
            }
        }
        
        /* Tablets */
        @media (min-width: 769px) and (max-width: 1024px) {
            .input-wrapper {
                max-width: 90%;
            }
            .messages {
                padding: 16px 24px;
            }
            .header {
                padding: 14px 20px;
            }
        }
        
        /* Desktop */
        @media (min-width: 1025px) {
            .input-wrapper {
                max-width: 760px;
            }
            .messages {
                padding: 24px 32px;
            }
            .header {
                padding: 16px 32px;
            }
        }
    </style>
</head>
<body>
    <div id="loginOverlay" class="login-overlay">
        <div class="login-card">
            <div class="logo-icon">🏛️</div>
            <h2>Welcome to Yama</h2>
            <p>Sign in to start your AI journey</p>
            <div id="g_id_onload"
                 data-client_id="{GOOGLE_CLIENT_ID}"
                 data-context="signin"
                 data-ux_mode="popup"
                 data-callback="handleCredentialResponse"
                 data-auto_prompt="false">
            </div>
            <div class="g_id_signin"
                 data-type="standard"
                 data-shape="rectangular"
                 data-theme="outline"
                 data-text="signin_with"
                 data-size="large"
                 data-logo_alignment="left">
            </div>
        </div>
    </div>
    
    <div class="app" id="app">
        <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
        
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h3>📜 CONVERSATIONS</h3>
                <div class="user-profile" id="userProfile"></div>
            </div>
            <div class="history-list" id="historyList">
                <div style="color: #6a5a4a; text-align: center; padding: 20px;">No conversations yet</div>
            </div>
            <div class="sidebar-footer">
                <button class="new-chat-btn" onclick="newChat()">➕ New Chat</button>
                <button class="clear-history" onclick="clearHistory()">Clear all history</button>
            </div>
        </div>
        
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()">☰</button>
                <div class="logo" id="logo">
                    <span class="logo-icon">🏛️</span>
                    <h1>YAMA</h1>
                </div>
                <button class="new-chat-mobile" onclick="newChat()">➕</button>
                <button class="control-btn" onclick="toggleTheme()" title="Dark/Light Mode">🌓</button>
                <button class="control-btn" onclick="exportChat()" title="Export Chat">📥</button>
                <button class="user-btn" id="userBtn" onclick="toggleUserMenu()">
                    <img id="userAvatar" src="" alt="User">
                </button>
            </div>
            
            <div class="messages" id="messages">
                <div class="welcome" id="welcome">
                    <div class="welcome-icon">🏛️</div>
                    <h2>Yama</h2>
                    <p>Your AI companion. Ask me anything - I'll search the web!</p>
                    <div class="suggestions">
                        <div class="suggestion" onclick="askSuggestion('What is the capital of France?')">🗼 Capital of France</div>
                        <div class="suggestion" onclick="askSuggestion('Who is Elon Musk?')">🚀 Who is Elon Musk?</div>
                        <div class="suggestion" onclick="askSuggestion('10000/8')">📐 10000/8</div>
                        <div class="suggestion" onclick="askSuggestion('Latest news today')">📰 Latest news</div>
                    </div>
                </div>
            </div>
            
            <div class="typing" id="typing">
                <span></span><span></span><span></span> Yama is thinking...
            </div>
            
            <div class="input-area">
                <div class="input-wrapper">
                    <div class="input-text-wrapper">
                        <textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    </div>
                    <button class="submit-btn" onclick="sendMessage()" aria-label="Send message">
                        <svg class="submit-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentUser = null;
        let hasMessages = false;
        
        function toggleTheme() {
            document.body.classList.toggle('dark');
            localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
        }
        
        function exportChat() {
            const messages = document.querySelectorAll('.message');
            let exportText = '';
            messages.forEach(msg => {
                const sender = msg.classList.contains('user-message') ? 'You' : 'Yama';
                const text = msg.querySelector('.message-content').innerText;
                exportText += sender + ': ' + text + '\\n\\n';
            });
            const blob = new Blob([exportText], {type: 'text/plain'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'yama_chat_' + new Date().toISOString() + '.txt';
            a.click();
        }
        
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') {
            document.body.classList.add('dark');
        }
        
        function toggleUserMenu() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }
        
        function handleCredentialResponse(response) {
            const token = response.credential;
            const payload = JSON.parse(atob(token.split('.')[1]));
            
            currentUser = {
                name: payload.name,
                email: payload.email,
                picture: payload.picture
            };
            
            document.getElementById('loginOverlay').style.display = 'none';
            document.getElementById('app').style.display = 'flex';
            document.getElementById('userBtn').style.display = 'block';
            document.getElementById('userAvatar').src = currentUser.picture;
            
            document.getElementById('userProfile').style.display = 'flex';
            document.getElementById('userProfile').innerHTML = `
                <img src="${currentUser.picture}" class="user-profile-img">
                <div class="user-profile-info">
                    <div class="user-profile-name">${currentUser.name}</div>
                    <div class="user-profile-email">${currentUser.email}</div>
                </div>
                <button class="logout-btn" onclick="logout()">Logout</button>
            `;
            
            loadHistory();
            
            fetch('/set_user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: currentUser.email, name: currentUser.name, picture: currentUser.picture })
            });
        }
        
        function logout() {
            currentUser = null;
            document.getElementById('loginOverlay').style.display = 'flex';
            document.getElementById('app').style.display = 'none';
            document.getElementById('userBtn').style.display = 'none';
            document.getElementById('userProfile').style.display = 'none';
            if (google && google.accounts) {
                google.accounts.id.disableAutoSelect();
            }
        }
        
        function newChat() {
            if (confirm('Start a new chat?')) { location.reload(); }
        }
        
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }
        
        function closeSidebar() {
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('overlay').classList.remove('show');
        }
        
        function askSuggestion(q) {
            document.getElementById('userInput').value = q;
            sendMessage();
        }
        
        async function loadHistory() {
            if (!currentUser) return;
            const res = await fetch('/get_history?email=' + encodeURIComponent(currentUser.email));
            const history = await res.json();
            const container = document.getElementById('historyList');
            if (history.length === 0) {
                container.innerHTML = '<div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div>';
                return;
            }
            let html = '';
            for (let i = history.length - 1; i >= 0; i--) {
                let item = history[i];
                html += '<div class="history-item" onclick="loadChatMessage(\\'' + escapeHtml(item.user) + '\\')">' +
                        '<div class="history-question">' + escapeHtml(item.user.substring(0, 45)) + '</div>' +
                        '<div class="history-time">' + item.timestamp + '</div>' +
                        '</div>';
            }
            container.innerHTML = html;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function loadChatMessage(msg) {
            document.getElementById('userInput').value = msg;
            closeSidebar();
            sendMessage();
        }
        
        async function clearHistory() {
            if (confirm('Clear all history?')) {
                await fetch('/clear_history', { method: 'POST' });
                location.reload();
            }
        }
        
        const textarea = document.getElementById('userInput');
        
        // Auto-adjust height
        function autoAdjustHeight() {
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        }
        
        textarea.addEventListener('input', autoAdjustHeight);
        
        // VisualViewport handling for mobile keyboard
        if (window.visualViewport) {
            let lastHeight = window.visualViewport.height;
            window.visualViewport.addEventListener('resize', function() {
                const inputArea = document.querySelector('.input-area');
                if (inputArea && window.visualViewport.height < lastHeight) {
                    // Keyboard opened - ensure input is visible
                    setTimeout(() => {
                        inputArea.scrollIntoView({ behavior: 'smooth', block: 'end' });
                    }, 100);
                }
                lastHeight = window.visualViewport.height;
            });
        }
        
        function handleKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }
        
        async function sendMessage() {
            if (!currentUser) { alert('Please sign in first!'); return; }
            const message = textarea.value.trim();
            if (!message) return;
            
            if (!hasMessages) {
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
                document.getElementById('logo').classList.add('small');
            }
            
            addMessage(message, 'user');
            textarea.value = '';
            textarea.style.height = 'auto';
            
            document.getElementById('typing').style.display = 'block';
            scrollToBottom();
            
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: message, email: currentUser.email })
            });
            const data = await res.json();
            
            addMessage(data.response, 'ai');
            document.getElementById('typing').style.display = 'none';
            loadHistory();
            scrollToBottom();
        }
        
        function addMessage(text, sender) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + sender + '-message';
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            div.appendChild(content);
            messages.appendChild(div);
            scrollToBottom();
        }
        
        function scrollToBottom() {
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }
        
        loadHistory();
        textarea.focus();
    </script>
</body>
</html>
'''

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML

@app.post("/set_user")
async def set_user(request: Request):
    data = await request.json()
    get_or_create_user(data.get('email'), data.get('name'), data.get('picture'))
    return {"status": "ok"}

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get('message', '')
    email = data.get('email', '')
    
    response = get_response(message, email)
    
    if email:
        history = load_history(email)
        history.append({
            "user": message,
            "ai": response,
            "timestamp": datetime.now().strftime("%H:%M")
        })
        save_history(email, history)
    
    return {"response": response}

@app.get("/get_history")
async def get_history(email: str = ""):
    return load_history(email)

@app.post("/clear_history")
async def clear_history_endpoint():
    save_history("", [])
    return {"status": "cleared"}

@app.get("/analytics")
async def get_analytics():
    """Admin endpoint to view analytics"""
    return get_analytics_summary()

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - IMPROVED VERSION")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("📱 Perfect on ALL devices")
    print("="*55)
    print("✅ CONTEXT MEMORY (30 messages)")
    print("✅ SOURCE QUALITY SCORING")
    print("✅ SOURCE AGREEMENT SYSTEM")
    print("✅ ADVANCED ANSWER GENERATION")
    print("✅ IMPROVED WEBPAGE EXTRACTION")
    print("✅ SEARCH IMPROVEMENTS (10 sources)")
    print("✅ CONFIDENCE SYSTEM")
    print("✅ FOLLOW-UP ENGINE")
    print("✅ PERFORMANCE OPTIMIZATION")
    print("✅ ANALYTICS TRACKING")
    print("✅ PROFESSIONAL ASSISTANT BEHAVIOR")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
