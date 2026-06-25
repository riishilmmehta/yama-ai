from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import json
import os
import re
from datetime import datetime, timedelta
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urlparse
from tinydb import TinyDB, Query
import secrets
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import time
from typing import List, Dict, Optional, Set
from collections import defaultdict
import random

app = FastAPI(title="Yama AI - Professional Search Assistant")

# ============ CONFIGURATION ============
MAX_SEARCH_RESULTS = 10
MAX_READ_PAGES = 5
CACHE_DURATION = timedelta(hours=1)
REQUEST_TIMEOUT = 10
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
executor = ThreadPoolExecutor(max_workers=5)

# ============ CACHE SYSTEM ============
class SearchCache:
    def __init__(self):
        self.cache = {}
        self.webpage_cache = {}
    
    def get_search(self, query: str) -> Optional[List[Dict]]:
        key = hashlib.md5(query.lower().encode()).hexdigest()
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < CACHE_DURATION:
                return data
            del self.cache[key]
        return None
    
    def set_search(self, query: str, results: List[Dict]):
        key = hashlib.md5(query.lower().encode()).hexdigest()
        self.cache[key] = (results, datetime.now())
    
    def get_webpage(self, url: str) -> Optional[str]:
        key = hashlib.md5(url.encode()).hexdigest()
        if key in self.webpage_cache:
            data, timestamp = self.webpage_cache[key]
            if datetime.now() - timestamp < CACHE_DURATION:
                return data
            del self.webpage_cache[key]
        return None
    
    def set_webpage(self, url: str, content: str):
        key = hashlib.md5(url.encode()).hexdigest()
        self.webpage_cache[key] = (content, datetime.now())

cache = SearchCache()

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

def get_or_create_user(session_id):
    user = user_db.get(User.session_id == session_id)
    if not user:
        user_id = secrets.token_urlsafe(16)
        user_db.insert({
            "session_id": session_id,
            "user_id": user_id,
            "message_count": 0,
            "level": 1,
            "title": "🌟 Newbie Chatter",
            "created_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        })
        user = user_db.get(User.session_id == session_id)
    else:
        user_db.update({"last_seen": datetime.now().isoformat()}, User.session_id == session_id)
    return user

def update_user_stats(session_id):
    user = user_db.get(User.session_id == session_id)
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
        }, User.session_id == session_id)
        
        return {"count": new_count, "level": new_level, "title": new_title}
    return {"count": 0, "level": 1, "title": "🌟 Newbie Chatter"}

# ============ ENHANCED SEARCH ============
def classify_source(url: str) -> str:
    """Classify source type based on URL"""
    domain = urlparse(url).netloc.lower()
    
    # Government
    if domain.endswith('.gov') or 'gov' in domain:
        return '🏛️ Government'
    
    # Educational
    if domain.endswith('.edu') or 'edu' in domain:
        return '🎓 Educational'
    
    # News
    news_sites = ['cnn', 'bbc', 'nytimes', 'guardian', 'reuters', 'apnews', 'bloomberg', 
                  'wsj', 'washingtonpost', 'time', 'news', 'cnbc', 'forbes', 'economist']
    if any(site in domain for site in news_sites):
        return '📰 News'
    
    # Research
    research_sites = ['pubmed', 'sciencedirect', 'nature', 'science', 'arxiv', 'research']
    if any(site in domain for site in research_sites):
        return '🔬 Research'
    
    return '🌐 Blog/Website'

def clean_text(text: str) -> str:
    """Clean extracted text"""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove special characters
    text = re.sub(r'[^\w\s.,!?-]', '', text)
    return text.strip()

def extract_main_content(soup: BeautifulSoup) -> str:
    """Extract main content from webpage"""
    # Remove unwanted elements
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 
                     'nav', 'form', 'button', 'iframe', 'noscript']):
        tag.decompose()
    
    content_parts = []
    
    # Try article first
    article = soup.find('article')
    if article:
        for p in article.find_all('p'):
            text = clean_text(p.get_text())
            if len(text) > 30:
                content_parts.append(text)
    
    # Try main content
    if not content_parts:
        main = soup.find('main') or soup.find('div', {'role': 'main'})
        if main:
            for p in main.find_all('p'):
                text = clean_text(p.get_text())
                if len(text) > 30:
                    content_parts.append(text)
    
    # Try generic paragraphs
    if not content_parts:
        for p in soup.find_all('p'):
            text = clean_text(p.get_text())
            if len(text) > 50:
                content_parts.append(text)
    
    # Limit to 30 paragraphs
    return ' '.join(content_parts[:30])

def search_web(query: str) -> List[Dict]:
    """Search the web and return results"""
    # Check cache
    cached = cache.get_search(query)
    if cached:
        return cached
    
    results = []
    try:
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=MAX_SEARCH_RESULTS))
            for r in search_results:
                url = r.get('href', '')
                if not url:
                    continue
                results.append({
                    "title": r.get('title', 'Untitled'),
                    "snippet": r.get('body', '')[:300],
                    "url": url,
                    "source_type": classify_source(url),
                    "domain": urlparse(url).netloc
                })
    except Exception as e:
        print(f"Search error: {e}")
    
    # Cache results
    if results:
        cache.set_search(query, results)
    
    return results

def read_full_webpage(url: str) -> Optional[str]:
    """Read and extract content from a webpage with caching"""
    # Check cache
    cached = cache.get_webpage(url)
    if cached:
        return cached
    
    try:
        headers = {'User-Agent': USER_AGENT}
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        content = extract_main_content(soup)
        
        if content and len(content) > 100:
            # Cache the content
            cache.set_webpage(url, content)
            return content[:2000]
        
    except Exception as e:
        print(f"Error reading {url}: {e}")
    
    return None

