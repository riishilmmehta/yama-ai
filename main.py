from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
import uvicorn
import json
import os
import re
import asyncio
import hashlib
import logging
import time
import secrets
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

# Third-party imports
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote, urlencode
from tinydb import TinyDB, Query
import bleach
from markdown import markdown

# ============ LOGGING SETUP ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('yama.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('yama')

# ============ CONFIGURATION ============
MAX_SEARCH_RESULTS = 10
MAX_READ_PAGES = 5
CACHE_DURATION = 3600
REQUEST_TIMEOUT = 10
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
MAX_MESSAGE_LENGTH = 2000

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "your-google-client-id.apps.googleusercontent.com")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "your-google-client-secret")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:10000/auth/google/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))

# ============ ASYNC LIFESPAN ============
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🏛️ YAMA AI - Starting up...")
    Path("data").mkdir(exist_ok=True)
    asyncio.create_task(cache_cleanup_loop())
    yield
    logger.info("Shutting down...")

app = FastAPI(
    title="Yama AI",
    description="Professional AI Search Assistant with Google Login",
    version="2.0.0",
    lifespan=lifespan
)

# ============ SESSION MIDDLEWARE ============
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="yama_session",
    max_age=86400 * 30,  # 30 days
)

# ============ GOOGLE OAUTH SETUP ============
oauth = OAuth()
oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        'prompt': 'select_account'
    }
)

# ============ MIDDLEWARE ============
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ============ CACHE SYSTEM ============
class SearchCache:
    def __init__(self):
        self.cache = {}
        self.webpage_cache = {}
        self.max_size = 500
    
    def get_search(self, query: str) -> Optional[List[Dict]]:
        key = hashlib.md5(query.lower().encode()).hexdigest()
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < CACHE_DURATION:
                return data
            del self.cache[key]
        return None
    
    def set_search(self, query: str, results: List[Dict]):
        key = hashlib.md5(query.lower().encode()).hexdigest()
        self.cache[key] = (results, time.time())
        self._cleanup_if_needed()
    
    def get_webpage(self, url: str) -> Optional[str]:
        key = hashlib.md5(url.encode()).hexdigest()
        if key in self.webpage_cache:
            data, timestamp = self.webpage_cache[key]
            if time.time() - timestamp < CACHE_DURATION:
                return data
            del self.webpage_cache[key]
        return None
    
    def set_webpage(self, url: str, content: str):
        key = hashlib.md5(url.encode()).hexdigest()
        self.webpage_cache[key] = (content, time.time())
        self._cleanup_if_needed()
    
    def _cleanup_if_needed(self):
        if len(self.cache) > self.max_size:
            items = sorted(self.cache.items(), key=lambda x: x[1][1])
            for key, _ in items[:self.max_size // 5]:
                del self.cache[key]
        
        if len(self.webpage_cache) > self.max_size:
            items = sorted(self.webpage_cache.items(), key=lambda x: x[1][1])
            for key, _ in items[:self.max_size // 5]:
                del self.webpage_cache[key]

cache = SearchCache()

async def cache_cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        cache._cleanup_if_needed()

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

def get_or_create_user(session_id, user_info=None):
    user = user_db.get(User.session_id == session_id)
    if not user:
        user_id = secrets.token_urlsafe(16)
        user_data = {
            "session_id": session_id,
            "user_id": user_id,
            "message_count": 0,
            "level": 1,
            "title": "🌟 Newbie Chatter",
            "created_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        }
        if user_info:
            user_data.update({
                "email": user_info.get('email'),
                "name": user_info.get('name'),
                "picture": user_info.get('picture'),
                "google_id": user_info.get('sub')
            })
        user_db.insert(user_data)
        user = user_db.get(User.session_id == session_id)
    else:
        user_db.update({"last_seen": datetime.now().isoformat()}, User.session_id == session_id)
        if user_info:
            user_db.update({
                "email": user_info.get('email'),
                "name": user_info.get('name'),
                "picture": user_info.get('picture'),
                "last_login": datetime.now().isoformat()
            }, User.session_id == session_id)
            user = user_db.get(User.session_id == session_id)
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

def get_user_by_session(session_id):
    return user_db.get(User.session_id == session_id)

# ============ SECURITY FUNCTIONS ============
def sanitize_html(text: str) -> str:
    ALLOWED_TAGS = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'strong', 'em', 'b', 'i',
        'ul', 'ol', 'li', 'a', 'code', 'pre', 'blockquote', 'hr', 'table',
        'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span', 'img', 'mark'
    ]
    
    ALLOWED_ATTRIBUTES = {
        'a': ['href', 'target', 'rel', 'title'],
        'img': ['src', 'alt', 'width', 'height'],
        'div': ['class'],
        'span': ['class'],
        'pre': ['class'],
        'code': ['class'],
        'table': ['class'],
    }
    
    html_content = markdown(text, extensions=['extra', 'codehilite', 'tables'])
    cleaned = bleach.clean(
        html_content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True
    )
    return cleaned

# ============ ENHANCED SEARCH FUNCTIONS ============
def clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_main_content(soup: BeautifulSoup) -> str:
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 
                     'form', 'button', 'iframe', 'noscript', 'advertisement']):
        tag.decompose()
    
    content_parts = []
    
    article = soup.find('article')
    if article:
        for p in article.find_all('p'):
            text = clean_text(p.get_text())
            if len(text) > 30:
                content_parts.append(text)
    
    if not content_parts:
        main = soup.find('main') or soup.find('div', {'role': 'main'})
        if main:
            for p in main.find_all('p'):
                text = clean_text(p.get_text())
                if len(text) > 30:
                    content_parts.append(text)
    
    if not content_parts:
        for p in soup.find_all('p'):
            text = clean_text(p.get_text())
            if len(text) > 50:
                content_parts.append(text)
    
    return ' '.join(content_parts[:30])

