from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime

app = FastAPI(title="Yama AI")

# ============ WORKING SEARCH ============

def search_web(query):
    results = []
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for result in soup.find_all('div', class_='result')[:7]:
            title_elem = result.find('a', class_='result__a')
            snippet_elem = result.find('a', class_='result__snippet')
            
            if title_elem:
                title = title_elem.get_text(strip=True)
                link = title_elem.get('href', '')
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                if link and link.startswith('http'):
                    results.append({"title": title, "snippet": snippet[:300], "url": link})
    except:
        pass
    
    if not results:
        try:
            url = f"https://www.google.com/search?q={quote(query)}&num=10"
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for result in soup.find_all('div', class_='g')[:7]:
                title_elem = result.find('h3')
                link_elem = result.find('a')
                snippet_elem = result.find('div', class_='VwiC3b')
                
                if title_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    link = link_elem.get('href', '')
                    if link.startswith('/url?q='):
                        link = link.split('/url?q=')[1].split('&')[0]
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    results.append({"title": title, "snippet": snippet[:300], "url": link})
        except:
            pass
    
    return results

def get_response(message):
    msg = message.strip()
    
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
            return f"🧮 {a} {op} {b} = {result}"
        except:
            pass
    
    search_results = search_web(msg)
    
    if not search_results:
        return f"I searched for '{msg}' but found no results. Please try a different question."
    
    response = f"🔍 **Search results for: {msg}**\n\n"
    for i, r in enumerate(search_results[:7], 1):
        response += f"**{i}. {r['title']}**\n"
        response += f"{r['snippet']}\n"
        response += f"🔗 {r['url']}\n\n"
    
    return response

HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============ UI WITH NEW CHAT AT BOTTOM ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        
        body {
            font-family: 'Inter', sans-serif;
            background: #f5f0e8;
            height: 100vh;
            overflow: hidden;
            position: fixed;
            width: 100%;
        }
        
        .app {
            display: flex;
            height: 100vh;
            width: 100%;
            position: relative;
            overflow: hidden;
        }
        
        /* Sidebar - New Chat at Bottom */
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
        
        .sidebar.open {
            transform: translateX(0);
        }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid #4a3f2f;
            background: #1f1912;
        }
        
        .sidebar-header h3 {
            color: #d4c5a9;
            font-family: 'Playfair Display', serif;
            font-size: 1rem;
            margin-bottom: 12px;
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
        
        /* New Chat Button at Bottom */
        .sidebar-footer {
            padding: 16px;
            border-top: 1px solid #4a3f2f;
            background: #1f1912;
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
        
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            width: 100%;
            overflow: hidden;
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
        
        .menu-btn:hover {
            background: #d4c5a9;
            color: #2c2418;
        }
        
        .logo {
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 6px;
        }
        
        .logo-icon {
            font-size: 1.8rem;
        }
        
        .logo h1 {
            font-family: 'Playfair Display', serif;
            font-size: 1.3rem;
            color: #2c2418;
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
        
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
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
        
        .typing {
            display: none;
            padding: 10px 16px;
            gap: 5px;
            color: #888;
            font-size: 0.8rem;
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
            padding: 12px 16px 16px;
            background: linear-gradient(to top, #f5f0e8, transparent);
            flex-shrink: 0;
        }
        
        .input-wrapper {
            display: flex;
            gap: 8px;
            background: white;
            border-radius: 30px;
            padding: 5px 5px 5px 16px;
            border: 1px solid #d4c5a9;
        }
        
        textarea {
            flex: 1;
            background: transparent;
            border: none;
            color: #2c2418;
            font-size: 0.9rem;
            resize: none;
            outline: none;
            padding: 10px 0;
            font-family: inherit;
        }
        
        textarea::placeholder {
            color: #b8a88a;
        }
        
        button {
            background: #2c2418;
            border: none;
            border-radius: 30px;
            padding: 10px 20px;
            color: #f5f0e8;
            font-weight: 500;
            cursor: pointer;
        }
        
        button:hover {
            background: #4a3f2f;
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
        
        /* Mobile Responsive */
        @media (max-width: 768px) {
            .message-content {
                max-width: 90%;
                font-size: 0.85rem;
            }
            
            .suggestions {
                display: none;
            }
            
            .new-chat-mobile {
                display: block;
            }
            
            .header {
                padding: 10px 12px;
            }
            
            .logo h1 {
                font-size: 1.1rem;
            }
            
            .logo-icon {
                font-size: 1.4rem;
            }
            
            .messages {
                padding: 12px;
            }
            
            .input-area {
                padding: 10px 12px 14px;
            }
            
            button {
                padding: 8px 16px;
            }
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
                <button class="new-chat-btn" onclick="newChat()">
                    ➕ New Chat
                </button>
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
            if (confirm('Start a new chat? Current conversation will be saved in history.')) {
                location.reload();
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
            if (confirm('Clear all conversation history?')) {
                await fetch('/clear_history', { method: 'POST' });
                location.reload();
            }
        }
        
        const textarea = document.getElementById('userInput');
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 100) + 'px';
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
                body: JSON.stringify({ message: message })
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
    response = get_response(message)
    
    history = load_history()
    history.append({
        "id": len(history),
        "user": message,
        "ai": response[:500],
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
    uvicorn.run(app, host="0.0.0.0", port=10000)
