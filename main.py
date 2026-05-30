# yama_complete.py - 🏛️ Logo + Google Search + Original UI
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

# ============ GOOGLE SEARCH (WORKING) ============

def google_search(query):
    """Search Google and get real results"""
    results = []
    try:
        url = f"https://www.google.com/search?q={quote(query)}&num=10"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
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
                results.append({"title": title, "snippet": snippet, "url": link})
    except Exception as e:
        print(f"Google search error: {e}")
    
    # Fallback to DuckDuckGo
    if not results:
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(ddg_url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for result in soup.find_all('div', class_='result')[:7]:
                title_elem = result.find('a', class_='result__a')
                snippet_elem = result.find('a', class_='result__snippet')
                
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    link = title_elem.get('href', '')
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    results.append({"title": title, "snippet": snippet, "url": link})
        except:
            pass
    
    return results

def get_response(message):
    msg = message.lower().strip()
    
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
            return f"🧮 {a} {op} {b} = {result}"
        except:
            pass
    
    # Search Google for everything else
    search_results = google_search(message)
    
    if not search_results:
        return f"I searched for '{message}' but found no results. Please try a different question."
    
    response = f"**🔍 Search results for: {message}**\n\n"
    for i, r in enumerate(search_results[:7], 1):
        response += f"**{i}. {r['title']}**\n"
        response += f"{r['snippet']}\n"
        response += f"🔗 {r['url']}\n\n"
    
    return response

# ============ HISTORY ============
HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============ ORIGINAL UI WITH 🏛️ LOGO ============
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Yama - AI Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: #f5f0e8;
            height: 100vh;
            overflow: hidden;
        }
        .app {
            display: flex;
            height: 100vh;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            position: relative;
        }
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: 300px;
            background: #2c2418;
            border-right: 1px solid #4a3f2f;
            display: flex;
            flex-direction: column;
            transform: translateX(-100%);
            transition: transform 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            z-index: 100;
            box-shadow: 4px 0 20px rgba(0,0,0,0.1);
        }
        .sidebar.open { transform: translateX(0); }
        .sidebar-header {
            padding: 28px 24px;
            border-bottom: 1px solid #4a3f2f;
            background: #1f1912;
        }
        .sidebar-header h3 {
            color: #d4c5a9;
            font-family: 'Playfair Display', serif;
            font-size: 1.1rem;
            margin-bottom: 16px;
        }
        .clear-history {
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 8px 16px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.75rem;
        }
        .clear-history:hover {
            background: rgba(212,197,169,0.2);
            border-color: #c4a57b;
            color: #c4a57b;
        }
        .history-list {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
        }
        .history-item {
            padding: 12px 16px;
            margin-bottom: 6px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid transparent;
        }
        .history-item:hover {
            background: rgba(212,197,169,0.08);
            border-color: #4a3f2f;
        }
        .history-question {
            font-size: 0.85rem;
            color: #d4c5a9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .history-time {
            font-size: 0.65rem;
            color: #6a5a4a;
            margin-top: 6px;
        }
        .overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.4);
            display: none;
            z-index: 90;
        }
        .overlay.show { display: block; }
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            width: 100%;
        }
        .header {
            padding: 20px 32px;
            display: flex;
            align-items: center;
            gap: 20px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
        }
        .menu-btn {
            background: none;
            border: none;
            font-size: 1.5rem;
            cursor: pointer;
            color: #6a5a4a;
            padding: 8px;
            border-radius: 12px;
        }
        .menu-btn:hover {
            background: #d4c5a9;
            color: #2c2418;
        }
        .logo {
            flex: 1;
            transition: all 0.4s ease;
            display: flex;
            align-items: baseline;
            gap: 8px;
        }
        .logo-icon { font-size: 2rem; }
        .logo h1 {
            font-family: 'Playfair Display', serif;
            font-size: 1.8rem;
            color: #2c2418;
            letter-spacing: 2px;
        }
        .logo p {
            font-size: 0.7rem;
            color: #6a5a4a;
            letter-spacing: 3px;
            margin-left: 10px;
        }
        .logo.small { transform: scale(0.7) translateX(-30px); }
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 32px;
            scroll-behavior: smooth;
        }
        .messages::-webkit-scrollbar { width: 6px; }
        .messages::-webkit-scrollbar-track { background: #e8e0d5; border-radius: 10px; }
        .messages::-webkit-scrollbar-thumb { background: #c4a57b; border-radius: 10px; }
        .message { margin-bottom: 28px; animation: fadeIn 0.4s ease; }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(15px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .user-message { text-align: right; }
        .ai-message { text-align: left; }
        .message-content {
            display: inline-block;
            max-width: 85%;
            font-size: 0.95rem;
            line-height: 1.7;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
        }
        .user-message .message-content { color: #2c2418; }
        .ai-message .message-content { color: #2c2418; }
        .user-message {
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(44,36,24,0.1);
        }
        .typing {
            display: none;
            padding: 16px 32px;
            gap: 6px;
        }
        .typing span {
            width: 8px;
            height: 8px;
            background: #c4a57b;
            border-radius: 50%;
            display: inline-block;
            animation: bounce 1.4s infinite;
        }
        @keyframes bounce {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-8px); }
        }
        .input-area {
            padding: 20px 32px 28px;
            background: linear-gradient(to top, #f5f0e8, transparent);
        }
        .input-wrapper {
            display: flex;
            gap: 12px;
            background: white;
            border-radius: 50px;
            padding: 6px 6px 6px 24px;
            border: 1px solid #d4c5a9;
        }
        .input-wrapper:focus-within {
            border-color: #c4a57b;
            box-shadow: 0 4px 15px rgba(196,165,123,0.1);
        }
        textarea {
            flex: 1;
            background: transparent;
            border: none;
            color: #2c2418;
            font-size: 0.95rem;
            resize: none;
            outline: none;
            padding: 12px 0;
            font-family: inherit;
        }
        textarea::placeholder { color: #b8a88a; }
        button {
            background: #2c2418;
            border: none;
            border-radius: 40px;
            padding: 12px 28px;
            color: #f5f0e8;
            font-weight: 500;
            cursor: pointer;
        }
        button:hover { background: #4a3f2f; transform: scale(1.02); }
        .welcome {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 65vh;
            text-align: center;
        }
        .welcome-icon {
            font-size: 4rem;
            margin-bottom: 20px;
            animation: float 3s ease-in-out infinite;
        }
        @keyframes float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-10px); }
        }
        .welcome h2 {
            font-family: 'Playfair Display', serif;
            font-size: 3rem;
            color: #2c2418;
            margin-bottom: 12px;
        }
        .welcome p {
            color: #6a5a4a;
            font-size: 0.95rem;
            margin-bottom: 24px;
        }
        .suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            justify-content: center;
            margin-top: 20px;
        }
        .suggestion {
            background: white;
            border: 1px solid #d4c5a9;
            border-radius: 30px;
            padding: 10px 20px;
            font-size: 0.85rem;
            color: #2c2418;
            cursor: pointer;
        }
        .suggestion:hover {
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }
        @media (max-width: 768px) {
            .messages { padding: 20px; }
            .message-content { max-width: 100%; }
            .suggestions { display: none; }
        }
    </style>
</head>
<body>
    <div class="app">
        <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h3>📜 CONVERSATIONS</h3>
                <button class="clear-history" onclick="clearHistory()">Clear history</button>
            </div>
            <div class="history-list" id="historyList"><div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div></div>
        </div>
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()">☰</button>
                <div class="logo" id="logo">
                    <span class="logo-icon">🏛️</span>
                    <h1>YAMA</h1>
                    <p>AI ASSISTANT</p>
                </div>
                <div style="width: 40px;"></div>
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
            <div class="typing" id="typing"><span></span><span></span><span></span></div>
            <div class="input-area">
                <div class="input-wrapper">
                    <textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    <button onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let hasMessages = false;
        
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
            container.innerHTML = history.slice().reverse().map(item => `<div class="history-item" onclick="loadChatMessage('${escapeHtml(item.user)}')"><div class="history-question">${escapeHtml(item.user.substring(0, 45))}</div><div class="history-time">${item.timestamp}</div></div>`).join('');
        }
        function escapeHtml(t) { const div = document.createElement('div'); div.textContent = t; return div.innerHTML; }
        function loadChatMessage(msg) { document.getElementById('userInput').value = msg; closeSidebar(); sendMessage(); }
        async function clearHistory() { if (confirm('Clear all history?')) { await fetch('/clear_history', { method: 'POST' }); location.reload(); } }
        const textarea = document.getElementById('userInput');
        textarea.addEventListener('input', function() { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 120) + 'px'; });
        function handleKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }
        async function sendMessage() {
            const message = textarea.value.trim();
            if (!message) return;
            if (!hasMessages) { const welcome = document.getElementById('welcome'); if (welcome) welcome.style.display = 'none'; hasMessages = true; document.getElementById('logo').classList.add('small'); }
            addMessage(message, 'user');
            textarea.value = '';
            textarea.style.height = 'auto';
            document.getElementById('typing').style.display = 'flex';
            scrollToBottom();
            const res = await fetch('/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: message }) });
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
        function scrollToBottom() { const messages = document.getElementById('messages'); messages.scrollTop = messages.scrollHeight; }
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
    print("\n" + "="*55)
    print("🏛️ YAMA AI - COMPLETE EDITION")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("🏛️ Original Logo Restored!")
    print("🔍 Google Search Working!")
    print("📜 Chat History with ☰ menu")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)                                                                                         
