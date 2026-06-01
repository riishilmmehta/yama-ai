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

app = FastAPI(title="Yama AI")

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

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
            "title": "Newbie",
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
            1: "🌱 Newbie",
            2: "💬 Talker",
            3: "🔥 Chatter",
            4: "⚡ Power User",
            5: "👑 Master",
            6: "🏆 Legend",
            7: "🧠 Yama God"
        }
        new_title = titles.get(new_level, "🧠 Yama God")
        
        user_db.update({
            "message_count": new_count,
            "level": new_level,
            "title": new_title,
            "last_seen": datetime.now().isoformat()
        }, User.email == email)
        
        return {"count": new_count, "level": new_level, "title": new_title}
    return {"count": 0, "level": 1, "title": "Newbie"}

# ============ SEARCH FUNCTION ============

def search_web(query):
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
            return f"🧮 {a} {op} {b} = {result}\n\n✨ Great job, {user_name}! Level {stats['level']} - {stats['title']} ({stats['count']} msgs)"
        except:
            pass
    
    # Greetings
    if msg in ['hi', 'hello', 'hey', 'sup', 'yo']:
        return f"👋 Hello {user_name}! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
    
    if 'how are you' in msg:
        return f"😊 I'm doing great! Thanks for asking, {user_name}! (Level {stats['level']} {stats['title']})"
    
    # Search
    search_results = search_web(message)
    
    if not search_results:
        return f"I searched for '{message}' but found no results."
    
    response = f"🔍 **Search results for: {message}**\n\n"
    response += f"📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)\n\n"
    
    for i, r in enumerate(search_results[:7], 1):
        response += f"**{i}. {r['title']}**\n"
        response += f"{r['snippet']}\n"
        response += f"🔗 {r['url']}\n\n"
    
    return response

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

# ============ GOOGLE CLIENT ID ============
GOOGLE_CLIENT_ID = "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com"

