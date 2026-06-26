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
from urllib.parse import quote, urlparse
from tinydb import TinyDB, Query
import secrets
import asyncio
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from typing import List, Dict, Any, Optional
import urllib.parse

app = FastAPI(title="Yama AI")

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

# ============ CONTEXT MEMORY ============
conversation_context = defaultdict(list)
MAX_CONTEXT = 30

# ============ CACHE ============
cache = {}
CACHE_TTL = 3600

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

# ============ SOURCE CLASSIFICATION ============
def classify_source(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    classification = {"type": "Unknown", "score": 40, "emoji": "🔍"}
    
    if domain.endswith('.gov') or 'gov' in domain:
        classification = {"type": "Government", "score": 100, "emoji": "🏛️"}
    elif domain.endswith('.edu') or '.ac.' in domain:
        classification = {"type": "Educational", "score": 95, "emoji": "🎓"}
    elif any(x in domain for x in ['arxiv', 'pubmed', 'researchgate', 'scholar', 'acm', 'ieee', 'nature', 'science']):
        classification = {"type": "Research", "score": 90, "emoji": "📚"}
    elif any(x in domain for x in ['microsoft', 'apple', 'google', 'amazon', 'github', 'stackoverflow']):
        classification = {"type": "Official", "score": 90, "emoji": "🏢"}
    elif any(x in domain for x in ['news', 'bbc', 'cnn', 'reuters', 'apnews', 'guardian', 'nytimes']):
        classification = {"type": "News", "score": 80, "emoji": "📰"}
    elif any(x in domain for x in ['reddit', 'quora', 'stackexchange', 'wikipedia']):
        classification = {"type": "Community", "score": 60, "emoji": "👥"}
    elif 'blog' in domain or 'wordpress' in domain:
        classification = {"type": "Blog", "score": 50, "emoji": "📝"}
    
    return classification

# ============ CONTENT EXTRACTION ============
def clean_html_content(soup):
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg', 'meta', 'link']):
        tag.decompose()
    
    for tag in soup.find_all(['nav', 'header', 'footer', 'aside']):
        tag.decompose()
    
    noise_patterns = ['cookie', 'popup', 'ad', 'banner', 'modal', 'overlay', 'subscribe', 'newsletter', 'social', 'comments', 'related', 'recommended']
    
    for pattern in noise_patterns:
        for element in soup.find_all(class_=re.compile(pattern, re.I)):
            element.decompose()
        for element in soup.find_all(id=re.compile(pattern, re.I)):
            element.decompose()
    
    return soup

def extract_main_content(soup):
    soup = clean_html_content(soup)
    content_parts = []
    
    article = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile(r'article|content|main|post|entry', re.I))
    
    if article:
        for p in article.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 30:
                content_parts.append(text)
    else:
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 50 and not re.search(r'cookie|advertisement|subscribe', text, re.I):
                content_parts.append(text)
    
    seen = set()
    unique_parts = []
    for part in content_parts:
        if part not in seen and len(part) > 20:
            seen.add(part)
            unique_parts.append(part)
    
    text = ' '.join(unique_parts[:15])
    text = re.sub(r'\s+', ' ', text)
    return text[:3000]

def read_full_webpage(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        content = extract_main_content(soup)
        return content if len(content) > 50 else None
    except:
        return None

# ============ PARALLEL PAGE READING ============
def read_pages_parallel(urls, max_workers=5):
    results = {}
    failed = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(read_full_webpage, url): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                content = future.result(timeout=10)
                if content:
                    results[url] = content
                else:
                    failed.append(url)
            except:
                failed.append(url)
    return results, failed

# ============ SEARCH ============
def search_web(query):
    results = []
    try:
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=15))
            seen_domains = set()
            scored_results = []
            
            for r in search_results[:12]:
                url = r.get('href', '')
                if not url:
                    continue
                domain = re.sub(r'^https?://', '', url).split('/')[0]
                if domain in seen_domains:
                    continue
                seen_domains.add(domain)
                
                classification = classify_source(url)
                scored_results.append({
                    "title": r.get('title', ''),
                    "snippet": r.get('body', '')[:300],
                    "url": url,
                    "domain": domain,
                    "type": classification["type"],
                    "score": classification["score"],
                    "emoji": classification["emoji"]
                })
            
            scored_results.sort(key=lambda x: x['score'], reverse=True)
            results = scored_results[:8]
    except Exception as e:
        print(f"Search error: {e}")
    return results