def read_multiple_pages(urls: List[str], max_pages: int = MAX_READ_PAGES) -> List[Dict]:
    """Read multiple webpages concurrently"""
    results = []
    urls_to_read = urls[:max_pages]
    
    # Read pages concurrently
    def read_page(url):
        content = read_full_webpage(url)
        return url, content
    
    # Use ThreadPoolExecutor for concurrent reads
    with ThreadPoolExecutor(max_workers=min(len(urls_to_read), 5)) as executor:
        future_to_url = {executor.submit(read_page, url): url for url in urls_to_read}
        for future in future_to_url:
            url = future_to_url[future]
            try:
                content = future.result()
                if content[1]:
                    results.append({
                        "url": url,
                        "content": content[1],
                        "source_type": classify_source(url)
                    })
            except Exception as e:
                print(f"Error processing {url}: {e}")
    
    return results

# ============ INTELLIGENT SUMMARIZATION ============
def generate_summary(query: str, search_results: List[Dict], webpage_contents: List[Dict]) -> str:
    """Generate an AI-style summary from multiple sources"""
    
    # Format: Quick Answer + Detailed Explanation + Key Points + Sources
    
    # Extract key information from all sources
    all_text = ' '.join([wc['content'] for wc in webpage_contents[:3]])
    
    # Find sentences with query relevance
    query_words = set(query.lower().split())
    sentences = re.split(r'[.!?]+', all_text)
    relevant_sentences = []
    
    for sentence in sentences:
        sentence_words = set(sentence.lower().split())
        if len(sentence_words.intersection(query_words)) >= 2:
            relevant_sentences.append(sentence.strip())
    
    # Quick Answer
    quick_answer = "I couldn't find a clear answer."
    if relevant_sentences:
        # Take first relevant sentence as quick answer
        quick_answer = relevant_sentences[0]
        # Clean it up
        quick_answer = clean_text(quick_answer)
        if len(quick_answer) > 150:
            quick_answer = quick_answer[:150] + "..."
    elif search_results:
        quick_answer = search_results[0].get('snippet', quick_answer)
    
    # Detailed Explanation
    detailed_parts = []
    if len(relevant_sentences) > 1:
        detailed_parts = [s for s in relevant_sentences[1:5] if len(s) > 50]
    
    detailed_explanation = '\n'.join(detailed_parts) if detailed_parts else "Additional details found in the sources below."
    
    # Key Points (extract key facts)
    key_points = []
    for sentence in relevant_sentences[:8]:
        if len(sentence) > 30 and len(sentence) < 200:
            # Clean and format
            clean_s = clean_text(sentence)
            if clean_s and len(clean_s) > 10:
                key_points.append(clean_s)
    
    # Build response
    response = f"📌 **Quick Answer**\n\n{quick_answer}\n\n"
    
    if detailed_explanation and detailed_explanation != "Additional details found in the sources below.":
        response += f"📖 **Detailed Explanation**\n\n{detailed_explanation}\n\n"
    
    if key_points:
        response += f"📊 **Key Points**\n\n"
        for i, point in enumerate(key_points[:5], 1):
            response += f"{i}. {point}\n"
        response += "\n"
    
    # Sources with trust indicators
    response += f"📚 **Sources** ({len(webpage_contents)} sources)\n\n"
    for i, wc in enumerate(webpage_contents[:5], 1):
        source_type = wc.get('source_type', '🌐')
        response += f"{i}. **{wc['url']}** ({source_type})\n"
    
    return response

def generate_follow_up_questions(query: str, context: str) -> List[str]:
    """Generate relevant follow-up questions"""
    questions = []
    
    # Generate based on query type
    lower_query = query.lower()
    
    if 'what' in lower_query or 'who' in lower_query or 'when' in lower_query:
        questions.append("🔍 Explain more simply")
        questions.append("📋 Give me examples")
        questions.append("📰 What are the latest updates?")
        questions.append("⚖️ What are the pros and cons?")
        questions.append("📖 Tell me more details")
    
    elif 'how' in lower_query or 'why' in lower_query:
        questions.append("🔍 Can you explain step by step?")
        questions.append("📋 Show me a real example")
        questions.append("⚖️ What are the alternatives?")
        questions.append("📰 Any recent developments?")
        questions.append("📖 Provide more context")
    
    elif 'news' in lower_query or 'latest' in lower_query:
        questions.append("🔍 What's the background?")
        questions.append("📋 Who's involved?")
        questions.append("⚖️ What are the implications?")
        questions.append("📰 Is there a timeline?")
        questions.append("📖 Tell me more about the key players")
    
    else:
        questions = [
            "🔍 Can you explain in more detail?",
            "📋 Give me examples",
            "⚖️ What are the pros and cons?",
            "📰 Are there any recent updates?",
            "📖 Tell me more"
        ]
    
    return questions

