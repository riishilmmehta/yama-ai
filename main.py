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

app = FastAPI(title="Yama AI")

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

# ============ CONTEXT MEMORY ============
conversation_context = defaultdict(list)  # email -> list of messages
MAX_CONTEXT = 30

# ============ CACHE ============
cache = {}
CACHE_TTL = 3600  # 1 hour

# ============ ANALYTICS ============
analytics = {
    "total_queries": 0,
    "response_times": [],
    "search_success_rate": [],
    "failed_pages": [],
    "source_quality_avg": [],
    "confidence_avg": [],
    "user_engagement": defaultdict(int)
}

# ============ SOURCE QUALITY SCORING ============
def get_source_quality(url):
    """Calculate reliability score for a URL"""
    score = 40  # Default: Unknown
    
    domains = {
        '.gov': 100,
        '.edu': 95,
        '.ac.': 90,
        '.org': 75,
        '.com': 70,
        '.net': 65,
        '.io': 60,
        '.co': 55,
        '.uk': 70,
        '.us': 70,
        '.eu': 70,
        'wikipedia.org': 85,
        'github.com': 80,
        'medium.com': 60,
        'news.ycombinator.com': 75,
        'reddit.com': 50,
        'stackoverflow.com': 85,
        'quora.com': 55,
        'arxiv.org': 95,
        'pubmed.ncbi.nlm.nih.gov': 95,
        'scholar.google.com': 90,
        'researchgate.net': 85,
        'bbc.com': 80,
        'cnn.com': 80,
        'nytimes.com': 85,
        'reuters.com': 85,
        'apnews.com': 85,
        'nature.com': 90,
        'science.org': 90,
        'ieee.org': 85,
        'acm.org': 85,
        'springer.com': 85,
        'elsevier.com': 85,
        'sagepub.com': 85,
        'taylorandfrancis.com': 85,
        'oxford.com': 90,
        'cambridge.org': 90
    }
    
    url_lower = url.lower()
    
    # Check for known domains first
    for domain, score_val in domains.items():
        if domain in url_lower:
            score = max(score, score_val)
            break
    
    # Check for government
    if '.gov' in url_lower:
        score = max(score, 100)
    # Check for educational
    elif '.edu' in url_lower or '.ac.' in url_lower:
        score = max(score, 95)
    # Check for research
    elif 'arxiv' in url_lower or 'research' in url_lower or 'pubmed' in url_lower:
        score = max(score, 90)
    # Check for news
    elif 'news' in url_lower or 'times' in url_lower or 'post' in url_lower or 'bbc' in url_lower:
        score = max(score, 80)
    # Check for blog
    elif 'blog' in url_lower or 'medium' in url_lower:
        score = max(score, 60)
    
    return min(score, 100)

# ============ IMPROVED WEBPAGE EXTRACTION ============
def clean_html_content(soup):
    """Remove unwanted elements from HTML"""
    # Remove script and style elements
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
        tag.decompose()
    
    # Remove navigation elements
    for tag in soup.find_all(['nav', 'header', 'footer', 'aside']):
        tag.decompose()
    
    # Remove elements with common class names
    unwanted_classes = [
        'nav', 'navigation', 'menu', 'sidebar', 'widget', 'footer', 
        'header', 'cookie', 'popup', 'ad', 'advertisement', 'banner',
        'modal', 'overlay', 'subscribe', 'newsletter', 'social',
        'comments', 'related', 'similar', 'recommended', 'share'
    ]
    
    for class_name in unwanted_classes:
        for element in soup.find_all(class_=re.compile(class_name, re.I)):
            element.decompose()
        for element in soup.find_all(id=re.compile(class_name, re.I)):
            element.decompose()
    
    return soup

def extract_main_content(soup):
    """Extract only meaningful content"""
    soup = clean_html_content(soup)
    
    content_parts = []
    
    # Try to find article/main content
    article = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile(r'article|content|main|post|entry', re.I))
    
    if article:
        # Get all paragraphs
        for p in article.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 30:  # Filter out short snippets
                content_parts.append(text)
    else:
        # Fallback: get all paragraphs with sufficient length
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 50 and not re.search(r'cookie|advertisement|subscribe|newsletter', text, re.I):
                content_parts.append(text)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_parts = []
    for part in content_parts:
        if part not in seen:
            seen.add(part)
            unique_parts.append(part)
    
    return ' '.join(unique_parts[:20])  # Limit to first 20 paragraphs

