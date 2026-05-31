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

# ============ RESPONSE FUNCTION ============

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
    
    # Search
    search_results = search_web(message)
    
    if not search_results:
        return f"I searched for '{message}' but found no results."
    
    # Regular results
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
GOOGLE_CLIENT_ID = "46152262032-c9qrascvpktt4bkhlcnqsuce10pm0mcv.apps.googleusercontent.com"

# ============ HTML WITH GOOGLE SIGN-IN ============
HTML = f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>Yama - AI Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #f5f0e8;
            height: 100vh;
            overflow: hidden;
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
            padding: 50px;
            text-align: center;
            max-width: 450px;
            width: 90%;
            box-shadow: 0 25px 50px rgba(0,0,0,0.2);
        }}
        
        .login-card .logo-icon {{ font-size: 4rem; margin-bottom: 20px; }}
        .login-card h2 {{ font-family: 'Playfair Display', serif; font-size: 2rem; margin-bottom: 10px; }}
        .login-card p {{ color: #666; margin-bottom: 30px; }}
        
        .app {{
            display: none;
            height: 100vh;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
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
            transition: transform 0.3s ease;
            z-index: 1000;
        }}
        
        .sidebar.open {{ transform: translateX(0); }}
        
        .sidebar-header {{ padding: 20px; border-bottom: 1px solid #4a3f2f; background: #1f1912; }}
        .sidebar-header h3 {{ color: #d4c5a9; font-family: 'Playfair Display', serif; margin-bottom: 15px; }}
        
        .user-profile {{ display: flex; align-items: center; gap: 12px; padding: 10px; background: rgba(212,197,169,0.1); border-radius: 12px; }}
        .user-profile-img {{ width: 45px; height: 45px; border-radius: 50%; object-fit: cover; }}
        .user-profile-name {{ color: #d4c5a9; font-weight: 600; font-size: 0.9rem; }}
        .user-profile-email {{ color: #8a7a6a; font-size: 0.7rem; }}
        .logout-btn {{ background: rgba(212,197,169,0.1); border: 1px solid #4a3f2f; border-radius: 20px; padding: 6px 12px; color: #d4c5a9; cursor: pointer; font-size: 0.7rem; }}
        
        .history-list {{ flex: 1; overflow-y: auto; padding: 12px; }}
        .history-item {{ padding: 10px; margin-bottom: 6px; border-radius: 10px; cursor: pointer; background: rgba(212,197,169,0.05); }}
        .history-item:hover {{ background: rgba(212,197,169,0.15); }}
        .history-question {{ font-size: 0.8rem; color: #d4c5a9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .history-time {{ font-size: 0.6rem; color: #6a5a4a; margin-top: 4px; }}
        
        .sidebar-footer {{ padding: 16px; border-top: 1px solid #4a3f2f; background: #1f1912; }}
        .new-chat-btn {{ background: #4a3f2f; border: none; border-radius: 25px; padding: 12px; color: #d4c5a9; cursor: pointer; width: 100%; font-size: 0.85rem; }}
        
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
        
        .main {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; }}
        
        .header {{
            padding: 12px 20px;
            display: flex;
            align-items: center;
            gap: 15px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
        }}
        
        .menu-btn {{ background: none; border: none; font-size: 1.3rem; cursor: pointer; color: #6a5a4a; padding: 8px; border-radius: 10px; }}
        .logo {{ flex: 1; display: flex; align-items: baseline; gap: 8px; }}
        .logo-icon {{ font-size: 1.8rem; }}
        .logo h1 {{ font-family: 'Playfair Display', serif; font-size: 1.3rem; color: #2c2418; }}
        
        .user-btn {{ background: none; border: none; cursor: pointer; }}
        .user-btn img {{ width: 35px; height: 35px; border-radius: 50%; }}
        
        .messages {{ flex: 1; overflow-y: auto; padding: 20px; }}
        .message {{ margin-bottom: 20px; animation: fadeIn 0.3s ease; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .user-message {{ text-align: right; }}
        .ai-message {{ text-align: left; }}
        
        .message-content {{
            display: inline-block;
            max-width: 80%;
            padding: 12px 18px;
            border-radius: 20px;
            font-size: 0.9rem;
            line-height: 1.5;
        }}
        .user-message .message-content {{ background: #2c2418; color: white; }}
        .ai-message .message-content {{ background: white; color: #2c2418; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
        
        .typing {{ display: none; padding: 10px 20px; color: #888; font-size: 0.8rem; }}
        .typing span {{ display: inline-block; width: 6px; height: 6px; background: #c4a57b; border-radius: 50%; margin: 0 2px; animation: bounce 1.4s infinite; }}
        @keyframes bounce {{ 0%, 60%, 100% {{ transform: translateY(0); }} 30% {{ transform: translateY(-6px); }} }}
        
        .input-area {{ padding: 15px 20px 25px; background: linear-gradient(to top, #f5f0e8, transparent); }}
        .input-wrapper {{ display: flex; gap: 12px; background: white; border-radius: 30px; padding: 8px 8px 8px 20px; border: 1px solid #d4c5a9; }}
        textarea {{ flex: 1; background: transparent; border: none; font-size: 0.9rem; resize: none; outline: none; padding: 12px 0; font-family: inherit; }}
        button {{ background: #2c2418; border: none; border-radius: 30px; padding: 10px 24px; color: white; cursor: pointer; }}
        
        .welcome {{ text-align: center; padding: 40px; }}
        .welcome-icon {{ font-size: 3rem; margin-bottom: 15px; animation: float 3s ease-in-out infinite; }}
        @keyframes float {{ 0%, 100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-8px); }} }}
        .welcome h2 {{ font-family: 'Playfair Display', serif; font-size: 2rem; margin-bottom: 8px; }}
        .welcome p {{ color: #6a5a4a; margin-bottom: 20px; }}
        .suggestions {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }}
        .suggestion {{ background: white; border: 1px solid #d4c5a9; border-radius: 30px; padding: 8px 16px; font-size: 0.8rem; cursor: pointer; }}
        
        @media (max-width: 768px) {{
            .message-content {{ max-width: 90%; }}
            .suggestions {{ display: none; }}
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
            </div>
        </div>
        
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()">☰</button>
                <div class="logo">
                    <span class="logo-icon">🏛️</span>
                    <h1>YAMA</h1>
                </div>
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
                    </div>
                </div>
            </div>
            
            <div class="typing" id="typing"><span></span><span></span><span></span> Yama is thinking...</div>
            
            <div class="input-area">
                <div class="input-wrapper">
                    <textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    <button onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentUser = null;
        let hasMessages = false;
        
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
            
            document.getElementById('userAvatar').src = currentUser.picture;
            document.getElementById('userProfile').innerHTML = `
                <img src="${{currentUser.picture}}" class="user-profile-img">
                <div class="user-profile-info">
                    <div class="user-profile-name">${{currentUser.name}}</div>
                    <div class="user-profile-email">${{currentUser.email}}</div>
                </div>
                <button class="logout-btn" onclick="logout()">Logout</button>
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
            if (google && google.accounts) {{
                google.accounts.id.disableAutoSelect();
            }}
        }}
        
        function toggleUserMenu() {{ document.getElementById('sidebar').classList.toggle('open'); }}
        function toggleSidebar() {{ document.getElementById('sidebar').classList.toggle('open'); document.getElementById('overlay').classList.toggle('show'); }}
        function closeSidebar() {{ document.getElementById('sidebar').classList.remove('open'); document.getElementById('overlay').classList.remove('show'); }}
        function newChat() {{ if (confirm('Start a new chat?')) location.reload(); }}
        function askSuggestion(q) {{ document.getElementById('userInput').value = q; sendMessage(); }}
        
        async function loadHistory() {{
            if (!currentUser) return;
            const res = await fetch('/get_history?email=' + encodeURIComponent(currentUser.email));
            const history = await res.json();
            const container = document.getElementById('historyList');
            if (history.length === 0) {{
                container.innerHTML = '<div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div>';
                return;
            }}
            container.innerHTML = history.slice().reverse().map(item => `
                <div class="history-item" onclick="loadChatMessage('${{escapeHtml(item.user)}}')">
                    <div class="history-question">${{escapeHtml(item.user.substring(0, 45))}}</div>
                    <div class="history-time">${{item.timestamp}}</div>
                </div>
            `).join('');
        }}
        
        function escapeHtml(text) {{ const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }}
        function loadChatMessage(msg) {{ document.getElementById('userInput').value = msg; closeSidebar(); sendMessage(); }}
        
        const textarea = document.getElementById('userInput');
        textarea.addEventListener('input', function() {{ this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 100) + 'px'; }});
        function handleKey(e) {{ if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); sendMessage(); }} }}
        
        async function sendMessage() {{
            if (!currentUser) {{ alert('Please sign in first!'); return; }}
            const message = textarea.value.trim();
            if (!message) return;
            if (!hasMessages) {{ const welcome = document.getElementById('welcome'); if (welcome) welcome.style.display = 'none'; hasMessages = true; }}
            
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
            div.className = `message ${{sender}}-message`;
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = text.replace(/\\n/g, '<br>');
            div.appendChild(content);
            messages.appendChild(div);
            scrollToBottom();
        }}
        
        function scrollToBottom() {{ const messages = document.getElementById('messages'); messages.scrollTop = messages.scrollHeight; }}
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

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - WITH GOOGLE SIGN-IN")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("🔐 Sign in with Google to start")
    print("✨ Features: User Profiles, Leveling System, Chat History")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
