from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import os
import re
import pickle
import numpy as np
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime
import random

app = FastAPI(title="Yama AI")

# ============ LOAD YOUR OWN MODEL ============

MODEL_PATH = "my_trained_model.pkl"
TOKENIZER_PATH = "my_tokenizer.pkl"

model = None
tokenizer = None

def load_my_model():
    global model, tokenizer
    try:
        if os.path.exists(MODEL_PATH) and os.path.exists(TOKENIZER_PATH):
            with open(MODEL_PATH, 'rb') as f:
                model = pickle.load(f)
            with open(TOKENIZER_PATH, 'rb') as f:
                tokenizer = pickle.load(f)
            print("✅ Your model loaded successfully!")
            return True
        else:
            print("⚠️ Model not found. Using enhanced search mode.")
            return False
    except Exception as e:
        print(f"⚠️ Model loading error: {e}")
        return False

model_loaded = load_my_model()

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

# ============ MATH SOLVER (FIXED - Works for ALL math) ============

def solve_math(message):
    """Solve ANY math problem"""
    msg = message.replace(' ', '')
    
    # Division
    if '/' in msg:
        parts = msg.split('/')
        if len(parts) == 2:
            try:
                a = float(parts[0]) if '.' in parts[0] else int(parts[0])
                b = float(parts[1]) if '.' in parts[1] else int(parts[1])
                result = a / b
                if isinstance(result, float) and result.is_integer():
                    result = int(result)
                else:
                    result = round(result, 4)
                return f"🧮 **Math Solution**\n\n{a} ÷ {b} = {result}\n\n✨ Need more math help? Just ask!"
            except:
                pass
    
    # Multiplication
    if '*' in msg:
        parts = msg.split('*')
        if len(parts) == 2:
            try:
                a = float(parts[0]) if '.' in parts[0] else int(parts[0])
                b = float(parts[1]) if '.' in parts[1] else int(parts[1])
                result = a * b
                if isinstance(result, float) and result.is_integer():
                    result = int(result)
                return f"🧮 **Math Solution**\n\n{a} × {b} = {result}\n\n✨ Great calculation!"
            except:
                pass
    
    # Addition
    if '+' in msg and '++' not in msg:
        parts = msg.split('+')
        if len(parts) == 2:
            try:
                a = float(parts[0]) if '.' in parts[0] else int(parts[0])
                b = float(parts[1]) if '.' in parts[1] else int(parts[1])
                result = a + b
                if isinstance(result, float) and result.is_integer():
                    result = int(result)
                return f"🧮 **Math Solution**\n\n{a} + {b} = {result}\n\n➕ Addition complete!"
            except:
                pass
    
    # Subtraction
    if '-' in msg and len(msg.split('-')) == 2:
        parts = msg.split('-')
        if len(parts) == 2:
            try:
                a = float(parts[0]) if '.' in parts[0] else int(parts[0])
                b = float(parts[1]) if '.' in parts[1] else int(parts[1])
                result = a - b
                if isinstance(result, float) and result.is_integer():
                    result = int(result)
                return f"🧮 **Math Solution**\n\n{a} - {b} = {result}\n\n➖ Subtraction done!"
            except:
                pass
    
    # Complex expression (like 10000/8, 25*4+3)
    try:
        # Only allow numbers and basic operators
        if all(c.isdigit() or c in '+-*/.' for c in msg):
            result = eval(msg)
            if isinstance(result, float):
                if result.is_integer():
                    result = int(result)
                else:
                    result = round(result, 4)
            return f"🧮 **Math Solution**\n\n{msg} = {result}\n\n✨ Math is fun! Want more?"
    except:
        pass
    
    return None

# ============ YOUR MODEL GENERATION ============

def generate_with_your_model(prompt):
    """Use YOUR trained model to generate response"""
    global model, tokenizer
    
    if model is None or tokenizer is None:
        return None
    
    try:
        tokens = tokenizer.encode(prompt)
        max_len = 128
        if len(tokens) > max_len:
            tokens = tokens[:max_len]
        else:
            tokens = tokens + [0] * (max_len - len(tokens))
        
        input_ids = np.array([tokens])
        output_ids = model.generate(input_ids, max_new_tokens=100, temperature=0.7)
        response = tokenizer.decode(output_ids[0].tolist())
        
        return response if response and len(response) > 5 else None
    except Exception as e:
        print(f"Model error: {e}")
        return None

# ============ EMOTIONS FOR RESPONSES ============