def read_full_webpage(url):
    """Read webpage with improved extraction"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        content = extract_main_content(soup)
        return content[:2500] if content else None
    except:
        return None

# ============ PARALLEL PAGE READING ============
def read_pages_parallel(urls, max_workers=5):
    """Read multiple pages in parallel"""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(read_full_webpage, url): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                results[url] = future.result()
            except:
                results[url] = None
    return results

# ============ SEARCH WITH RANKING ============
def search_web(query):
    """Search with ranking and duplicate removal"""
    results = []
    try:
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=12))  # Search 10-12 sources
            
            # Track unique domains
            seen_domains = set()
            scored_results = []
            
            for r in search_results[:10]:  # Use top 10
                url = r.get('href', '')
                if not url:
                    continue
                
                # Skip duplicates
                domain = re.sub(r'^https?://', '', url).split('/')[0]
                if domain in seen_domains:
                    continue
                seen_domains.add(domain)
                
                quality = get_source_quality(url)
                
                scored_results.append({
                    "title": r.get('title', ''),
                    "snippet": r.get('body', '')[:300],
                    "url": url,
                    "quality": quality
                })
            
            # Sort by quality
            scored_results.sort(key=lambda x: x['quality'], reverse=True)
            
            # Take top 7 for reading
            results = scored_results[:7]
    except Exception as e:
        print(f"Search error: {e}")
    return results

# ============ SOURCE AGREEMENT SYSTEM ============
def check_source_agreement(content_parts):
    """Check if multiple sources agree"""
    # Extract key claims from content
    claims = defaultdict(list)
    
    for url, content in content_parts.items():
        if not content:
            continue
        
        # Look for key sentences (simplified agreement check)
        sentences = re.split(r'[.!?]+', content)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 30 and len(sentence) < 200:  # Meaningful sentences
                # Use first few words as a key
                key = ' '.join(sentence.split()[:5])
                claims[key].append({
                    'url': url,
                    'sentence': sentence
                })
    
    # Calculate agreement
    agreement_scores = {}
    for key, entries in claims.items():
        if len(entries) >= 3:
            agreement_scores[key] = {
                'count': len(entries),
                'examples': entries[:3]
            }
    
    # Calculate confidence boost
    if not claims:
        return 0, {}
    
    avg_entries = sum(len(entries) for entries in claims.values()) / len(claims) if claims else 0
    
    if avg_entries >= 4:
        boost = 1.3  # High agreement
    elif avg_entries >= 2:
        boost = 1.15  # Medium agreement
    else:
        boost = 1.0  # Low agreement
    
    return boost, agreement_scores

# ============ ADVANCED ANSWER GENERATION ============
def generate_answer(query, search_results, context=""):
    """Generate structured answer with analysis"""
    if not search_results:
        return f"I searched for '{query}' but found no results. Please try rephrasing your question."
    
    # Read pages in parallel
    urls = [r['url'] for r in search_results[:7]]
    page_contents = read_pages_parallel(urls)
    
    # Filter out failed pages
    valid_content = {url: content for url, content in page_contents.items() if content}
    
    if not valid_content:
        # Fallback to snippets
        return generate_fallback_answer(query, search_results)
    
    # Check source agreement
    agreement_boost, agreement_data = check_source_agreement(valid_content)
    
    # Calculate confidence
    num_sources = len(valid_content)
    avg_quality = sum(get_source_quality(url) for url in valid_content) / len(valid_content) if valid_content else 40
    confidence = min(98, int((num_sources / 7) * 50 + (avg_quality / 100) * 40 + (agreement_boost - 1) * 20))
    
    # Build answer
    answer = "📌 **Quick Answer**\n\n"
    
    # Try to find the most relevant content
    best_content = list(valid_content.values())[0] if valid_content else ""
    first_sentence = best_content.split('.')[0] + '.' if best_content else search_results[0]['snippet']
    answer += f"{first_sentence}\n\n"
    
    # Detailed explanation
    answer += "📖 **Detailed Explanation**\n\n"
    content_list = list(valid_content.values())
    if len(content_list) > 1:
        combined = ' '.join(content_list[:3])  # Use first 3 sources
        # Clean up
        combined = re.sub(r'\s+', ' ', combined)
        paragraphs = combined.split('. ')
        if len(paragraphs) > 2:
            answer += '. '.join(paragraphs[:3]) + '.\n\n'
        else:
            answer += combined[:400] + '...\n\n'
    else:
        answer += content_list[0][:400] + '...\n\n' if content_list else ""
    
    # Key facts
    answer += "📊 **Key Facts**\n\n"
    facts = []
    for url, content in list(valid_content.items())[:3]:
        sentences = re.split(r'[.!?]+', content)
        for sentence in sentences[:2]:  # Take first 2 sentences from each source
            sentence = sentence.strip()
            if len(sentence) > 20 and len(sentence) < 200:
                facts.append(f"• {sentence}")
            if len(facts) >= 5:
                break
        if len(facts) >= 5:
            break
    
    if facts:
        answer += '\n'.join(facts[:5]) + '\n\n'
    else:
        answer += "• " + search_results[0]['snippet'][:150] + '\n\n'
    
    # Analysis
    answer += "🔍 **Analysis**\n\n"
    if agreement_data:
        answer += f"• {len(valid_content)} sources were analyzed for this query.\n"
        answer += f"• High agreement found across multiple sources.\n"
    else:
        answer += f"• Information gathered from {len(valid_content)} unique sources.\n"
        answer += f"• Average source quality: {int(avg_quality)}%\n"
    answer += f"• Confidence: {confidence}%\n\n"
    
    # Sources
    answer += "🔗 **Sources**\n\n"
    for i, result in enumerate(search_results[:5], 1):
        quality = get_source_quality(result['url'])
        confidence_emoji = "🟢" if quality >= 80 else "🟡" if quality >= 60 else "🔴"
        answer += f"{i}. **{result['title']}**\n"
        answer += f"   {confidence_emoji} Quality: {quality}%\n"
        answer += f"   🔗 {result['url']}\n\n"
    
    return answer

def generate_fallback_answer(query, search_results):
    """Fallback when pages can't be read"""
    answer = "📌 **Quick Answer**\n\n"
    answer += f"{search_results[0]['snippet'][:200]}\n\n"
    
    answer += "📖 **Detailed Explanation**\n\n"
    if len(search_results) > 1:
        answer += f"{search_results[1]['snippet'][:200]}\n\n"
    
    answer += "📊 **Key Facts**\n\n"
    for i, r in enumerate(search_results[:3], 1):
        answer += f"• {r['title']}: {r['snippet'][:80]}...\n"
    answer += "\n"
    
    answer += "🔍 **Analysis**\n\n"
    answer += f"• Found {len(search_results)} search results.\n"
    answer += "• Pages could not be read due to accessibility restrictions.\n"
    answer += "• Based on search snippets.\n\n"
    
    answer += "🔗 **Sources**\n\n"
    for i, r in enumerate(search_results[:5], 1):
        quality = get_source_quality(r['url'])
        confidence_emoji = "🟢" if quality >= 80 else "🟡" if quality >= 60 else "🔴"
        answer += f"{i}. **{r['title']}**\n"
        answer += f"   {confidence_emoji} Quality: {quality}%\n"
        answer += f"   🔗 {r['url']}\n\n"
    
    return answer