# ============ SOURCE AGREEMENT ============
def check_source_agreement(content_parts: Dict[str, str]) -> Dict[str, Any]:
    if not content_parts:
        return {"boost": 1.0, "agreement_level": "No Data"}
    
    claims = defaultdict(list)
    for url, content in content_parts.items():
        sentences = re.split(r'[.!?]+', content)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 30 and len(sentence) < 200:
                key = ' '.join(sentence.split()[:5])
                claims[key].append({'url': url, 'sentence': sentence})
    
    agreement_count = sum(1 for entries in claims.values() if len(entries) >= 3)
    total_claims = len(claims)
    agreement_ratio = agreement_count / total_claims if total_claims > 0 else 0
    
    if agreement_ratio >= 0.5:
        return {"boost": 1.3, "agreement_level": "High Agreement"}
    elif agreement_ratio >= 0.3:
        return {"boost": 1.15, "agreement_level": "Medium Agreement"}
    return {"boost": 1.0, "agreement_level": "Low Agreement"}

# ============ CONFIDENCE ============
def calculate_confidence(num_sources, source_scores, agreement_data, content_lengths):
    source_factor = min(num_sources / 8, 1.0) * 30
    avg_quality = sum(source_scores) / len(source_scores) if source_scores else 0
    quality_factor = (avg_quality / 100) * 30
    agreement_boost = agreement_data.get("boost", 1.0)
    agreement_factor = (agreement_boost - 1) * 30 + 15
    avg_length = sum(content_lengths) / len(content_lengths) if content_lengths else 0
    completeness = 1.0 if avg_length > 1000 else 0.8 if avg_length > 500 else 0.5 if avg_length > 200 else 0.3
    completeness_factor = completeness * 15
    
    confidence = min(99, source_factor + quality_factor + agreement_factor + completeness_factor)
    
    level = "Very High" if confidence >= 85 else "High" if confidence >= 70 else "Medium" if confidence >= 55 else "Low" if confidence >= 40 else "Very Low"
    
    return {"score": round(confidence, 1), "level": level}

# ============ RESPONSE GENERATION ============
def generate_structured_answer(query, search_results, page_contents, failed_sources, confidence_data):
    answer = {
        "quick_answer": "",
        "detailed_explanation": "",
        "key_facts": [],
        "analysis": "",
        "sources": [],
        "confidence": confidence_data,
        "follow_up": []
    }
    
    if not page_contents:
        answer["quick_answer"] = f"I searched for '{query}' but found no usable content. Please try rephrasing your question."
        return answer
    
    contents = list(page_contents.values())
    
    if contents:
        first_sentence = contents[0].split('.')[0] + '.'
        answer["quick_answer"] = first_sentence if len(first_sentence) > 20 else contents[0][:200] + '...'
    
    explanation_parts = []
    for content in contents[:3]:
        sentences = content.split('. ')
        if len(sentences) > 3:
            explanation_parts.append('. '.join(sentences[:3]) + '.')
    
    if explanation_parts:
        unique_parts = []
        seen = set()
        for part in explanation_parts:
            if part not in seen:
                seen.add(part)
                unique_parts.append(part)
        answer["detailed_explanation"] = ' '.join(unique_parts[:2])[:800]
    
    facts = set()
    for content in contents[:5]:
        sentences = content.split('. ')
        for sentence in sentences[:3]:
            sentence = sentence.strip()
            if len(sentence) > 20 and len(sentence) < 200:
                facts.add(sentence)
            if len(facts) >= 6:
                break
        if len(facts) >= 6:
            break
    answer["key_facts"] = list(facts)[:6]
    
    source_count = len(page_contents)
    avg_quality = sum(s.get('score', 40) for s in search_results[:source_count]) / source_count if source_count > 0 else 40
    
    analysis_parts = [
        f"• {source_count} sources were successfully analyzed.",
        f"• Average source quality: {round(avg_quality)}%.",
        f"• {len(failed_sources)} sources were unavailable."
    ]
    
    if confidence_data.get('score', 0) > 70:
        analysis_parts.append("• High confidence in the information provided.")
    elif confidence_data.get('score', 0) > 50:
        analysis_parts.append("• Medium confidence - sources partially agree.")
    else:
        analysis_parts.append("• Low confidence - consider verifying with additional sources.")
    
    answer["analysis"] = '\n'.join(analysis_parts)
    
    for result in search_results[:5]:
        answer["sources"].append({
            "title": result.get('title', ''),
            "url": result.get('url', ''),
            "domain": result.get('domain', ''),
            "type": result.get('type', 'Unknown'),
            "score": result.get('score', 40),
            "emoji": result.get('emoji', '🔍')
        })
    
    return answer