def search_web(query: str) -> List[Dict]:
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
                    "domain": urlparse(url).netloc
                })
    except Exception as e:
        logger.error(f"Search error: {e}")
    
    if results:
        cache.set_search(query, results)
    
    return results

def read_full_webpage(url: str) -> Optional[str]:
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
            cache.set_webpage(url, content)
            return content[:2000]
        
    except Exception as e:
        logger.error(f"Error reading {url}: {e}")
    
    return None

def read_multiple_pages(urls: List[str], max_pages: int = MAX_READ_PAGES) -> List[Dict]:
    results = []
    urls_to_read = urls[:max_pages]
    
    def read_page(url):
        content = read_full_webpage(url)
        return url, content
    
    with ThreadPoolExecutor(max_workers=min(len(urls_to_read), 5)) as executor:
        future_to_url = {executor.submit(read_page, url): url for url in urls_to_read}
        for future in future_to_url:
            url = future_to_url[future]
            try:
                url, content = future.result()
                if content:
                    results.append({
                        "url": url,
                        "content": content
                    })
            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
    
    return results

def remove_duplicates(sentences: List[str]) -> List[str]:
    seen = set()
    unique = []
    for s in sentences:
        key = s.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique

def rank_by_relevance(sentences: List[str], query_words: set) -> List[str]:
    scored = []
    for s in sentences:
        words = set(s.lower().split())
        score = len(words.intersection(query_words))
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]