# ============ COMPLETE RESPONSE GENERATION ============
def get_response(message: str, session_id: str) -> Dict:
    """Generate complete AI-style response"""
    
    msg = message.strip()
    lower_msg = msg.lower()
    
    # Update user stats
    stats = update_user_stats(session_id)
    user = user_db.get(User.session_id == session_id)
    
    # Math
    math_match = re.search(r'(\d+)\s*([\+\-\*\/])\s*(\d+)', lower_msg)
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
            
            return {
                "response": f"🧮 **Quick Answer**\n\n{a} {op} {b} = {result}\n\n📊 **Your Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)",
                "follow_up": [
                    "🔢 Try another calculation",
                    "📐 What's the formula for percentage?",
                    "🧮 Show me algebra basics"
                ],
                "sources": []
            }
        except:
            pass
    
    # Greetings
    if lower_msg in ['hi', 'hello', 'hey', 'sup', 'yo', 'good morning', 'good evening']:
        return {
            "response": f"👋 **Hello!**\n\nYou are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages.\n\nHow can I help you today? I can search the web, answer questions, or just chat! 🏛️",
            "follow_up": [
                "🔍 What can you search for?",
                "📋 How do I level up?",
                "🏛️ Tell me about Yama"
            ],
            "sources": []
        }
    
    if 'how are you' in lower_msg:
        return {
            "response": f"😊 **I'm doing great!**\n\nThanks for asking! You're a {stats['title']} with {stats['count']} messages.\n\nReady to help with your next question! 🏛️",
            "follow_up": [
                "🔍 What should I ask next?",
                "📋 How does Yama work?",
                "⚡ Tell me something interesting"
            ],
            "sources": []
        }
    
    # ============ SEARCH AND SUMMARIZE ============
    # Search the web
    search_results = search_web(message)
    
    if not search_results:
        return {
            "response": f"🔍 **No Results Found**\n\nI searched for '{message}' but couldn't find any results.\n\nTry:\n• Using different keywords\n• Being more specific\n• Asking about a different topic",
            "follow_up": [
                "🔍 Try a different search",
                "📋 What else can I ask?",
                "🏛️ How does Yama search?"
            ],
            "sources": []
        }
    
    # Read multiple webpages
    urls = [r['url'] for r in search_results[:MAX_READ_PAGES]]
    webpage_contents = read_multiple_pages(urls, max_pages=MAX_READ_PAGES)
    
    # If no webpage content, fallback to snippets
    if not webpage_contents:
        # Use snippets as fallback
        for r in search_results[:3]:
            webpage_contents.append({
                "url": r['url'],
                "content": r['snippet'],
                "source_type": r.get('source_type', '🌐')
            })
    
    # Generate summary
    summary = generate_summary(message, search_results, webpage_contents)
    
    # Generate follow-up questions
    follow_ups = generate_follow_up_questions(message, summary)
    
    # Add user stats
    summary += f"\n\n📊 **Your Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
    
    # Prepare sources for display
    sources = []
    for wc in webpage_contents[:5]:
        sources.append({
            "url": wc['url'],
            "title": next((r['title'] for r in search_results if r['url'] == wc['url']), wc['url']),
            "domain": urlparse(wc['url']).netloc,
            "source_type": wc.get('source_type', '🌐 Blog/Website')
        })
    
    return {
        "response": summary,
        "follow_up": follow_ups,
        "sources": sources,
        "search_results": search_results
    }