# ============ FOLLOW-UP ============
def generate_follow_up(query):
    follow_ups = []
    if 'what' in query.lower() or 'who' in query.lower():
        follow_ups.extend(["Explain in simple terms", "Give real-world examples", "Advantages and disadvantages?"])
    if 'how' in query.lower():
        follow_ups.extend(["What are the key steps?", "Any tools for this?", "Common mistakes?"])
    if 'why' in query.lower():
        follow_ups.extend(["What are the reasons?", "Alternative perspectives?", "What does research say?"])
    if len(follow_ups) < 3:
        follow_ups.extend(["Tell me more", "Latest updates", "How is this relevant?"])
    return follow_ups[:4]

# ============ CONTEXT ============
def update_context(email, user_msg, ai_response):
    if email:
        context = conversation_context[email]
        context.append({"user": user_msg, "ai": ai_response, "timestamp": datetime.now().isoformat()})
        if len(context) > MAX_CONTEXT:
            context = context[-MAX_CONTEXT:]
        conversation_context[email] = context

def get_context(email):
    return conversation_context.get(email, []) if email else []

def resolve_references(query, context):
    if not context:
        return query
    
    resolved = query
    reference_patterns = [r'\bit\b', r'\bthey\b', r'\bthem\b', r'\bthis\b', r'\bthat\b', r'\bthose\b', r'\bthese\b']
    
    has_reference = any(re.search(pattern, resolved, re.I) for pattern in reference_patterns)
    
    if has_reference and context:
        last_messages = []
        for msg in reversed(context):
            if msg.get('user'):
                last_messages.append(msg.get('user'))
            if len(last_messages) >= 3:
                break
        
        if last_messages:
            topic_parts = []
            for msg in last_messages:
                words = msg.split()
                key_words = [w for w in words if len(w) > 3 and w.lower() not in ['what', 'why', 'how', 'when', 'where', 'who', 'which']]
                if key_words:
                    topic_parts.extend(key_words[:3])
            
            if topic_parts:
                topic = ' '.join(topic_parts[:3])
                for pattern in reference_patterns:
                    resolved = re.sub(pattern, topic, resolved, flags=re.I, count=1)
                    break
    
    return resolved

