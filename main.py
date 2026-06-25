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
from concurrent.futures import ThreadPoolExecutor
import hashlib
from typing import List, Dict, Optional
import time

app = FastAPI(title="Yama AI")

# ============ CONFIGURATION ============
MAX_SEARCH_RESULTS = 10
MAX_READ_PAGES = 5
CACHE_DURATION = timedelta(hours=1)
REQUEST_TIMEOUT = 10
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
executor = ThreadPoolExecutor(max_workers=5)

# ============ GOOGLE OAUTH CONFIG ============
# IMPORTANT: Use the EXACT Client ID from your Google Cloud Console
# Based on your screenshot: 4615226032-411aiprsbes52knkch3hlj7reqc6eb.apps.googleusercontent.com
GOOGLE_CLIENT_ID = "4615226032-411aiprsbes52knkch3hlj7reqc6eb.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

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
            "last_seen": datetime.now().isoformat(),
            "google_user": None
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

# ============ GOOGLE SIGN-IN HANDLERS ============
@app.post("/google_login")
async def google_login(request: Request):
    try:
        data = await request.json()
        google_token = data.get('id_token')
        session_id = data.get('session_id', 'default')
        
        if not google_token:
            return JSONResponse({"error": "No token provided", "success": False}, status_code=400)
        
        # Verify the token with Google
        response = requests.get(
            'https://oauth2.googleapis.com/tokeninfo',
            params={'id_token': google_token},
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"Token verification failed: {response.text}")
            return JSONResponse({"error": "Invalid token", "success": False}, status_code=401)
        
        user_info = response.json()
        
        # Verify the audience matches our client ID
        if user_info.get('aud') != GOOGLE_CLIENT_ID:
            print(f"Audience mismatch: {user_info.get('aud')} != {GOOGLE_CLIENT_ID}")
            return JSONResponse({"error": "Invalid audience", "success": False}, status_code=401)
        
        # Get or create user with Google info
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
                "last_seen": datetime.now().isoformat(),
                "google_user": {
                    "email": user_info.get('email'),
                    "name": user_info.get('name'),
                    "picture": user_info.get('picture'),
                    "google_id": user_info.get('sub')
                }
            })
        else:
            user_db.update({
                "google_user": {
                    "email": user_info.get('email'),
                    "name": user_info.get('name'),
                    "picture": user_info.get('picture'),
                    "google_id": user_info.get('sub')
                },
                "last_seen": datetime.now().isoformat()
            }, User.session_id == session_id)
        
        return JSONResponse({
            "success": True,
            "user": user_info.get('name'),
            "email": user_info.get('email'),
            "picture": user_info.get('picture')
        })
        
    except Exception as e:
        print(f"Google login error: {e}")
        return JSONResponse({"error": str(e), "success": False}, status_code=500)

@app.get("/google_user")
async def get_google_user(session_id: str = "default"):
    try:
        user = user_db.get(User.session_id == session_id)
        if user and user.get("google_user"):
            return JSONResponse({
                "logged_in": True,
                "name": user["google_user"].get("name"),
                "email": user["google_user"].get("email"),
                "picture": user["google_user"].get("picture")
            })
        return JSONResponse({"logged_in": False})
    except Exception as e:
        print(f"Error getting Google user: {e}")
        return JSONResponse({"logged_in": False})

@app.post("/google_logout")
async def google_logout(request: Request):
    try:
        data = await request.json()
        session_id = data.get('session_id', 'default')
        user_db.update({"google_user": None}, User.session_id == session_id)
        return JSONResponse({"success": True})
    except Exception as e:
        print(f"Logout error: {e}")
        return JSONResponse({"success": False}, status_code=500)

# ============ ENHANCED SEARCH FUNCTIONS ============

def clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s.,!?-]', '', text)
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
        content_divs = soup.find_all(['div', 'section'], class_=re.compile(r'content|article|post|entry|body', re.I))
        for div in content_divs[:5]:
            for p in div.find_all('p'):
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
        print(f"Search error: {e}")
    
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
        print(f"Error reading {url}: {e}")
    
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
                content = future.result()
                if content[1]:
                    results.append({
                        "url": url,
                        "content": content[1]
                    })
            except Exception as e:
                print(f"Error processing {url}: {e}")
    
    return results