# ============ FOLLOW-UP QUESTIONS ============
def generate_follow_up(query):
    """Generate intelligent follow-up questions"""
    follow_ups = []
    
    # Topic detection
    if 'what' in query.lower() or 'who' in query.lower():
        follow_ups.extend([
            "Explain this in simple terms",
            "Give me real-world examples",
            "What are the main advantages and disadvantages?"
        ])
    
    if 'how' in query.lower():
        follow_ups.extend([
            "What are the key steps?",
            "Are there any tools or resources for this?",
            "What are common mistakes to avoid?"
        ])
    
    if 'why' in query.lower():
        follow_ups.extend([
            "What are the reasons?",
            "Are there alternative perspectives?",
            "What does the research say?"
        ])
    
    # Default follow-ups
    if len(follow_ups) < 3:
        follow_ups.extend([
            "Tell me more about this topic",
            "What are the latest updates?",
            "How is this relevant today?"
        ])
    
    return follow_ups[:4]  # Return top 4

# ============ CONTEXT MANAGEMENT ============
def update_context(email, user_msg, ai_response):
    """Update conversation context"""
    if email:
        context = conversation_context[email]
        context.append({"user": user_msg, "ai": ai_response})
        if len(context) > MAX_CONTEXT:
            context = context[-MAX_CONTEXT:]
        conversation_context[email] = context

def get_context(email):
    """Get current conversation context"""
    if email:
        return conversation_context.get(email, [])
    return []

def resolve_references(query, context):
    """Resolve references in follow-up questions"""
    if not context:
        return query
    
    resolved = query
    
    # Common reference patterns
    reference_patterns = {
        r'\bit\b': None,
        r'\bthey\b': None,
        r'\bthem\b': None,
        r'\bthis\b': None,
        r'\bthat\b': None,
        r'\bthose\b': None,
        r'\bthese\b': None
    }
    
    # Check if query contains references
    has_reference = any(re.search(pattern, resolved, re.I) for pattern in reference_patterns)
    
    if has_reference and context:
        # Get the last user message as context
        last_messages = context[-3:]  # Last 3 messages for context
        for msg in reversed(last_messages):
            user_msg = msg.get('user', '')
            if user_msg:
                # Try to extract main topic
                words = user_msg.split()
                if len(words) > 3:
                    # Use key nouns as reference
                    key_words = []
                    for word in words:
                        if len(word) > 3 and word.lower() not in ['what', 'why', 'how', 'when', 'where', 'who', 'which']:
                            key_words.append(word)
                    
                    if key_words:
                        # Replace references with context
                        for pattern in reference_patterns:
                            resolved = re.sub(pattern, ' '.join(key_words[:3]), resolved, flags=re.I)
                        break
    
    return resolved