# ============ MAIN RESPONSE ============
def get_response(message, email):
    start_time = time.time()
    msg = message.strip()
    
    analytics["total_queries"] += 1
    
    stats = update_user_stats(email)
    user = user_db.get(User.email == email)
    user_name = user.get('name', 'User') if user else 'User'
    
    context = get_context(email)
    resolved_message = resolve_references(msg, context)
    
    cache_key = hashlib.md5(f"{resolved_message}_{email}".encode()).hexdigest()
    if cache_key in cache:
        cached_response, cached_time = cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            response = cached_response
            response["user_stats"] = {"name": user_name, "level": stats['level'], "title": stats['title'], "count": stats['count']}
            return response
    
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
            
            response = {
                "quick_answer": f"🧮 {a} {op} {b} = {result}",
                "detailed_explanation": f"Great job, {user_name}!",
                "key_facts": [f"{a} {op} {b} = {result}"],
                "analysis": "Simple arithmetic calculation completed.",
                "sources": [],
                "confidence": {"score": 100, "level": "Very High"},
                "user_stats": {"name": user_name, "level": stats['level'], "title": stats['title'], "count": stats['count']}
            }
            update_context(email, message, json.dumps(response))
            cache[cache_key] = (response, time.time())
            return response
        except:
            pass
    
    if msg.lower() in ['hi', 'hello', 'hey', 'sup', 'yo']:
        response = {
            "quick_answer": f"👋 Hello {user_name}!",
            "detailed_explanation": f"You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!",
            "key_facts": [f"Level: {stats['level']}", f"Title: {stats['title']}", f"Messages: {stats['count']}"],
            "analysis": "Ready to assist you.",
            "sources": [],
            "confidence": {"score": 100, "level": "Very High"},
            "user_stats": {"name": user_name, "level": stats['level'], "title": stats['title'], "count": stats['count']}
        }
        update_context(email, message, json.dumps(response))
        cache[cache_key] = (response, time.time())
        return response
    
    if 'how are you' in msg.lower():
        response = {
            "quick_answer": "😊 I'm doing great!",
            "detailed_explanation": f"Thanks for asking, {user_name}!",
            "key_facts": ["Always available to assist", "Powered by Yama AI"],
            "analysis": "Ready and waiting for your questions.",
            "sources": [],
            "confidence": {"score": 100, "level": "Very High"},
            "user_stats": {"name": user_name, "level": stats['level'], "title": stats['title'], "count": stats['count']}
        }
        update_context(email, message, json.dumps(response))
        cache[cache_key] = (response, time.time())
        return response
    
    search_results = search_web(resolved_message)
    
    if not search_results:
        response = {
            "quick_answer": f"I searched for '{message}' but found no results.",
            "detailed_explanation": "Please try rephrasing your question.",
            "key_facts": ["No search results found"],
            "analysis": "The search returned no results.",
            "sources": [],
            "confidence": {"score": 0, "level": "No Data"},
            "user_stats": {"name": user_name, "level": stats['level'], "title": stats['title'], "count": stats['count']}
        }
        update_context(email, message, json.dumps(response))
        return response
    
    urls = [r['url'] for r in search_results[:8]]
    page_contents, failed_sources = read_pages_parallel(urls)
    
    source_scores = [r.get('score', 40) for r in search_results if r['url'] in page_contents]
    agreement_data = check_source_agreement(page_contents)
    content_lengths = [len(content) for content in page_contents.values()]
    confidence_data = calculate_confidence(len(page_contents), source_scores, agreement_data, content_lengths)
    
    answer_data = generate_structured_answer(resolved_message, search_results, page_contents, failed_sources, confidence_data)
    answer_data["follow_up"] = generate_follow_up(resolved_message)
    answer_data["user_stats"] = {"name": user_name, "level": stats['level'], "title": stats['title'], "count": stats['count']}
    
    response_time = time.time() - start_time
    analytics["response_times"].append(response_time)
    analytics["search_success_rate"].append(len(page_contents) > 0)
    analytics["failed_pages"].extend(failed_sources)
    analytics["source_quality_avg"].append(sum(source_scores) / len(source_scores) if source_scores else 0)
    analytics["confidence_avg"].append(confidence_data["score"])
    
    update_context(email, message, json.dumps(answer_data))
    cache[cache_key] = (answer_data, time.time())
    
    if len(cache) > 1000:
        current_time = time.time()
        for key, (_, timestamp) in list(cache.items()):
            if current_time - timestamp > CACHE_TTL:
                del cache[key]
    
    return answer_data

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