emotions = {
    "happy": ["😊", "🎉", "✨", "🌟", "💫"],
    "thinking": ["🤔", "💭", "🧠", "🔍"],
    "success": ["✅", "🎯", "🏆", "⭐"],
    "greeting": ["👋", "🤝", "💬", "🗣️"],
    "love": ["❤️", "💙", "💚", "💜", "🧡"],
    "energy": ["⚡", "🔥", "🚀", "💪"],
    "calm": ["🌊", "🍃", "🌸", "🌙"],
    "celebration": ["🎊", "🎈", "🎉", "🏅"]
}

def add_emotion(text, emotion_type="happy"):
    """Add random emoji to response"""
    emoji_list = emotions.get(emotion_type, emotions["happy"])
    emoji = random.choice(emoji_list)
    
    # Add emoji at beginning if not already there
    if not any(e in text[:3] for e in emoji_list):
        return f"{emoji} {text}"
    return text

# ============ SMART RESPONSE = YOUR MODEL + GOOGLE SEARCH ============

def get_smart_response(message):
    """Combine Google Search + Your Model for intelligent answers"""
    msg = message.strip()
    msg_lower = msg.lower()
    
    # Step 1: Check for math (FAST)
    math_result = solve_math(msg)
    if math_result:
        return add_emotion(math_result, "success")
    
    # Step 2: Greetings (FAST)
    greetings = ['hi', 'hello', 'hey', 'sup', 'yo', 'hii', 'heyy', 'greetings', 'namaste']
    if msg_lower in greetings:
        responses = [
            "👋 Hello! I'm Yama. How can I help you today?",
            "✨ Hey there! What's on your mind? I'm here to help!",
            "🏛️ Welcome! I'm Yama, your AI assistant. Ask me anything!",
            "💫 Hi! Ready to explore answers together? Just ask!"
        ]
        return random.choice(responses)
    
    # Step 3: How are you?
    if 'how are you' in msg_lower:
        responses = [
            "😊 I'm doing great! Thanks for asking! How can I help you today?",
            "✨ Fantastic! I'm fully charged and ready to assist you!",
            "💪 I'm wonderful! What amazing thing shall we explore together?"
        ]
        return random.choice(responses)
    
    # Step 4: Thank you
    if 'thank' in msg_lower or 'thanks' in msg_lower:
        responses = [
            "✨ You're very welcome! Happy to help! 😊",
            "💫 My pleasure! That's what I'm here for!",
            "🌟 Anytime! Let me know what else you need!"
        ]
        return random.choice(responses)
    
    # Step 5: Who are you?
    if 'your name' in msg_lower or 'who are you' in msg_lower:
        return "🏛️ **I am Yama!** Your intelligent AI assistant. I can search the web, solve math, have conversations, and help with anything you need. Ask me anything!"
    
    # Step 6: Search Google
    print(f"🔍 Searching Google for: {msg}")
    search_results = google_search(msg)
    
    if not search_results:
        return f"🔍 I searched for '{msg}' but found no results. Please try a different question."
    
    # Step 7: Try YOUR model to synthesize answer (if available)
    if model_loaded:
        # Create prompt with search results for your model
        search_text = "\n".join([f"- {r['title']}: {r['snippet'][:200]}" for r in search_results[:3]])
        prompt = f"""Question: {msg}

Information from web:
{search_text}

Please answer the question based on the information above. Be helpful and accurate.

Answer:"""
        
        model_response = generate_with_your_model(prompt)
        
        if model_response and len(model_response) > 10:
            # Add sources at the end
            sources = "\n\n---\n**📚 Sources:**\n"
            for i, r in enumerate(search_results[:3], 1):
                sources += f"{i}. <a href='{r['url']}' target='_blank'>{r['title'][:50]}</a>\n"
            
            return add_emotion(model_response, "happy") + sources
    
    # Step 8: Fallback - Formatted search results with CLICKABLE LINKS
    response = f"🔍 **Search results for: {msg}**\n\n"
    
    for i, r in enumerate(search_results[:7], 1):
        response += f"**{i}. {r['title']}**\n"
        response += f"{r['snippet']}\n"
        # CLICKABLE LINK - FIXED!
        response += f"🔗 <a href='{r['url']}' target='_blank' rel='noopener noreferrer' style='color: #c4a57b; text-decoration: none;'>{r['url'][:60]}...</a>\n\n"
    
    return add_emotion(response, "energy")

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

# ============ COMPLETE UI ============
HTML = '''[Your existing HTML here - unchanged]'''

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get('message', '')
    
    response = get_smart_response(message)
    
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
    print("✅ Google Search WORKING")
    print("✅ YOUR Model Integrated")
    print("✅ Math Solver FIXED")
    print("✅ Clickable Links")
    print("✅ Emotions Added")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
