from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import os
import re
import asyncio
from datetime import datetime
from ddgs import DDGS
import random
import string
import qrcode
from io import BytesIO
import base64
import requests
from thefuzz import fuzz, process
import dateparser
from tinydb import TinyDB, Query

app = FastAPI(title="Yama AI")

# ============ NEW FEATURES SETUP ============

# User Leveling System with TinyDB
user_db = TinyDB('user_stats.json')
User = Query()

# Session memory for conversations
session_memory = {}

# Synonym mapping
synonyms = {
    "hi": ["hello", "hey", "yo", "sup", "hii", "heyy", "greetings"],
    "how are you": ["how r u", "how're you", "how you doing", "how's it going"],
    "bye": ["goodbye", "see you", "bye bye", "take care"],
    "thanks": ["thank you", "thx", "thank u", "appreciate it"]
}

# Keyword weights for intent detection
keyword_weights = {
    "weather": 10,
    "temperature": 10,
    "rain": 8,
    "stock": 10,
    "price": 8,
    "discount": 8,
    "sale": 7,
    "news": 9
}

# ============ NEW FEATURE 1: URL SHORTENER & QR CODE ============

def shorten_url(long_url):
    """Shorten URL using free tinyurl API"""
    try:
        response = requests.get(f"https://tinyurl.com/api-create.php?url={long_url}")
        if response.status_code == 200:
            return response.text
    except:
        pass
    return long_url

def generate_qr_code(data):
    """Generate QR code image as base64"""
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return f"data:image/png;base64,{img_str}"
    except:
        return None

# ============ NEW FEATURE 2: USER LEVELING SYSTEM ============

def get_user_level(user_id):
    """Get or create user stats"""
    user = user_db.get(User.user_id == user_id)
    if not user:
        user_db.insert({
            "user_id": user_id,
            "message_count": 0,
            "level": 1,
            "title": "Newbie Chatter",
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        })
        user = user_db.get(User.user_id == user_id)
    return user

def update_user_stats(user_id):
    """Update message count and level"""
    user = get_user_level(user_id)
    new_count = user.get("message_count", 0) + 1
    new_level = 1 + (new_count // 50)
    
    titles = {
        1: "Newbie Chatter",
        2: "Regular Talker",
        3: "Chatty User",
        4: "Power User",
        5: "Super Chat Master",
        6: "Ultimate Reviewer",
        7: "Yama Legend"
    }
    new_title = titles.get(new_level, "Yama Legend")
    
    user_db.update({
        "message_count": new_count,
        "level": new_level,
        "title": new_title,
        "last_seen": datetime.now().isoformat()
    }, User.user_id == user_id)
    
    return {"count": new_count, "level": new_level, "title": new_title}

# ============ NEW FEATURE 3: SESSION MEMORY ============

def get_session_memory(session_id):
    """Get or create session memory"""
    if session_id not in session_memory:
        session_memory[session_id] = {
            "user_name": None,
            "issue": None,
            "last_topic": None,
            "facts": []
        }
    return session_memory[session_id]

def update_session_memory(session_id, key, value):
    """Update session memory"""
    memory = get_session_memory(session_id)
    memory[key] = value
    session_memory[session_id] = memory

# ============ NEW FEATURE 4: FUZZY MATCHING & KEYWORD WEIGHTING ============

def fuzzy_match(user_input, target_list, threshold=80):
    """Check if user input matches any target using fuzzy matching"""
    for target in target_list:
        if fuzz.ratio(user_input.lower(), target.lower()) >= threshold:
            return True
    return False

def calculate_intent_weight(message):
    """Calculate intent score based on keyword weights"""
    score = 0
    for keyword, weight in keyword_weights.items():
        if keyword in message.lower():
            score += weight
    return score

# ============ NEW FEATURE 5: DATE/TIME EXTRACTION ============

def extract_datetime(text):
    """Extract date and time from natural language"""
    try:
        parsed = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})
        if parsed:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except:
        pass
    return None

# ============ NEW FEATURE 6: TEXT ANALYSIS (Simple version without spaCy) ============

def analyze_text(text):
    """Simple text analysis without spaCy"""
    sentences = text.split('.')
    keywords = [w for w in text.lower().split() if len(w) > 3][:5]
    return {
        "sentences": sentences,
        "keywords": keywords,
        "is_question": text.strip().endswith("?")
    }