# ============ USER ============
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
        titles = {1: "🌟 Newbie Chatter", 2: "💬 Regular Talker", 3: "🔥 Chatty User", 4: "⚡ Power User", 5: "👑 Super Chat Master", 6: "🏆 Ultimate Reviewer", 7: "🧠 Yama Legend"}
        new_title = titles.get(new_level, "🧠 Yama Legend")
        user_db.update({"message_count": new_count, "level": new_level, "title": new_title, "last_seen": datetime.now().isoformat()}, User.email == email)
        return {"count": new_count, "level": new_level, "title": new_title}
    return {"count": 0, "level": 1, "title": "🌟 Newbie Chatter"}

# ============ GOOGLE CLIENT ID ============
GOOGLE_CLIENT_ID = "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com"

# ============ HTML ============
HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        
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
        
        img, video, iframe {{
            max-width: 100%;
            height: auto;
        }}
        
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
        
        .login-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            z-index: 2000;
            display: flex !important;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        
        .login-overlay.hidden {{
            display: none !important;
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
        
        .app {{
            display: none !important;
            flex-direction: column;
            height: 100dvh;
            min-height: 100vh;
            width: 100%;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            position: relative;
            overflow: hidden;
        }}
        
        .app.visible {{
            display: flex !important;
        }}
        
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
        
        .main {{
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
            height: 100%;
            width: 100%;
            overflow: hidden;
        }}
        
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
            line-height: 1.6;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
        }}
        
        .user-message .message-content {{
            background: #2c2418 !important;
            color: white !important;
            padding: 12px 18px !important;
            border-radius: 20px !important;
        }}
        
        .ai-message .message-content {{
            background: white !important;
            color: #2c2418 !important;
            padding: 16px 20px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }}
        
        .source-card {{
            display: inline-block;
            background: #f8f5f0;
            border: 1px solid #e0d8cc;
            border-radius: 12px;
            padding: 12px 16px;
            margin: 6px 0;
            width: 100%;
            max-width: 400px;
            transition: all 0.2s;
        }}
        
        body.dark .source-card {{
            background: #2a2a4e;
            border-color: #3a3a5e;
        }}
        
        .source-card:hover {{
            border-color: #c4a57b;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        
        .source-card-title {{
            font-weight: 600;
            font-size: 0.9rem;
            color: #2c2418;
            margin-bottom: 4px;
        }}
        
        body.dark .source-card-title {{
            color: #d4c5a9;
        }}
        
        .source-card-meta {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.75rem;
            color: #6a5a4a;
            margin-bottom: 8px;
            flex-wrap: wrap;
        }}
        
        body.dark .source-card-meta {{
            color: #8a7a6a;
        }}
        
        .source-card-domain {{
            background: #e8e0d5;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.65rem;
        }}
        
        body.dark .source-card-domain {{
            background: #3a3a5e;
            color: #d4c5a9;
        }}
        
        .source-card-score {{
            font-size: 0.65rem;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 10px;
        }}
        
        .source-card-score.high {{
            background: #d4edda;
            color: #155724;
        }}
        
        .source-card-score.medium {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .source-card-score.low {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        body.dark .source-card-score.high {{
            background: #1e7e34;
            color: #d4edda;
        }}
        
        body.dark .source-card-score.medium {{
            background: #856404;
            color: #fff3cd;
        }}
        
        body.dark .source-card-score.low {{
            background: #721c24;
            color: #f8d7da;
        }}
        
        .source-card-open {{
            display: inline-block;
            background: #2c2418;
            color: white;
            padding: 4px 12px;
            border-radius: 15px;
            font-size: 0.7rem;
            text-decoration: none;
            transition: all 0.2s;
        }}
        
        body.dark .source-card-open {{
            background: #4a3f2f;
        }}
        
        .source-card-open:hover {{
            background: #4a3f2f;
            transform: scale(1.02);
        }}
        
        body.dark .source-card-open:hover {{
            background: #5a4f3f;
        }}
        
        .message-actions {{
            display: flex;
            gap: 8px;
            margin-top: 8px;
            opacity: 0.6;
            transition: opacity 0.2s;
        }}
        
        .message-actions:hover {{
            opacity: 1;
        }}
        
        .message-action-btn {{
            background: none;
            border: 1px solid #d4c5a9;
            border-radius: 15px;
            padding: 4px 12px;
            font-size: 0.7rem;
            color: #6a5a4a;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        
        body.dark .message-action-btn {{
            border-color: #3a3a5e;
            color: #8a7a6a;
        }}
        
        .message-action-btn:hover {{
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }}
        
        body.dark .message-action-btn:hover {{
            background: #4a3f2f;
            color: #d4c5a9;
            border-color: #4a3f2f;
        }}
        
        .follow-ups {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 12px;
        }}
        
        .follow-up-btn {{
            background: #f0ebe4;
            border: 1px solid #d4c5a9;
            border-radius: 20px;
            padding: 4px 14px;
            font-size: 0.7rem;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        body.dark .follow-up-btn {{
            background: #2a2a4e;
            border-color: #3a3a5e;
            color: #d4c5a9;
        }}
        
        .follow-up-btn:hover {{
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }}
        
        body.dark .follow-up-btn:hover {{
            background: #4a3f2f;
            color: #d4c5a9;
        }}
        
        .confidence-indicator {{
            display: inline-block;
            font-size: 0.75rem;
            padding: 3px 12px;
            border-radius: 15px;
            margin: 6px 0;
        }}
        
        .confidence-very-high {{
            background: #d4edda;
            color: #155724;
        }}
        
        .confidence-high {{
            background: #d1ecf1;
            color: #0c5460;
        }}
        
        .confidence-medium {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .confidence-low {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .confidence-very-low {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        body.dark .confidence-very-high {{
            background: #1e7e34;
            color: #d4edda;
        }}
        
        body.dark .confidence-high {{
            background: #0c5460;
            color: #d1ecf1;
        }}
        
        body.dark .confidence-medium {{
            background: #856404;
            color: #fff3cd;
        }}
        
        body.dark .confidence-low {{
            background: #721c24;
            color: #f8d7da;
        }}
        
        body.dark .confidence-very-low {{
            background: #721c24;
            color: #f8d7da;
        }}
        
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
            .source-card {{
                max-width: 100%;
            }}
        }}
        
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
            .source-card {{
                padding: 10px 12px;
            }}
        }}
        
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
                    <p>Your AI research assistant. I search multiple sources, verify information, and provide trustworthy answers.</p>
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
            
            // Save to localStorage
            localStorage.setItem('yama_user', JSON.stringify(currentUser));
            
            // Hide login, show app
            document.getElementById('loginOverlay').classList.add('hidden');
            document.getElementById('app').classList.add('visible');
            
            // Show user button
            document.getElementById('userBtn').style.display = 'block';
            document.getElementById('userAvatar').src = currentUser.picture;
            
            // Show user profile in sidebar
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
            localStorage.removeItem('yama_user');
            
            // Show login, hide app
            document.getElementById('loginOverlay').classList.remove('hidden');
            document.getElementById('app').classList.remove('visible');
            
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
        
        function autoAdjustHeight() {{
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        }}
        
        textarea.addEventListener('input', autoAdjustHeight);
        
        if (window.visualViewport) {{
            let lastHeight = window.visualViewport.height;
            window.visualViewport.addEventListener('resize', function() {{
                const inputArea = document.querySelector('.input-area');
                if (inputArea && window.visualViewport.height < lastHeight) {{
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
            
            addStructuredMessage(data.response, 'ai');
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
        
        function addStructuredMessage(responseData, sender) {{
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + sender + '-message';
            const content = document.createElement('div');
            content.className = 'message-content';
            
            let html = '';
            
            if (typeof responseData === 'string') {{
                html = responseData.replace(/\\n/g, '<br>');
            }} else {{
                const data = responseData;
                
                if (data.quick_answer) {{
                    html += '<strong>📌 Quick Answer</strong><br>';
                    html += data.quick_answer + '<br><br>';
                }}
                
                if (data.detailed_explanation) {{
                    html += '<strong>📖 Detailed Explanation</strong><br>';
                    html += data.detailed_explanation + '<br><br>';
                }}
                
                if (data.key_facts && data.key_facts.length > 0) {{
                    html += '<strong>📊 Key Facts</strong><br>';
                    data.key_facts.forEach(fact => {{
                        html += '• ' + fact + '<br>';
                    }});
                    html += '<br>';
                }}
                
                if (data.analysis) {{
                    html += '<strong>🔍 Analysis</strong><br>';
                    html += data.analysis.replace(/\\n/g, '<br>') + '<br><br>';
                }}
                
                if (data.confidence) {{
                    const conf = data.confidence;
                    let confClass = 'confidence-' + (conf.level || 'medium').toLowerCase().replace(' ', '-');
                    html += '<span class=\"confidence-indicator ' + confClass + '\">';
                    html += '🎯 Confidence: ' + conf.score + '% - ' + (conf.level || 'Medium');
                    html += '</span><br><br>';
                }}
                
                if (data.sources && data.sources.length > 0) {{
                    html += '<strong>🔗 Sources</strong><br>';
                    data.sources.forEach(source => {{
                        let scoreClass = 'medium';
                        if (source.score >= 80) scoreClass = 'high';
                        else if (source.score < 60) scoreClass = 'low';
                        
                        html += '<div class=\"source-card\">';
                        html += '<div class=\"source-card-title\">' + escapeHtml(source.title) + '</div>';
                        html += '<div class=\"source-card-meta\">';
                        html += '<span>' + source.emoji + ' ' + source.type + '</span>';
                        html += '<span class=\"source-card-domain\">' + escapeHtml(source.domain) + '</span>';
                        html += '<span class=\"source-card-score ' + scoreClass + '\">' + source.score + '%</span>';
                        html += '</div>';
                        html += '<a href=\"' + escapeHtml(source.url) + '\" target=\"_blank\" class=\"source-card-open\">🔗 Open Source</a>';
                        html += '</div>';
                    }});
                    html += '<br>';
                }}
                
                if (data.follow_up && data.follow_up.length > 0) {{
                    html += '<strong>💡 Follow-up Questions</strong><br>';
                    html += '<div class=\"follow-ups\">';
                    data.follow_up.forEach(fu => {{
                        html += '<button class=\"follow-up-btn\" onclick=\"askSuggestion(\\'' + escapeHtml(fu) + '\\')\">' + escapeHtml(fu) + '</button>';
                    }});
                    html += '</div><br>';
                }}
                
                if (data.user_stats) {{
                    const stats = data.user_stats;
                    html += '📊 <strong>' + escapeHtml(stats.name) + '\'s Stats:</strong> ';
                    html += 'Level ' + stats.level + ' - ' + escapeHtml(stats.title) + ' (' + stats.count + ' messages)';
                }}
            }}
            
            content.innerHTML = html;
            div.appendChild(content);
            
            const actions = document.createElement('div');
            actions.className = 'message-actions';
            actions.innerHTML = `
                <button class=\"message-action-btn\" onclick=\"copyMessage(this)\">📋 Copy</button>
                <button class=\"message-action-btn\" onclick=\"regenerateMessage(this)\">🔄 Regenerate</button>
                <button class=\"message-action-btn\" onclick=\"scrollToSources(this)\">🔗 Sources</button>
            `;
            div.appendChild(actions);
            
            messages.appendChild(div);
            scrollToBottom();
        }}
        
        function copyMessage(btn) {{
            const msgDiv = btn.closest('.message');
            const content = msgDiv.querySelector('.message-content');
            const text = content.innerText;
            navigator.clipboard.writeText(text).then(() => {{
                const originalText = btn.innerHTML;
                btn.innerHTML = '✅ Copied!';
                setTimeout(() => {{ btn.innerHTML = originalText; }}, 2000);
            }});
        }}
        
        function regenerateMessage(btn) {{
            const msgDiv = btn.closest('.message');
            const prevMsg = msgDiv.previousElementSibling;
            if (prevMsg && prevMsg.classList.contains('user-message')) {{
                const userText = prevMsg.querySelector('.message-content').innerText;
                msgDiv.remove();
                document.getElementById('userInput').value = userText;
                sendMessage();
            }}
        }}
        
        function scrollToSources(btn) {{
            const msgDiv = btn.closest('.message');
            const sourceCards = msgDiv.querySelectorAll('.source-card');
            if (sourceCards.length > 0) {{
                sourceCards[0].scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                sourceCards.forEach(card => {{
                    card.style.transition = 'background 0.3s';
                    card.style.background = '#e8e0d5';
                    setTimeout(() => {{ card.style.background = ''; }}, 1000);
                }});
            }}
        }}
        
        function scrollToBottom() {{
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }}
        
        // Check for existing session
        document.addEventListener('DOMContentLoaded', function() {{
            const savedUser = localStorage.getItem('yama_user');
            if (savedUser) {{
                try {{
                    const user = JSON.parse(savedUser);
                    currentUser = user;
                    document.getElementById('loginOverlay').classList.add('hidden');
                    document.getElementById('app').classList.add('visible');
                    document.getElementById('userBtn').style.display = 'block';
                    document.getElementById('userAvatar').src = user.picture;
                    
                    document.getElementById('userProfile').style.display = 'flex';
                    document.getElementById('userProfile').innerHTML = `
                        <img src=\"${{user.picture}}\" class=\"user-profile-img\">
                        <div class=\"user-profile-info\">
                            <div class=\"user-profile-name\">${{user.name}}</div>
                            <div class=\"user-profile-email\">${{user.email}}</div>
                        </div>
                        <button class=\"logout-btn\" onclick=\"logout()\">Logout</button>
                    `;
                    
                    loadHistory();
                }} catch(e) {{
                    console.log('Error loading session');
                }}
            }}
            textarea.focus();
        }});
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
    
    response_data = get_response(message, email)
    
    if email:
        history = load_history(email)
        history.append({
            "user": message,
            "ai": json.dumps(response_data),
            "timestamp": datetime.now().strftime("%H:%M")
        })
        save_history(email, history)
    
    return {"response": response_data}

@app.get("/get_history")
async def get_history(email: str = ""):
    history = load_history(email)
    parsed_history = []
    for item in history:
        try:
            if isinstance(item.get('ai'), str):
                item['ai'] = json.loads(item['ai'])
        except:
            pass
        parsed_history.append(item)
    return parsed_history

@app.post("/clear_history")
async def clear_history_endpoint():
    save_history("", [])
    return {"status": "cleared"}

@app.get("/analytics")
async def get_analytics():
    avg_response_time = sum(analytics["response_times"]) / len(analytics["response_times"]) if analytics["response_times"] else 0
    avg_confidence = sum(analytics["confidence_avg"]) / len(analytics["confidence_avg"]) if analytics["confidence_avg"] else 0
    avg_quality = sum(analytics["source_quality_avg"]) / len(analytics["source_quality_avg"]) if analytics["source_quality_avg"] else 0
    success_rate = analytics["search_success_rate"].count(True) / len(analytics["search_success_rate"]) * 100 if analytics["search_success_rate"] else 0
    
    return {
        "total_queries": analytics["total_queries"],
        "avg_response_time": round(avg_response_time, 2),
        "avg_confidence": round(avg_confidence, 1),
        "avg_source_quality": round(avg_quality, 1),
        "failed_pages": len(set(analytics["failed_pages"])),
        "search_success_rate": round(success_rate, 1),
        "user_engagement": dict(analytics["user_engagement"])
    }

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🏛️ YAMA AI - PROFESSIONAL RESEARCH ASSISTANT")
    print("="*60)
    print("🌐 Open: http://localhost:8000")
    print("")
    print("✅ ALL FEATURES IMPLEMENTED")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