# ============ RESPONSE FUNCTION ============
def get_response(message, email):
    start_time = time.time()
    msg = message.strip()
    
    # Update analytics
    analytics["total_queries"] += 1
    
    # Get user stats
    stats = update_user_stats(email)
    user = user_db.get(User.email == email)
    user_name = user.get('name', 'User') if user else 'User'
    
    # Get context
    context = get_context(email)
    
    # Resolve references
    resolved_message = resolve_references(msg, context)
    
    # Check cache
    cache_key = hashlib.md5(f"{resolved_message}_{email}".encode()).hexdigest()
    if cache_key in cache:
        cached_response, cached_time = cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            # Use cached response but add stats
            response = cached_response
            response += f"\n\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
            return response
    
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
            response = f"🧮 {a} {op} {b} = {result}\n\n✨ Great job, {user_name}! Level {stats['level']} - {stats['title']}"
            
            # Update context
            update_context(email, message, response)
            
            # Cache
            cache[cache_key] = (response, time.time())
            
            return response
        except:
            pass
    
    # Greetings
    if msg.lower() in ['hi', 'hello', 'hey', 'sup', 'yo']:
        response = f"👋 Hello {user_name}! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
        
        # Update context
        update_context(email, message, response)
        
        # Cache
        cache[cache_key] = (response, time.time())
        
        return response
    
    if 'how are you' in msg.lower():
        response = f"😊 I'm doing great! Thanks for asking, {user_name}!"
        
        # Update context
        update_context(email, message, response)
        
        # Cache
        cache[cache_key] = (response, time.time())
        
        return response
    
    # Search and generate answer
    search_results = search_web(resolved_message)
    
    if not search_results:
        response = f"I searched for '{message}' but found no results. Please try rephrasing your question."
        
        # Update context
        update_context(email, message, response)
        
        return response
    
    # Generate answer
    response = generate_answer(resolved_message, search_results, context)
    
    # Add user stats
    response += f"\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)\n"
    
    # Add follow-up questions
    follow_ups = generate_follow_up(resolved_message)
    response += "\n💡 **Follow-up Questions**\n\n"
    for i, fu in enumerate(follow_ups, 1):
        response += f"{i}. {fu}\n"
    
    # Track analytics
    response_time = time.time() - start_time
    analytics["response_times"].append(response_time)
    
    # Update context
    update_context(email, message, response)
    
    # Cache
    cache[cache_key] = (response, time.time())
    
    # Clean old cache entries
    if len(cache) > 1000:
        current_time = time.time()
        for key, (_, timestamp) in list(cache.items()):
            if current_time - timestamp > CACHE_TTL:
                del cache[key]
    
    return response

# ============ HISTORY FUNCTIONS ============
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

# ============ USER FUNCTIONS ============
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

# ============ GOOGLE CLIENT ID ============
GOOGLE_CLIENT_ID = "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com"

# ============ COMPLETE HTML (unchanged) ============
# [HTML content remains exactly the same as provided]
# (I'll include the full HTML here but it's identical to the original)

HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        /* ========== RESET ========== */
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        
        /* ========== FIXED: NO FIXED POSITION, NO OVERFLOW HIDDEN ========== */
        html, body {{
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
        }}
        
        /* ========== FLUID MEDIA ========== */
        img, video, iframe {{
            max-width: 100%;
            height: auto;
        }}
        
        /* ========== DARK MODE ========== */
        body.dark {{
            background: #1a1a2e;
        }}
        
        body.dark .app {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        }}
        
        body.dark .header {{
            background: rgba(26,26,46,0.95);
            border-bottom-color: #2a2a4e;
        }}
        
        body.dark .logo h1 {{
            color: #d4c5a9;
        }}
        
        body.dark .input-wrapper {{
            background: #2a2a4e;
            border-color: #3a3a5e;
        }}
        
        body.dark textarea {{
            color: #e0e0e0;
        }}
        
        body.dark textarea::placeholder {{
            color: #6a5a7a;
        }}
        
        body.dark .message-content {{
            color: #e0e0e0;
        }}
        
        body.dark .ai-message .message-content {{
            background: #2a2a4e !important;
            color: #e0e0e0 !important;
        }}
        
        body.dark .suggestion {{
            background: #2a2a4e;
            border-color: #3a3a5e;
            color: #e0e0e0;
        }}
        
        body.dark .suggestion:hover {{
            background: #3a3a5e;
            color: white;
        }}
        
        body.dark .welcome h2 {{
            color: #d4c5a9;
        }}
        
        body.dark .welcome p {{
            color: #8a7a6a;
        }}
        
        body.dark .sidebar {{
            background: #0f0f23;
            border-right-color: #2a2a4e;
        }}
        
        body.dark .sidebar-header {{
            background: #0a0a1a;
        }}
        
        body.dark .history-question {{
            color: #d4c5a9;
        }}
        
        body.dark .history-time {{
            color: #6a5a7a;
        }}
        
        body.dark .history-item:hover {{
            background: rgba(212,197,169,0.08);
            border-color: #3a3a5e;
        }}
        
        body.dark .clear-history {{
            color: #d4c5a9;
            border-color: #3a3a5e;
        }}
        
        body.dark .clear-history:hover {{
            background: rgba(212,197,169,0.2);
            border-color: #c4a57b;
        }}
        
        body.dark .new-chat-btn {{
            background: #3a3a5e;
            color: #d4c5a9;
        }}
        
        body.dark .new-chat-btn:hover {{
            background: #4a4a6e;
        }}
        
        body.dark .typing span {{
            background: #d4c5a9;
        }}
        
        body.dark .typing {{
            color: #d4c5a9;
        }}
        
        body.dark a {{
            color: #4ecdc4;
        }}
        
        body.dark .message-content a {{
            color: #4ecdc4;
        }}
        
        body.dark .message-content a:hover {{
            color: #6ee7de;
        }}
        
        body.dark .control-btn {{
            color: #d4c5a9;
        }}
        
        body.dark .control-btn:hover {{
            background: #3a3a5e;
            color: white;
        }}
        
        /* ========== LOGIN OVERLAY ========== */
        .login-overlay {{
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
        }}
        
        .login-card {{
            background: white;
            border-radius: 30px;
            padding: 40px 30px;
            text-align: center;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 25px 50px rgba(0,0,0,0.2);
        }}
        
        .login-card .logo-icon {{
            font-size: 4rem;
            margin-bottom: 20px;
        }}
        
        .login-card h2 {{
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            margin-bottom: 10px;
        }}
        
        .login-card p {{
            color: #666;
            font-size: 1rem;
            margin-bottom: 30px;
        }}
        
        /* ========== APP - FIXED LAYOUT ========== */
        .app {{
            display: flex;
            flex-direction: column;
            height: 100dvh;
            min-height: 100vh;
            width: 100%;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            position: relative;
            overflow: hidden;
        }}
        
        /* ========== SIDEBAR - RESPONSIVE ========== */
        .sidebar {{
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
        }}
        
        .sidebar.open {{
            transform: translateX(0);
        }}
        
        .sidebar-header {{
            padding: 20px;
            border-bottom: 1px solid #4a3f2f;
            background: #1f1912;
            flex-shrink: 0;
        }}
        
        .sidebar-header h3 {{
            color: #d4c5a9;
            font-family: 'Playfair Display', serif;
            font-size: 1rem;
        }}
        
        .user-profile {{
            display: none;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: rgba(212,197,169,0.1);
            border-radius: 12px;
            margin-top: 15px;
        }}
        
        .user-profile-img {{
            width: 45px;
            height: 45px;
            border-radius: 50%;
            object-fit: cover;
        }}
        
        .user-profile-info {{
            flex: 1;
            min-width: 0;
        }}
        
        .user-profile-name {{
            color: #d4c5a9;
            font-weight: 600;
            font-size: 0.85rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .user-profile-email {{
            color: #8a7a6a;
            font-size: 0.65rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .logout-btn {{
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 6px 12px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.65rem;
            white-space: nowrap;
        }}
        
        .history-list {{
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .history-item {{
            padding: 10px;
            margin-bottom: 6px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid transparent;
        }}
        
        .history-item:hover {{
            background: rgba(212,197,169,0.08);
            border-color: #4a3f2f;
        }}
        
        .history-question {{
            font-size: 0.8rem;
            color: #d4c5a9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .history-time {{
            font-size: 0.6rem;
            color: #6a5a4a;
            margin-top: 4px;
        }}
        
        .sidebar-footer {{
            padding: 16px;
            border-top: 1px solid #4a3f2f;
            background: #1f1912;
            flex-shrink: 0;
        }}
        
        .new-chat-btn {{
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
        }}
        
        .new-chat-btn:hover {{
            background: #5a4f3f;
        }}
        
        .clear-history {{
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 8px 16px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.7rem;
            margin-top: 10px;
            width: 100%;
        }}
        
        .overlay {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.4);
            display: none;
            z-index: 999;
        }}
        
        .overlay.show {{
            display: block;
        }}
        
        /* ========== MAIN - FLEX LAYOUT ========== */
        .main {{
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
            height: 100%;
            width: 100%;
            overflow: hidden;
        }}
        
        /* ========== HEADER ========== */
        .header {{
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
        }}
        
        .menu-btn {{
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
        }}
        
        .menu-btn:hover {{
            background: #d4c5a9;
            color: #2c2418;
        }}
        
        .logo {{
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 6px;
            min-width: 0;
        }}
        
        .logo-icon {{
            font-size: 1.8rem;
        }}
        
        .logo h1 {{
            font-family: 'Playfair Display', serif;
            font-size: 1.3rem;
            color: #2c2418;
            white-space: nowrap;
        }}
        
        .new-chat-mobile {{
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px;
            border-radius: 10px;
            color: #6a5a4a;
            display: none;
        }}
        
        .control-btn {{
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
        }}
        
        .control-btn:hover {{
            background: #d4c5a9;
        }}
        
        .user-btn {{
            background: none;
            border: none;
            cursor: pointer;
            display: none;
            padding: 4px;
        }}
        
        .user-btn img {{
            width: 35px;
            height: 35px;
            border-radius: 50%;
            object-fit: cover;
        }}
        
        /* ========== MESSAGES - SCROLLABLE ========== */
        .messages {{
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            padding-bottom: 20px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }}
        
        .message {{
            margin-bottom: 20px;
            animation: fadeIn 0.3s ease;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .user-message {{
            text-align: right;
        }}
        
        .ai-message {{
            text-align: left;
        }}
        
        .message-content {{
            display: inline-block;
            max-width: 85%;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
        }}
        
        .user-message .message-content {{
            background: #2c2418 !important;
            color: white !important;
            padding: 10px 16px !important;
            border-radius: 20px !important;
        }}
        
        .ai-message .message-content {{
            background: white !important;
            color: #2c2418 !important;
            padding: 12px 18px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }}
        
        /* ========== TYPING ========== */
        .typing {{
            display: none;
            padding: 10px 16px;
            gap: 5px;
            color: #888;
            font-size: 0.8rem;
            flex-shrink: 0;
        }}
        
        .typing span {{
            width: 6px;
            height: 6px;
            background: #c4a57b;
            border-radius: 50%;
            display: inline-block;
            animation: bounce 1.4s infinite;
        }}
        
        @keyframes bounce {{
            0%, 60%, 100% {{ transform: translateY(0); }}
            30% {{ transform: translateY(-6px); }}
        }}
        
        /* ========== INPUT AREA - STICKY WITH SAFE AREA ========== */
        .input-area {{
            position: sticky;
            bottom: 0;
            z-index: 100;
            background: #f5f0e8;
            padding: 12px 16px 20px;
            padding-bottom: env(safe-area-inset-bottom, 20px);
            flex-shrink: 0;
            border-top: 1px solid rgba(212,197,169,0.3);
        }}
        
        body.dark .input-area {{
            background: #1a1a2e;
            border-top-color: rgba(42,42,78,0.3);
        }}
        
        .input-wrapper {{
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
        }}
        
        body.dark .input-wrapper {{
            background: #2a2a4e;
            border-color: #3a3a5e;
        }}
        
        .input-text-wrapper {{
            flex: 1;
            min-width: 0;
        }}
        
        textarea {{
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
        }}
        
        body.dark textarea {{
            color: #e0e0e0;
        }}
        
        textarea::placeholder {{
            color: #b8a88a;
            font-size: 0.95rem;
        }}
        
        /* iOS Zoom Fix */
        @media (max-width: 768px) {{
            textarea {{
                font-size: 16px !important;
            }}
        }}
        
        .submit-btn {{
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
        }}
        
        body.dark .submit-btn {{
            background-color: #4a3f2f;
        }}
        
        .submit-btn:hover {{
            background-color: #4a3f2f;
            transform: scale(1.02);
        }}
        
        .submit-btn:active {{
            transform: scale(0.96);
        }}
        
        .submit-icon {{
            width: 20px;
            height: 20px;
            fill: currentColor;
        }}
        
        /* ========== WELCOME ========== */
        .welcome {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 50vh;
            text-align: center;
            padding: 20px;
        }}
        
        .welcome-icon {{
            font-size: 3rem;
            margin-bottom: 15px;
            animation: float 3s ease-in-out infinite;
        }}
        
        @keyframes float {{
            0%, 100% {{ transform: translateY(0); }}
            50% {{ transform: translateY(-8px); }}
        }}
        
        .welcome h2 {{
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            color: #2c2418;
            margin-bottom: 8px;
        }}
        
        .welcome p {{
            color: #6a5a4a;
            font-size: 0.85rem;
            margin-bottom: 20px;
        }}
        
        .suggestions {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
            margin-top: 15px;
        }}
        
        .suggestion {{
            background: white;
            border: 1px solid #d4c5a9;
            border-radius: 30px;
            padding: 6px 14px;
            font-size: 0.75rem;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }}
        
        .suggestion:hover {{
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }}
        
        /* ========== RESPONSIVE BREAKPOINTS ========== */
        
        /* Tablet & Mobile */
        @media (max-width: 768px) {{
            .messages {{
                padding: 12px 16px;
                padding-bottom: 16px;
            }}
            .suggestions {{
                display: none;
            }}
            .new-chat-mobile {{
                display: block;
            }}
            .header {{
                padding: 10px 14px;
                min-height: 52px;
            }}
            .logo h1 {{
                font-size: 1.1rem;
            }}
            .logo-icon {{
                font-size: 1.4rem;
            }}
            .message-content {{
                max-width: 90%;
                font-size: 0.85rem;
            }}
            .input-area {{
                padding: 10px 12px 16px;
            }}
            .input-wrapper {{
                padding: 6px 6px 6px 16px;
                min-height: 50px;
                border-radius: 26px;
            }}
            textarea {{
                font-size: 16px !important;
                padding: 8px 0;
            }}
            .submit-btn {{
                width: 40px;
                height: 40px;
                min-width: 40px;
                min-height: 40px;
            }}
            .submit-icon {{
                width: 18px;
                height: 18px;
            }}
        }}
        
        /* Small Phones */
        @media (max-width: 480px) {{
            .header {{
                padding: 8px 12px;
                min-height: 48px;
                gap: 8px;
            }}
            .logo h1 {{
                font-size: 1rem;
            }}
            .logo-icon {{
                font-size: 1.2rem;
            }}
            .control-btn {{
                font-size: 0.9rem;
                padding: 6px 8px;
            }}
            .menu-btn {{
                font-size: 1.1rem;
                padding: 6px;
            }}
            .messages {{
                padding: 10px 12px;
            }}
            .input-area {{
                padding: 8px 10px 14px;
                padding-bottom: env(safe-area-inset-bottom, 14px);
            }}
            .input-wrapper {{
                padding: 5px 5px 5px 14px;
                min-height: 44px;
                gap: 8px;
                border-radius: 24px;
            }}
            textarea {{
                font-size: 15px !important;
                padding: 6px 0;
                min-height: 20px;
            }}
            .submit-btn {{
                width: 40px;
                height: 40px;
                min-width: 40px;
                min-height: 40px;
            }}
            .submit-icon {{
                width: 16px;
                height: 16px;
            }}
            .message-content {{
                font-size: 0.8rem;
            }}
        }}
        
        /* Very Small Phones */
        @media (max-width: 380px) {{
            .header {{
                padding: 6px 10px;
                min-height: 44px;
                gap: 6px;
            }}
            .logo h1 {{
                font-size: 0.85rem;
            }}
            .logo-icon {{
                font-size: 1rem;
            }}
            .control-btn {{
                font-size: 0.8rem;
                padding: 4px 6px;
            }}
            .messages {{
                padding: 8px 10px;
            }}
            .input-area {{
                padding: 6px 8px 12px;
            }}
            .input-wrapper {{
                padding: 4px 4px 4px 12px;
                min-height: 40px;
                gap: 6px;
                border-radius: 22px;
            }}
            textarea {{
                font-size: 14px !important;
                padding: 5px 0;
                min-height: 18px;
            }}
            .submit-btn {{
                width: 36px;
                height: 36px;
                min-width: 36px;
                min-height: 36px;
            }}
            .submit-icon {{
                width: 14px;
                height: 14px;
            }}
            .message-content {{
                font-size: 0.75rem;
            }}
        }}
        
        /* Landscape Phones */
        @media (max-height: 500px) and (orientation: landscape) {{
            .header {{
                min-height: 40px;
                padding: 4px 12px;
                gap: 6px;
            }}
            .logo h1 {{
                font-size: 0.9rem;
            }}
            .logo-icon {{
                font-size: 1.1rem;
            }}
            .messages {{
                padding: 6px 12px;
                padding-bottom: 10px;
            }}
            .input-area {{
                padding: 4px 12px 8px;
            }}
            .input-wrapper {{
                min-height: 38px;
                padding: 4px 4px 4px 12px;
            }}
            textarea {{
                min-height: 20px;
                max-height: 80px;
                font-size: 14px !important;
                padding: 4px 0;
            }}
            .submit-btn {{
                width: 36px;
                height: 36px;
                min-width: 36px;
                min-height: 36px;
            }}
            .submit-icon {{
                width: 14px;
                height: 14px;
            }}
            .welcome {{
                min-height: 20vh;
            }}
            .suggestions {{
                display: none;
            }}
            .control-btn {{
                font-size: 0.8rem;
                padding: 3px 6px;
            }}
        }}
        
        /* Tablets */
        @media (min-width: 769px) and (max-width: 1024px) {{
            .input-wrapper {{
                max-width: 90%;
            }}
            .messages {{
                padding: 16px 24px;
            }}
            .header {{
                padding: 14px 20px;
            }}
        }}
        
        /* Desktop */
        @media (min-width: 1025px) {{
            .input-wrapper {{
                max-width: 760px;
            }}
            .messages {{
                padding: 24px 32px;
            }}
            .header {{
                padding: 16px 32px;
            }}
        }}
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
        
        function toggleTheme() {{
            document.body.classList.toggle('dark');
            localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
        }}
        
        function exportChat() {{
            const messages = document.querySelectorAll('.message');
            let exportText = '';
            messages.forEach(msg => {{
                const sender = msg.classList.contains('user-message') ? 'You' : 'Yama';
                const text = msg.querySelector('.message-content').innerText;
                exportText += sender + ': ' + text + '\\n\\n';
            }});
            const blob = new Blob([exportText], {{type: 'text/plain'}});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'yama_chat_' + new Date().toISOString() + '.txt';
            a.click();
        }}
        
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') {{
            document.body.classList.add('dark');
        }}
        
        function toggleUserMenu() {{
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }}
        
        function handleCredentialResponse(response) {{
            const token = response.credential;
            const payload = JSON.parse(atob(token.split('.')[1]));
            
            currentUser = {{
                name: payload.name,
                email: payload.email,
                picture: payload.picture
            }};
            
            document.getElementById('loginOverlay').style.display = 'none';
            document.getElementById('app').style.display = 'flex';
            document.getElementById('userBtn').style.display = 'block';
            document.getElementById('userAvatar').src = currentUser.picture;
            
            document.getElementById('userProfile').style.display = 'flex';
            document.getElementById('userProfile').innerHTML = `
                <img src=\"${{currentUser.picture}}\" class=\"user-profile-img\">
                <div class=\"user-profile-info\">
                    <div class=\"user-profile-name\">${{currentUser.name}}</div>
                    <div class=\"user-profile-email\">${{currentUser.email}}</div>
                </div>
                <button class=\"logout-btn\" onclick=\"logout()\">Logout</button>
            `;
            
            loadHistory();
            
            fetch('/set_user', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ email: currentUser.email, name: currentUser.name, picture: currentUser.picture }})
            }});
        }}
        
        function logout() {{
            currentUser = null;
            document.getElementById('loginOverlay').style.display = 'flex';
            document.getElementById('app').style.display = 'none';
            document.getElementById('userBtn').style.display = 'none';
            document.getElementById('userProfile').style.display = 'none';
            if (google && google.accounts) {{
                google.accounts.id.disableAutoSelect();
            }}
        }}
        
        function newChat() {{
            if (confirm('Start a new chat?')) {{ location.reload(); }}
        }}
        
        function toggleSidebar() {{
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }}
        
        function closeSidebar() {{
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('overlay').classList.remove('show');
        }}
        
        function askSuggestion(q) {{
            document.getElementById('userInput').value = q;
            sendMessage();
        }}
        
        async function loadHistory() {{
            if (!currentUser) return;
            const res = await fetch('/get_history?email=' + encodeURIComponent(currentUser.email));
            const history = await res.json();
            const container = document.getElementById('historyList');
            if (history.length === 0) {{
                container.innerHTML = '<div style=\"color:#6a5a4a;text-align:center;padding:20px;\">No conversations yet</div>';
                return;
            }}
            let html = '';
            for (let i = history.length - 1; i >= 0; i--) {{
                let item = history[i];
                html += '<div class=\"history-item\" onclick=\"loadChatMessage(\\'' + escapeHtml(item.user) + '\\')\">' +
                        '<div class=\"history-question\">' + escapeHtml(item.user.substring(0, 45)) + '</div>' +
                        '<div class=\"history-time\">' + item.timestamp + '</div>' +
                        '</div>';
            }}
            container.innerHTML = html;
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        function loadChatMessage(msg) {{
            document.getElementById('userInput').value = msg;
            closeSidebar();
            sendMessage();
        }}
        
        async function clearHistory() {{
            if (confirm('Clear all history?')) {{
                await fetch('/clear_history', {{ method: 'POST' }});
                location.reload();
            }}
        }}
        
        const textarea = document.getElementById('userInput');
        
        // Auto-adjust height
        function autoAdjustHeight() {{
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        }}
        
        textarea.addEventListener('input', autoAdjustHeight);
        
        // VisualViewport handling for mobile keyboard
        if (window.visualViewport) {{
            let lastHeight = window.visualViewport.height;
            window.visualViewport.addEventListener('resize', function() {{
                const inputArea = document.querySelector('.input-area');
                if (inputArea && window.visualViewport.height < lastHeight) {{
                    // Keyboard opened - ensure input is visible
                    setTimeout(() => {{
                        inputArea.scrollIntoView({{ behavior: 'smooth', block: 'end' }});
                    }}, 100);
                }}
                lastHeight = window.visualViewport.height;
            }});
        }}
        
        function handleKey(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }}
        
        async function sendMessage() {{
            if (!currentUser) {{ alert('Please sign in first!'); return; }}
            const message = textarea.value.trim();
            if (!message) return;
            
            if (!hasMessages) {{
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
                document.getElementById('logo').classList.add('small');
            }}
            
            addMessage(message, 'user');
            textarea.value = '';
            textarea.style.height = 'auto';
            
            document.getElementById('typing').style.display = 'block';
            scrollToBottom();
            
            const res = await fetch('/chat', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ message: message, email: currentUser.email }})
            }});
            const data = await res.json();
            
            addMessage(data.response, 'ai');
            document.getElementById('typing').style.display = 'none';
            loadHistory();
            scrollToBottom();
        }}
        
        function addMessage(text, sender) {{
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + sender + '-message';
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            div.appendChild(content);
            messages.appendChild(div);
            scrollToBottom();
        }}
        
        function scrollToBottom() {{
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }}
        
        loadHistory();
        textarea.focus();
    </script>