# ============ DDGS SEARCH (WORKING) - UNCHANGED ============

def search_web(query):
    """Search using DDGS (DuckDuckGo Search) - WORKS ON RENDER"""
    results = []
    try:
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=7))
            for r in search_results:
                results.append({
                    "title": r.get('title', ''),
                    "snippet": r.get('body', '')[:300],
                    "url": r.get('href', '')
                })
    except Exception as e:
        print(f"Search error: {e}")
    return results

# ============ NEW FEATURE 7: EXTERNAL API (Weather) ============

def get_weather(city):
    """Get weather using free wttr.in API"""
    try:
        response = requests.get(f"https://wttr.in/{city}?format=%C+%t")
        if response.status_code == 200:
            return f"🌤️ Weather in {city}: {response.text}"
    except:
        pass
    return None

# ============ MAIN RESPONSE WITH ALL NEW FEATURES (ORIGINAL KEPT) ============

def get_response(message, session_id="default"):
    msg = message.strip().lower()
    
    # Update user stats
    stats = update_user_stats(session_id)
    
    # Update session memory
    update_session_memory(session_id, "last_topic", msg)
    
    # Check for name memory
    name_match = re.search(r'my name is (\w+)|i am (\w+)|call me (\w+)', msg)
    if name_match:
        name = name_match.group(1) or name_match.group(2) or name_match.group(3)
        update_session_memory(session_id, "user_name", name)
        return f"✨ Nice to meet you, {name}! I'll remember that. (You're a {stats['title']} with {stats['count']} messages!)"
    
    # Check for URL shortening request
    if 'shorten' in msg and ('http' in msg or 'https' in msg):
        url_match = re.search(r'(https?://[^\s]+)', msg)
        if url_match:
            short = shorten_url(url_match.group(1))
            qr = generate_qr_code(url_match.group(1))
            response = f"🔗 **Shortened URL:** {short}\n\n"
            if qr:
                response += f"📱 **QR Code:**\n![QR Code]({qr})"
            return response
    
    # Check for weather request
    if 'weather' in msg:
        city_match = re.search(r'weather in (\w+)', msg)
        if city_match:
            weather = get_weather(city_match.group(1))
            if weather:
                return weather
    
    # Math (ORIGINAL - UNCHANGED)
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
            user_level = stats['title']
            return f"🧮 {a} {op} {b} = {result}\n\n✨ Great math, {user_level}! {stats['count']} messages so far!"
        except:
            pass
    
    # Greetings with fuzzy matching (ORIGINAL + IMPROVED)
    greetings_list = ["hi", "hello", "hey", "sup", "yo", "hii", "heyy"]
    if fuzzy_match(msg, greetings_list) or msg in synonyms.get("hi", []):
        memory = get_session_memory(session_id)
        name_part = f", {memory['user_name']}" if memory['user_name'] else ""
        return f"👋 Hello{name_part}! I'm Yama. You're a **{stats['title']}** with {stats['count']} messages! How can I help you today?"
    
    if 'how are you' in msg or fuzzy_match(msg, ["how are you", "how r u", "how're you"]):
        return f"😊 I'm doing great! Thanks for asking! (You're a {stats['title']} with {stats['count']} messages!)"
    
    # Thank you response
    if 'thank' in msg or fuzzy_match(msg, ["thanks", "thank you", "thx"]):
        return f"✨ You're very welcome! Happy to help a {stats['title']} like you! 😊"
    
    # Analyze text
    analysis = analyze_text(message)
    
    # Search using DDGS (ORIGINAL - UNCHANGED)
    search_results = search_web(message)
    
    if not search_results:
        return f"I searched for '{message}' but found no results. Please try a different question."
    
    # ORIGINAL RESPONSE FORMAT - KEPT EXACTLY THE SAME
    response = f"**🔍 Search results for: {message}**\n\n"
    response += f"📊 **Your Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)\n\n"
    
    for i, r in enumerate(search_results[:7], 1):
        response += f"**{i}. {r['title']}**\n"
        response += f"{r['snippet']}\n"
        response += f"🔗 {r['url']}\n\n"
    
    # Add datetime extraction info if applicable (NEW - EXTRA)
    extracted_date = extract_datetime(message)
    if extracted_date:
        response += f"\n📅 *Detected date/time: {extracted_date}*\n"
    
    # Add session memory info (NEW - EXTRA)
    memory = get_session_memory(session_id)
    if memory['user_name']:
        response += f"\n💭 *I remember you're {memory['user_name']}!*\n"
    
    # Add keyword weight score (NEW - EXTRA)
    intent_score = calculate_intent_weight(message)
    if intent_score > 15:
        response += f"\n🎯 *High intent detected (score: {intent_score})*\n"
    
    return response