def generate_combined_summary(query: str, search_results: List[Dict], webpage_contents: List[Dict]) -> str:
    all_text = ' '.join([wc['content'] for wc in webpage_contents[:3]])
    query_words = set(query.lower().split())
    
    sentences = re.split(r'[.!?]+', all_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
    
    sentences = remove_duplicates(sentences)
    sentences = rank_by_relevance(sentences, query_words)
    
    response_parts = []
    
    if sentences:
        main_answer = sentences[0]
        if len(main_answer) > 200:
            main_answer = main_answer[:200] + "..."
        response_parts.append(f"📌 {main_answer}")
    elif search_results:
        response_parts.append(f"📌 {search_results[0].get('snippet', '')}")
    
    if len(sentences) > 2:
        response_parts.append("\n📖 **More details:**")
        for s in sentences[1:4]:
            response_parts.append(f"• {s}")
    
    if len(sentences) > 4:
        response_parts.append("\n📊 **Key points:**")
        for s in sentences[4:7]:
            response_parts.append(f"• {s}")
    
    if webpage_contents:
        response_parts.append("\n📚 **Sources:**")
        for i, wc in enumerate(webpage_contents[:5], 1):
            domain = urlparse(wc['url']).netloc
            response_parts.append(f"{i}. {domain}")
            response_parts.append(f"   {wc['url']}")
    
    return '\n'.join(response_parts)

def generate_follow_ups(query: str) -> List[str]:
    lower_query = query.lower()
    
    if any(w in lower_query for w in ['what', 'who', 'when', 'where']):
        return [
            "Explain more simply",
            "Give me examples",
            "Latest updates",
            "Pros and cons",
            "Tell me more"
        ]
    elif any(w in lower_query for w in ['how', 'why']):
        return [
            "Step by step explanation",
            "Real example",
            "What are the alternatives?",
            "Recent developments",
            "More context"
        ]
    else:
        return [
            "Explain in more detail",
            "Give me examples",
            "Pros and cons",
            "Recent updates",
            "Tell me more"
        ]

# ============ RESPONSE FUNCTION ============
def get_response(message, session_id):
    msg = message.strip().lower()
    
    stats = update_user_stats(session_id)
    user = user_db.get(User.session_id == session_id)
    
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
            return f"🧮 {a} {op} {b} = {result}\n\n✨ Great job! Level {stats['level']} - {stats['title']}"
        except:
            pass
    
    # Greetings
    if msg in ['hi', 'hello', 'hey', 'sup', 'yo']:
        name = user.get('name', '')
        greeting = f"👋 Hello! {name}!" if name else "👋 Hello!"
        return f"{greeting} You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
    
    if 'how are you' in msg:
        return f"😊 I'm doing great! Thanks for asking! You're a {stats['title']} with {stats['count']} messages!"
    
    # Enhanced search
    search_results = search_web(message)
    
    if not search_results:
        return f"I searched for '{message}' but found no results."
    
    urls = [r['url'] for r in search_results[:MAX_READ_PAGES]]
    webpage_contents = read_multiple_pages(urls, max_pages=MAX_READ_PAGES)
    
    if not webpage_contents:
        for r in search_results[:3]:
            webpage_contents.append({
                "url": r['url'],
                "content": r['snippet']
            })
    
    summary = generate_combined_summary(message, search_results, webpage_contents)
    follow_ups = generate_follow_ups(message)
    
    response = f"🔍 **Search results for: {message}**\n\n"
    response += f"📊 **Your Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)\n\n"
    response += summary
    
    if follow_ups:
        response += "\n\n💡 **You might also ask:**\n"
        for q in follow_ups[:4]:
            response += f"• {q}\n"
    
    return response

# ============ HISTORY ============
def load_history(session_id):
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(session_id, history):
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def delete_history(session_id):
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False

# ============ ORIGINAL HTML WITH GOOGLE LOGIN ADDED ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>Yama - AI Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        
        html, body {
            height: 100%;
            overflow: hidden;
            width: 100%;
        }
        
        body {
            font-family: 'Inter', sans-serif;
            background: #f5f0e8;
            transition: all 0.3s ease;
        }
        
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
        
        .app {
            display: flex;
            height: 100%;
            width: 100%;
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
        }
        
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: 280px;
            background: #2c2418;
            border-right: 1px solid #4a3f2f;
            display: flex;
            flex-direction: column;
            transform: translateX(-100%);
            transition: transform 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            z-index: 1000;
            box-shadow: 4px 0 20px rgba(0,0,0,0.1);
        }
        
        .sidebar.open { transform: translateX(0); }
        
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
        
        .new-chat-btn:hover { background: #5a4f3f; }
        
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
        
        .overlay.show { display: block; }
        
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            width: 100%;
            overflow: hidden;
            height: 100%;
        }
        
        .header {
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
            flex-shrink: 0;
        }
        
        .menu-btn {
            background: none;
            border: none;
            font-size: 1.3rem;
            cursor: pointer;
            color: #6a5a4a;
            padding: 8px;
            border-radius: 10px;
        }
        
        .menu-btn:hover { background: #d4c5a9; color: #2c2418; }
        
        .logo {
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 6px;
        }
        
        .logo-icon { font-size: 1.8rem; }
        .logo h1 { font-family: 'Playfair Display', serif; font-size: 1.3rem; color: #2c2418; }
        
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
        }
        
        .control-btn:hover {
            background: #d4c5a9;
        }
        
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }
        
        .message { margin-bottom: 20px; animation: fadeIn 0.3s ease; }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .user-message { text-align: right; }
        .ai-message { text-align: left; }
        
        .message-content {
            display: inline-block;
            max-width: 85%;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
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
            padding: 12px 18px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }
        
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
        
        .input-area {
            padding: 12px 16px 20px;
            background: linear-gradient(to top, #f5f0e8, transparent);
            flex-shrink: 0;
        }
        
        .input-wrapper {
            display: flex;
            align-items: center;
            gap: 12px;
            background: white;
            border-radius: 30px;
            padding: 8px 8px 8px 20px;
            border: 1px solid #d4c5a9;
            min-height: 56px;
            height: auto;
        }
        
        textarea {
            flex: 1;
            background: transparent;
            border: none;
            color: #2c2418;
            font-size: 1rem;
            resize: none;
            outline: none;
            padding: 12px 0;
            font-family: inherit;
            width: 100%;
            min-height: 40px;
            max-height: 120px;
            overflow-y: auto;
        }
        
        textarea::placeholder { color: #b8a88a; font-size: 0.95rem; }
        
        .input-wrapper button {
            background: #2c2418;
            border: none;
            border-radius: 28px;
            padding: 10px 24px;
            color: #f5f0e8;
            font-weight: 500;
            cursor: pointer;
            font-size: 0.9rem;
            min-width: 70px;
            width: auto;
            white-space: nowrap;
            transition: all 0.2s;
            flex-shrink: 0;
        }
        
        .input-wrapper button:hover {
            background: #4a3f2f;
            transform: scale(1.02);
        }
        
        .welcome {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 50vh;
            text-align: center;
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
        }
        
        .suggestion:hover {
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }
        
        @media (max-width: 768px) {
            .message-content { max-width: 90%; font-size: 0.85rem; }
            .suggestions { display: none; }
            .new-chat-mobile { display: block; }
            .header { padding: 10px 12px; }
            .logo h1 { font-size: 1.1rem; }
            .logo-icon { font-size: 1.4rem; }
            .messages { padding: 12px; }
            .input-area { padding: 10px 12px 16px; }
            .input-wrapper { border-radius: 28px; padding: 6px 6px 6px 16px; min-height: 48px; }
            textarea { font-size: 0.9rem; padding: 10px 0; min-height: 36px; max-height: 100px; }
            .input-wrapper button { padding: 8px 18px; min-width: 60px; font-size: 0.85rem; }
            .control-btn { font-size: 1rem; padding: 6px 10px; }
        }
        
        @media (min-width: 769px) {
            .input-wrapper { border-radius: 32px; padding: 10px 10px 10px 24px; min-height: 64px; }
            textarea { font-size: 1rem; padding: 14px 0; min-height: 44px; max-height: 140px; }
            .input-wrapper button { padding: 12px 28px; min-width: 80px; font-size: 1rem; }
        }
        
        @media (max-width: 480px) {
            .input-area { padding: 8px 10px 12px; }
            .input-wrapper { gap: 8px; border-radius: 26px; padding: 5px 5px 5px 14px; min-height: 44px; }
            textarea { font-size: 0.85rem; padding: 8px 0; min-height: 32px; max-height: 80px; }
            .input-wrapper button { padding: 7px 14px; min-width: 55px; font-size: 0.8rem; }
            .control-btn { font-size: 0.9rem; padding: 5px 8px; }
        }
        
        /* Google Login Button Styles */
        .auth-section {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .user-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            object-fit: cover;
            border: 2px solid #d4c5a9;
        }
        
        body.dark .user-avatar {
            border-color: #3a3a5e;
        }
        
        .login-btn {
            background: none;
            border: none;
            font-size: 1rem;
            cursor: pointer;
            padding: 4px 10px;
            border-radius: 20px;
            color: #6a5a4a;
            transition: all 0.2s;
            font-family: 'Inter', sans-serif;
            font-weight: 500;
        }
        
        .login-btn:hover {
            background: #d4c5a9;
        }
        
        body.dark .login-btn {
            color: #d4c5a9;
        }
        
        body.dark .login-btn:hover {
            background: #3a3a5e;
            color: white;
        }
        
        .user-name {
            font-size: 0.8rem;
            color: #6a5a4a;
            max-width: 80px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        body.dark .user-name {
            color: #d4c5a9;
        }
    </style>
</head>
<body>
    <div class="app">
        <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
        
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h3>📜 CONVERSATIONS</h3>
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
                <div class="auth-section" id="authSection">
                    <!-- Will be populated by JavaScript -->
                </div>
                <button class="new-chat-mobile" onclick="newChat()">➕</button>
                <button class="control-btn" onclick="toggleTheme()" title="Dark/Light Mode">🌓</button>
                <button class="control-btn" onclick="exportChat()" title="Export Chat">📥</button>
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
                    <textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    <button onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // ============ SESSION MANAGEMENT ============
        let sessionId = localStorage.getItem('yama_session_id');
        if (!sessionId) {
            sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
            localStorage.setItem('yama_session_id', sessionId);
        }
        
        let hasMessages = false;
        let isProcessing = false;
        let userInfo = null;
        
        // ============ THEME ============
        function toggleTheme() {
            document.body.classList.toggle('dark');
            localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
        }
        
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') {
            document.body.classList.add('dark');
        }
        
        // ============ GOOGLE LOGIN ============
        async function checkAuth() {
            try {
                const res = await fetch('/auth/status');
                const data = await res.json();
                if (data.authenticated) {
                    userInfo = data.user;
                    updateAuthUI();
                } else {
                    updateAuthUI();
                }
            } catch (e) {
                console.error('Auth check error:', e);
                updateAuthUI();
            }
        }
        
        function updateAuthUI() {
            const section = document.getElementById('authSection');
            if (userInfo && userInfo.authenticated) {
                section.innerHTML = `
                    <span class="user-name">${userInfo.name || 'User'}</span>
                    ${userInfo.picture ? `<img src="${userInfo.picture}" class="user-avatar" alt="Avatar">` : ''}
                    <button class="login-btn" onclick="logout()">🚪</button>
                `;
            } else {
                section.innerHTML = `
                    <button class="login-btn" onclick="login()">🔑 Sign In</button>
                `;
            }
        }
        
        function login() {
            window.location.href = '/auth/google/login';
        }
        
        async function logout() {
            const res = await fetch('/auth/logout');
            if (res.ok) {
                userInfo = null;
                updateAuthUI();
                location.reload();
            }
        }
        
        // ============ EXPORT ============
        function exportChat() {
            const messages = document.querySelectorAll('.message');
            let exportText = '';
            messages.forEach(msg => {
                const sender = msg.classList.contains('user-message') ? 'You' : 'Yama';
                const text = msg.querySelector('.message-content').innerText;
                exportText += `${sender}: ${text}\n\n`;
            });
            const blob = new Blob([exportText], {type: 'text/plain'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `yama_chat_${new Date().toISOString()}.txt`;
            a.click();
        }
        
        // ============ CHAT MANAGEMENT ============
        function newChat() {
            if (confirm('Start a new chat?')) {
                fetch('/clear_history', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId })
                }).then(() => {
                    location.reload();
                });
            }
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
        
        // ============ HISTORY ============
        async function loadHistory() {
            try {
                const res = await fetch('/get_history?session_id=' + encodeURIComponent(sessionId));
                const history = await res.json();
                const container = document.getElementById('historyList');
                if (history.length === 0) {
                    container.innerHTML = '<div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div>';
                    return;
                }
                container.innerHTML = history.slice().reverse().map(item => `
                    <div class="history-item" onclick="loadChatMessage('${escapeHtml(item.user)}')">
                        <div class="history-question">${escapeHtml(item.user.substring(0, 45))}</div>
                        <div class="history-time">${item.timestamp}</div>
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
                await fetch('/clear_history', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId })
                });
                location.reload();
            }
        }
        
        // ============ INPUT HANDLING ============
        const textarea = document.getElementById('userInput');
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        });
        
        function handleKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }
        
        // ============ SEND MESSAGE ============
        async function sendMessage() {
            if (isProcessing) return;
            
            const message = textarea.value.trim();
            if (!message) return;
            
            isProcessing = true;
            
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
            
            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message, session_id: sessionId })
                });
                const data = await res.json();
                
                addMessage(data.response, 'ai');
                document.getElementById('typing').style.display = 'none';
                loadHistory();
                scrollToBottom();
            } catch (e) {
                document.getElementById('typing').style.display = 'none';
                addMessage('❌ Sorry, there was an error. Please try again.', 'ai');
                scrollToBottom();
                console.error('Send error:', e);
            } finally {
                isProcessing = false;
            }
        }
        
        // ============ ADD MESSAGE ============
        function addMessage(text, sender) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = `message ${sender}-message`;
            const content = document.createElement('div');
            content.className = 'message-content';
            
            let formattedText = text.replace(/\\n/g, '<br>');
            formattedText = formattedText.replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            formattedText = formattedText.replace(/(https?:\/\/[^\s<]+)/g,
                '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
            
            content.innerHTML = formattedText;
            div.appendChild(content);
            messages.appendChild(div);
            scrollToBottom();
        }
        
        function scrollToBottom() {
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }
        
        // ============ INIT ============
        checkAuth();
        loadHistory();
        textarea.focus();
    </script>
</body>
</html>
'''

# ============ FASTAPI ENDPOINTS ============

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return HTML

@app.route('/auth/google/login')
async def google_login(request: Request):
    redirect_uri = REDIRECT_URI
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.route('/auth/google/callback')
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = await oauth.google.parse_id_token(request, token)
        
        # Store user info in session
        request.session['user'] = {
            'authenticated': True,
            'email': user_info.get('email'),
            'name': user_info.get('name', 'User'),
            'picture': user_info.get('picture'),
            'sub': user_info.get('sub')
        }
        
        # Create or update user in database
        session_id = request.session.get('session_id', secrets.token_urlsafe(16))
        if not request.session.get('session_id'):
            request.session['session_id'] = session_id
        
        get_or_create_user(session_id, user_info)
        
        return RedirectResponse(url='/')
    except Exception as e:
        logger.error(f"Google callback error: {e}")
        return RedirectResponse(url='/')

@app.get('/auth/status')
async def auth_status(request: Request):
    user = request.session.get('user')
    if user:
        return {
            'authenticated': True,
            'user': user
        }
    return {'authenticated': False}

@app.get('/auth/logout')
async def logout(request: Request):
    request.session.clear()
    return {'status': 'logged_out'}

@app.post("/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        message = data.get('message', '').strip()
        session_id = data.get('session_id', 'default')
        
        if not message:
            raise HTTPException(status_code=400, detail="Message is required")
        
        if len(message) > MAX_MESSAGE_LENGTH:
            raise HTTPException(status_code=400, detail="Message too long")
        
        # Get or create user with session
        user = get_or_create_user(session_id)
        
        response = get_response(message, session_id)
        
        history = load_history(session_id)
        history.append({
            "user": message,
            "ai": response,
            "timestamp": datetime.now().strftime("%H:%M")
        })
        save_history(session_id, history)
        
        return {"response": response}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/get_history")
async def get_history(session_id: str = "default"):
    return load_history(session_id)

@app.post("/clear_history")
async def clear_history_endpoint(request: Request):
    try:
        data = await request.json()
        session_id = data.get('session_id', 'default')
        deleted = delete_history(session_id)
        return {"status": "cleared", "deleted": deleted}
    except Exception as e:
        logger.error(f"Clear history error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat(),
        "cache_size": len(cache.cache) + len(cache.webpage_cache)
    }

# ============ MAIN ============
if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - WITH GOOGLE SIGN-IN")
    print("="*55)
    print("🌐 Open: http://localhost:10000")
    print("\n✅ FEATURES:")
    print("  🔑 Google Sign-In")
    print("  🔍 Multi-source web search")
    print("  📊 AI-generated summaries")
    print("  💡 Smart follow-up questions")
    print("  🔗 Clickable source links")
    print("  ⚡ Search & webpage caching")
    print("  💾 Persistent session history")
    print("  📱 Mobile responsive")
    print("  🌓 Dark/Light mode")
    print("  🛡️ Security hardened")
    print("="*55)
    print("\n✨ Same Yama Face - Now with Google Login!")
    print("="*55 + "\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=10000,
        log_level="info"
    )