def generate_combined_summary(query: str, search_results: List[Dict], webpage_contents: List[Dict]) -> str:
    all_text = ' '.join([wc['content'] for wc in webpage_contents[:3]])
    
    query_words = set(query.lower().split())
    sentences = re.split(r'[.!?]+', all_text)
    relevant_sentences = []
    
    for sentence in sentences:
        sentence_words = set(sentence.lower().split())
        if len(sentence_words.intersection(query_words)) >= 2:
            relevant_sentences.append(sentence.strip())
    
    response = ""
    
    if relevant_sentences:
        main_answer = relevant_sentences[0]
        main_answer = clean_text(main_answer)
        if len(main_answer) > 200:
            main_answer = main_answer[:200] + "..."
        response += f"📌 {main_answer}\n\n"
    elif search_results:
        response += f"📌 {search_results[0].get('snippet', '')}\n\n"
    
    if len(relevant_sentences) > 1:
        response += "📖 **More details:**\n"
        for sentence in relevant_sentences[1:4]:
            clean_s = clean_text(sentence)
            if len(clean_s) > 20:
                response += f"• {clean_s}\n"
        response += "\n"
    
    key_points = []
    for sentence in relevant_sentences[4:8]:
        clean_s = clean_text(sentence)
        if len(clean_s) > 30 and len(clean_s) < 150:
            key_points.append(clean_s)
    
    if key_points:
        response += "📊 **Key points:**\n"
        for point in key_points[:3]:
            response += f"• {point}\n"
        response += "\n"
    
    if webpage_contents:
        response += "📚 **Sources:**\n"
        for i, wc in enumerate(webpage_contents[:5], 1):
            response += f"{i}. {wc['url']}\n"
        response += "\n"
    
    return response

def generate_follow_ups(query: str) -> List[str]:
    lower_query = query.lower()
    
    if 'what' in lower_query or 'who' in lower_query or 'when' in lower_query:
        return [
            "Explain more simply",
            "Give me examples",
            "Latest updates",
            "Pros and cons",
            "Tell me more"
        ]
    elif 'how' in lower_query or 'why' in lower_query:
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
    
    if msg in ['hi', 'hello', 'hey', 'sup', 'yo']:
        return f"👋 Hello! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
    
    if 'how are you' in msg:
        return f"😊 I'm doing great! Thanks for asking! You're a {stats['title']} with {stats['count']} messages!"
    
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
        response += "\n💡 **You might also ask:**\n"
        for q in follow_ups[:3]:
            response += f"• {q}\n"
    
    return response

# ============ HISTORY ============