# ============ HISTORY ============
def load_history(session_id: str) -> List[Dict]:
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(session_id: str, history: List[Dict]):
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============ ENHANCED HTML ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>Yama - AI Search Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        /* ========== RESET & BASE ========== */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        
        html, body {
            height: 100%;
            overflow: hidden;
            position: fixed;
            width: 100%;
            font-family: 'Inter', sans-serif;
            background: #f5f0e8;
            transition: background 0.3s ease;
        }
        
        body.dark {
            background: #1a1a2e;
        }
        
        /* ========== APP CONTAINER ========== */
        .app {
            display: flex;
            height: 100%;
            width: 100%;
            position: relative;
            overflow: hidden;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            transition: background 0.3s ease;
        }
        
        body.dark .app {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        }
        
        /* ========== SIDEBAR ========== */
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: 280px;
            max-width: 85vw;
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
        
        body.dark .sidebar {
            background: #0f0f23;
            border-right-color: #2a2a4e;
        }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid #4a3f2f;
            background: #1f1912;
            flex-shrink: 0;
        }
        
        body.dark .sidebar-header {
            background: #0a0a1a;
            border-bottom-color: #2a2a4e;
        }
        
        .sidebar-header h3 {
            color: #d4c5a9;
            font-family: 'Playfair Display', serif;
            font-size: 0.9rem;
            letter-spacing: 1px;
        }
        
        .history-list {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            -webkit-overflow-scrolling: touch;
        }
        
        .history-item {
            padding: 10px 12px;
            margin-bottom: 4px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid transparent;
        }
        
        .history-item:hover {
            background: rgba(212,197,169,0.08);
            border-color: #4a3f2f;
        }
        
        body.dark .history-item:hover {
            background: rgba(212,197,169,0.08);
            border-color: #3a3a5e;
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
        
        body.dark .history-time {
            color: #6a5a7a;
        }
        
        .sidebar-footer {
            padding: 16px;
            border-top: 1px solid #4a3f2f;
            background: #1f1912;
            flex-shrink: 0;
        }
        
        body.dark .sidebar-footer {
            background: #0a0a1a;
            border-top-color: #2a2a4e;
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
        
        body.dark .new-chat-btn {
            background: #3a3a5e;
            color: #d4c5a9;
        }
        
        body.dark .new-chat-btn:hover {
            background: #4a4a6e;
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
            transition: all 0.2s;
        }
        
        .clear-history:hover {
            background: rgba(212,197,169,0.2);
            border-color: #c4a57b;
        }
        
        body.dark .clear-history {
            border-color: #3a3a5e;
        }
        
        body.dark .clear-history:hover {
            background: rgba(212,197,169,0.2);
            border-color: #c4a57b;
        }
        
        /* ========== OVERLAY ========== */
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
        
        /* ========== MAIN ========== */
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            width: 100%;
            overflow: hidden;
            height: 100%;
        }
        
        /* ========== HEADER ========== */
        .header {
            padding: 10px 12px;
            display: flex;
            align-items: center;
            gap: 8px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
            flex-shrink: 0;
            min-height: 56px;
            z-index: 10;
        }
        
        body.dark .header {
            background: rgba(26,26,46,0.95);
            border-bottom-color: #2a2a4e;
        }
        
        .menu-btn {
            background: none;
            border: none;
            font-size: 1.3rem;
            cursor: pointer;
            color: #6a5a4a;
            padding: 6px 8px;
            border-radius: 10px;
            transition: all 0.2s;
        }
        
        .menu-btn:hover {
            background: #d4c5a9;
            color: #2c2418;
        }
        
        body.dark .menu-btn {
            color: #d4c5a9;
        }
        
        body.dark .menu-btn:hover {
            background: #3a3a5e;
            color: white;
        }
        
        .logo {
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 4px;
        }
        
        .logo-icon {
            font-size: 1.6rem;
        }
        
        .logo h1 {
            font-family: 'Playfair Display', serif;
            font-size: 1.2rem;
            color: #2c2418;
        }
        
        body.dark .logo h1 {
            color: #d4c5a9;
        }
        
        .header-actions {
            display: flex;
            gap: 4px;
            align-items: center;
        }
        
        .new-chat-mobile {
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 6px 8px;
            border-radius: 10px;
            color: #6a5a4a;
            transition: all 0.2s;
        }
        
        .new-chat-mobile:hover {
            background: #d4c5a9;
        }
        
        body.dark .new-chat-mobile {
            color: #d4c5a9;
        }
        
        body.dark .new-chat-mobile:hover {
            background: #3a3a5e;
            color: white;
        }
        
        .control-btn {
            background: none;
            border: none;
            font-size: 1.1rem;
            cursor: pointer;
            padding: 6px 8px;
            border-radius: 20px;
            color: #6a5a4a;
            transition: all 0.2s;
        }
        
        .control-btn:hover {
            background: #d4c5a9;
        }
        
        body.dark .control-btn {
            color: #d4c5a9;
        }
        
        body.dark .control-btn:hover {
            background: #3a3a5e;
            color: white;
        }
        
        /* ========== MESSAGES ========== */
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 12px 16px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }
        
        .message {
            margin-bottom: 16px;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from {
                opacity: 0;
                transform: translateY(12px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .user-message {
            text-align: right;
        }
        
        .ai-message {
            text-align: left;
        }
        
        .message-content {
            display: inline-block;
            max-width: 92%;
            font-size: 0.9rem;
            line-height: 1.6;
            color: #2c2418;
            word-wrap: break-word;
            overflow-wrap: break-word;
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
            padding: 14px 18px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        
        body.dark .ai-message .message-content {
            background: #2a2a4e !important;
            color: #e0e0e0 !important;
        }
        
        body.dark .user-message .message-content {
            background: #3a3a5e !important;
        }
        
        .message-content h1, .message-content h2, .message-content h3 {
            font-size: 1.1rem;
            margin: 6px 0;
        }
        
        .message-content strong {
            font-weight: 600;
        }
        
        .message-content a {
            color: #4ecdc4;
            text-decoration: underline;
            word-break: break-all;
        }
        
        body.dark .message-content a {
            color: #4ecdc4;
        }
        
        .message-content a:hover {
            color: #6ee7de;
        }
        
        /* ========== SOURCE CARDS ========== */
        .source-card {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            margin: 6px 0;
            background: rgba(0,0,0,0.04);
            border-radius: 12px;
            border: 1px solid rgba(0,0,0,0.06);
            transition: all 0.2s;
        }
        
        body.dark .source-card {
            background: rgba(255,255,255,0.04);
            border-color: rgba(255,255,255,0.08);
        }
        
        .source-card:hover {
            background: rgba(0,0,0,0.08);
        }
        
        body.dark .source-card:hover {
            background: rgba(255,255,255,0.08);
        }
        
        .source-icon {
            font-size: 1.2rem;
            flex-shrink: 0;
        }
        
        .source-info {
            flex: 1;
            min-width: 0;
        }
        
        .source-domain {
            font-size: 0.7rem;
            color: #888;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        body.dark .source-domain {
            color: #8a8a9a;
        }
        
        .source-title {
            font-size: 0.8rem;
            color: #2c2418;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        body.dark .source-title {
            color: #d4c5a9;
        }
        
        .source-open-btn {
            background: none;
            border: 1px solid #4ecdc4;
            color: #4ecdc4;
            padding: 4px 12px;
            border-radius: 16px;
            font-size: 0.7rem;
            cursor: pointer;
            transition: all 0.2s;
            flex-shrink: 0;
            text-decoration: none;
        }
        
        .source-open-btn:hover {
            background: #4ecdc4;
            color: white;
        }
        
        /* ========== FOLLOW-UP QUESTIONS ========== */
        .follow-up-section {
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid rgba(0,0,0,0.08);
        }
        
        body.dark .follow-up-section {
            border-top-color: rgba(255,255,255,0.08);
        }
        
        .follow-up-label {
            font-size: 0.7rem;
            color: #888;
            margin-bottom: 8px;
        }
        
        body.dark .follow-up-label {
            color: #8a8a9a;
        }
        
        .follow-up-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        
        .follow-up-btn {
            background: rgba(78, 205, 196, 0.1);
            border: 1px solid rgba(78, 205, 196, 0.2);
            border-radius: 20px;
            padding: 4px 12px;
            font-size: 0.75rem;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        
        body.dark .follow-up-btn {
            color: #d4c5a9;
            background: rgba(78, 205, 196, 0.08);
            border-color: rgba(78, 205, 196, 0.15);
        }
        
        .follow-up-btn:hover {
            background: #4ecdc4;
            color: white;
            border-color: #4ecdc4;
        }
        
        body.dark .follow-up-btn:hover {
            background: #4ecdc4;
            color: #1a1a2e;
        }
        
        /* ========== MESSAGE ACTIONS ========== */
        .message-actions {
            display: flex;
            gap: 4px;
            margin-top: 6px;
            opacity: 0.6;
            transition: opacity 0.2s;
        }
        
        .message:hover .message-actions {
            opacity: 1;
        }
        
        .action-btn {
            background: none;
            border: none;
            font-size: 0.8rem;
            color: #888;
            cursor: pointer;
            padding: 2px 6px;
            border-radius: 6px;
            transition: all 0.2s;
        }
        
        .action-btn:hover {
            background: rgba(0,0,0,0.06);
            color: #2c2418;
        }
        
        body.dark .action-btn {
            color: #8a8a9a;
        }
        
        body.dark .action-btn:hover {
            background: rgba(255,255,255,0.06);
            color: #d4c5a9;
        }
        
        .action-btn.copy-success {
            color: #4ecdc4;
        }
        
        /* ========== TYPING INDICATOR ========== */
        .typing {
            display: none;
            padding: 8px 16px;
            gap: 5px;
            color: #888;
            font-size: 0.8rem;
            flex-shrink: 0;
            align-items: center;
        }
        
        .typing span {
            width: 6px;
            height: 6px;
            background: #c4a57b;
            border-radius: 50%;
            display: inline-block;
            animation: typingBounce 1.4s infinite;
        }
        
        .typing span:nth-child(2) {
            animation-delay: 0.2s;
        }
        
        .typing span:nth-child(3) {
            animation-delay: 0.4s;
        }
        
        @keyframes typingBounce {
            0%, 60%, 100% {
                transform: translateY(0);
            }
            30% {
                transform: translateY(-6px);
            }
        }
        
        body.dark .typing {
            color: #8a8a9a;
        }
        
        body.dark .typing span {
            background: #d4c5a9;
        }
        
        /* ========== INPUT AREA ========== */
        .input-area {
            padding: 10px 12px 14px;
            background: linear-gradient(to top, #f5f0e8, transparent);
            flex-shrink: 0;
            position: relative;
            z-index: 10;
        }
        
        body.dark .input-area {
            background: linear-gradient(to top, #1a1a2e, transparent);
        }
        
        .input-wrapper {
            display: flex;
            align-items: center;
            gap: 10px;
            background: white;
            border-radius: 28px;
            padding: 6px 6px 6px 18px;
            border: 1px solid #d4c5a9;
            min-height: 50px;
            height: auto;
            transition: border-color 0.2s;
        }
        
        body.dark .input-wrapper {
            background: #2a2a4e;
            border-color: #3a3a5e;
        }
        
        .input-wrapper:focus-within {
            border-color: #4ecdc4;
        }
        
        textarea {
            flex: 1;
            background: transparent;
            border: none;
            color: #2c2418;
            font-size: 0.95rem;
            resize: none;
            outline: none;
            padding: 10px 0;
            font-family: inherit;
            width: 100%;
            min-height: 36px;
            max-height: 100px;
            overflow-y: auto;
            line-height: 1.4;
        }
        
        body.dark textarea {
            color: #e0e0e0;
        }
        
        textarea::placeholder {
            color: #b8a88a;
        }
        
        body.dark textarea::placeholder {
            color: #6a5a7a;
        }
        
        .input-wrapper button {
            background: #2c2418;
            border: none;
            border-radius: 28px;
            padding: 8px 20px;
            color: #f5f0e8;
            font-weight: 500;
            cursor: pointer;
            font-size: 0.85rem;
            min-width: 60px;
            width: auto;
            white-space: nowrap;
            transition: all 0.2s;
            flex-shrink: 0;
        }
        
        body.dark .input-wrapper button {
            background: #4a4a6e;
            color: #d4c5a9;
        }
        
        .input-wrapper button:hover {
            background: #4a3f2f;
            transform: scale(1.02);
        }
        
        body.dark .input-wrapper button:hover {
            background: #5a5a7e;
        }
        
        .input-wrapper button:active {
            transform: scale(0.98);
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
            0%, 100% {
                transform: translateY(0);
            }
            50% {
                transform: translateY(-8px);
            }
        }
        
        .welcome h2 {
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            color: #2c2418;
            margin-bottom: 8px;
        }
        
        body.dark .welcome h2 {
            color: #d4c5a9;
        }
        
        .welcome p {
            color: #6a5a4a;
            font-size: 0.85rem;
            margin-bottom: 20px;
            max-width: 400px;
        }
        
        body.dark .welcome p {
            color: #8a7a6a;
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
        }
        
        body.dark .suggestion {
            background: #2a2a4e;
            border-color: #3a3a5e;
            color: #e0e0e0;
        }
        
        .suggestion:hover {
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }
        
        body.dark .suggestion:hover {
            background: #4a4a6e;
            border-color: #4a4a6e;
        }
        
        /* ========== TOAST ========== */
        .toast {
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0,0,0,0.8);
            color: white;
            padding: 8px 20px;
            border-radius: 20px;
            font-size: 0.8rem;
            z-index: 2000;
            opacity: 0;
            transition: opacity 0.3s ease;
            pointer-events: none;
            white-space: nowrap;
        }
        
        .toast.show {
            opacity: 1;
        }
        
        body.dark .toast {
            background: rgba(255,255,255,0.9);
            color: #1a1a2e;
        }
        
        /* ========== SCROLLBAR ========== */
        .messages::-webkit-scrollbar,
        .history-list::-webkit-scrollbar {
            width: 4px;
        }
        
        .messages::-webkit-scrollbar-track,
        .history-list::-webkit-scrollbar-track {
            background: transparent;
        }
        
        .messages::-webkit-scrollbar-thumb,
        .history-list::-webkit-scrollbar-thumb {
            background: #d4c5a9;
            border-radius: 4px;
        }
        
        body.dark .messages::-webkit-scrollbar-thumb,
        body.dark .history-list::-webkit-scrollbar-thumb {
            background: #3a3a5e;
        }
        
        /* ========== RESPONSIVE ========== */
        @media (max-width: 768px) {
            .message-content {
                max-width: 95%;
                font-size: 0.85rem;
            }
            
            .ai-message .message-content {
                padding: 12px 14px !important;
            }
            
            .user-message .message-content {
                padding: 8px 14px !important;
            }
            
            .messages {
                padding: 10px 12px;
            }
            
            .header {
                padding: 8px 10px;
                min-height: 48px;
            }
            
            .logo h1 {
                font-size: 1rem;
            }
            
            .logo-icon {
                font-size: 1.3rem;
            }
            
            .input-area {
                padding: 8px 10px 12px;
            }
            
            .input-wrapper {
                min-height: 44px;
                border-radius: 24px;
                padding: 4px 4px 4px 14px;
                gap: 8px;
            }
            
            textarea {
                font-size: 0.9rem;
                padding: 8px 0;
                min-height: 32px;
                max-height: 80px;
            }
            
            .input-wrapper button {
                padding: 7px 16px;
                min-width: 54px;
                font-size: 0.8rem;
            }
            
            .control-btn {
                font-size: 1rem;
                padding: 4px 6px;
            }
            
            .welcome h2 {
                font-size: 1.6rem;
            }
            
            .suggestions {
                gap: 6px;
            }
            
            .suggestion {
                font-size: 0.7rem;
                padding: 5px 12px;
            }
            
            .source-card {
                padding: 8px 10px;
                gap: 8px;
            }
            
            .source-title {
                font-size: 0.75rem;
            }
            
            .follow-up-btn {
                font-size: 0.7rem;
                padding: 3px 10px;
            }
            
            .toast {
                bottom: 70px;
                font-size: 0.75rem;
                padding: 6px 16px;
            }
        }
        
        @media (max-width: 480px) {
            .message-content {
                font-size: 0.82rem;
            }
            
            .ai-message .message-content {
                padding: 10px 12px !important;
            }
            
            .user-message .message-content {
                padding: 7px 12px !important;
            }
            
            .messages {
                padding: 8px 10px;
            }
            
            .header {
                padding: 6px 8px;
                min-height: 44px;
            }
            
            .logo h1 {
                font-size: 0.9rem;
            }
            
            .input-area {
                padding: 6px 8px 10px;
            }
            
            .input-wrapper {
                min-height: 40px;
                border-radius: 20px;
                padding: 3px 3px 3px 12px;
                gap: 6px;
            }
            
            textarea {
                font-size: 0.85rem;
                padding: 6px 0;
                min-height: 28px;
                max-height: 70px;
            }
            
            .input-wrapper button {
                padding: 6px 14px;
                min-width: 48px;
                font-size: 0.75rem;
            }
            
            .welcome h2 {
                font-size: 1.3rem;
            }
            
            .welcome-icon {
                font-size: 2.2rem;
            }
            
            .source-card {
                padding: 6px 8px;
                gap: 6px;
            }
            
            .follow-up-btn {
                font-size: 0.65rem;
                padding: 2px 8px;
            }
        }
        
        @media (min-width: 769px) {
            .input-wrapper {
                border-radius: 32px;
                padding: 8px 8px 8px 22px;
                min-height: 58px;
            }
            
            textarea {
                font-size: 1rem;
                padding: 12px 0;
                min-height: 40px;
                max-height: 130px;
            }
            
            .input-wrapper button {
                padding: 10px 28px;
                min-width: 76px;
                font-size: 0.95rem;
            }
            
            .message-content {
                max-width: 80%;
                font-size: 0.95rem;
            }
            
            .messages {
                padding: 16px 24px;
            }
        }
        
        @media (max-width: 360px) {
            .header {
                gap: 4px;
            }
            
            .control-btn {
                font-size: 0.85rem;
                padding: 4px 4px;
            }
            
            .new-chat-mobile {
                font-size: 1rem;
                padding: 4px 6px;
            }
            
            .input-wrapper {
                min-height: 36px;
                padding: 2px 2px 2px 10px;
            }
            
            textarea {
                font-size: 0.8rem;
                min-height: 24px;
            }
            
            .input-wrapper button {
                padding: 5px 10px;
                min-width: 40px;
                font-size: 0.7rem;
            }
        }
        
        /* ========== UTILITY ========== */
        .hidden {
            display: none !important;
        }
        
        .text-center {
            text-align: center;
        }
        
        .mt-8 {
            margin-top: 8px;
        }
    </style>
</head>
<body>
    <!-- Toast -->
    <div class="toast" id="toast"></div>
    
    <!-- Overlay -->
    <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
    
    <!-- Sidebar -->
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <h3>📜 CONVERSATIONS</h3>
        </div>
        <div class="history-list" id="historyList">
            <div class="text-center" style="color: #6a5a4a; padding: 20px; font-size: 0.85rem;">No conversations yet</div>
        </div>
        <div class="sidebar-footer">
            <button class="new-chat-btn" onclick="newChat()">➕ New Chat</button>
            <button class="clear-history" onclick="clearHistory()">🗑️ Clear all history</button>
        </div>
    </div>
    
    <!-- Main -->
    <div class="app">
        <div class="main">
            <!-- Header -->
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()" aria-label="Toggle menu">☰</button>
                <div class="logo" id="logo">
                    <span class="logo-icon">🏛️</span>
                    <h1>YAMA</h1>
                </div>
                <div class="header-actions">
                    <button class="new-chat-mobile" onclick="newChat()" aria-label="New chat">➕</button>
                    <button class="control-btn" onclick="toggleTheme()" aria-label="Toggle theme">🌓</button>
                    <button class="control-btn" onclick="exportChat()" aria-label="Export chat">📥</button>
                </div>
            </div>
            
            <!-- Messages -->
            <div class="messages" id="messages">
                <div class="welcome" id="welcome">
                    <div class="welcome-icon">🏛️</div>
                    <h2>Yama</h2>
                    <p>Your AI search assistant. I read multiple sources and give you clear answers.</p>
                    <div class="suggestions">
                        <div class="suggestion" onclick="askSuggestion('What is the capital of France?')">🗼 Capital of France</div>
                        <div class="suggestion" onclick="askSuggestion('Who is Elon Musk?')">🚀 Elon Musk</div>
                        <div class="suggestion" onclick="askSuggestion('Latest AI news')">📰 AI News</div>
                        <div class="suggestion" onclick="askSuggestion('What is climate change?')">🌍 Climate Change</div>
                    </div>
                </div>
            </div>
            
            <!-- Typing -->
            <div class="typing" id="typing">
                <span></span><span></span><span></span> Yama is thinking...
            </div>
            
            <!-- Input -->
            <div class="input-area">
                <div class="input-wrapper">
                    <textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    <button onclick="sendMessage()" id="sendBtn">Send</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // ============ STATE ============
        let sessionId = 'session_' + Date.now();
        let hasMessages = false;
        let currentFollowUps = [];
        
        // ============ THEME ============
        function toggleTheme() {
            document.body.classList.toggle('dark');
            localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
        }
        
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') {
            document.body.classList.add('dark');
        }
        
        // ============ TOAST ============
        let toastTimeout = null;
        
        function showToast(message) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.classList.add('show');
            
            if (toastTimeout) {
                clearTimeout(toastTimeout);
            }
            
            toastTimeout = setTimeout(() => {
                toast.classList.remove('show');
            }, 2000);
        }
        
        // ============ SIDEBAR ============
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }
        
        function closeSidebar() {
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('overlay').classList.remove('show');
        }
        
        // ============ CHAT MANAGEMENT ============
        function newChat() {
            if (confirm('Start a new chat? Current conversation will be cleared.')) {
                location.reload();
            }
        }
        
        function exportChat() {
            const messages = document.querySelectorAll('.message');
            let exportText = '🏛️ YAMA CHAT EXPORT\n' + '='.repeat(40) + '\n\n';
            
            messages.forEach(msg => {
                const sender = msg.classList.contains('user-message') ? '👤 You' : '🏛️ Yama';
                const text = msg.querySelector('.message-content').innerText;
                exportText += `${sender}: ${text}\n\n`;
            });
            
            if (exportText === '🏛️ YAMA CHAT EXPORT\n========================================\n\n') {
                showToast('No messages to export');
                return;
            }
            
            const blob = new Blob([exportText], {type: 'text/plain'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `yama_chat_${new Date().toISOString().slice(0, 19).replace(/[:]/g, '-')}.txt`;
            a.click();
            URL.revokeObjectURL(a.href);
            showToast('✅ Chat exported!');
        }
        
        // ============ SUGGESTIONS ============
        function askSuggestion(q) {
            document.getElementById('userInput').value = q;
            sendMessage();
        }
        
        // ============ HISTORY ============
        async function loadHistory() {
            try {
                const res = await fetch('/get_history?session_id=' + encodeURIComponent(sessionId));
                const history = await res.json();
                const container = document.getElementById('historyList');
                
                if (!history || history.length === 0) {
                    container.innerHTML = '<div class="text-center" style="color: #6a5a4a; padding: 20px; font-size: 0.85rem;">No conversations yet</div>';
                    return;
                }
                
                container.innerHTML = history.slice().reverse().map(item => `
                    <div class="history-item" onclick="loadChatMessage('${escapeHtml(item.user)}')">
                        <div class="history-question">${escapeHtml(item.user.substring(0, 45))}${item.user.length > 45 ? '...' : ''}</div>
                        <div class="history-time">${item.timestamp || ''}</div>
                    </div>
                `).join('');
            } catch (e) {
                console.error('History load error:', e);
            }
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
                showToast('🗑️ History cleared');
                location.reload();
            }
        }
        
        // ============ AUTO-RESIZE TEXTAREA ============
        const textarea = document.getElementById('userInput');
        
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 100) + 'px';
        });
        
        // ============ KEYBOARD HANDLING ============
        function handleKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }
        
        // ============ SEND MESSAGE ============
        async function sendMessage() {
            const message = textarea.value.trim();
            if (!message) return;
            
            // Reset textarea
            textarea.value = '';
            textarea.style.height = 'auto';
            
            // Hide welcome
            if (!hasMessages) {
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
            }
            
            // Add user message
            addMessage(message, 'user');
            
            // Show typing
            const typing = document.getElementById('typing');
            typing.style.display = 'flex';
            scrollToBottom();
            
            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message, session_id: sessionId })
                });
                
                const data = await res.json();
                
                // Hide typing
                typing.style.display = 'none';
                
                // Add AI response
                addAIResponse(data);
                
                // Load history
                loadHistory();
                scrollToBottom();
                
            } catch (e) {
                typing.style.display = 'none';
                addMessage('❌ Sorry, there was an error. Please try again.', 'ai');
                scrollToBottom();
                console.error('Send error:', e);
            }
        }
        
        // ============ ADD MESSAGE ============
        function addMessage(text, sender) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = `message ${sender}-message`;
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = formatMessage(text);
            div.appendChild(content);
            messages.appendChild(div);
            scrollToBottom();
        }
        
        function addAIResponse(data) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ai-message';
            
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = formatMessage(data.response);
            
            // Add sources if available
            if (data.sources && data.sources.length > 0) {
                const sourcesHTML = data.sources.map(source => `
                    <div class="source-card">
                        <span class="source-icon">${source.source_type ? source.source_type.split(' ')[0] : '🌐'}</span>
                        <div class="source-info">
                            <div class="source-title">${escapeHtml(source.title || source.domain)}</div>
                            <div class="source-domain">${escapeHtml(source.domain)}</div>
                        </div>
                        <a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer" class="source-open-btn">Open</a>
                    </div>
                `).join('');
                
                content.innerHTML += `<div class="follow-up-section"><div class="follow-up-label">📚 Sources</div>${sourcesHTML}</div>`;
            }
            
            // Add follow-up questions
            if (data.follow_up && data.follow_up.length > 0) {
                currentFollowUps = data.follow_up;
                const followHTML = data.follow_up.map(q => `
                    <button class="follow-up-btn" onclick="askFollowUp('${escapeHtml(q)}')">${escapeHtml(q)}</button>
                `).join('');
                
                content.innerHTML += `<div class="follow-up-section"><div class="follow-up-label">💡 Related Questions</div><div class="follow-up-buttons">${followHTML}</div></div>`;
            }
            
            // Add message actions
            const actionsHTML = `
                <div class="message-actions">
                    <button class="action-btn" onclick="copyMessage(this)" title="Copy response">📋 Copy</button>
                </div>
            `;
            content.innerHTML += actionsHTML;
            
            div.appendChild(content);
            messages.appendChild(div);
        }
        
        function askFollowUp(q) {
            document.getElementById('userInput').value = q;
            sendMessage();
        }
        
        // ============ MESSAGE FORMATTING ============
        function formatMessage(text) {
            // Convert **bold** to <strong>
            text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            
            // Convert newlines to <br>
            text = text.replace(/\n/g, '<br>');
            
            // Convert URLs to links
            text = text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
            
            return text;
        }
        
        // ============ COPY MESSAGE ============
        function copyMessage(btn) {
            const content = btn.closest('.message-content');
            const text = content.innerText
                .replace(/Copy/g, '')
                .replace(/📋/g, '')
                .trim();
            
            navigator.clipboard.writeText(text).then(() => {
                btn.textContent = '✅ Copied!';
                btn.classList.add('copy-success');
                setTimeout(() => {
                    btn.textContent = '📋 Copy';
                    btn.classList.remove('copy-success');
                }, 2000);
                showToast('📋 Copied to clipboard!');
            }).catch(() => {
                // Fallback
                const range = document.createRange();
                range.selectNode(content);
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                document.execCommand('copy');
                window.getSelection().removeAllRanges();
                showToast('📋 Copied!');
            });
        }
        
        // ============ SCROLL ============
        function scrollToBottom() {
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }
        
        // ============ INIT ============
        document.addEventListener('DOMContentLoaded', () => {
            loadHistory();
            textarea.focus();
            
            // Handle keyboard visibility on mobile
            if ('visualViewport' in window) {
                window.visualViewport.addEventListener('resize', () => {
                    const messages = document.getElementById('messages');
                    if (messages) {
                        messages.scrollTop = messages.scrollHeight;
                    }
                });
            }
        });
        
        // Prevent zoom on double tap
        document.addEventListener('touchend', function(e) {
            if (e.target.closest('.message-content')) {
                e.preventDefault();
            }
        }, { passive: false });
    </script>
</body>
</html>
'''

# ============ FASTAPI ENDPOINTS ============

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get('message', '')
    session_id = data.get('session_id', 'default')
    
    # Get response
    result = get_response(message, session_id)
    
    # Save history
    history = load_history(session_id)
    history.append({
        "user": message,
        "ai": result['response'],
        "timestamp": datetime.now().strftime("%H:%M")
    })
    save_history(session_id, history)
    
    return JSONResponse(result)

@app.get("/get_history")
async def get_history(session_id: str = "default"):
    return load_history(session_id)

@app.post("/clear_history")
async def clear_history_endpoint():
    save_history("default", [])
    return {"status": "cleared"}

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - PROFESSIONAL SEARCH ASSISTANT")
    print("="*55)
    print("🌐 Open: http://localhost:10000")
    print("📌 Features:")
    print("  • Multi-source search (reads multiple webpages)")
    print("  • AI-style summaries with quick answers")
    print("  • Clickable source cards")
    print("  • Source classification")
    print("  • Follow-up questions")
    print("  • Copy response")
    print("  • Export chat")
    print("  • Dark/Light mode")
    print("  • Fully mobile responsive")
    print("  • Search caching")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