# ============ HISTORY (UNCHANGED) ============
HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============ COMPLETE UI (YOUR EXACT HTML - UNCHANGED) ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
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
        }
        
        .app {
            display: flex;
            height: 100%;
            width: 100%;
            position: relative;
            overflow: hidden;
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
            padding: 0 8px 0 20px;
            border: 1px solid #d4c5a9;
            min-height: 60px;
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
            padding: 16px 0;
            font-family: inherit;
            width: calc(100% - 90px);
            min-height: 56px;
            max-height: 120px;
        }
        
        textarea::placeholder { color: #b8a88a; font-size: 0.95rem; }
        
        .input-wrapper button {
            background: #2c2418;
            border: none;
            border-radius: 28px;
            padding: 12px 24px;
            color: #f5f0e8;
            font-weight: 500;
            cursor: pointer;
            font-size: 0.9rem;
            min-width: 70px;
            width: auto;
            transition: all 0.2s;
            flex-shrink: 0;
        }
        
        .input-wrapper button:hover { background: #4a3f2f; transform: scale(1.02); }
        
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
            .input-wrapper { border-radius: 28px; padding: 0 6px 0 16px; min-height: 56px; }
            textarea { font-size: 0.9rem; padding: 14px 0; min-height: 52px; width: calc(100% - 80px); }
            .input-wrapper button { padding: 10px 18px; min-width: 65px; font-size: 0.85rem; border-radius: 25px; }
        }
        
        @media (min-width: 769px) {
            .input-wrapper { border-radius: 32px; padding: 0 10px 0 22px; min-height: 64px; }
            textarea { font-size: 1rem; padding: 18px 0; min-height: 60px; width: calc(100% - 90px); }
            .input-wrapper button { padding: 14px 28px; min-width: 80px; font-size: 1rem; border-radius: 30px; }
        }
        
        @media (max-width: 480px) {
            .input-area { padding: 8px 10px 12px; }
            .input-wrapper { gap: 8px; border-radius: 26px; min-height: 52px; }
            textarea { font-size: 0.85rem; padding: 12px 0; min-height: 48px; width: calc(100% - 75px); }
            .input-wrapper button { padding: 8px 14px; min-width: 60px; font-size: 0.8rem; border-radius: 24px; }
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
                <button class="new-chat-mobile" onclick="newChat()">➕</button>
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
            const res = await fetch('/get_history');
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
            const message = textarea.value.trim();
            if (!message) return;
            
            if (!hasMessages) {
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
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

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get('message', '')
    session_id = data.get('session_id', 'default')
    
    response = get_response(message, session_id)
    
    history = load_history()
    history.append({
        "id": len(history),
        "user": message,
        "ai": response,
        "timestamp": datetime.now().strftime("%H:%M")
    })
    save_history(history)
    
    return {"response": response}

@app.get("/get_history")
async def get_history():
    return load_history()

@app.post("/clear_history")
async def clear_history_endpoint():
    save_history([])
    return {"status": "cleared"}

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - COMPLETE EDITION")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("🏛️ Original Logo Restored!")
    print("🔍 DDGS Search Working!")
    print("📜 Chat History with ☰ menu")
    print("➕ New Chat Button")
    print("📱 Mobile Optimized")
    print("✨ NEW: URL Shortener & QR Code")
    print("✨ NEW: User Leveling System")
    print("✨ NEW: Session Memory")
    print("✨ NEW: Fuzzy Matching")
    print("✨ NEW: Keyword Weighting")
    print("✨ NEW: Date/Time Extraction")
    print("✨ NEW: Weather API")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