def load_history(session_id):
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(session_id, history):
    safe_id = session_id.replace('-', '_').replace('.', '_')
    filepath = f"history_{safe_id}.json"
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============ HTML WITH LOGIN POPUP ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>Yama - AI Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        
        html, body {
            height: 100%;
            overflow: hidden;
            position: fixed;
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
        
        /* Login Overlay Styles */
        .login-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(10px);
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.5s ease;
        }
        
        .login-overlay.hidden {
            opacity: 0;
            pointer-events: none;
        }
        
        .login-box {
            background: white;
            border-radius: 30px;
            padding: 50px 40px 40px;
            max-width: 420px;
            width: 90%;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            animation: slideUp 0.6s ease;
        }
        
        body.dark .login-box {
            background: #1a1a2e;
            border: 1px solid #3a3a5e;
        }
        
        @keyframes slideUp {
            from { transform: translateY(50px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        
        .login-box .logo-icon {
            font-size: 4rem;
            margin-bottom: 10px;
        }
        
        .login-box h1 {
            font-family: 'Playfair Display', serif;
            font-size: 2.5rem;
            color: #2c2418;
            margin-bottom: 8px;
        }
        
        body.dark .login-box h1 {
            color: #d4c5a9;
        }
        
        .login-box p {
            color: #6a5a4a;
            font-size: 0.95rem;
            margin-bottom: 30px;
        }
        
        body.dark .login-box p {
            color: #8a7a6a;
        }
        
        .login-box .google-btn-container {
            display: flex;
            justify-content: center;
            min-height: 50px;
        }
        
        .login-box .sub-text {
            font-size: 0.7rem;
            color: #b8a88a;
            margin-top: 20px;
        }
        
        body.dark .login-box .sub-text {
            color: #6a5a7a;
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
        
        /* User Profile in Top Corner */
        .user-profile-container {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            position: relative;
        }
        
        .user-profile-circle {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            border: 2px solid #d4c5a9;
            object-fit: cover;
            transition: all 0.2s;
            background: #2c2418;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 0.8rem;
            font-weight: 600;
        }
        
        .user-profile-circle:hover {
            transform: scale(1.05);
            border-color: #c4a57b;
        }
        
        .user-profile-circle.default {
            background: #4a3f2f;
            font-size: 1.2rem;
        }
        
        body.dark .user-profile-circle {
            border-color: #3a3a5e;
        }
        
        .profile-dropdown {
            position: absolute;
            top: 45px;
            right: 0;
            background: white;
            border-radius: 16px;
            padding: 12px 0;
            min-width: 180px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            display: none;
            z-index: 100;
            border: 1px solid #e8e0d5;
        }
        
        body.dark .profile-dropdown {
            background: #1a1a2e;
            border-color: #3a3a5e;
        }
        
        .profile-dropdown.show {
            display: block;
            animation: slideDown 0.2s ease;
        }
        
        @keyframes slideDown {
            from { transform: translateY(-10px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        
        .profile-dropdown-item {
            padding: 10px 20px;
            display: flex;
            align-items: center;
            gap: 12px;
            color: #2c2418;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
            border: none;
            background: none;
            width: 100%;
            text-align: left;
        }
        
        body.dark .profile-dropdown-item {
            color: #d4c5a9;
        }
        
        .profile-dropdown-item:hover {
            background: rgba(44, 36, 24, 0.08);
        }
        
        body.dark .profile-dropdown-item:hover {
            background: rgba(212, 197, 169, 0.08);
        }
        
        .profile-dropdown-item .user-info {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
        
        .profile-dropdown-item .user-name-display {
            font-weight: 600;
        }
        
        .profile-dropdown-item .user-email-display {
            font-size: 0.7rem;
            color: #8a7a6a;
        }
        
        body.dark .profile-dropdown-item .user-email-display {
            color: #6a5a7a;
        }
        
        .profile-dropdown-divider {
            height: 1px;
            background: #e8e0d5;
            margin: 6px 0;
        }
        
        body.dark .profile-dropdown-divider {
            background: #3a3a5e;
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
            .user-profile-circle { width: 30px; height: 30px; font-size: 0.7rem; }
            .login-box { padding: 30px 20px 25px; }
            .login-box h1 { font-size: 2rem; }
            .login-box .logo-icon { font-size: 3rem; }
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
            .user-profile-circle { width: 26px; height: 26px; font-size: 0.6rem; }
            .login-box { padding: 25px 15px 20px; }
            .login-box h1 { font-size: 1.8rem; }
            .login-box .logo-icon { font-size: 2.5rem; }
        }
    </style>
</head>
<body>
    <!-- Login Overlay -->
    <div class="login-overlay" id="loginOverlay">
        <div class="login-box">
            <div class="logo-icon">🏛️</div>
            <h1>Yama AI</h1>
            <p>Sign in to start your AI journey</p>
            <div class="google-btn-container" id="loginGoogleBtn"></div>
            <div class="sub-text">Secure login with your Google account</div>
        </div>
    </div>

    <!-- Main App -->
    <div class="app" id="app" style="display:none;">
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
                <div class="user-profile-container" id="profileContainer">
                    <!-- Profile will be rendered here -->
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
        let sessionId = 'session_' + Date.now();
        let hasMessages = false;
        let googleUser = null;
        let isLoggedIn = false;
        let googleInitialized = false;
        let loginOverlay = document.getElementById('loginOverlay');
        let app = document.getElementById('app');
        
        // IMPORTANT: Use the EXACT Client ID from your Google Cloud Console
        const GOOGLE_CLIENT_ID = '4615226032-411aiprsbes52knkch3hlj7reqc6eb.apps.googleusercontent.com';
        
        console.log('Using Google Client ID:', GOOGLE_CLIENT_ID);
        
        // ============ GOOGLE SIGN-IN ============
        function initGoogleSignIn() {
            if (typeof google !== 'undefined' && google.accounts) {
                console.log('Initializing Google Sign-In...');
                google.accounts.id.initialize({
                    client_id: GOOGLE_CLIENT_ID,
                    callback: handleGoogleCredentialResponse,
                    auto_select: false,
                    cancel_on_tap_outside: true
                });
                
                // Render login button in overlay
                const loginBtn = document.getElementById('loginGoogleBtn');
                if (loginBtn) {
                    google.accounts.id.renderButton(
                        loginBtn,
                        { 
                            type: 'standard',
                            theme: 'outline',
                            size: 'large',
                            text: 'signin_with',
                            shape: 'pill',
                            logo_alignment: 'left',
                            width: 280
                        }
                    );
                    console.log('Login button rendered');
                }
                
                googleInitialized = true;
                
                // Check if user is already logged in
                checkGoogleUser();
            } else {
                console.log('Google Sign-In script not loaded, retrying...');
                setTimeout(initGoogleSignIn, 500);
            }
        }
        
        async function handleGoogleCredentialResponse(response) {
            console.log('Google Sign-In credential received');
            
            try {
                const res = await fetch('/google_login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        id_token: response.credential,
                        session_id: sessionId
                    })
                });
                
                const data = await res.json();
                console.log('Login response:', data);
                
                if (data.success) {
                    googleUser = data;
                    isLoggedIn = true;
                    hideLoginOverlay();
                    updateProfileUI(data);
                    addMessage(`👋 Welcome, ${data.user}! You're signed in with Google.`, 'ai');
                    scrollToBottom();
                } else {
                    console.error('Login failed:', data.error);
                    alert('Login failed: ' + (data.error || 'Please try again'));
                }
            } catch (error) {
                console.error('Error during Google login:', error);
                alert('Error connecting to Google. Please try again.');
            }
        }
        
        async function checkGoogleUser() {
            try {
                const res = await fetch('/google_user?session_id=' + encodeURIComponent(sessionId));
                const data = await res.json();
                if (data.logged_in) {
                    googleUser = data;
                    isLoggedIn = true;
                    hideLoginOverlay();
                    updateProfileUI(data);
                    console.log('User already logged in:', data.name);
                }
            } catch (error) {
                console.error('Error checking Google user:', error);
            }
        }
        
        function hideLoginOverlay() {
            loginOverlay.classList.add('hidden');
            app.style.display = 'flex';
            setTimeout(() => {
                loginOverlay.style.display = 'none';
            }, 500);
        }
        
        function updateProfileUI(data) {
            const container = document.getElementById('profileContainer');
            const initial = data.name ? data.name.charAt(0).toUpperCase() : 'U';
            const hasPicture = data.picture && data.picture.startsWith('http');
            
            container.innerHTML = `
                <div class="user-profile-container" onclick="toggleProfileDropdown()">
                    ${hasPicture ? 
                        `<img src="${data.picture}" class="user-profile-circle" alt="Profile">` :
                        `<div class="user-profile-circle default">${initial}</div>`
                    }
                </div>
                <div class="profile-dropdown" id="profileDropdown">
                    <div class="profile-dropdown-item" style="cursor:default;">
                        <div class="user-info">
                            <span class="user-name-display">${data.name || 'User'}</span>
                            <span class="user-email-display">${data.email || ''}</span>
                        </div>
                    </div>
                    <div class="profile-dropdown-divider"></div>
                    <button class="profile-dropdown-item" onclick="logoutGoogle()">
                        🚪 Sign Out
                    </button>
                </div>
            `;
        }
        
        function toggleProfileDropdown() {
            const dropdown = document.getElementById('profileDropdown');
            if (dropdown) {
                dropdown.classList.toggle('show');
            }
        }
        
        // Close dropdown when clicking outside
        document.addEventListener('click', function(event) {
            const container = document.getElementById('profileContainer');
            if (container && !container.contains(event.target)) {
                const dropdown = document.getElementById('profileDropdown');
                if (dropdown) {
                    dropdown.classList.remove('show');
                }
            }
        });
        
        async function logoutGoogle() {
            if (!confirm('Sign out from Google?')) return;
            
            try {
                await fetch('/google_logout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId })
                });
                
                googleUser = null;
                isLoggedIn = false;
                
                // Show login overlay again
                loginOverlay.style.display = 'flex';
                loginOverlay.classList.remove('hidden');
                app.style.display = 'none';
                
                // Reset profile
                document.getElementById('profileContainer').innerHTML = '';
                
                addMessage('👋 You have been signed out.', 'ai');
                scrollToBottom();
            } catch (error) {
                console.error('Error during logout:', error);
            }
        }
        
        // ============ THEME ============
        function toggleTheme() {
            document.body.classList.toggle('dark');
            localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
        }
        
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') {
            document.body.classList.add('dark');
        }
        
        // ============ CHAT FUNCTIONS ============
        function exportChat() {
            const messages = document.querySelectorAll('.message');
            let exportText = '';
            messages.forEach(msg => {
                const sender = msg.classList.contains('user-message') ? 'You' : 'Yama';
                const text = msg.querySelector('.message-content').innerText;
                exportText += `${sender}: ${text}\\n\\n`;
            });
            const blob = new Blob([exportText], {type: 'text/plain'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `yama_chat_${new Date().toISOString()}.txt`;
            a.click();
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
        
        async function sendMessage() {
            if (!isLoggedIn) {
                alert('Please sign in with Google first!');
                return;
            }
            
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
                body: JSON.stringify({ message: message, session_id: sessionId })
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
            div.className = `message ${sender}-message`;
            const content = document.createElement('div');
            content.className = 'message-content';
            let formattedText = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            formattedText = formattedText.replace(/(https?:\\/\\/[^\\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
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
        window.onload = function() {
            initGoogleSignIn();
            loadHistory();
            textarea.focus();
        };
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
    
    response = get_response(message, session_id)
    
    history = load_history(session_id)
    history.append({
        "user": message,
        "ai": response,
        "timestamp": datetime.now().strftime("%H:%M")
    })
    save_history(session_id, history)
    
    return {"response": response}

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
    print("🏛️ YAMA AI - GOOGLE LOGIN POPUP (FIXED)")
    print("="*55)
    print("🌐 Open: http://localhost:10000")
    print("🔐 Client ID:", GOOGLE_CLIENT_ID)
    print("📌 Make sure this matches your Google Cloud Console!")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