# ============ COMPLETE HTML WITH AUTO-ADJUST INPUT ============
HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
        
        html, body {{
            height: 100%;
            overflow: hidden;
            position: fixed;
            width: 100%;
        }}
        
        body {{
            font-family: 'Inter', sans-serif;
            background: #f5f0e8;
            transition: all 0.3s ease;
        }}
        
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
        }}
        
        .login-card {{
            background: white;
            border-radius: 30px;
            padding: 40px 30px;
            text-align: center;
            max-width: 400px;
            width: 85%;
            box-shadow: 0 25px 50px rgba(0,0,0,0.2);
        }}
        
        .login-card .logo-icon {{ font-size: 3.5rem; margin-bottom: 15px; }}
        .login-card h2 {{ font-family: 'Playfair Display', serif; font-size: 1.8rem; margin-bottom: 8px; }}
        .login-card p {{ color: #666; font-size: 0.85rem; margin-bottom: 25px; }}
        
        .app {{
            display: none;
            height: 100vh;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
        }}
        
        body.dark .app {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        }}
        
        .sidebar {{
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
        }}
        
        .sidebar.open {{ transform: translateX(0); }}
        
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
        
        .user-level {{
            color: #c4a57b;
            font-size: 0.6rem;
            margin-top: 2px;
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
            font-size: 0.75rem;
            color: #d4c5a9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .history-time {{
            font-size: 0.55rem;
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
            padding: 12px;
            color: #d4c5a9;
            cursor: pointer;
            width: 100%;
            font-size: 0.8rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s;
        }}
        
        .new-chat-btn:hover {{ background: #5a4f3f; }}
        
        .clear-history {{
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 8px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.65rem;
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
        
        .overlay.show {{ display: block; }}
        
        .main {{
            flex: 1;
            display: flex;
            flex-direction: column;
            width: 100%;
            overflow: hidden;
            height: 100%;
        }}
        
        .header {{
            padding: 10px 16px;
            display: flex;
            align-items: center;
            gap: 10px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
            flex-shrink: 0;
        }}
        
        body.dark .header {{
            background: rgba(26,26,46,0.95);
            border-bottom-color: #2a2a4e;
        }}
        
        .menu-btn {{
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            color: #6a5a4a;
            padding: 8px;
            border-radius: 10px;
        }}
        
        .menu-btn:hover {{ background: #d4c5a9; color: #2c2418; }}
        
        .logo {{
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 5px;
        }}
        
        .logo-icon {{ font-size: 1.5rem; }}
        .logo h1 {{ font-family: 'Playfair Display', serif; font-size: 1.2rem; color: #2c2418; }}
        body.dark .logo h1 {{ color: #d4c5a9; }}
        
        .user-btn {{
            background: none;
            border: none;
            cursor: pointer;
            display: none;
            padding: 5px;
        }}
        
        .user-btn img {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            object-fit: cover;
        }}
        
        .new-chat-mobile {{
            background: none;
            border: none;
            font-size: 1.1rem;
            cursor: pointer;
            padding: 6px;
            border-radius: 10px;
            color: #6a5a4a;
            display: none;
        }}
        
        .control-btn {{
            background: none;
            border: none;
            font-size: 1rem;
            cursor: pointer;
            padding: 6px 10px;
            border-radius: 20px;
            color: #6a5a4a;
            transition: all 0.2s;
        }}
        
        .control-btn:hover {{ background: #d4c5a9; }}
        body.dark .control-btn {{ color: #d4c5a9; }}
        body.dark .control-btn:hover {{ background: #3a3a5e; color: white; }}
        
        .messages {{
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }}
        
        .message {{ margin-bottom: 20px; animation: fadeIn 0.3s ease; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .user-message {{ text-align: right; }}
        .ai-message {{ text-align: left; }}
        
        .message-content {{
            display: inline-block;
            max-width: 85%;
            font-size: 0.85rem;
            line-height: 1.5;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
        }}
        
        .user-message .message-content {{
            background: #2c2418 !important;
            color: white !important;
            padding: 8px 14px !important;
            border-radius: 20px !important;
        }}
        
        .ai-message .message-content {{
            background: white !important;
            color: #2c2418 !important;
            padding: 10px 16px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }}
        
        body.dark .ai-message .message-content {{ background: #2a2a4e !important; color: #e0e0e0 !important; }}
        
        .typing {{
            display: none;
            padding: 10px 16px;
            gap: 5px;
            color: #888;
            font-size: 0.75rem;
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
        
        @keyframes bounce {{ 0%, 60%, 100% {{ transform: translateY(0); }} 30% {{ transform: translateY(-6px); }} }}
        
        /* ========== AUTO-ADJUST INPUT FIELD ========== */
        .input-area {{
            padding: 12px 16px 20px;
            background: linear-gradient(to top, #f5f0e8, transparent);
            flex-shrink: 0;
        }}
        
        /* Responsive container that handles all screen variations */
        .chat-input-wrapper {{
            display: flex;
            align-items: flex-end;
            gap: 8px;
            width: 100%;
            max-width: 800px;
            padding: 8px 12px;
            background-color: #ffffff;
            border: 1px solid #d4c5a9;
            border-radius: 24px;
            box-sizing: border-box;
            margin: 0 auto;
        }}
        
        body.dark .chat-input-wrapper {{
            background-color: #2a2a4e;
            border-color: #3a3a5e;
        }}
        
        /* Flex-grow input field */
        .chat-input-field {{
            flex: 1;
            min-width: 0;
            min-height: 24px;
            max-height: 160px;
            padding: 8px 4px;
            font-size: 16px;
            line-height: 1.5;
            border: none;
            outline: none;
            resize: none;
            background: transparent;
            font-family: 'Inter', sans-serif;
        }}
        
        body.dark .chat-input-field {{
            color: #e0e0e0;
        }}
        
        .chat-input-field::placeholder {{
            color: #b8a88a;
        }}
        
        /* Fixed-size touch-friendly button */
        .chat-submit-btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            border: none;
            background-color: #2c2418;
            color: #ffffff;
            cursor: pointer;
            transition: background-color 0.2s, transform 0.2s;
        }}
        
        .chat-submit-btn:hover {{
            background-color: #4a3f2f;
            transform: scale(1.02);
        }}
        
        body.dark .chat-submit-btn {{
            background-color: #4a3f2f;
        }}
        
        body.dark .chat-submit-btn:hover {{
            background-color: #5a4f3f;
        }}
        
        .send-icon {{
            width: 20px;
            height: 20px;
            fill: currentColor;
        }}
        
        /* Mobile adjustments */
        @media (max-width: 768px) {{
            .input-area {{
                padding: 10px 12px 16px;
            }}
            .chat-input-wrapper {{
                padding: 6px 10px;
                border-radius: 28px;
            }}
            .chat-input-field {{
                font-size: 15px;
                padding: 6px 2px;
                min-height: 20px;
                max-height: 120px;
            }}
            .chat-submit-btn {{
                width: 38px;
                height: 38px;
            }}
            .send-icon {{
                width: 18px;
                height: 18px;
            }}
        }}
        
        @media (max-width: 480px) {{
            .input-area {{
                padding: 8px 10px 14px;
            }}
            .chat-input-wrapper {{
                padding: 5px 8px;
                gap: 6px;
                border-radius: 26px;
            }}
            .chat-input-field {{
                font-size: 14px;
                padding: 5px 2px;
            }}
            .chat-submit-btn {{
                width: 34px;
                height: 34px;
            }}
            .send-icon {{
                width: 16px;
                height: 16px;
            }}
        }}
        
        .welcome {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 50vh;
            text-align: center;
        }}
        
        .welcome-icon {{ font-size: 2.5rem; margin-bottom: 12px; animation: float 3s ease-in-out infinite; }}
        @keyframes float {{ 0%, 100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-8px); }} }}
        .welcome h2 {{ font-family: 'Playfair Display', serif; font-size: 1.8rem; margin-bottom: 6px; }}
        body.dark .welcome h2 {{ color: #d4c5a9; }}
        .welcome p {{ color: #6a5a4a; font-size: 0.8rem; margin-bottom: 20px; }}
        
        .suggestions {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
        }}
        
        .suggestion {{
            background: white;
            border: 1px solid #d4c5a9;
            border-radius: 30px;
            padding: 5px 12px;
            font-size: 0.7rem;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        .suggestion:hover {{ background: #2c2418; color: white; border-color: #2c2418; }}
        body.dark .suggestion {{ background: #2a2a4e; border-color: #3a3a5e; color: #e0e0e0; }}
        body.dark .suggestion:hover {{ background: #3a3a5e; color: white; }}
        
        @media (max-width: 768px) {{
            .message-content {{ max-width: 90%; font-size: 0.8rem; }}
            .suggestions {{ display: none; }}
            .new-chat-mobile {{ display: block; }}
            .header {{ padding: 8px 12px; }}
            .logo h1 {{ font-size: 1rem; }}
            .logo-icon {{ font-size: 1.3rem; }}
            .messages {{ padding: 12px; }}
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
                 data-client_id=\"{GOOGLE_CLIENT_ID}\"
                 data-context=\"signin\"
                 data-ux_mode=\"popup\"
                 data-callback=\"handleCredentialResponse\"
                 data-auto_prompt=\"false\">
            </div>
            <div class=\"g_id_signin\"
                 data-type=\"standard\"
                 data-shape=\"rectangular\"
                 data-theme=\"outline\"
                 data-text=\"signin_with\"
                 data-size=\"large\"
                 data-logo_alignment=\"left\">
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
                <div style="color: #6a5a4a; text-align: center; padding: 20px; font-size: 0.75rem;">No conversations yet</div>
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
                <div class="chat-input-wrapper">
                    <textarea class="chat-input-field" id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    <button class="chat-submit-btn" onclick="sendMessage()" aria-label="Send message">
                        <svg class="send-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
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
        let userLevel = 1;
        let userTitle = "Newbie";
        
        // Auto-height adjustment for textarea
        const tx = document.getElementById('userInput');
        if (tx) {{
            tx.addEventListener('input', function() {{
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
            }});
        }}
        
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
                <img src="${{currentUser.picture}}" class="user-profile-img">
                <div class="user-profile-info">
                    <div class="user-profile-name">${{currentUser.name}}</div>
                    <div class="user-profile-email">${{currentUser.email}}</div>
                    <div class="user-level" id="sidebarUserLevel">🏆 Level 1 - Newbie</div>
                </div>
                <button class="logout-btn" onclick="logout()">Logout</button>
            `;
            
            loadHistory();
            loadUserLevel();
            
            fetch('/set_user', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ email: currentUser.email, name: currentUser.name, picture: currentUser.picture }})
            }});
        }}
        
        async function loadUserLevel() {{
            if (!currentUser) return;
            const res = await fetch('/get_user_stats?email=' + encodeURIComponent(currentUser.email));
            const stats = await res.json();
            userLevel = stats.level;
            userTitle = stats.title;
            const sidebarLevel = document.getElementById('sidebarUserLevel');
            if (sidebarLevel) {{
                sidebarLevel.innerHTML = `🏆 Level ${{userLevel}} - ${{userTitle}}`;
            }}
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
                container.innerHTML = '<div style=\"color:#6a5a4a;text-align:center;padding:20px;font-size:0.75rem;\">No conversations yet</div>';
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
        
        function handleKey(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }}
        
        async function sendMessage() {{
            if (!currentUser) {{ alert('Please sign in first!'); return; }}
            const message = document.getElementById('userInput').value.trim();
            if (!message) return;
            
            if (!hasMessages) {{
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
                document.getElementById('logo').classList.add('small');
            }}
            
            addMessage(message, 'user');
            document.getElementById('userInput').value = '';
            document.getElementById('userInput').style.height = 'auto';
            
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
            loadUserLevel();
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
        
        // Focus on input
        document.getElementById('userInput').focus();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML

@app.get("/get_user_stats")
async def get_user_stats(email: str = ""):
    user = user_db.get(User.email == email)
    if user:
        return {"level": user.get("level", 1), "title": user.get("title", "Newbie")}
    return {"level": 1, "title": "Newbie"}

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

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - COMPLETE EDITION")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("🔐 Google Sign-In Working")
    print("📊 Level System Working")
    print("📱 Auto-Adjust Input Button - PERFECT!")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