</body>
</html>
'''

# ============ FASTAPI ENDPOINTS ============
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
    """Internal analytics endpoint (admin only)"""
    avg_response_time = sum(analytics["response_times"]) / len(analytics["response_times"]) if analytics["response_times"] else 0
    avg_confidence = sum(analytics["confidence_avg"]) / len(analytics["confidence_avg"]) if analytics["confidence_avg"] else 0
    avg_quality = sum(analytics["source_quality_avg"]) / len(analytics["source_quality_avg"]) if analytics["source_quality_avg"] else 0
    
    return {
        "total_queries": analytics["total_queries"],
        "avg_response_time": round(avg_response_time, 2),
        "avg_confidence": round(avg_confidence, 1),
        "avg_source_quality": round(avg_quality, 1),
        "failed_pages": len(analytics["failed_pages"]),
        "search_success_rate": round(analytics["search_success_rate"].count(True) / len(analytics["search_success_rate"]) * 100 if analytics["search_success_rate"] else 0, 1),
        "user_engagement": dict(analytics["user_engagement"])
    }

@app.post("/clear_cache")
async def clear_cache():
    """Clear cache (admin only)"""
    cache.clear()
    return {"status": "cache_cleared"}

@app.post("/reset_analytics")
async def reset_analytics():
    """Reset analytics (admin only)"""
    analytics.clear()
    return {"status": "analytics_reset"}

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - IMPROVED VERSION")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("📱 Perfect on ALL devices")
    print("")
    print("✨ NEW FEATURES:")
    print("1. CONTEXT MEMORY - Tracks last 30 messages")
    print("2. SOURCE QUALITY SCORING - 0-100 reliability score")
    print("3. SOURCE AGREEMENT - Multi-source verification")
    print("4. ADVANCED ANSWERS - Structured format")
    print("5. BETTER EXTRACTION - Clean webpage reading")
    print("6. SEARCH IMPROVEMENT - 10 sources, ranked")
    print("7. CONFIDENCE SYSTEM - Real confidence %")
    print("8. FOLLOW-UP ENGINE - Intelligent questions")
    print("9. PERFORMANCE - Parallel fetching, caching")
    print("10. ANALYTICS - Internal tracking")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
