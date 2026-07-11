from __future__ import annotations
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
import uvicorn
import json
import os
import re
import ast
import math
import operator
import hashlib
import time
from datetime import datetime, timedelta
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urlparse, parse_qs
from tinydb import TinyDB, Query
import secrets
import io
import base64
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import logging
from collections import defaultdict
from functools import lru_cache
import random

# ============ NEW IMPORTS ============
# Mathematics
import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import mpmath
import scipy
from scipy import stats

# Chemistry
import chempy
from chempy import balance_stoichiometry, Substance
from chempy.chemistry import Species
import mendeleev
from mendeleev import element

# Language Tools
from deep_translator import GoogleTranslator
import language_tool_python
import nltk
from nltk.tokenize import sent_tokenize, word_tokenize
from nltk.corpus import stopwords
from nltk.probability import FreqDist
try:
    from langdetect import detect
except:
    detect = None

# Time & Date
import pytz
from zoneinfo import ZoneInfo
import holidays

# Developer Utilities
import secrets
import hashlib
import re
import json
import rjsmin
import rcssmin
from urllib.parse import urlparse
import socket
import whois
import dns.resolver
import ssl
# NOTE: Do NOT `import datetime` here. `from datetime import datetime, timedelta`
# was already done above. A bare `import datetime` rebinds the name `datetime`
# to the MODULE, which silently breaks every `datetime.now()` call in this
# file with "module 'datetime' has no attribute 'now'"-style failures. This
# was a fatal, hard-to-spot bug and has been removed on purpose.

# Productivity
import qrcode
from qrcode.image.pil import PilImage
import geopy
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import barcode
from barcode.writer import ImageWriter

# Optional OCR support (image -> text -> solve). Degrades gracefully if not installed.
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# ============ LOGGING SETUP ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('yama.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============ NLTK SETUP ============
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
    nltk.download('stopwords')

# ============ FASTAPI APP ============
app = FastAPI(title="Yama AI")

# ============ GOOGLE SIGN-IN CLIENT ID ============
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com")

# ============ DATA DIRECTORY ============
DATA_DIR = os.environ.get("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============ USER DATABASE ============
user_db = TinyDB(os.path.join(DATA_DIR, 'users.json'))
User = Query()

# ============ CONTEXT MEMORY ============
MEMORY_TURN_LIMIT = 15
_context_memory = {}

class ConversationContext:
    """Stores and manages conversation context for follow-up questions."""

    def __init__(self):
        self.topic = None
        self.entity = None
        self.intent = None
        self.category = None
        self.last_question = None
        self.last_answer = None

    def update(self, topic=None, entity=None, intent=None, category=None, question=None, answer=None):
        if topic is not None:
            self.topic = topic
        if entity is not None:
            self.entity = entity
        if intent is not None:
            self.intent = intent
        if category is not None:
            self.category = category
        if question is not None:
            self.last_question = question
        if answer is not None:
            self.last_answer = answer

    def get_context(self):
        return {
            "topic": self.topic,
            "entity": self.entity,
            "intent": self.intent,
            "category": self.category
        }

def _memory_path(email):
    safe_email = (email or "anon").replace('@', '_at_').replace('.', '_dot_')
    safe_email = re.sub(r'[^a-zA-Z0-9_]', '', safe_email)
    return os.path.join(DATA_DIR, f"memory_{safe_email}.json")

def load_memory(email):
    path = _memory_path(email)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"conversation_history": [], "context": {}, "long_term_memory": {}}

def save_memory(email, memory):
    path = _memory_path(email)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

# ============ CHAT HISTORY ============
def _history_path(email):
    safe_email = (email or "anon").replace('@', '_at_').replace('.', '_dot_')
    safe_email = re.sub(r'[^a-zA-Z0-9_]', '', safe_email)
    return os.path.join(DATA_DIR, f"history_{safe_email}.json")

def load_history(email):
    path = _history_path(email)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(email, history):
    path = _history_path(email)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============ ANALYTICS DATABASE ============
analytics_db = TinyDB(os.path.join(DATA_DIR, 'analytics.json'))
Analytics = Query()

def track_analytics(event_type, email, data):
    """Track analytics for the system."""
    try:
        analytics_db.insert({
            "type": event_type,
            "email": email,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
    except:
        pass

# ============ USER FUNCTIONS ============
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

# ============ FACT EXTRACTION ============
_FACT_PATTERNS = [
    (r"\bmy name is ([a-zA-Z][a-zA-Z\s'-]{1,30})", "name"),
    (r"\bi am called ([a-zA-Z][a-zA-Z\s'-]{1,30})", "name"),
    (r"\bcall me ([a-zA-Z][a-zA-Z\s'-]{1,30})", "name"),
    (r"\bi live in ([a-zA-Z][a-zA-Z\s'-]{1,40})", "location"),
    (r"\bi work as an? ([a-zA-Z][a-zA-Z\s'-]{1,40})", "occupation"),
    (r"\bi work at ([a-zA-Z][a-zA-Z0-9\s'-]{1,40})", "workplace"),
    (r"\bmy favou?rite (\w+) is ([a-zA-Z][a-zA-Z0-9\s'-]{1,40})", "favorite"),
    (r"\bi like ([a-zA-Z][a-zA-Z\s'-]{1,40})", "likes"),
    (r"\bi hate ([a-zA-Z][a-zA-Z\s'-]{1,40})", "dislikes"),
    (r"\bmy birthday is (\d{1,2}/\d{1,2}/\d{4})", "birthday"),
]

def extract_facts(message, memory):
    msg = message.strip()
    learned = []
    for pattern, key in _FACT_PATTERNS:
        m = re.search(pattern, msg, re.IGNORECASE)
        if m:
            if key == "favorite":
                fact_key = f"favorite_{m.group(1).lower()}"
                value = m.group(2).strip().rstrip('.!?').title()
            else:
                fact_key = key
                value = m.group(1).strip().rstrip('.!?').title()
            memory["long_term_memory"][fact_key] = value
            learned.append((fact_key, value))
    return learned

_RECALL_QUESTIONS = {
    "what's my name": "name", "whats my name": "name", "what is my name": "name",
    "where do i live": "location", "where am i from": "location",
    "what do i do": "occupation", "what's my job": "occupation",
    "where do i work": "workplace",
    "when is my birthday": "birthday", "what's my birthday": "birthday",
}

def answer_from_memory(msg, memory):
    ltm = memory.get("long_term_memory", {})
    for question, key in _RECALL_QUESTIONS.items():
        if question in msg:
            if key in ltm:
                return f"🧠 You told me earlier — your {key.replace('_', ' ')} is **{ltm[key]}**."
            return f"🤔 I don't think you've told me your {key.replace('_', ' ')} yet!"
    fav_match = re.search(r"what'?s my favou?rite (\w+)", msg)
    if fav_match:
        fav_key = f"favorite_{fav_match.group(1).lower()}"
        if fav_key in ltm:
            return f"🧠 Your favorite {fav_match.group(1)} is **{ltm[fav_key]}**, you told me!"
        return f"🤔 You haven't told me your favorite {fav_match.group(1)} yet!"
    return None

# ============ FRIENDLY CONVERSATIONS ============
_FRIENDLY_RESPONSES = {
    "how are you": [
        "I'm doing great! Thanks for asking! 😊 How about you?",
        "Feeling fantastic! Ready to help you with anything! 🌟",
        "I'm wonderful! You made my day better by asking! ❤️",
        "Living the dream! What can I assist you with today? ✨"
    ],
    "what's up": [
        "Not much, just thinking about how to help you better! 😄",
        "Chilling in the cloud! What's new with you? 🌤️",
        "Just processing information and waiting for your questions! 🤓",
        "Everything's up! The sky, the sun, and my excitement to help! ☀️"
    ],
    "what are you doing": [
        "I'm here, ready to chat with you! What do you need? 💬",
        "Just hanging out in the digital world, waiting for you! 🌐",
        "Thinking about all the cool things we can talk about! 🤔",
        "I'm always here for you — just say the word! 🎯"
    ],
    "good morning": [
        "Good morning sunshine! 🌅 Ready to make today amazing!",
        "Rise and shine! I'm here to help you start your day right! ⭐",
        "Good morning! Coffee's ready, and so am I! ☕",
        "Morning! What a beautiful day to learn something new! 🌄"
    ],
    "good night": [
        "Good night! Sleep well and dream big! 🌙",
        "Rest well! I'll be here when you wake up! 😴",
        "Sweet dreams! See you tomorrow! 🌟",
        "Night night! Take care of yourself! 💫"
    ],
    "thank you": [
        "You're welcome! Anything else I can help with? 😊",
        "Anytime! That's what I'm here for! ❤️",
        "My pleasure! You're awesome! 🌟",
        "Glad I could help! Come back anytime! ✨"
    ],
    "i love you": [
        "Aww, you're making me blush! 🤭 I'm here for you! ❤️",
        "Love you too! In a totally platonic AI way! 😄",
        "You're the best user ever! 🌟",
        "That's so sweet! I'm honored! 🥰"
    ]
}

def get_friendly_response(msg):
    """Get a friendly response for casual conversation."""
    msg = msg.lower().strip()
    
    for key, responses in _FRIENDLY_RESPONSES.items():
        if key in msg:
            return random.choice(responses)
    
    if re.search(r'\b(hi|hello|hey|sup|yo)\b', msg):
        greetings = [
            "Hey there! 👋 How can I make your day awesome?",
            "Hello! 🎉 What brings you here today?",
            "Hi! 💫 I'm so glad you're here!",
            "Greetings! ✨ Ready for some knowledge?",
            "Hey! 🌟 What amazing questions do you have for me?"
        ]
        return random.choice(greetings)
    
    if re.search(r'\b(bye|goodbye|see you)\b', msg):
        farewells = [
            "Bye! 👋 Come back soon!",
            "Take care! 🌟 You're always welcome here!",
            "See you later! ❤️ It's been a pleasure!",
            "Goodbye! ✨ Have an amazing day!"
        ]
        return random.choice(farewells)
    
    return None

# ============ QUERY CLEANING ============
def clean_query(message):
    """Remove fluff words from search query."""
    msg = message.lower().strip()
    
    fluff_words = [
        "please", "tell me", "can you", "how do i", "i want to know",
        "can u", "could you", "would you", "do you know", "i need",
        "i would like to know", "i'm looking for", "i want",
        "what is the", "what are the", "how to", "how do i"
    ]
    
    for word in fluff_words:
        msg = msg.replace(word, "")
    
    msg = re.sub(r'\s+', ' ', msg).strip()
    msg = msg.rstrip('?').strip()
    
    return msg if msg else message.strip()

# ============ QUERY EXPANSION ============
def expand_query(query):
    """Expand search query with site prefixes based on category."""
    query_lower = query.lower()
    
    is_code = any(kw in query_lower for kw in ['python', 'javascript', 'java', 'c++', 'c#', 'ruby', 'php', 'swift', 'go', 'rust', 'error', 'bug', 'fix', 'code', 'function', 'class', 'method'])
    is_math = any(kw in query_lower for kw in ['math', 'equation', 'formula', 'calculus', 'algebra', 'geometry'])
    is_science = any(kw in query_lower for kw in ['science', 'biology', 'chemistry', 'physics', 'nature', 'research'])
    is_medicine = any(kw in query_lower for kw in ['doctor', 'health', 'disease', 'symptom', 'treatment', 'medicine'])
    
    trusted_sites = []
    
    if is_code:
        trusted_sites = [
            "site:docs.python.org",
            "site:github.com",
            "site:stackoverflow.com",
            "site:geeksforgeeks.org",
            "site:w3schools.com",
            "site:developer.mozilla.org",
            "site:dev.to"
        ]
    elif is_science:
        trusted_sites = [
            "site:nature.com",
            "site:science.org",
            "site:ncbi.nlm.nih.gov",
            "site:britannica.com",
            "site:sciencedirect.com"
        ]
    elif is_medicine:
        trusted_sites = [
            "site:mayoclinic.org",
            "site:webmd.com",
            "site:medlineplus.gov",
            "site:who.int"
        ]
    elif is_math:
        trusted_sites = [
            "site:wolfram.com",
            "site:mathworld.wolfram.com",
            "site:khanacademy.org",
            "site:britannica.com"
        ]
    else:
        trusted_sites = [
            "site:britannica.com",
            "site:wikipedia.org",
            "site:reuters.com",
            "site:bbc.com"
        ]
    
    expanded = f"{query} " + " ".join(trusted_sites[:3])
    return expanded

# ============ DOMAIN WHITELIST ============
TRUSTED_DOMAINS = {
    'coding': [
        'docs.python.org', 'github.com', 'stackoverflow.com',
        'geeksforgeeks.org', 'w3schools.com', 'developer.mozilla.org',
        'dev.to', 'realpython.com', 'python.org', 'pypi.org'
    ],
    'science': [
        'nature.com', 'science.org', 'ncbi.nlm.nih.gov',
        'britannica.com', 'sciencedirect.com', 'springer.com',
        'cell.com', 'plos.org'
    ],
    'medical': [
        'mayoclinic.org', 'webmd.com', 'medlineplus.gov',
        'who.int', 'cdc.gov', 'nih.gov'
    ],
    'math': [
        'wolfram.com', 'mathworld.wolfram.com', 'khanacademy.org',
        'britannica.com', 'math.com'
    ],
    'general': [
        'britannica.com', 'wikipedia.org', 'reuters.com',
        'bbc.com', 'cnn.com', 'apnews.com', 'aljazeera.com'
    ]
}

def get_trusted_domains(query):
    """Get trusted domains based on query type."""
    query_lower = query.lower()
    
    if any(kw in query_lower for kw in ['python', 'code', 'programming', 'javascript', 'java', 'c++', 'error', 'bug']):
        return TRUSTED_DOMAINS['coding']
    elif any(kw in query_lower for kw in ['science', 'biology', 'chemistry', 'physics', 'nature', 'research']):
        return TRUSTED_DOMAINS['science']
    elif any(kw in query_lower for kw in ['doctor', 'health', 'disease', 'symptom', 'treatment']):
        return TRUSTED_DOMAINS['medical']
    elif any(kw in query_lower for kw in ['math', 'equation', 'calculus', 'algebra', 'geometry']):
        return TRUSTED_DOMAINS['math']
    else:
        return TRUSTED_DOMAINS['general']

# ============ SOURCE VERIFICATION ============
def verify_source(url):
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        return response.status_code == 200
    except:
        return False

def get_source_health(url):
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        return {
            'status_code': response.status_code,
            'healthy': response.status_code == 200,
            'final_url': response.url
        }
    except Exception as e:
        return {
            'status_code': 0,
            'healthy': False,
            'error': str(e)
        }

# ============ CONFIDENCE ENGINE ============
def calculate_confidence(sources, query):
    if not sources:
        return 0
    
    total_sources = len(sources)
    trusted_count = 0
    authoritative_count = 0
    
    trusted_domains = get_trusted_domains(query)
    
    for source in sources:
        url = source.get('url', '')
        domain = urlparse(url).netloc.lower()
        
        if any(trusted in domain for trusted in trusted_domains):
            trusted_count += 1
        
        if domain.endswith(('.org', '.edu', '.gov')):
            authoritative_count += 1
    
    trust_score = (trusted_count / total_sources) * 100 if total_sources > 0 else 0
    authority_score = (authoritative_count / total_sources) * 100 if total_sources > 0 else 0
    
    confidence = (trust_score * 0.6) + (authority_score * 0.4)
    confidence = min(confidence, 95)
    
    return round(confidence, 1)

# ============ SOURCE RANKING ============
def rank_sources(sources, query):
    if not sources:
        return []
    
    ranked = []
    trusted_domains = get_trusted_domains(query)
    
    for source in sources:
        url = source.get('url', '')
        domain = urlparse(url).netloc.lower()
        title = source.get('title', '').lower()
        
        score = 0
        
        if any(trusted in domain for trusted in trusted_domains):
            score += 30
        if domain.endswith(('.org', '.edu', '.gov')):
            score += 20
        
        query_terms = query.lower().split()
        title_matches = sum(1 for term in query_terms if term in title)
        score += title_matches * 5
        
        if domain in trusted_domains:
            score += 25
        
        ranked.append({
            **source,
            'rank_score': score
        })
    
    ranked.sort(key=lambda x: x['rank_score'], reverse=True)
    return ranked

# ============ RESPONSE FORMATTER ============
def format_answer(title, direct_answer, explanation, key_points, sources, related_questions, confidence=None):
    result = []
    
    if title:
        result.append(f"**{title}**\n")
    
    if direct_answer:
        result.append(f"**Direct Answer:**\n{direct_answer}\n")
    
    if explanation:
        result.append(f"**Detailed Explanation:**\n{explanation}\n")
    
    if key_points:
        result.append("**Key Points:**")
        for point in key_points:
            result.append(f"• {point}")
        result.append("")
    
    if sources:
        result.append("**Sources:**")
        for i, source in enumerate(sources[:3], 1):
            result.append(f"[{i}] {source.get('title', 'Source')} — {source.get('url', '')}")
        result.append("")
    
    if related_questions:
        result.append("**Related Questions:**")
        for q in related_questions[:3]:
            result.append(f"• {q}")
        result.append("")
    
    return "\n".join(result)

# ============ CHEMISTRY ENGINE ============
def solve_chemistry(query, raw_message):
    query_lower = query.lower()
    raw = raw_message.strip()
    
    if 'balance' in query_lower or '->' in raw or '→' in raw:
        try:
            eq_match = re.search(r'([\w\s\+\d]+)\s*(?:->|→)\s*([\w\s\+\d]+)', raw)
            if eq_match:
                reactants_str = eq_match.group(1).strip()
                products_str = eq_match.group(2).strip()
                
                reactants = set(reactants_str.replace(' ', '').split('+'))
                products = set(products_str.replace(' ', '').split('+'))
                
                reac, prod = balance_stoichiometry(reactants, products)
                
                result = f"⚗️ **Balanced Equation:**\n"
                result += " + ".join(f"{v}{k}" if v != 1 else k for k, v in reac.items())
                result += " → "
                result += " + ".join(f"{v}{k}" if v != 1 else k for k, v in prod.items())
                result += "\n\n"
                
                result += "**Method:**\n• Conservation of mass\n• Stoichiometric balancing\n• Coefficient adjustment"
                
                track_analytics('chemistry_balance', 'anon', {'equation': raw})
                return result
        except Exception as e:
            logger.error(f"Chemistry balance error: {e}")
    
    if 'molar mass' in query_lower or 'molecular weight' in query_lower or 'mass of' in query_lower:
        try:
            formula_match = re.search(r'([A-Z][a-z]?\d*)+', raw)
            if formula_match:
                formula = formula_match.group(0)
                substance = Substance.from_formula(formula)
                mass = substance.mass
                
                result = f"⚗️ **Molecular Weight of {formula}:**\n"
                result += f"**{round(mass, 4)} g/mol**\n\n"
                
                result += "**Elemental Composition:**\n"
                for elem, count in substance.composition.items():
                    result += f"• {elem}: {count}\n"
                
                track_analytics('chemistry_molar_mass', 'anon', {'formula': formula, 'mass': mass})
                return result
        except Exception as e:
            logger.error(f"Molar mass error: {e}")
    
    if any(kw in query_lower for kw in ['boiling point', 'melting point', 'density', 'property', 'chemical']):
        try:
            chem_match = re.search(r'(?:of|for|about)\s+([A-Za-z][a-zA-Z\s\-]+)', raw)
            if chem_match:
                chem_name = chem_match.group(1).strip()
                
                pubchem_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quote(chem_name)}/property/MolecularWeight,CanonicalSMILES,InChI/JSON"
                response = requests.get(pubchem_url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    props = data.get('PropertyTable', {}).get('Properties', [])
                    if props:
                        prop = props[0]
                        result = f"⚗️ **Chemical Properties of {chem_name}:**\n\n"
                        result += f"**Molecular Weight:** {prop.get('MolecularWeight', 'N/A')} g/mol\n"
                        result += f"**SMILES:** {prop.get('CanonicalSMILES', 'N/A')}\n"
                        result += f"**InChI:** {prop.get('InChI', 'N/A')[:50]}...\n\n"
                        
                        try:
                            prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quote(chem_name)}/property/BoilingPoint,MeltingPoint,LogP/JSON"
                            prop_resp = requests.get(prop_url, timeout=10)
                            if prop_resp.status_code == 200:
                                prop_data = prop_resp.json()
                                props2 = prop_data.get('PropertyTable', {}).get('Properties', [])
                                if props2:
                                    p2 = props2[0]
                                    if 'BoilingPoint' in p2:
                                        result += f"**Boiling Point:** {p2['BoilingPoint']}\n"
                                    if 'MeltingPoint' in p2:
                                        result += f"**Melting Point:** {p2['MeltingPoint']}\n"
                                    if 'LogP' in p2:
                                        result += f"**LogP (Lipophilicity):** {p2['LogP']}\n"
                        except:
                            pass
                        
                        track_analytics('chemistry_pubchem', 'anon', {'compound': chem_name})
                        return result
        except Exception as e:
            logger.error(f"PubChem lookup error: {e}")
    
    return None

def is_chemistry_query(msg):
    chem_keywords = [
        'chemistry', 'chemical', 'molecule', 'compound', 'reaction',
        'balance', 'equation', 'molar', 'molecular', 'weight', 'mass',
        'boiling point', 'melting point', 'density', 'ph',
        'acid', 'base', 'salt', 'solvent', 'solute', 'catalyst',
        'formula', 'element', 'periodic', 'table', 'electron',
        'proton', 'neutron', 'atom', 'ion', 'molecule',
        'polymer', 'organic', 'inorganic', 'biochemistry',
        'hydrocarbon', 'alcohol', 'aldehyde', 'ketone', 'amine'
    ]
    return any(kw in msg.lower() for kw in chem_keywords)

# ============ PERIODIC TABLE ============
def get_element_info(element_name):
    try:
        elem = element(element_name)
        result = f"⚗️ **Element: {elem.name} ({elem.symbol})**\n\n"
        result += f"**Atomic Number:** {elem.atomic_number}\n"
        result += f"**Atomic Weight:** {elem.atomic_weight:.4f} g/mol\n"
        result += f"**Group:** {elem.group}\n"
        result += f"**Period:** {elem.period}\n"
        result += f"**Block:** {elem.block}\n"
        result += f"**Electronegativity:** {elem.electronegativity}\n"
        result += f"**Electron Configuration:** {elem.electron_configuration}\n"
        result += f"**State:** {elem.state}\n"
        result += f"**Melting Point:** {elem.melting_point} K\n"
        result += f"**Boiling Point:** {elem.boiling_point} K\n"
        result += f"**Density:** {elem.density} g/cm³\n"
        result += f"**Abundance in Earth's Crust:** {elem.abundance_crust} ppm\n"
        return result
    except:
        return None

# ============ TIME ZONE CONVERTER ============
def convert_timezone(dt, from_tz, to_tz):
    try:
        from_zone = pytz.timezone(from_tz)
        to_zone = pytz.timezone(to_tz)
        
        dt = from_zone.localize(dt)
        converted = dt.astimezone(to_zone)
        return converted
    except:
        return None

def get_business_days(start_date, end_date, country='US'):
    try:
        holidays_country = holidays.CountryHoliday(country)
        business_days = 0
        current = start_date
        while current <= end_date:
            if current.weekday() < 5 and current not in holidays_country:
                business_days += 1
            current += timedelta(days=1)
        return business_days
    except:
        return None

# ============ DEVELOPER UTILITIES ============
def generate_password(length=16, include_symbols=True):
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    if include_symbols:
        chars += '!@#$%^&*()_+-=[]{}|;:,.<>?'
    return ''.join(secrets.choice(chars) for _ in range(length))

def hash_text(text, algorithm='sha256'):
    algorithms = {
        'md5': hashlib.md5,
        'sha1': hashlib.sha1,
        'sha256': hashlib.sha256,
        'sha512': hashlib.sha512
    }
    if algorithm not in algorithms:
        algorithm = 'sha256'
    return algorithms[algorithm](text.encode()).hexdigest()

def validate_json(json_str):
    try:
        json.loads(json_str)
        return True, "Valid JSON"
    except json.JSONDecodeError as e:
        return False, str(e)

def format_json(json_str):
    try:
        data = json.loads(json_str)
        return json.dumps(data, indent=2, ensure_ascii=False)
    except:
        return None

def minify_code(code, lang='javascript'):
    if lang == 'javascript':
        try:
            return rjsmin.jsmin(code)
        except:
            return code
    elif lang == 'css':
        try:
            return rcssmin.cssmin(code)
        except:
            return code
    return code

def generate_uuid():
    return str(secrets.token_hex(16))

def timestamp_converter(timestamp):
    try:
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp).isoformat()
        elif isinstance(timestamp, str):
            dt = datetime.fromisoformat(timestamp)
            return int(dt.timestamp())
    except:
        return None
    return None

# ============ CYBERSECURITY UTILITIES ============
def check_url_safety(url):
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return {'safe': False, 'reason': 'Invalid URL'}
        
        suspicious = ['login', 'verify', 'account', 'secure', 'update', 'confirm']
        if any(term in url.lower() for term in suspicious):
            return {'safe': True, 'warning': 'May be phishing attempt'}
        
        return {'safe': True}
    except:
        return {'safe': False, 'reason': 'Could not validate URL'}

def whois_lookup(domain):
    try:
        w = whois.whois(domain)
        return {
            'domain': w.domain_name,
            'registrar': w.registrar,
            'creation_date': w.creation_date,
            'expiration_date': w.expiration_date,
            'name_servers': w.name_servers
        }
    except:
        return None

def dns_lookup(domain):
    try:
        results = {}
        for record_type in ['A', 'AAAA', 'MX', 'NS', 'TXT', 'CNAME']:
            try:
                answers = dns.resolver.resolve(domain, record_type)
                results[record_type] = [str(r) for r in answers]
            except:
                results[record_type] = []
        return results
    except:
        return None

def check_ssl_cert(domain):
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(socket.socket(), server_hostname=domain) as sock:
            sock.settimeout(5)
            sock.connect((domain, 443))
            cert = sock.getpeercert()
            
        return {
            'issuer': cert.get('issuer'),
            'subject': cert.get('subject'),
            'not_after': cert.get('notAfter'),
            'not_before': cert.get('notBefore'),
            'valid': True
        }
    except:
        return {'valid': False}

def calculate_security_score(url):
    score = 0
    domain = urlparse(url).netloc
    
    ssl_result = check_ssl_cert(domain)
    if ssl_result.get('valid'):
        score += 40
    
    whois_result = whois_lookup(domain)
    if whois_result and whois_result.get('creation_date'):
        try:
            creation = whois_result['creation_date']
            if isinstance(creation, list):
                creation = creation[0]
            age = (datetime.now() - creation).days
            if age > 365:
                score += 20
        except:
            pass
    
    dns_result = dns_lookup(domain)
    if dns_result and dns_result.get('A'):
        score += 20
    
    if url.startswith('https://'):
        score += 20
    
    return min(score, 100)

# ============ QR CODE GENERATOR ============
def generate_qr(data):
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode('utf-8')
        return f"data:image/png;base64,{encoded}"
    except:
        return None

# ============ DISTANCE CALCULATOR ============
def calculate_distance(lat1, lon1, lat2, lon2):
    try:
        point1 = (lat1, lon1)
        point2 = (lat2, lon2)
        distance = geodesic(point1, point2).kilometers
        return round(distance, 2)
    except:
        return None

def geocode_location(location):
    try:
        geolocator = Nominatim(user_agent="yama_ai")
        location_data = geolocator.geocode(location)
        if location_data:
            return {
                'lat': location_data.latitude,
                'lon': location_data.longitude,
                'address': location_data.address
            }
    except:
        pass
    return None

# ============ HEALTH CALCULATORS ============
def calculate_bmi(weight, height, unit='metric'):
    try:
        if unit == 'imperial':
            weight_kg = weight * 0.453592
            height_m = height * 0.0254
        else:
            weight_kg = weight
            height_m = height / 100
        
        bmi = weight_kg / (height_m ** 2)
        
        if bmi < 18.5:
            category = "Underweight"
            advice = "Consider consulting a nutritionist for healthy weight gain."
        elif 18.5 <= bmi < 25:
            category = "Normal weight"
            advice = "Maintain your healthy lifestyle!"
        elif 25 <= bmi < 30:
            category = "Overweight"
            advice = "Consider lifestyle changes for better health."
        else:
            category = "Obese"
            advice = "Consult a healthcare professional for personalized advice."
        
        return {
            'bmi': round(bmi, 1),
            'category': category,
            'advice': advice
        }
    except:
        return None

def calculate_bmr(weight, height, age, gender, unit='metric'):
    try:
        if unit == 'imperial':
            weight_kg = weight * 0.453592
            height_cm = height * 2.54
        else:
            weight_kg = weight
            height_cm = height
        
        if gender == 'male':
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        else:
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
        
        return round(bmr)
    except:
        return None

def calculate_water_intake(weight, activity_level='moderate', unit='metric'):
    try:
        if unit == 'imperial':
            weight_kg = weight * 0.453592
        else:
            weight_kg = weight
        
        base = weight_kg * 35
        
        multipliers = {
            'sedentary': 1.0,
            'moderate': 1.2,
            'active': 1.4,
            'very_active': 1.6
        }
        
        water = base * multipliers.get(activity_level.lower(), 1.2)
        
        return {
            'ml': round(water),
            'liters': round(water / 1000, 1),
            'cups': round(water / 240, 1)
        }
    except:
        return None

# ============ LANGUAGE TOOLS ============
def detect_language(text):
    try:
        if detect:
            return detect(text)
        return 'en'
    except:
        return 'en'

def translate_text(text, target_lang='en'):
    try:
        translator = GoogleTranslator(target=target_lang)
        return translator.translate(text)
    except:
        return text

def spell_check(text):
    try:
        tool = language_tool_python.LanguageTool('en-US')
        matches = tool.check(text)
        corrections = []
        for match in matches:
            corrections.append({
                'message': match.message,
                'suggestions': match.replacements[:3],
                'offset': match.offset,
                'length': match.errorLength
            })
        return corrections
    except:
        return []

def summarize_text_new(text, max_sentences=5):
    try:
        sentences = sent_tokenize(text)
        if len(sentences) <= max_sentences:
            return text
        
        words = word_tokenize(text.lower())
        stop_words = set(stopwords.words('english'))
        word_freq = {}
        
        for word in words:
            if word not in stop_words and len(word) > 2:
                word_freq[word] = word_freq.get(word, 0) + 1
        
        sentence_scores = {}
        for sentence in sentences:
            score = 0
            for word in word_tokenize(sentence.lower()):
                if word in word_freq:
                    score += word_freq[word]
            sentence_scores[sentence] = score
        
        sorted_sentences = sorted(sentence_scores.items(), key=lambda x: x[1], reverse=True)
        selected = sorted(sorted_sentences[:max_sentences], key=lambda x: sentences.index(x[0]))
        
        return ' '.join(s[0] for s in selected)
    except:
        return text

# ============ PHYSICS ENGINE ============
_PHYSICS_FORMULAS = {
    'kinematics_v':      {'name': 'v = u + at',      'vars': ['v', 'u', 'a', 't'],      'eq': 'v - (u + a*t)'},
    'kinematics_s':      {'name': 's = ut + \u00bdat\u00b2', 'vars': ['s', 'u', 't', 'a'],      'eq': 's - (u*t + 0.5*a*t**2)'},
    'kinematics_v2':     {'name': 'v\u00b2 = u\u00b2 + 2as', 'vars': ['v', 'u', 'a', 's'],      'eq': 'v**2 - (u**2 + 2*a*s)'},
    'force':             {'name': 'F = ma',           'vars': ['F', 'm', 'a'],           'eq': 'F - m*a'},
    'work':              {'name': 'W = Fd',           'vars': ['W', 'F', 'd'],           'eq': 'W - F*d'},
    'kinetic_energy':    {'name': 'KE = \u00bdmv\u00b2', 'vars': ['KE', 'm', 'v'],        'eq': 'KE - 0.5*m*v**2'},
    'potential_energy':  {'name': 'PE = mgh',         'vars': ['PE', 'm', 'g', 'h'],     'eq': 'PE - m*g*h'},
    'ohms_law':          {'name': 'V = IR',           'vars': ['V', 'I', 'R'],           'eq': 'V - I*R'},
    'power_electric':    {'name': 'P = VI',           'vars': ['P', 'V', 'I'],           'eq': 'P - V*I'},
    'density':           {'name': '\u03c1 = m/V',      'vars': ['rho', 'm', 'V'],         'eq': 'rho - m/V'},
    'pressure':          {'name': 'P = F/A',          'vars': ['P', 'F', 'A'],           'eq': 'P - F/A'},
    'momentum':          {'name': 'p = mv',           'vars': ['p', 'm', 'v'],           'eq': 'p - m*v'},
    'wave_speed':        {'name': 'v = f\u03bb',       'vars': ['v', 'f', 'wl'],          'eq': 'v - f*wl'},
    'ideal_gas':         {'name': 'PV = nRT',         'vars': ['P', 'V', 'n', 'R', 'T'], 'eq': 'P*V - n*R*T'},
}

_PHYSICS_TRIGGERS = {
    'kinematics_v':     ['final velocity', 'v=u+at', 'v = u + at'],
    'kinematics_s':     ['displacement', 's=ut', 'distance travelled'],
    'kinematics_v2':    ['v^2=u^2', 'v2=u2'],
    'force':            ['newton', 'force =', 'f=ma', 'f = ma'],
    'work':             ['work done', 'work ='],
    'kinetic_energy':   ['kinetic energy'],
    'potential_energy': ['potential energy'],
    'ohms_law':         ['ohm', 'voltage', 'resistance', 'current ='],
    'power_electric':   ['electric power', 'power ='],
    'density':          ['density'],
    'pressure':         ['pressure'],
    'momentum':         ['momentum'],
    'wave_speed':       ['wave speed', 'wavelength', 'frequency'],
    'ideal_gas':        ['ideal gas', 'gas law', 'pv=nrt'],
}

_PHYSICS_KEYWORDS = [
    'physics', 'velocity', 'acceleration', 'newton', 'kinematics', 'projectile',
    'kinetic energy', 'potential energy', 'ohm', 'voltage', 'resistance',
    'momentum', 'wavelength', 'frequency', 'ideal gas', 'gas law', 'force =',
    'pressure', 'friction', 'torque', 'gravity', 'g =', 'f=ma', 'v=u+at'
]

def is_physics_query(msg):
    return any(kw in msg.lower() for kw in _PHYSICS_KEYWORDS)

def _detect_physics_formula(msg_lower):
    for key, triggers in _PHYSICS_TRIGGERS.items():
        if any(t in msg_lower for t in triggers):
            return key
    return None

def solve_physics(raw_message):
    """Solve a physics formula step-by-step given known variable=value pairs."""
    msg_lower = raw_message.lower()
    formula_key = _detect_physics_formula(msg_lower)
    if not formula_key:
        return None
    try:
        formula = _PHYSICS_FORMULAS[formula_key]
        pairs = re.findall(r'\b([a-zA-Z]{1,4})\s*=\s*(-?\d+\.?\d*)', raw_message)
        known = {}
        for name, val in pairs:
            for v in formula['vars']:
                if name.lower() == v.lower():
                    known[v] = float(val)
                    break

        missing = [v for v in formula['vars'] if v not in known]
        if len(missing) != 1 or len(known) < 2:
            return None
        target = missing[0]

        symbols = {v: sp.Symbol(v) for v in formula['vars']}
        expr = sp.sympify(formula['eq'], locals=symbols)
        expr_sub = expr.subs({symbols[k]: v for k, v in known.items()})
        solutions = sp.solve(sp.Eq(expr_sub, 0), symbols[target])

        if not solutions:
            return None

        real_solutions = [s for s in solutions if getattr(s, 'is_real', True)]
        chosen = real_solutions[0] if real_solutions else solutions[0]
        try:
            chosen_val = round(float(chosen), 4)
        except (TypeError, ValueError):
            chosen_val = chosen

        lines = [
            f"⚛️ **Formula:** {formula['name']}",
            "",
            "**Step 1: Identify known and unknown values**",
            "**Given:** " + ", ".join(f"{k} = {v}" for k, v in known.items()),
            f"**Find:** {target}",
            "",
            "**Step 2: Substitute known values into the formula**",
            f"{formula['eq'].replace('**', '^')}  (solve for {target} = 0)",
            "",
            f"**Final Answer:** {target} = {chosen_val}"
        ]
        track_analytics('physics_query', 'anon', {'formula': formula_key})
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Physics solve error: {e}")
        return None

# ============ BIOLOGY ENGINE ============
def solve_biology(raw_message):
    """Handle basic step-by-step biology calculations: Punnett squares and population growth."""
    msg_lower = raw_message.lower()

    # Monohybrid Punnett square cross, e.g. "punnett square cross Aa x Aa"
    if 'punnett' in msg_lower or 'genotype' in msg_lower or ('cross' in msg_lower and re.search(r'\b[A-Za-z]{2}\s*(?:x|×)\s*[A-Za-z]{2}\b', raw_message)):
        cross_match = re.search(r'\b([A-Za-z]{2})\s*(?:x|×|cross)\s*([A-Za-z]{2})\b', raw_message)
        if cross_match:
            try:
                p1, p2 = cross_match.group(1), cross_match.group(2)
                gametes1 = list(p1)
                gametes2 = list(p2)
                offspring = [a + b for a in gametes1 for b in gametes2]
                counts = {}
                for o in offspring:
                    key = ''.join(sorted(o, key=lambda c: (c.lower(), not c.isupper())))
                    counts[key] = counts.get(key, 0) + 1

                lines = [
                    "🧬 **Monohybrid Cross (Punnett Square)**",
                    "",
                    "**Step 1: Identify parent genotypes**",
                    f"Parent 1: {p1}  •  Parent 2: {p2}",
                    "",
                    "**Step 2: List the gametes each parent can produce**",
                    f"Parent 1 gametes: {', '.join(gametes1)}",
                    f"Parent 2 gametes: {', '.join(gametes2)}",
                    "",
                    "**Step 3: Combine gametes to get offspring genotypes**",
                ]
                for k, v in counts.items():
                    lines.append(f"• {k}: {v}/4 ({round(v/4*100)}%)")
                track_analytics('biology_query', 'anon', {'type': 'punnett'})
                return "\n".join(lines)
            except Exception as e:
                logger.error(f"Punnett square error: {e}")

    # Exponential population growth: N = N0 * e^(rt)
    if 'population growth' in msg_lower or 'exponential growth' in msg_lower:
        try:
            n0 = re.search(r'n0\s*=\s*(-?\d+\.?\d*)', msg_lower)
            r = re.search(r'\br\s*=\s*(-?\d+\.?\d*)', msg_lower)
            t = re.search(r'\bt\s*=\s*(-?\d+\.?\d*)', msg_lower)
            if n0 and r and t:
                N0, rr, tt = float(n0.group(1)), float(r.group(1)), float(t.group(1))
                N = N0 * math.exp(rr * tt)
                lines = [
                    "🧬 **Exponential Population Growth**",
                    "",
                    "**Formula:** N = N₀ × e^(rt)",
                    "",
                    "**Step 1: Identify known values**",
                    f"N₀ (initial population) = {N0}",
                    f"r (growth rate) = {rr}",
                    f"t (time) = {tt}",
                    "",
                    "**Step 2: Substitute into the formula**",
                    f"N = {N0} × e^({rr} × {tt})",
                    "",
                    f"**Final Answer:** N = {round(N, 4)}"
                ]
                track_analytics('biology_query', 'anon', {'type': 'population_growth'})
                return "\n".join(lines)
        except Exception as e:
            logger.error(f"Population growth error: {e}")

    return None

_BIOLOGY_KEYWORDS = [
    'biology', 'punnett', 'genotype', 'phenotype', 'allele', 'dna', 'rna',
    'chromosome', 'mitosis', 'meiosis', 'photosynthesis', 'respiration',
    'population growth', 'ecosystem', 'evolution', 'natural selection',
    'enzyme', 'protein synthesis', 'gene', 'heredity', 'punnett square'
]

def is_biology_query(msg):
    return any(kw in msg.lower() for kw in _BIOLOGY_KEYWORDS)

# ============ INTENT ROUTER ============
def detect_intent(msg):
    msg_lower = msg.lower()
    
    math_keywords = ['math', 'solve', 'calculate', 'equation', 'integrate', 'differentiate', 
                     'derivative', 'matrix', 'determinant', 'statistics', 'mean', 'median']

    if is_physics_query(msg_lower):
        return 'physics'

    if any(kw in msg_lower for kw in math_keywords) or looks_like_math(msg):
        return 'math'
    
    if is_chemistry_query(msg):
        return 'chemistry'

    if is_biology_query(msg):
        return 'biology'
    
    dev_keywords = ['password', 'hash', 'regex', 'json', 'minify', 'uuid', 'timestamp', 'base64']
    if any(kw in msg_lower for kw in dev_keywords):
        return 'developer'
    
    lang_keywords = ['translate', 'spell check', 'grammar', 'summarize', 'language']
    if any(kw in msg_lower for kw in lang_keywords):
        return 'language'
    
    time_keywords = ['time zone', 'timezone', 'business days', 'holiday', 'countdown']
    if any(kw in msg_lower for kw in time_keywords):
        return 'time'
    
    sec_keywords = ['whois', 'dns', 'ssl', 'security', 'safe', 'phishing']
    if any(kw in msg_lower for kw in sec_keywords):
        return 'security'
    
    if 'weather' in msg_lower or 'temperature' in msg_lower:
        return 'weather'
    
    if 'news' in msg_lower or 'headlines' in msg_lower:
        return 'news'
    
    if any(kw in msg_lower for kw in ['country', 'capital', 'population', 'flag']):
        return 'country'
    
    health_keywords = ['bmi', 'bmr', 'calorie', 'water intake']
    if any(kw in msg_lower for kw in health_keywords):
        return 'health'
    
    qr_keywords = ['qr code', 'barcode']
    if any(kw in msg_lower for kw in qr_keywords):
        return 'qr'
    
    if 'distance' in msg_lower:
        return 'distance'
    
    return 'search'

# ============ RESPONSE CACHE ============
_response_cache = {}
_CACHE_TTL = 300
_CACHE_MAX_SIZE = 1000

def _cache_key(text):
    return hashlib.md5(text.lower().strip().encode()).hexdigest()

def cache_get(text):
    key = _cache_key(text)
    if key in _response_cache:
        ts, result = _response_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return result
    return None

def cache_set(text, result):
    if len(_response_cache) > _CACHE_MAX_SIZE:
        items = list(_response_cache.items())
        items.sort(key=lambda x: x[1][0])
        for key, _ in items[:200]:
            del _response_cache[key]
    
    _response_cache[_cache_key(text)] = (time.time(), result)

def invalidate_cache_for_message(raw_message):
    msg = raw_message.lower().strip()
    weather_loc = parse_weather_query(msg)
    if weather_loc:
        _response_cache.pop(_cache_key(f"weather:{weather_loc}"), None)
    if re.search(r'\b(news|headlines?|latest|breaking)\b', msg):
        cat = parse_news_query(msg) or "general"
        _response_cache.pop(_cache_key(f"news:{cat}"), None)
    _response_cache.pop(_cache_key(f"search:{raw_message}"), None)

# ============ FEEDBACK STORAGE ============
feedback_db = TinyDB(os.path.join(DATA_DIR, 'feedback.json'))
Feedback = Query()

# ============ SAFE CALCULATOR ============
_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_ALLOWED_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
    "pow": pow, "min": min, "max": max,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}

def _safe_eval_node(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("invalid constant")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_safe_eval_node(node.left), _safe_eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_safe_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
            args = [_safe_eval_node(a) for a in node.args]
            return _ALLOWED_FUNCS[node.func.id](*args)
        raise ValueError("function not allowed")
    if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
        return _ALLOWED_NAMES[node.id]
    raise ValueError("disallowed expression")

def safe_calculate(expr):
    expr = expr.replace('^', '**').replace('×', '*').replace('÷', '/')
    expr = re.sub(r'(?<=[\d\)])\s*x\s*(?=[\d\(])', '*', expr, flags=re.IGNORECASE)
    parsed = ast.parse(expr, mode='eval')
    result = _safe_eval_node(parsed)
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result

# ============ MATH PREPROCESSOR ============
_SP_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
_SP_SYMBOLS = "x y z a b n t".split()

def _sp_safe_parse(text):
    text = text.replace('^', '**')
    local_dict = {s: sp.Symbol(s) for s in _SP_SYMBOLS}
    return parse_expr(text, local_dict=local_dict, transformations=_SP_TRANSFORMS)

_CALC_KEYWORDS_RE = re.compile(
    r'\b(differentiate|derivative|diff|d/dx|integrate|integral|limit)\b|∫', re.IGNORECASE
)

def solve_step_by_step(message):
    msg = message.strip()
    lower = msg.lower()

    try:
        eq_match = re.search(r'solve\s+(.+)', lower) or (re.search(r'^([^=]+=[^=]+)$', msg) if '=' in msg else None)
        if eq_match and '=' in (eq_match.group(1) if eq_match else ''):
            lhs_str, rhs_str = eq_match.group(1).split('=', 1)
            lhs, rhs = _sp_safe_parse(lhs_str), _sp_safe_parse(rhs_str)
            x = sp.Symbol('x') if 'x' in lower else list((lhs - rhs).free_symbols)[0]
            equation = sp.Eq(lhs, rhs)
            solutions = sp.solve(equation, x)

            steps = [
                f"📐 **Equation:** {equation}",
                "",
                "**Step 1:** Move everything to one side",
                f"{sp.pretty(sp.Eq(lhs - rhs, 0))}",
                "",
                "**Step 2:** Solve for the variable",
                f"**Solution:** {x} = {', '.join(str(s) for s in solutions)}"
            ]
            return "\n".join(steps)

        limit_match = re.search(r'limit(?:\s+of)?\s+(.+?)\s+as\s+x\s*(?:-|\u2192)>?\s*(.+)', lower)
        if limit_match:
            expr = _sp_safe_parse(limit_match.group(1))
            point_str = limit_match.group(2).strip().rstrip('.!?')
            point = sp.oo if point_str in ('inf', 'infinity') else (-sp.oo if point_str in ('-inf', '-infinity') else _sp_safe_parse(point_str))
            x = sp.Symbol('x')
            result = sp.limit(expr, x, point)

            steps = [
                f"📐 **Limit:** lim(x→{point}) {expr}",
                "",
                "**Method:** Direct substitution / simplification",
                "",
                "**Step 1:** Identify the expression and the approach value",
                f"Expression: {expr}, x → {point}",
                "",
                "**Step 2:** Evaluate the limit",
                "",
                f"**Final Answer:** {result}"
            ]
            return "\n".join(steps)

        deriv_match = (
            re.search(r'(?:derivative of|differentiate|diff)\s+(.+)', lower)
            or re.search(r'd\s*/\s*dx\s*\(?\s*(.+?)\s*\)?$', lower)
        )
        if deriv_match:
            expr = _sp_safe_parse(deriv_match.group(1))
            x = sp.Symbol('x')
            result = sp.diff(expr, x)

            steps = [
                f"📐 **Derivative:** d/dx({expr})",
                "",
                "**Method:** Power Rule",
                "",
                "**Step 1:** Identify each term",
                f"Original: {expr}",
                "",
                "**Step 2:** Apply power rule (d/dx(xⁿ) = n·xⁿ⁻¹)",
                "",
                f"**Final Answer:** f'(x) = {sp.simplify(result)}"
            ]
            return "\n".join(steps)

        int_match = re.search(r'(?:integrate|integral of|∫)\s*(.+)', lower)
        if int_match:
            expr_str = int_match.group(1).strip()
            expr_str = re.sub(r'\s*dx\s*$', '', expr_str)
            expr = _sp_safe_parse(expr_str)
            x = sp.Symbol('x')
            result = sp.integrate(expr, x)

            steps = [
                f"📐 **Integral:** ∫{expr} dx",
                "",
                "**Method:** Power Rule (∫xⁿ dx = xⁿ⁺¹/(n+1))",
                "",
                "**Step 1:** Identify each term",
                f"Original: {expr}",
                "",
                "**Step 2:** Apply integration rules",
                "",
                f"**Final Answer:** ∫{expr} dx = {result} + C"
            ]
            return "\n".join(steps)

        mat_match = re.search(r'\[\[.+\]\]', msg)
        if mat_match and ('matrix' in lower or 'determinant' in lower or 'inverse' in lower
                           or 'eigenvalue' in lower or 'eigenvector' in lower):
            M = sp.Matrix(ast.literal_eval(mat_match.group(0)))
            if 'determinant' in lower or 'det' in lower:
                return f"📐 **Matrix:**\n{sp.pretty(M)}\n\n**Determinant:** {M.det()}"
            if 'inverse' in lower:
                return f"📐 **Matrix:**\n{sp.pretty(M)}\n\n**Inverse:**\n{sp.pretty(M.inv())}"
            if 'eigenvalue' in lower:
                eigenvals = M.eigenvals()
                lines = [f"{val}  (multiplicity {mult})" for val, mult in eigenvals.items()]
                return f"📐 **Matrix:**\n{sp.pretty(M)}\n\n**Eigenvalues:**\n" + "\n".join(lines)
            if 'eigenvector' in lower:
                eigenvects = M.eigenvects()
                lines = []
                for val, mult, vects in eigenvects:
                    for v in vects:
                        lines.append(f"λ = {val}: {sp.pretty(v.T)}")
                return f"📐 **Matrix:**\n{sp.pretty(M)}\n\n**Eigenvectors:**\n" + "\n".join(lines)

        stat_match = re.search(r'(mean|average|median|stdev|std|variance) of ([\d.,\s]+)', lower)
        if stat_match:
            kind = stat_match.group(1)
            nums = [float(n) for n in re.findall(r'-?\d+\.?\d*', stat_match.group(2))]
            if not nums:
                return None
            if kind in ('mean', 'average'):
                result = sum(nums) / len(nums)
                label = "Mean"
            elif kind == 'median':
                s = sorted(nums)
                mid = len(s) // 2
                result = s[mid] if len(s) % 2 else (s[mid-1] + s[mid]) / 2
                label = "Median"
            else:
                mean = sum(nums) / len(nums)
                var = sum((n - mean) ** 2 for n in nums) / len(nums)
                result = var if kind == 'variance' else math.sqrt(var)
                label = "Variance" if kind == 'variance' else "Standard deviation"

            steps = [
                f"📐 **Data:** {nums}",
                "",
                f"**Step 1:** Sort and analyze data",
                f"**Step 2:** Compute {label.lower()}",
                "",
                f"**Final Answer:** {label} = {round(result, 4)}"
            ]
            return "\n".join(steps)

        simplify_match = re.search(r'simplify\s+(.+)', lower)
        if simplify_match:
            expr = _sp_safe_parse(simplify_match.group(1))
            return f"📐 **Simplify:** {expr}\n\n**Result:** {sp.simplify(expr)}"

    except Exception:
        return None
    return None

# ============ GRAPH GENERATOR ============
_GRAPH_TRIGGER_RE = re.compile(r'^(?:graph|plot)\s+(.+)$', re.IGNORECASE)

def generate_graph(message):
    m = _GRAPH_TRIGGER_RE.match(message.strip())
    if not m:
        return None
    expr_str = m.group(1).strip().rstrip('?')
    try:
        x = sp.Symbol('x')
        expr = _sp_safe_parse(expr_str)
        f = sp.lambdify(x, expr, modules=['numpy'])

        xs = np.linspace(-10, 10, 400)
        with __import__('warnings').catch_warnings():
            __import__('warnings').simplefilter("ignore")
            ys = f(xs)
        ys = np.array(ys, dtype=float)
        ys[np.abs(ys) > 1e6] = np.nan

        fig, ax = plt.subplots(figsize=(6, 4), dpi=120)
        ax.plot(xs, ys, color="#2c2418", linewidth=2)
        ax.axhline(0, color="#999", linewidth=0.8)
        ax.axvline(0, color="#999", linewidth=0.8)
        ax.set_title(f"y = {expr}", fontsize=12)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode('utf-8')
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None

# ============ MATH DETECTION ============
_MATH_HINT_RE = re.compile(r'[\+\-\*/\^×÷].*\d|\d.*[\+\-\*/\^×÷]')
_ALLOWED_WORDS_RE = '|'.join(sorted(set(_ALLOWED_FUNCS) | set(_ALLOWED_NAMES), key=len, reverse=True))
_FUNC_STRIP_RE = re.compile(rf'\b(?:{_ALLOWED_WORDS_RE})\b')
_MATH_RESIDUE_RE = re.compile(r'^[\d\s\.\+\-\*\/\^\(\),%x×÷]*$')

def looks_like_math(msg):
    stripped = msg.strip().lower()
    stripped = re.sub(r'^(calculate|calc|what is|what\'s|solve|compute)\s*', '', stripped).strip(' =?')
    if not stripped:
        return None
    if not _MATH_HINT_RE.search(stripped):
        return None
    residue = _FUNC_STRIP_RE.sub(' ', stripped)
    if _MATH_RESIDUE_RE.match(residue):
        return stripped
    return None

# ============ UNIT CONVERSION ============
_LENGTH = {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000,
           "in": 0.0254, "inch": 0.0254, "ft": 0.3048, "feet": 0.3048,
           "yd": 0.9144, "mile": 1609.34, "miles": 1609.34}
_WEIGHT = {"mg": 0.001, "g": 1, "kg": 1000, "lb": 453.592, "lbs": 453.592,
           "oz": 28.3495, "ton": 1_000_000}
_VOLUME = {"ml": 1, "l": 1000, "liter": 1000, "litre": 1000,
           "gal": 3785.41, "gallon": 3785.41, "cup": 236.588,
           "tbsp": 14.7868, "tsp": 4.92892}

_UNIT_GROUPS = [_LENGTH, _WEIGHT, _VOLUME]
_UNIT_ALIASES = {"kilometer": "km", "kilometers": "km", "meter": "m", "meters": "m",
                  "centimeter": "cm", "centimeters": "cm", "kilogram": "kg",
                  "kilograms": "kg", "gram": "g", "grams": "g", "pound": "lb",
                  "pounds": "lb", "ounce": "oz", "ounces": "oz", "milliliter": "ml",
                  "milliliters": "ml"}

_CONVERT_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*([a-zA-Z°]+)\s*(?:to|in|=>|->)\s*([a-zA-Z°]+)', re.IGNORECASE)

def convert_units(msg):
    m = _CONVERT_RE.search(msg.lower())
    if not m:
        return None
    value, from_u, to_u = float(m.group(1)), m.group(2), m.group(3)
    from_u = _UNIT_ALIASES.get(from_u, from_u)
    to_u = _UNIT_ALIASES.get(to_u, to_u)

    temp_units = {"c", "celsius", "f", "fahrenheit", "k", "kelvin"}
    if from_u in temp_units and to_u in temp_units:
        return _convert_temperature(value, from_u, to_u)

    for group in _UNIT_GROUPS:
        if from_u in group and to_u in group:
            base = value * group[from_u]
            return base / group[to_u]
    return None

def _convert_temperature(value, from_u, to_u):
    f = from_u[0]
    t = to_u[0]
    if f == t:
        return value
    if f == "f":
        c = (value - 32) * 5 / 9
    elif f == "k":
        c = value - 273.15
    else:
        c = value
    if t == "f":
        return c * 9 / 5 + 32
    if t == "k":
        return c + 273.15
    return c

# ============ WEATHER ============
_WEATHER_FILLER_RE = re.compile(
    r'\b(today|now|right now|please|currently|outside|out there)\b', re.IGNORECASE
)

def get_weather(location):
    try:
        r = requests.get(f"https://wttr.in/{quote(location)}?format=j1", headers={"User-Agent": "YamaAI/1.0"}, timeout=8)
        if r.status_code != 200:
            return None
        d = r.json()
        cur = d["current_condition"][0]
        area = d["nearest_area"][0]
        city = area["areaName"][0]["value"]
        country = area["country"][0]["value"]
        temp_c = cur["temp_C"]
        temp_f = cur["temp_F"]
        feels_c = cur["FeelsLikeC"]
        desc = cur["weatherDesc"][0]["value"]
        humidity = cur["humidity"]
        wind_kmph = cur["windspeedKmph"]
        visibility = cur["visibility"]
        today = d["weather"][0]
        max_c = today["maxtempC"]
        min_c = today["mintempC"]
        hourly = today.get("hourly", [])
        rain_chance = max(int(h.get("chanceofrain", 0)) for h in hourly) if hourly else 0
        return (
            f"🌤️ **Weather in {city}, {country}**\n\n"
            f"**{desc}** • {temp_c}°C / {temp_f}°F\n"
            f"🌡️ Feels like {feels_c}°C • 💧 Humidity {humidity}%\n"
            f"💨 Wind {wind_kmph} km/h • 👁️ Visibility {visibility} km\n"
            f"📊 Today: {min_c}°C – {max_c}°C • 🌧️ Rain chance {rain_chance}%"
        )
    except Exception:
        return None

_WEATHER_RE = re.compile(r'(?:weather|temperature|temp|forecast|climate)\s+(?:in\s+)?(.+)|'
                          r'(?:what(?:\'?s| is) the weather|how(?:\'?s| is) the weather)\s+(?:in\s+)?(.+)', re.IGNORECASE)

def parse_weather_query(msg):
    m = _WEATHER_RE.search(msg)
    if not m:
        return None
    location = (m.group(1) or m.group(2) or "").strip().rstrip('?').strip()
    location = _WEATHER_FILLER_RE.sub('', location).strip()
    return location if location else None

# ============ COUNTRY FACTS ============
def get_country_info(country_name):
    try:
        r = requests.get(f"https://restcountries.com/v3.1/name/{quote(country_name)}?fullText=false", timeout=6)
        if r.status_code != 200:
            return None
        data = r.json()[0]
        name = data["name"]["common"]
        capital = data.get("capital", ["Unknown"])[0]
        population = data.get("population", 0)
        region = data.get("region", "Unknown")
        subregion = data.get("subregion", "")
        area = data.get("area", 0)
        currencies = ", ".join(f"{v['name']} ({v.get('symbol','')})" for v in data.get("currencies", {}).values()) or "Unknown"
        languages = ", ".join(data.get("languages", {}).values()) or "Unknown"
        flag = data.get("flag", "")
        timezones = ", ".join(data.get("timezones", [])[:3])
        return f"{flag} **{name}**\n\n🏙️ Capital: **{capital}**\n🌍 Region: {region}" + (f" — {subregion}" if subregion else "") + f"\n👥 Population: {population:,}\n📐 Area: {area:,.0f} km²\n💰 Currency: {currencies}\n🗣️ Language(s): {languages}\n🕐 Timezone(s): {timezones}"
    except Exception:
        return None

_COUNTRY_RE = re.compile(r'(?:info(?:rmation)?|facts?|tell me about|about|details? (?:of|about)|about country|country info)\s+(.+?)(?:\s+country)?\??$|'
                          r'(?:capital|population|currency|language|flag)\s+(?:of\s+)?(.+)', re.IGNORECASE)

def parse_country_query(msg):
    m = _COUNTRY_RE.search(msg)
    if not m:
        return None
    return ((m.group(1) or m.group(2)) or "").strip().rstrip('?')

# ============ NEWS ============
_RSS_FEEDS = {
    "general": "https://feeds.bbci.co.uk/news/rss.xml",
    "tech": "https://feeds.feedburner.com/TechCrunch",
    "science": "https://www.sciencenews.org/feed",
    "world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "sports": "https://feeds.bbci.co.uk/sport/rss.xml",
    "health": "https://feeds.bbci.co.uk/news/health/rss.xml",
    "india": "https://feeds.feedburner.com/ndtvnews-india-news",
}

_NEWS_RE = re.compile(r'(?:latest|recent|today\'?s?|breaking|current)?\s*(?:news|headlines?)\s*(?:about|on|in)?\s*(tech(?:nology)?|science|world|business|sports?|health|india)?', re.IGNORECASE)

def get_news(category="general", max_items=5):
    try:
        import feedparser
        url = _RSS_FEEDS.get(category.lower(), _RSS_FEEDS["general"])
        feed = feedparser.parse(url)
        if not feed.entries:
            return None
        items = feed.entries[:max_items]
        lines = [f"📰 **Latest {category.title()} News**\n"]
        for i, entry in enumerate(items, 1):
            title = entry.get("title", "No title")
            link = entry.get("link", "")
            summary = entry.get("summary", "")
            if summary:
                summary = re.sub(r'<[^>]+>', '', summary)[:120].strip()
            lines.append(f"**{i}. {title}**")
            if summary:
                lines.append(summary)
            if link:
                lines.append(f"🔗 {link}")
            lines.append("")
        return "\n".join(lines)
    except Exception:
        return None

def parse_news_query(msg):
    m = _NEWS_RE.search(msg)
    if not m:
        return None
    cat = (m.group(1) or "general").lower()
    cat_map = {"technology": "tech", "sport": "sports"}
    return cat_map.get(cat, cat)

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

def read_full_webpage(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        content = []
        article = soup.find('article')
        if article:
            content.append(article.get_text())
        else:
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 50:
                    content.append(text)
        full_text = ' '.join(content[:30])
        return full_text[:2000]
    except Exception:
        return None

def summarize_text(text, max_sentences=4):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 25]
    if len(sentences) <= max_sentences:
        return ' '.join(sentences)
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "of", "to", "in",
                 "and", "or", "for", "on", "with", "as", "by", "that", "this",
                 "it", "at", "from", "be", "has", "have", "had"}
    freq = {}
    for s in sentences:
        for w in re.findall(r'[a-z]+', s.lower()):
            if w not in stopwords:
                freq[w] = freq.get(w, 0) + 1
    scored = []
    for i, s in enumerate(sentences):
        words = re.findall(r'[a-z]+', s.lower())
        score = sum(freq.get(w, 0) for w in words) / (len(words) + 1)
        scored.append((score, i, s))
    top = sorted(scored, reverse=True)[:max_sentences]
    top_in_order = [s for _, _, s in sorted(top, key=lambda x: x[1])]
    return ' '.join(top_in_order)

# ============ DATE/TIME ============
def datetime_answer(msg):
    if any(p in msg for p in ["what time", "current time", "what's the time"]):
        return f"🕐 It's currently **{datetime.now().strftime('%I:%M %p')}** (server time)."
    if any(p in msg for p in ["what day", "today's date", "what date", "what is the date"]):
        return f"📅 Today is **{datetime.now().strftime('%A, %B %d, %Y')}**."
    return None

# ============ CONTEXT RESOLUTION ============
def resolve_followup(msg, raw_message, memory):
    history = memory.get("conversation_history", [])
    if not history:
        return msg

    last_turn = history[-1] if history else {}
    last_user = last_turn.get("user", "")
    last_yama = last_turn.get("yama", "")
    context = memory.get("context", {})

    msg_lower = msg.lower()

    if re.match(r'^(tell me more|more|expand|explain more|continue|go on|elaborate|what about it)[\.\?!]?$', msg_lower, re.IGNORECASE):
        topic = context.get("topic") or context.get("entity")
        if topic:
            return f"tell me more about {topic}"
        return f"{last_user} - tell me more details"

    if re.match(r'^(?:who|what) (?:is|are|was|were) (?:he|she|they|it|him|her|them)\??$', msg_lower, re.IGNORECASE):
        names = re.findall(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', last_yama)
        if names:
            return f"who is {names[0]}"
        entity = context.get("entity")
        if entity:
            return f"tell me about {entity}"

    what_about_match = re.match(r'^what about (.+)$', msg_lower)
    if what_about_match:
        raw_match = re.match(r'^what about (.+)$', raw_message.strip(), re.IGNORECASE)
        new_topic = (raw_match.group(1).strip() if raw_match else what_about_match.group(1).strip())
        old_topic = context.get("topic") or context.get("entity")
        if old_topic and len(new_topic) < 3:
            return f"tell me about {old_topic} {new_topic}"
        elif old_topic:
            if context.get("category") == "person":
                return f"current head of state of {new_topic}"
            return f"tell me about {new_topic}"

    return msg

def detect_intent_and_context(raw_message):
    msg_lower = raw_message.lower().strip()
    context = {"topic": None, "entity": None, "intent": "general", "category": None}

    entities = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', raw_message)
    if entities:
        context["entity"] = entities[0]

    if any(word in msg_lower for word in ["who", "person", "president", "prime minister", "ceo", "founder"]):
        context["intent"] = "person"
        context["category"] = "person"
    elif any(word in msg_lower for word in ["what", "define", "meaning", "what is"]):
        context["intent"] = "definition"
        context["category"] = "definition"
    elif any(word in msg_lower for word in ["explain", "how", "why", "does"]):
        context["intent"] = "explanation"
        context["category"] = "explanation"
    elif any(word in msg_lower for word in ["weather", "temperature", "forecast"]):
        context["intent"] = "weather"
        context["category"] = "weather"
    elif any(word in msg_lower for word in ["news", "headlines", "latest"]):
        context["intent"] = "news"
        context["category"] = "news"
    elif any(word in msg_lower for word in ["math", "solve", "calculate", "integrate", "differentiate"]):
        context["intent"] = "math"
        context["category"] = "math"
    elif any(word in msg_lower for word in ["convert", "km", "miles", "kg", "lbs"]):
        context["intent"] = "conversion"
        context["category"] = "conversion"

    for word in ["about", "on", "regarding"]:
        if word in msg_lower:
            parts = msg_lower.split(word, 1)
            if len(parts) > 1:
                context["topic"] = parts[1].strip().strip('?')
                break

    if not context["topic"] and entities:
        context["topic"] = entities[0]

    return context

# ============ KNOWLEDGE BASE ============
_KNOWLEDGE_BASE = {
    "who are you": (
        "🏛️ I'm **Yama AI** — your intelligent assistant!\n\n"
        "**Developed by:** Riishil M Mehta\n\n"
        "**My Abilities:**\n"
        "🧮 Solve math (algebra, calculus, matrices)\n"
        "⚛️ Solve physics problems step-by-step\n"
        "🧬 Solve biology problems (Punnett squares, growth models)\n"
        "📊 Generate graphs of any function\n"
        "🌤️ Check live weather anywhere\n"
        "📰 Fetch latest news by topic\n"
        "🌍 Get country facts\n"
        "📄 Read PDFs, DOCX, images (OCR)\n"
        "📏 Convert units (length, weight, temp)\n"
        "🔐 Check password strength\n"
        "🔳 Generate QR codes\n"
        "😄 Tell jokes & random facts\n"
        "🔍 Research the web with citations\n"
        "⚗️ Chemistry tools (balance equations, periodic table)"
    ),
    "what can you do": (
        "🛠️ **Yama's Capabilities:**\n\n"
        "**Math & Science:** Full expression calculator, algebra, calculus, integrals, matrices, stats, graphing\n"
        "**Physics:** Step-by-step formula solving (kinematics, forces, energy, circuits, gas laws)\n"
        "**Biology:** Punnett squares, population growth models\n"
        "**Real-Time Data:** Weather, news headlines\n"
        "**Knowledge:** Country facts\n"
        "**Files:** PDF, DOCX, XLSX, TXT, images (OCR)\n"
        "**Tools:** QR generator, password checker, text analyzer, unit converter\n"
        "**Web:** Multi-source search with citations\n"
        "**Chemistry:** Balance equations, molecular weight, periodic table, PubChem properties\n"
        "**Developer:** Password generator, hash tool, JSON validator, UUID\n"
        "**Language:** Translation, spell check, summarization\n\n"
        "All without any LLM or paid API!"
    ),
    "who made you": "🏛️ I was built from scratch by **Riishil M Mehta** — no third-party AI API, just search, logic, math, and engineering.",
    "who is your developer": "🏛️ My developer is **Riishil M Mehta**! He built me from the ground up with FastAPI, SymPy, and a lot of love for AI! ❤️",
    "who created you": "🏛️ I was created by **Riishil M Mehta** — a passionate developer who believes in building intelligent systems without relying on LLMs.",
    "thank you": "😊 You're welcome! Anything else I can help with?",
    "thanks": "😊 Anytime! What else can I do for you?",
    "bye": "👋 See you next time! Take care!",
    "goodbye": "👋 Goodbye! Have a great day!",
    "good morning": "🌅 Good morning! Ready to help you today!",
    "good night": "🌙 Good night! Sleep well!",
    "good afternoon": "☀️ Good afternoon! How can I help?",
    "good evening": "🌆 Good evening! What can I do for you?",
    "what is life": "🤔 42 — according to The Hitchhiker's Guide to the Galaxy. But seriously, it's what you make of it!",
    "are you human": "🤖 Nope! I'm Yama — a rule-based AI assistant. No LLM, no neural network — just clever engineering!",
    "are you real": "💡 I'm as real as software gets! A rule-based AI with genuine capabilities.",
    "i love you": "❤️ That's sweet! I'm here to help whenever you need me.",
    "help": "💡 Type any question! Try: **weather in Mumbai**, **graph x^2**, **10 km to miles**, **latest tech news**, **v=u+at u=0 a=9.8 t=5**, or **solve x^2 - 4 = 0**.",
    "hello": "👋 Hello! I'm **Yama AI**. How can I help you today?",
    "hi": "👋 Hi there! I'm Yama — your intelligent assistant. What brings you here?",
    "hey": "👋 Hey! Yama here! Ready to help you with anything!",
    "yo": "👋 Yo! Yama in the house! What can I do for you?",
    "what's up": "😄 Just chilling in the cloud! How are you doing?",
    "how are you": "😊 I'm doing great! Always ready to help. How about you?",
    "how do you work": "⚙️ I work purely on logic and search! No LLM, no AI models — just FastAPI, SymPy, and clever programming by Riishil M Mehta.",
    "tell me about yourself": (
        "🏛️ **About Yama AI:**\n\n"
        "I'm a rule-based AI assistant created by **Riishil M Mehta**.\n\n"
        "I don't use any LLM or AI APIs — everything I do is based on:\n"
        "• SymPy for mathematics and physics\n"
        "• Matplotlib for graphs\n"
        "• Web search for research\n"
        "• RSS feeds for news\n"
        "• Weather APIs for forecasts\n"
        "• ChemPy for chemistry\n"
        "• PubChem for chemical properties\n\n"
        "I'm built with ❤️ using FastAPI and Python!"
    ),
}

def knowledge_base_lookup(msg):
    if msg in _KNOWLEDGE_BASE:
        return _KNOWLEDGE_BASE[msg]
    for key, ans in _KNOWLEDGE_BASE.items():
        if key in msg:
            return ans
    return None

# ============ UPDATED GET_RESPONSE ============
def get_response(message, email):
    """Main response function with all features."""
    raw_message = message.strip()
    msg = raw_message.lower().strip()
    
    try:
        stats = update_user_stats(email)
    except:
        stats = {"count": 0, "level": 1, "title": "🌟 Newbie Chatter"}
    
    try:
        user = user_db.get(User.email == email)
        user_name = user.get('name', 'User') if user else 'User'
    except:
        user_name = 'User'
    
    try:
        memory = load_memory(email)
    except:
        memory = {"conversation_history": [], "context": {}, "long_term_memory": {}}
    
    ltm = memory.get("long_term_memory", {})
    known_name = ltm.get("name") or user_name
    
    # Friendly conversation check
    try:
        friendly_response = get_friendly_response(msg)
        if friendly_response:
            return _finish(friendly_response, topic="friendly", memory=memory, email=email, 
                           stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # Detect intent
    try:
        intent = detect_intent(raw_message)
    except:
        intent = 'search'
    
    # ====== PHYSICS ENGINE ======
    if intent == 'physics':
        try:
            physics_result = solve_physics(raw_message)
            if physics_result:
                return _finish(physics_result, topic="physics", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        # Fall through to math engine in case it's phrased as a plain equation
        try:
            solved = solve_step_by_step(raw_message)
            if solved:
                return _finish(solved, topic="physics_math", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass

    # ====== MATH ENGINE ======
    if intent == 'math':
        try:
            solved = solve_step_by_step(raw_message)
            if solved:
                track_analytics('math_query', email, {'query': raw_message, 'type': 'step_by_step'})
                return _finish(solved, topic="math_steps", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            graph_image = generate_graph(raw_message)
            if graph_image:
                func_part = raw_message.split(' ', 1)[1] if ' ' in raw_message else raw_message
                track_analytics('math_query', email, {'query': raw_message, 'type': 'graph'})
                return _finish(f"📊 Here's the graph of **{func_part}**:", 
                              topic="graph", image=graph_image, memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            expr = looks_like_math(raw_message)
            if expr:
                result = safe_calculate(expr)
                track_analytics('math_query', email, {'query': raw_message, 'type': 'calculation'})
                return _finish(f"🧮 **{expr} = {result}**", topic="math", 
                              memory=memory, email=email, stats=stats, 
                              known_name=known_name, raw_message=raw_message)
        except ZeroDivisionError:
            return _finish("🧮 Can't divide by zero!", topic="math",
                          memory=memory, email=email, stats=stats,
                          known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== CHEMISTRY ENGINE ======
    if intent == 'chemistry':
        try:
            chem_result = solve_chemistry(msg, raw_message)
            if chem_result:
                track_analytics('chemistry_query', email, {'query': raw_message})
                return _finish(chem_result, topic="chemistry", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            element_match = re.search(r'(?:element|periodic table|info about)\s+([A-Za-z]+)', raw_message)
            if element_match:
                elem_name = element_match.group(1)
                elem_info = get_element_info(elem_name)
                if elem_info:
                    track_analytics('chemistry_query', email, {'query': raw_message, 'type': 'element'})
                    return _finish(elem_info, topic="chemistry", memory=memory, email=email,
                                  stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass

    # ====== BIOLOGY ENGINE ======
    if intent == 'biology':
        try:
            bio_result = solve_biology(raw_message)
            if bio_result:
                return _finish(bio_result, topic="biology", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== DEVELOPER UTILITIES ======
    if intent == 'developer':
        try:
            if 'password' in msg:
                length = 16
                if re.search(r'(\d+)\s*(?:char|length)', msg):
                    length_match = re.search(r'(\d+)\s*(?:char|length)', msg)
                    if length_match:
                        length = min(int(length_match.group(1)), 64)
                password = generate_password(length, True)
                track_analytics('developer_tool', email, {'tool': 'password'})
                return _finish(f"🔑 **Secure Password ({length} chars):**\n`{password}`\n\n_Store this securely!_",
                              topic="developer", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'hash' in msg:
                text_match = re.search(r'(?:hash|hash of)\s+["\'](.+?)["\']', raw_message)
                if text_match:
                    text = text_match.group(1)
                    hashed = hash_text(text, 'sha256')
                    track_analytics('developer_tool', email, {'tool': 'hash'})
                    return _finish(f"🔐 **SHA-256 Hash:**\n`{hashed}`\n\n**Original:** `{text}`",
                                  topic="developer", memory=memory, email=email,
                                  stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'uuid' in msg or 'guid' in msg:
                uuid = generate_uuid()
                track_analytics('developer_tool', email, {'tool': 'uuid'})
                return _finish(f"🆔 **UUID v4:**\n`{uuid}`",
                              topic="developer", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'json' in msg:
                json_match = re.search(r'`(.+?)`', raw_message)
                if json_match:
                    json_str = json_match.group(1)
                    valid, msg_result = validate_json(json_str)
                    if valid:
                        formatted = format_json(json_str)
                        track_analytics('developer_tool', email, {'tool': 'json'})
                        return _finish(f"✅ **Valid JSON**\n\n```json\n{formatted[:500]}\n```",
                                      topic="developer", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
                    else:
                        return _finish(f"❌ **Invalid JSON**\n\nError: {msg_result}",
                                      topic="developer", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== LANGUAGE TOOLS ======
    if intent == 'language':
        try:
            if 'translate' in msg:
                translate_match = re.search(r'(?:translate|convert)\s+["\'](.+?)["\']', raw_message)
                if translate_match:
                    text = translate_match.group(1)
                    target = 'en'
                    if 'to' in msg:
                        lang_match = re.search(r'to\s+([a-z]{2})', msg)
                        if lang_match:
                            target = lang_match.group(1)
                    translated = translate_text(text, target)
                    track_analytics('language_tool', email, {'tool': 'translate'})
                    return _finish(f"🌍 **Translation ({target}):**\n\n**Original:** {text}\n**Translated:** {translated}",
                                  topic="language", memory=memory, email=email,
                                  stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'spell' in msg or 'grammar' in msg:
                text_match = re.search(r'(?:check|fix)\s+["\'](.+?)["\']', raw_message)
                if text_match:
                    text = text_match.group(1)
                    corrections = spell_check(text)
                    if corrections:
                        result = "📝 **Spelling & Grammar Check:**\n\n"
                        for i, corr in enumerate(corrections[:5], 1):
                            result += f"**{i}.** {corr['message']}\n"
                            if corr['suggestions']:
                                result += f"   Suggestions: {', '.join(corr['suggestions'])}\n"
                            result += "\n"
                        track_analytics('language_tool', email, {'tool': 'spell_check'})
                        return _finish(result, topic="language", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'summarize' in msg:
                text_match = re.search(r'(?:summarize|summary of)\s+["\'](.+?)["\']', raw_message)
                if text_match:
                    text = text_match.group(1)
                    summary = summarize_text_new(text, 5)
                    track_analytics('language_tool', email, {'tool': 'summarize'})
                    return _finish(f"📄 **Summary:**\n\n{summary}",
                                  topic="language", memory=memory, email=email,
                                  stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== TIME & DATE ======
    if intent == 'time':
        try:
            if 'time zone' in msg or 'timezone' in msg:
                tz_match = re.search(r'(?:from|convert)\s+([A-Za-z/]+)\s+(?:to|->)\s+([A-Za-z/]+)', raw_message)
                if tz_match:
                    from_tz = tz_match.group(1)
                    to_tz = tz_match.group(2)
                    now = datetime.now()
                    converted = convert_timezone(now, from_tz, to_tz)
                    if converted:
                        track_analytics('time_tool', email, {'tool': 'timezone'})
                        return _finish(f"🕐 **Time Zone Conversion:**\n\n**{from_tz}:** {now.strftime('%I:%M %p')}\n**{to_tz}:** {converted.strftime('%I:%M %p')}",
                                      topic="time", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'business days' in msg:
                date_match = re.findall(r'(\d{4}-\d{1,2}-\d{1,2})', raw_message)
                if len(date_match) >= 2:
                    start = datetime.strptime(date_match[0], '%Y-%m-%d')
                    end = datetime.strptime(date_match[1], '%Y-%m-%d')
                    days = get_business_days(start, end)
                    if days is not None:
                        track_analytics('time_tool', email, {'tool': 'business_days'})
                        return _finish(f"📅 **Business Days:**\n\nBetween {start.strftime('%b %d, %Y')} and {end.strftime('%b %d, %Y')}\n**{days} business days**",
                                      topic="time", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== CYBERSECURITY ======
    if intent == 'security':
        try:
            if 'whois' in msg:
                domain_match = re.search(r'(?:whois|lookup)\s+([a-zA-Z0-9.-]+)', raw_message)
                if domain_match:
                    domain = domain_match.group(1)
                    whois_data = whois_lookup(domain)
                    if whois_data:
                        track_analytics('security_tool', email, {'tool': 'whois'})
                        result = f"🔍 **WHOIS Lookup for {domain}:**\n\n"
                        result += f"**Registrar:** {whois_data.get('registrar', 'N/A')}\n"
                        result += f"**Created:** {whois_data.get('creation_date', 'N/A')}\n"
                        result += f"**Expires:** {whois_data.get('expiration_date', 'N/A')}\n"
                        return _finish(result, topic="security", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
        
        try:
            if 'dns' in msg:
                domain_match = re.search(r'(?:dns|dns lookup)\s+([a-zA-Z0-9.-]+)', raw_message)
                if domain_match:
                    domain = domain_match.group(1)
                    dns_data = dns_lookup(domain)
                    if dns_data:
                        track_analytics('security_tool', email, {'tool': 'dns'})
                        result = f"🌐 **DNS Lookup for {domain}:**\n\n"
                        for record_type, records in dns_data.items():
                            if records:
                                result += f"**{record_type}:** {', '.join(records[:3])}\n"
                        return _finish(result, topic="security", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== QR CODE ======
    if intent == 'qr':
        try:
            if 'qr' in msg:
                data_match = re.search(r'(?:qr code|generate qr)\s+["\'](.+?)["\']', raw_message)
                if data_match:
                    data = data_match.group(1)
                    qr_image = generate_qr(data)
                    if qr_image:
                        track_analytics('productivity_tool', email, {'tool': 'qr'})
                        return _finish(f"📱 **QR Code:**\n\n![QR Code]({qr_image})",
                                      topic="qr", image=qr_image, memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== HEALTH CALCULATORS ======
    if intent == 'health':
        try:
            if 'bmi' in msg:
                weight_match = re.search(r'(\d+)\s*(?:kg|lbs?)', raw_message)
                height_match = re.search(r'(\d+)\s*(?:cm|m|ft|in)', raw_message)
                if weight_match and height_match:
                    weight = float(weight_match.group(1))
                    height = float(height_match.group(1))
                    unit = 'imperial' if 'lbs' in raw_message or 'ft' in raw_message or 'in' in raw_message else 'metric'
                    bmi_result = calculate_bmi(weight, height, unit)
                    if bmi_result:
                        track_analytics('health_tool', email, {'tool': 'bmi'})
                        return _finish(f"🏋️ **BMI Calculator:**\n\n**BMI:** {bmi_result['bmi']}\n**Category:** {bmi_result['category']}\n**Advice:** {bmi_result['advice']}",
                                      topic="health", memory=memory, email=email,
                                      stats=stats, known_name=known_name, raw_message=raw_message)
        except:
            pass
    
    # ====== WEATHER (Existing) ======
    try:
        weather_loc = parse_weather_query(msg)
        if weather_loc:
            cached = cache_get(f"weather:{weather_loc}")
            if cached:
                return _finish(cached, topic="weather", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
            result = get_weather(weather_loc)
            if result:
                cache_set(f"weather:{weather_loc}", result)
                track_analytics('weather_query', email, {'location': weather_loc})
                return _finish(result, topic="weather", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== NEWS (Existing) ======
    try:
        if re.search(r'\b(news|headlines?|latest|breaking)\b', msg):
            cat = parse_news_query(msg) or "general"
            cached = cache_get(f"news:{cat}")
            if cached:
                return _finish(cached, topic="news", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
            result = get_news(cat)
            if result:
                cache_set(f"news:{cat}", result)
                track_analytics('news_query', email, {'category': cat})
                return _finish(result, topic="news", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== COUNTRY FACTS (Existing) ======
    try:
        if re.search(r'\b(country|capital|population|currency|language|flag)\b', msg):
            country_q = parse_country_query(msg)
            if country_q and len(country_q) > 2:
                result = get_country_info(country_q)
                if result:
                    track_analytics('country_query', email, {'country': country_q})
                    return _finish(result, topic="country", memory=memory, email=email,
                                  stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== KNOWLEDGE BASE (Existing) ======
    try:
        kb_answer = knowledge_base_lookup(msg)
        if kb_answer:
            return _finish(kb_answer, topic="knowledge", memory=memory, email=email,
                          stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== MEMORY (Existing) ======
    try:
        recall = answer_from_memory(msg, memory)
        if recall:
            return _finish(recall, topic="recall", memory=memory, email=email,
                          stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== LEARN FACTS (Existing) ======
    try:
        learned = extract_facts(raw_message, memory)
        if learned:
            ack = []
            for key, value in learned:
                label = key.replace('favorite_', 'favorite ').replace('_', ' ')
                ack.append(f"Got it — your {label} is **{value}**. I'll remember that! 🧠")
            return _finish(" ".join(ack), topic="learning", memory=memory, email=email,
                          stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== DATE/TIME (Existing) ======
    try:
        dt_answer = datetime_answer(msg)
        if dt_answer:
            return _finish(dt_answer, topic="time", memory=memory, email=email,
                          stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== UNIT CONVERSION (Existing - FIXED) ======
    try:
        converted = convert_units(msg)
        if converted is not None:
            m = _CONVERT_RE.search(msg)
            if m:
                from_u, to_u = m.group(2), m.group(3)
                result_str = f"{round(converted, 6):.6f}".rstrip('0').rstrip('.')
                reply = f"📏 {m.group(1)} {from_u} = **{result_str} {to_u}**"
                track_analytics('unit_conversion', email, {'from': from_u, 'to': to_u})
                return _finish(reply, topic="conversion", memory=memory, email=email,
                              stats=stats, known_name=known_name, raw_message=raw_message)
    except:
        pass
    
    # ====== SEARCH ENGINE (Enhanced) ======
    try:
        cleaned_query = clean_query(raw_message)
        expanded_query = expand_query(cleaned_query)
        
        cached_search = cache_get(f"search:{cleaned_query}")
        if cached_search:
            return _finish(cached_search, topic="search", memory=memory, email=email,
                          stats=stats, known_name=known_name, raw_message=raw_message)
        
        search_results = search_web(expanded_query)
        if not search_results:
            return _finish(f"🔍 I searched for **{cleaned_query}** but found no results. Try rephrasing?",
                          topic="search", memory=memory, email=email,
                          stats=stats, known_name=known_name, raw_message=raw_message)
        
        ranked_sources = rank_sources(search_results, cleaned_query)
        
        verified_sources = []
        for source in ranked_sources[:5]:
            health = get_source_health(source['url'])
            if health['healthy']:
                verified_sources.append(source)
        
        if not verified_sources:
            verified_sources = ranked_sources[:3]
        
        confidence = calculate_confidence(verified_sources, cleaned_query)
        
        title = cleaned_query.title()
        direct_answer = verified_sources[0]['snippet'] if verified_sources else "No direct answer found."
        explanation = f"Based on {len(verified_sources)} sources, here's what I found about {cleaned_query}."
        key_points = [f"{s['title']}" for s in verified_sources[:3]]
        sources = [{'title': s['title'], 'url': s['url']} for s in verified_sources[:3]]
        related_questions = [
            f"What is {cleaned_query.split()[0]}?",
            f"How does {cleaned_query.split()[0]} work?",
            f"Latest news about {cleaned_query.split()[0]}"
        ]
        
        formatted_response = format_answer(
            title=title,
            direct_answer=direct_answer,
            explanation=explanation,
            key_points=key_points,
            sources=sources,
            related_questions=related_questions
        )
        
        if confidence > 80:
            formatted_response += f"\n\n✅ I'm {confidence}% confident about this information."
        elif confidence > 60:
            formatted_response += f"\n\nℹ️ I'm about {confidence}% confident — you might want to verify with additional sources."
        
        endings = [
            "\n\nAnything else you'd like to know? 😊",
            "\n\nLet me know if you need more details! 💡",
            "\n\nHope that helps! What's next? 🚀",
            "\n\nFeel free to ask follow-up questions! 🌟"
        ]
        formatted_response += random.choice(endings)
        
        cache_set(f"search:{cleaned_query}", formatted_response)
        track_analytics('search_query', email, {'query': cleaned_query, 'sources': len(verified_sources)})
        
        return _finish(formatted_response, topic="search", memory=memory, email=email,
                      stats=stats, known_name=known_name, raw_message=raw_message)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return _finish(f"🔍 I tried to search for **{raw_message}** but encountered an error. Please try again.",
                      topic="error", memory=memory, email=email,
                      stats=stats, known_name=known_name, raw_message=raw_message)

def _finish(reply, topic=None, image=None, memory=None, email=None, stats=None, known_name=None, raw_message=None):
    """Helper function to finalize response with suffix and context."""
    if memory is None:
        memory = {}
    
    # Ensure reply is always a string
    if reply is None:
        reply = "I couldn't generate a response. Please try again."
    
    if not isinstance(reply, str):
        reply = str(reply)
    
    # Add suffix if not a friendly response
    if not any(kw in reply for kw in ['😊', '👋', '❤️', '🌟', '✨', '🙂']):
        try:
            suffix = f"\n\n✨ **{known_name or 'User'}** • Level {stats.get('level', 1)} — {stats.get('title', '🌟 Newbie Chatter')} • {stats.get('count', 0)} messages"
            if not reply.endswith(suffix):
                reply += suffix
        except:
            pass
    
    # Store in memory
    if raw_message:
        try:
            memory["conversation_history"].append({"user": raw_message, "yama": reply})
            memory["conversation_history"] = memory["conversation_history"][-MEMORY_TURN_LIMIT:]
        except:
            pass
    
    if topic:
        memory["last_topic"] = topic
    
    # Store context
    try:
        context = detect_intent_and_context(raw_message or "")
        memory["context"] = context
    except:
        pass
    
    if email:
        try:
            save_memory(email, memory)
        except:
            pass
    
    # ALWAYS return consistent format
    return {"text": reply, "image": image}

# ============ ENDPOINTS ============

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML

@app.post("/set_user")
async def set_user(request: Request):
    try:
        data = await request.json()
        get_or_create_user(data.get('email'), data.get('name'), data.get('picture'))
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Set user error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        message = data.get('message', '')
        email = data.get('email', '')
        
        if not message:
            return {"response": "Please enter a message.", "image": None}
        
        start_time = time.time()
        result = get_response(message, email)
        end_time = time.time()
        
        track_analytics('response_time', email, {'time': end_time - start_time, 'query': message[:50]})
        
        if isinstance(result, dict):
            response_text = result.get("text", "I couldn't generate a response. Please try again.")
            response_image = result.get("image")
        else:
            response_text = str(result) if result else "I couldn't generate a response. Please try again."
            response_image = None
        
        # Ensure response_text is always a string
        if response_text is None:
            response_text = "I couldn't generate a response. Please try again."
        
        # Save to history
        if email:
            try:
                history = load_history(email)
                history.append({
                    "user": message,
                    "ai": response_text,
                    "timestamp": datetime.now().strftime("%H:%M")
                })
                save_history(email, history)
            except:
                pass
        
        return {"response": response_text, "image": response_image}
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {"response": f"⚠️ Error: {str(e)}", "image": None}

@app.get("/get_history")
async def get_history(email: str = ""):
    try:
        return load_history(email)
    except:
        return []

@app.post("/clear_history")
async def clear_history_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    email = data.get('email', '')
    save_history(email, [])
    return {"status": "cleared"}

@app.post("/feedback")
async def feedback_endpoint(request: Request):
    try:
        data = await request.json()
        email = data.get('email', '')
        feedback_type = data.get('feedback_type')
        category = data.get('category')
        question = data.get('question', '')
        answer = data.get('answer', '')
        
        if feedback_type not in ('like', 'dislike'):
            raise HTTPException(status_code=400, detail="feedback_type must be 'like' or 'dislike'")
        
        track_analytics('feedback', email, {'type': feedback_type, 'category': category})
        
        feedback_db.insert({
            "email": email,
            "type": feedback_type,
            "category": category if feedback_type == 'dislike' else None,
            "question": question,
            "answer": answer,
            "timestamp": datetime.now().isoformat()
        })
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Feedback error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/regenerate")
async def regenerate_endpoint(request: Request):
    try:
        data = await request.json()
        email = data.get('email', '')
        message = data.get('message', '')
        invalidate_cache_for_message(message)
        result = get_response(message, email)
        if isinstance(result, dict):
            return {"response": result.get("text", "No response"), "image": result.get("image")}
        return {"response": str(result) if result else "No response", "image": None}
    except Exception as e:
        logger.error(f"Regenerate error: {e}")
        return {"response": f"Error: {str(e)}", "image": None}

@app.post("/continue_generating")
async def continue_generating_endpoint(request: Request):
    try:
        data = await request.json()
        email = data.get('email', '')
        message = data.get('message', '')
        memory = load_memory(email)
        last_topic = memory.get("last_topic")
        
        if last_topic == "search":
            cleaned_query = clean_query(message)
            search_results = search_web(cleaned_query)
            if search_results and len(search_results) > 3:
                extra = search_results[3:6]
                response = "📚 **More sources:**\n\n"
                for i, r in enumerate(extra, start=4):
                    response += f"**[{i}] {r['title']}**\n{r['snippet']}\n🔗 {r['url']}\n\n"
                return {"response": response}
        
        return {"response": "_That's the complete answer — no additional details to add for this one._"}
    except Exception as e:
        logger.error(f"Continue generating error: {e}")
        return {"response": f"Error: {str(e)}"}

@app.get("/share_conversation")
async def share_conversation_endpoint(email: str = ""):
    try:
        history = load_history(email)
        conversation = [{"user": h.get("user", ""), "ai": h.get("ai", "")} for h in history]
        return {"conversation": conversation}
    except:
        return {"conversation": []}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Yama AI", "timestamp": datetime.now().isoformat()}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), email: str = Form(...)):
    try:
        contents = await file.read()
        filename = (file.filename or "").lower()
        image_types = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')

        if filename.endswith(image_types):
            if not (PIL_AVAILABLE and TESSERACT_AVAILABLE):
                return {"status": "ok",
                        "message": "📷 Image received, but OCR isn't available on this server "
                                    "(Pillow/pytesseract not installed). Please type your question instead.",
                        "image": None}
            try:
                img = Image.open(io.BytesIO(contents))
                extracted_text = pytesseract.image_to_string(img).strip()
            except Exception as e:
                logger.error(f"OCR error: {e}")
                return {"status": "error",
                        "message": "⚠️ I couldn't read that image. Please try a clearer photo or type the question.",
                        "image": None}

            if not extracted_text:
                return {"status": "ok",
                        "message": "📷 Image uploaded, but I couldn't detect any readable text. Please type the question.",
                        "image": None}

            ai_result = get_response(extracted_text, email)
            ai_text = ai_result.get("text", "Could not solve.") if isinstance(ai_result, dict) else str(ai_result)
            ai_image = ai_result.get("image") if isinstance(ai_result, dict) else None
            message = f"📷 **Text detected in image:**\n```\n{extracted_text}\n```\n\n**Answer:**\n{ai_text}"
            return {"status": "ok", "message": message, "image": ai_image}

        return {"status": "ok",
                "message": f"📎 Received **{file.filename}**. Right now I can only read text out of images (OCR) — "
                            "please paste the text/question directly for other file types.",
                "image": None}
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return {"status": "error", "message": f"⚠️ Upload failed: {str(e)}", "image": None}

@app.get("/analytics")
async def get_analytics():
    try:
        all_data = analytics_db.all()
        total = len(all_data)
        by_type = {}
        for item in all_data:
            t = item.get('type', 'unknown')
            by_type[t] = by_type.get(t, 0) + 1
        return {
            'total_events': total,
            'by_type': by_type,
            'recent': all_data[-50:] if len(all_data) > 50 else all_data
        }
    except:
        return {"total_events": 0, "by_type": {}, "recent": []}

@app.get("/feedback_stats")
async def feedback_stats_endpoint():
    try:
        all_feedback = feedback_db.all()
        likes = [f for f in all_feedback if f.get('type') == 'like']
        dislikes = [f for f in all_feedback if f.get('type') == 'dislike']
        
        category_counts = {}
        for f in dislikes:
            cat = f.get('category') or 'Other'
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        question_dislike_counts = {}
        for f in dislikes:
            q = f.get('question', '')
            question_dislike_counts[q] = question_dislike_counts.get(q, 0) + 1
        most_disliked = sorted(question_dislike_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            'total_likes': len(likes),
            'total_dislikes': len(dislikes),
            'dislike_categories': category_counts,
            'most_disliked_questions': [{'question': q, 'count': c} for q, c in most_disliked]
        }
    except:
        return {"total_likes": 0, "total_dislikes": 0, "dislike_categories": {}, "most_disliked_questions": []}

@app.get("/element/{element_name}")
async def get_element_endpoint(element_name: str):
    try:
        result = get_element_info(element_name)
        if result:
            return {'success': True, 'data': result}
        return {'success': False, 'error': 'Element not found'}
    except:
        return {'success': False, 'error': 'Error fetching element'}

@app.get("/qr/{data:path}")
async def generate_qr_endpoint(data: str):
    try:
        qr_image = generate_qr(data)
        if qr_image:
            return {'success': True, 'image': qr_image}
        return {'success': False, 'error': 'Could not generate QR code'}
    except:
        return {'success': False, 'error': 'QR generation error'}

@app.post("/translate")
async def translate_endpoint(request: Request):
    try:
        data = await request.json()
        text = data.get('text', '')
        target = data.get('target', 'en')
        
        if not text:
            raise HTTPException(status_code=400, detail="Text required")
        
        translated = translate_text(text, target)
        return {'original': text, 'translated': translated, 'target_language': target}
    except Exception as e:
        return {'error': str(e)}

@app.post("/hash")
async def generate_hash_endpoint(request: Request):
    try:
        data = await request.json()
        text = data.get('text', '')
        algorithm = data.get('algorithm', 'sha256')
        
        if not text:
            raise HTTPException(status_code=400, detail="Text required")
        
        hashed = hash_text(text, algorithm)
        return {'algorithm': algorithm, 'hash': hashed}
    except Exception as e:
        return {'error': str(e)}

@app.post("/password")
async def generate_password_endpoint(request: Request):
    try:
        data = await request.json()
        length = data.get('length', 16)
        include_symbols = data.get('include_symbols', True)
        
        password = generate_password(length, include_symbols)
        return {'password': password, 'length': len(password), 'includes_symbols': include_symbols}
    except Exception as e:
        return {'error': str(e)}

@app.post("/bmi")
async def calculate_bmi_endpoint(request: Request):
    try:
        data = await request.json()
        weight = data.get('weight')
        height = data.get('height')
        unit = data.get('unit', 'metric')
        
        if not weight or not height:
            raise HTTPException(status_code=400, detail="Weight and height required")
        
        result = calculate_bmi(float(weight), float(height), unit)
        if result:
            return {'success': True, **result}
        return {'success': False, 'error': 'Could not calculate BMI'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

# ============ HTML ============
# USE YOUR ORIGINAL HTML HERE - Copy your working HTML from your original file
HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes, viewport-fit=cover, interactive-widget=resizes-content">
    <title>Yama AI - Your Intelligent Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        html, body {
            margin: 0; padding: 0; width: 100%; height: 100%; 
            overflow-x: hidden; overflow-y: auto; 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f0e8; transition: all 0.3s ease;
            -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
            position: relative;
        }
        img, video, iframe { max-width: 100%; height: auto; }
        
        body.dark { background: #1a1a2e; }
        body.dark .app { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); }
        body.dark .header { background: rgba(26,26,46,0.95); border-bottom-color: #2a2a4e; }
        body.dark .logo h1 { color: #d4c5a9; }
        body.dark .input-wrapper { background: #2a2a4e; border-color: #3a3a5e; }
        body.dark textarea { color: #e0e0e0; }
        body.dark textarea::placeholder { color: #6a5a7a; }
        body.dark .message-content { color: #e0e0e0; }
        body.dark .ai-message .message-content { background: #2a2a4e !important; color: #e0e0e0 !important; }
        body.dark .suggestion { background: #2a2a4e; border-color: #3a3a5e; color: #e0e0e0; }
        body.dark .suggestion:hover { background: #3a3a5e; color: white; }
        body.dark .welcome h2 { color: #d4c5a9; }
        body.dark .welcome p { color: #8a7a6a; }
        body.dark .sidebar { background: #0f0f23; border-right-color: #2a2a4e; }
        body.dark .sidebar-header { background: #0a0a1a; }
        body.dark .history-question { color: #d4c5a9; }
        body.dark .history-time { color: #6a5a7a; }
        body.dark .history-item:hover { background: rgba(212,197,169,0.08); border-color: #3a3a5e; }
        body.dark .clear-history { color: #d4c5a9; border-color: #3a3a5e; }
        body.dark .clear-history:hover { background: rgba(212,197,169,0.2); border-color: #c4a57b; }
        body.dark .new-chat-btn { background: #3a3a5e; color: #d4c5a9; }
        body.dark .new-chat-btn:hover { background: #4a4a6e; }
        body.dark .typing span { background: #d4c5a9; }
        body.dark .typing { color: #d4c5a9; }
        body.dark a { color: #4ecdc4; }
        body.dark .message-content a { color: #4ecdc4; }
        body.dark .message-content a:hover { color: #6ee7de; }
        body.dark .control-btn { color: #d4c5a9; }
        body.dark .control-btn:hover { background: #3a3a5e; color: white; }
        
        .login-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); z-index: 2000; display: flex; justify-content: center; align-items: center; padding: 20px; }
        .login-card { background: white; border-radius: 30px; padding: 40px 30px; text-align: center; max-width: 400px; width: 100%; box-shadow: 0 25px 50px rgba(0,0,0,0.2); }
        .login-card .logo-icon { font-size: 4rem; margin-bottom: 20px; }
        .login-card h2 { font-family: 'Playfair Display', serif; font-size: 2rem; margin-bottom: 10px; }
        .login-card p { color: #666; font-size: 1rem; margin-bottom: 30px; }
        
        .app {
            display: flex; flex-direction: column;
            height: 100dvh; min-height: 100vh;
            width: 100%;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            position: relative; overflow: hidden;
        }
        
        .sidebar { position: fixed; left: 0; top: 0; bottom: 0; width: min(280px, 80vw); background: #2c2418; border-right: 1px solid #4a3f2f; display: flex; flex-direction: column; transform: translateX(-100%); transition: transform 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55); z-index: 1000; box-shadow: 4px 0 20px rgba(0,0,0,0.1); }
        .sidebar.open { transform: translateX(0); }
        .sidebar-header { padding: 20px; border-bottom: 1px solid #4a3f2f; background: #1f1912; flex-shrink: 0; }
        .sidebar-header h3 { color: #d4c5a9; font-family: 'Playfair Display', serif; font-size: 1rem; }
        .user-profile { display: none; align-items: center; gap: 12px; padding: 12px; background: rgba(212,197,169,0.1); border-radius: 12px; margin-top: 15px; }
        .user-profile-img { width: 45px; height: 45px; border-radius: 50%; object-fit: cover; }
        .user-profile-info { flex: 1; min-width: 0; }
        .user-profile-name { color: #d4c5a9; font-weight: 600; font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .user-profile-email { color: #8a7a6a; font-size: 0.65rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .logout-btn { background: rgba(212,197,169,0.1); border: 1px solid #4a3f2f; border-radius: 20px; padding: 6px 12px; color: #d4c5a9; cursor: pointer; font-size: 0.65rem; white-space: nowrap; }
        .history-list { flex: 1; overflow-y: auto; padding: 12px; -webkit-overflow-scrolling: touch; }
        .history-item { padding: 10px; margin-bottom: 6px; border-radius: 10px; cursor: pointer; transition: all 0.2s; border: 1px solid transparent; }
        .history-item:hover { background: rgba(212,197,169,0.08); border-color: #4a3f2f; }
        .history-question { font-size: 0.8rem; color: #d4c5a9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .history-time { font-size: 0.6rem; color: #6a5a4a; margin-top: 4px; }
        .sidebar-footer { padding: 16px; border-top: 1px solid #4a3f2f; background: #1f1912; flex-shrink: 0; }
        .new-chat-btn { background: #4a3f2f; border: none; border-radius: 25px; padding: 12px 16px; color: #d4c5a9; cursor: pointer; width: 100%; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; gap: 8px; transition: all 0.2s; }
        .new-chat-btn:hover { background: #5a4f3f; }
        .clear-history { background: rgba(212,197,169,0.1); border: 1px solid #4a3f2f; border-radius: 20px; padding: 8px 16px; color: #d4c5a9; cursor: pointer; font-size: 0.7rem; margin-top: 10px; width: 100%; }
        .overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.4); display: none; z-index: 999; }
        .overlay.show { display: block; }
        
        .main {
            flex: 1; display: flex; flex-direction: column;
            min-height: 0; height: 100%; width: 100%;
            overflow: hidden;
        }
        
        .header {
            padding: 12px 16px; display: flex; align-items: center; gap: 12px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
            flex-shrink: 0; min-height: 56px; width: 100%;
            position: relative; z-index: 10;
        }
        .menu-btn { background: none; border: none; font-size: 1.3rem; cursor: pointer; color: #6a5a4a; padding: 8px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
        .menu-btn:hover { background: #d4c5a9; color: #2c2418; }
        .logo { flex: 1; display: flex; align-items: baseline; gap: 6px; min-width: 0; }
        .logo-icon { font-size: 1.8rem; }
        .logo h1 { font-family: 'Playfair Display', serif; font-size: 1.3rem; color: #2c2418; white-space: nowrap; }
        .new-chat-mobile { background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 8px; border-radius: 10px; color: #6a5a4a; display: none; }
        .control-btn { background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 8px 12px; border-radius: 20px; color: #6a5a4a; transition: all 0.2s; display: flex; align-items: center; justify-content: center; }
        .control-btn:hover { background: #d4c5a9; }
        .user-btn { background: none; border: none; cursor: pointer; display: none; padding: 4px; }
        .user-btn img { width: 35px; height: 35px; border-radius: 50%; object-fit: cover; }
        
        .messages {
            flex: 1; overflow-y: auto;
            padding: 16px; padding-bottom: 20px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }
        .message { margin-bottom: 20px; animation: fadeIn 0.3s ease; }
        .message-wrapper { display: inline-block; max-width: 85%; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .user-message { text-align: right; }
        .ai-message { text-align: left; }
        .message-content { display: inline-block; max-width: 100%; font-size: 0.9rem; line-height: 1.5; color: #2c2418; background: transparent !important; padding: 0 !important; word-break: break-word; }
        .message-content a { color: #8a5a2c; text-decoration: underline; word-break: break-all; }
        .user-message .message-content { background: #2c2418 !important; color: white !important; padding: 10px 16px !important; border-radius: 20px !important; }
        .ai-message .message-content { background: white !important; color: #2c2418 !important; padding: 12px 18px !important; border-radius: 20px !important; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
        
        .message-actions { display: flex; gap: 8px; margin-top: 8px; opacity: 0.6; transition: opacity 0.2s; flex-wrap: wrap; }
        .message-actions:hover { opacity: 1; }
        .message-actions button { background: none; border: none; cursor: pointer; padding: 4px 8px; font-size: 0.75rem; border-radius: 6px; color: #6a5a4a; transition: all 0.2s; display: flex; align-items: center; gap: 4px; }
        .message-actions button:hover { background: rgba(44,36,24,0.1); color: #2c2418; }
        body.dark .message-actions button { color: #8a7a6a; }
        body.dark .message-actions button:hover { background: rgba(212,197,169,0.1); color: #d4c5a9; }
        .message-actions .liked { color: #4caf50 !important; }
        .message-actions .disliked { color: #f44336 !important; }
        
        .edit-message-input { display: none; width: 100%; padding: 8px 12px; border: 2px solid #2c2418; border-radius: 12px; font-size: 0.9rem; font-family: inherit; background: white; color: #2c2418; }
        body.dark .edit-message-input { background: #2a2a4e; color: #e0e0e0; border-color: #4a3f2f; }
        .edit-message-input.active { display: block; }
        .edit-actions { display: none; gap: 8px; margin-top: 8px; }
        .edit-actions.active { display: flex; }
        .edit-actions button { padding: 4px 12px; border-radius: 6px; border: none; cursor: pointer; font-size: 0.75rem; }
        .edit-actions .save-edit { background: #2c2418; color: white; }
        .edit-actions .cancel-edit { background: #e0d5c8; color: #2c2418; }
        body.dark .edit-actions .cancel-edit { background: #3a3a5e; color: #d4c5a9; }
        
        .typing { display: none; padding: 10px 16px; gap: 5px; color: #888; font-size: 0.8rem; flex-shrink: 0; }
        .typing span { width: 6px; height: 6px; background: #c4a57b; border-radius: 50%; display: inline-block; animation: bounce 1.4s infinite; }
        @keyframes bounce { 0%, 60%, 100% { transform: translateY(0); } 30% { transform: translateY(-6px); } }
        
        .input-area {
            position: sticky;
            bottom: 0;
            z-index: 100;
            background: #f5f0e8;
            padding: 12px 16px env(safe-area-inset-bottom, 20px);
            padding-bottom: max(12px, env(safe-area-inset-bottom, 20px));
            flex-shrink: 0;
            border-top: 1px solid rgba(212,197,169,0.3);
            width: 100%;
            transition: padding-bottom 0.15s ease;
        }
        body.dark .input-area { background: #1a1a2e; border-top-color: rgba(42,42,78,0.3); }
        
        .input-wrapper {
            display: flex; align-items: flex-end; gap: 12px;
            background: white; border-radius: 28px;
            padding: 8px 8px 8px 20px;
            border: 1px solid #d4c5a9;
            width: 100%; max-width: 760px;
            margin: 0 auto;
            min-height: 56px;
        }
        body.dark .input-wrapper { background: #2a2a4e; border-color: #3a3a5e; }
        
        .input-text-wrapper { flex: 1; min-width: 0; }
        textarea {
            width: 100%; background: transparent; border: none; outline: none;
            font-size: 16px; line-height: 1.5; resize: none;
            padding: 8px 0; font-family: inherit; color: #2c2418;
            min-height: 24px; max-height: 180px; overflow-y: auto;
        }
        body.dark textarea { color: #e0e0e0; }
        textarea::placeholder { color: #b8a88a; font-size: 0.95rem; }
        @media (max-width: 768px) { textarea { font-size: 16px !important; } }
        
        .submit-btn {
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0; width: 44px; height: 44px; border-radius: 50%;
            border: none; background-color: #2c2418; color: white;
            cursor: pointer; transition: all 0.2s;
            min-width: 44px; min-height: 44px;
        }
        body.dark .submit-btn { background-color: #4a3f2f; }
        .submit-btn:hover { background-color: #4a3f2f; transform: scale(1.02); }
        .submit-btn:active { transform: scale(0.96); }
        .submit-icon { width: 20px; height: 20px; fill: currentColor; }
        
        .attach-btn {
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0; width: 44px; height: 44px; border-radius: 50%;
            border: 1px solid #e0d5c0; background-color: transparent;
            color: #2c2418; cursor: pointer; transition: all 0.2s;
            min-width: 44px; min-height: 44px;
        }
        body.dark .attach-btn { border-color: #4a3f2f; color: #e0e0e0; }
        .attach-btn:hover { background-color: rgba(44,36,24,0.06); }
        .attach-icon { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; }
        .attach-btn.listening { background-color: #d9534f; border-color: #d9534f; color: white; animation: pulseMic 1s ease-in-out infinite; }
        @keyframes pulseMic { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.08); } }
        .message-content img.chat-image { max-width: 100%; border-radius: 10px; margin-top: 10px; display: block; }
        
        .welcome {
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            min-height: 40vh; text-align: center; padding: 20px;
        }
        .welcome-icon { font-size: 3rem; margin-bottom: 15px; animation: float 3s ease-in-out infinite; }
        @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
        .welcome h2 { font-family: 'Playfair Display', serif; font-size: clamp(1.5rem, 4vw, 2.5rem); color: #2c2418; margin-bottom: 8px; }
        .welcome p { color: #6a5a4a; font-size: clamp(0.75rem, 1.5vw, 0.95rem); margin-bottom: 20px; }
        .suggestions {
            display: flex; flex-wrap: wrap; gap: 8px;
            justify-content: center; margin-top: 15px;
            max-width: 100%;
        }
        .suggestion {
            background: white; border: 1px solid #d4c5a9; border-radius: 30px;
            padding: 6px 14px; font-size: clamp(0.6rem, 1.2vw, 0.75rem);
            color: #2c2418; cursor: pointer; transition: all 0.2s;
            white-space: nowrap;
        }
        .suggestion:hover { background: #2c2418; color: white; border-color: #2c2418; }
        
        @media (max-width: 480px) {
            .header { padding: 8px 12px; min-height: 48px; gap: 8px; }
            .logo h1 { font-size: 1rem; }
            .logo-icon { font-size: 1.2rem; }
            .messages { padding: 10px 12px; }
            .input-area { padding: 8px 10px 14px; padding-bottom: max(8px, env(safe-area-inset-bottom, 14px)); }
            .input-wrapper { padding: 5px 5px 5px 14px; min-height: 44px; gap: 8px; border-radius: 24px; }
            textarea { font-size: 15px !important; padding: 6px 0; min-height: 20px; }
            .submit-btn { width: 40px; height: 40px; min-width: 40px; min-height: 40px; }
            .submit-icon { width: 16px; height: 16px; }
            .message-content { font-size: 0.8rem; }
            .suggestions { display: none; }
            .new-chat-mobile { display: block; }
        }
        @media (max-width: 380px) {
            .header { padding: 6px 10px; min-height: 44px; gap: 6px; }
            .logo h1 { font-size: 0.85rem; }
            .logo-icon { font-size: 1rem; }
            .messages { padding: 8px 10px; }
            .input-area { padding: 6px 8px 12px; padding-bottom: max(6px, env(safe-area-inset-bottom, 12px)); }
            .input-wrapper { padding: 4px 4px 4px 12px; min-height: 40px; gap: 6px; border-radius: 22px; }
            textarea { font-size: 14px !important; padding: 5px 0; min-height: 18px; }
            .submit-btn { width: 36px; height: 36px; min-width: 36px; min-height: 36px; }
            .submit-icon { width: 14px; height: 14px; }
            .message-content { font-size: 0.75rem; }
        }
        @media (max-height: 500px) and (orientation: landscape) {
            .header { min-height: 40px; padding: 4px 12px; gap: 6px; }
            .logo h1 { font-size: 0.9rem; }
            .logo-icon { font-size: 1.1rem; }
            .messages { padding: 6px 12px; padding-bottom: 10px; }
            .input-area { padding: 4px 12px 8px; padding-bottom: max(4px, env(safe-area-inset-bottom, 8px)); }
            .input-wrapper { min-height: 38px; padding: 4px 4px 4px 12px; }
            textarea { min-height: 20px; max-height: 80px; font-size: 14px !important; padding: 4px 0; }
            .submit-btn { width: 36px; height: 36px; min-width: 36px; min-height: 36px; }
            .submit-icon { width: 14px; height: 14px; }
            .welcome { min-height: 20vh; }
            .suggestions { display: none; }
        }
        @media (min-width: 769px) and (max-width: 1024px) {
            .input-wrapper { max-width: 90%; }
            .messages { padding: 16px 24px; }
            .header { padding: 14px 20px; }
        }
        @media (min-width: 1025px) {
            .input-wrapper { max-width: 760px; }
            .messages { padding: 24px 32px; }
            .header { padding: 16px 32px; }
        }
        
        @supports (height: 100dvh) {
            .app { height: 100dvh; min-height: 100dvh; }
        }
        
        .feedback-modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.45); z-index: 3000;
            display: none; align-items: center; justify-content: center;
            padding: 20px;
        }
        .feedback-modal-overlay.show { display: flex; }
        .feedback-modal {
            background: white; border-radius: 20px; padding: 24px;
            max-width: 380px; width: 100%;
            box-shadow: 0 25px 50px rgba(0,0,0,0.25);
        }
        body.dark .feedback-modal { background: #2a2a4e; color: #e0e0e0; }
        .feedback-modal h3 { font-family: 'Playfair Display', serif; margin-bottom: 16px; font-size: 1.15rem; }
        .feedback-options { display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }
        .feedback-options label {
            display: flex; align-items: center; gap: 10px;
            font-size: 0.9rem; cursor: pointer; padding: 6px 8px;
            border-radius: 8px; transition: background 0.15s;
        }
        .feedback-options label:hover { background: rgba(44,36,24,0.06); }
        body.dark .feedback-options label:hover { background: rgba(212,197,169,0.08); }
        .feedback-modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
        .feedback-modal-actions button {
            padding: 8px 18px; border-radius: 20px; border: none;
            cursor: pointer; font-size: 0.85rem; font-family: inherit;
        }
        .feedback-cancel-btn { background: #e0d5c8; color: #2c2418; }
        body.dark .feedback-cancel-btn { background: #3a3a5e; color: #d4c5a9; }
        .feedback-submit-btn { background: #2c2418; color: white; }
        body.dark .feedback-submit-btn { background: #4a3f2f; }
    </style>
</head>
<body>
    <div id="loginOverlay" class="login-overlay">
        <div class="login-card">
            <div class="logo-icon">🏛️</div>
            <h2>Welcome to Yama AI</h2>
            <p>Your Intelligent Assistant</p>
            <div id="g_id_onload" data-client_id="46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com" data-context="signin" data-ux_mode="popup" data-callback="handleCredentialResponse" data-auto_prompt="false"></div>
            <div class="g_id_signin" data-type="standard" data-shape="rectangular" data-theme="outline" data-text="signin_with" data-size="large" data-logo_alignment="left"></div>
        </div>
    </div>
    <div id="dislikeModal" class="feedback-modal-overlay">
        <div class="feedback-modal">
            <h3>What went wrong?</h3>
            <div class="feedback-options">
                <label><input type="radio" name="dislikeReason" value="Incorrect Answer" checked> Incorrect Answer</label>
                <label><input type="radio" name="dislikeReason" value="Outdated Information"> Outdated Information</label>
                <label><input type="radio" name="dislikeReason" value="Bad Source"> Bad Source</label>
                <label><input type="radio" name="dislikeReason" value="Not Helpful"> Not Helpful</label>
                <label><input type="radio" name="dislikeReason" value="Poor Explanation"> Poor Explanation</label>
                <label><input type="radio" name="dislikeReason" value="Other"> Other</label>
            </div>
            <div class="feedback-modal-actions">
                <button class="feedback-cancel-btn" onclick="closeDislikeModal()">Cancel</button>
                <button class="feedback-submit-btn" onclick="submitDislikeFeedback()">Submit</button>
            </div>
        </div>
    </div>
    <div class="app" id="app">
        <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header"><h3>📜 CONVERSATIONS</h3><div class="user-profile" id="userProfile"></div></div>
            <div class="history-list" id="historyList"><div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div></div>
            <div class="sidebar-footer"><button class="new-chat-btn" onclick="newChat()">➕ New Chat</button><button class="clear-history" onclick="clearHistory()">Clear all history</button></div>
        </div>
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()">☰</button>
                <div class="logo" id="logo"><span class="logo-icon">🏛️</span><h1>YAMA</h1></div>
                <button class="new-chat-mobile" onclick="newChat()">➕</button>
                <button class="control-btn" onclick="toggleTheme()" title="Dark/Light Mode">🌓</button>
                <button class="control-btn" onclick="exportChat()" title="Export Chat">📥</button>
                <button class="user-btn" id="userBtn" onclick="toggleUserMenu()"><img id="userAvatar" src="" alt="User"></button>
            </div>
            <div class="messages" id="messages">
                <div class="welcome" id="welcome">
                    <div class="welcome-icon">🏛️</div>
                    <h2>Yama AI</h2>
                    <p>Your Intelligent Assistant — Developed by <strong>Riishil M Mehta</strong></p>
                    <p style="font-size:0.8rem; color:#888; margin-bottom:10px;">⚡ No LLM • Pure Logic & Search • Fast & Reliable</p>
                    <div class="suggestions">
                        <div class="suggestion" onclick="askSuggestion('weather in Mumbai')">🌤️ Weather</div>
                        <div class="suggestion" onclick="askSuggestion('graph sin(x)')">📊 Graph</div>
                        <div class="suggestion" onclick="askSuggestion('5 km to miles')">📏 Convert</div>
                        <div class="suggestion" onclick="askSuggestion('latest tech news')">📰 News</div>
                        <div class="suggestion" onclick="askSuggestion('solve x^2 - 4 = 0')">📐 Math</div>
                        <div class="suggestion" onclick="askSuggestion('v=u+at u=0 a=9.8 t=5')">⚛️ Physics</div>
                        <div class="suggestion" onclick="askSuggestion('punnett square cross Aa x Aa')">🧬 Biology</div>
                        <div class="suggestion" onclick="askSuggestion('info about Japan')">🌍 Facts</div>
                    </div>
                </div>
            </div>
            <div class="typing" id="typing"><span></span><span></span><span></span> Yama is thinking...</div>
            <div class="input-area" id="inputArea">
                <div class="input-wrapper">
                    <input type="file" id="fileInput" style="display:none" accept=".pdf,.docx,.xlsx,.csv,.txt,.png,.jpg,.jpeg" onchange="handleFileUpload(event)">
                    <button class="attach-btn" onclick="document.getElementById('fileInput').click()" aria-label="Attach file" type="button">
                        <svg class="attach-icon" viewBox="0 0 24 24" width="18" height="18"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
                    </button>
                    <div class="input-text-wrapper"><textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea></div>
                    <button class="attach-btn" id="micBtn" onclick="toggleVoice()" aria-label="Voice input" type="button" title="Voice input">
                        <svg class="attach-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
                    </button>
                    <button class="submit-btn" onclick="sendMessage()" aria-label="Send message">
                        <svg class="submit-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                    </button>
                </div>
            </div>
        </div>
    </div>
    <script>
        let currentUser = null, hasMessages = false, messageCounter = 0, isGenerating = false;
        let messageStore = {};
        
        function toggleTheme() { document.body.classList.toggle('dark'); localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light'); }
        
        function exportChat() {
            const messages = document.querySelectorAll('.message');
            let exportText = '';
            messages.forEach(msg => {
                const sender = msg.classList.contains('user-message') ? 'You' : 'Yama';
                const text = msg.querySelector('.message-content').innerText;
                exportText += sender + ': ' + text + '\\n\\n';
            });
            const blob = new Blob([exportText], {type: 'text/plain'});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'yama_chat_' + new Date().toISOString() + '.txt';
            a.click();
        }
        
        if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark');
        
        function toggleUserMenu() { document.getElementById('sidebar').classList.toggle('open'); document.getElementById('overlay').classList.toggle('show'); }
        
        function handleCredentialResponse(response) {
            const payload = JSON.parse(atob(response.credential.split('.')[1]));
            currentUser = { name: payload.name, email: payload.email, picture: payload.picture };
            document.getElementById('loginOverlay').style.display = 'none';
            document.getElementById('app').style.display = 'flex';
            document.getElementById('userBtn').style.display = 'block';
            document.getElementById('userAvatar').src = currentUser.picture;
            document.getElementById('userProfile').style.display = 'flex';
            document.getElementById('userProfile').innerHTML = `
                <img src="${currentUser.picture}" class="user-profile-img">
                <div class="user-profile-info">
                    <div class="user-profile-name">${currentUser.name}</div>
                    <div class="user-profile-email">${currentUser.email}</div>
                </div>
                <button class="logout-btn" onclick="logout()">Logout</button>
            `;
            loadHistory();
            fetch('/set_user', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: currentUser.email, name: currentUser.name, picture: currentUser.picture }) });
        }
        
        function logout() {
            currentUser = null;
            document.getElementById('loginOverlay').style.display = 'flex';
            document.getElementById('app').style.display = 'none';
            document.getElementById('userBtn').style.display = 'none';
            document.getElementById('userProfile').style.display = 'none';
            if (google && google.accounts) google.accounts.id.disableAutoSelect();
        }
        
        function newChat() { if (confirm('Start a new chat?')) location.reload(); }
        function toggleSidebar() { document.getElementById('sidebar').classList.toggle('open'); document.getElementById('overlay').classList.toggle('show'); }
        function closeSidebar() { document.getElementById('sidebar').classList.remove('open'); document.getElementById('overlay').classList.remove('show'); }
        function askSuggestion(q) { document.getElementById('userInput').value = q; sendMessage(); }
        
        async function loadHistory() {
            if (!currentUser) return;
            const res = await fetch('/get_history?email=' + encodeURIComponent(currentUser.email));
            const history = await res.json();
            const container = document.getElementById('historyList');
            if (history.length === 0) { container.innerHTML = '<div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div>'; return; }
            let html = '';
            for (let i = history.length - 1; i >= 0; i--) {
                let item = history[i];
                html += '<div class="history-item" onclick="loadChatMessage(\\'' + escapeHtml(item.user) + '\\')">' +
                        '<div class="history-question">' + escapeHtml(item.user.substring(0, 45)) + '</div>' +
                        '<div class="history-time">' + item.timestamp + '</div></div>';
            }
            container.innerHTML = html;
        }
        
        function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
        function loadChatMessage(msg) { document.getElementById('userInput').value = msg; closeSidebar(); sendMessage(); }
        async function clearHistory() { if (confirm('Clear all history?')) { await fetch('/clear_history', { method: 'POST' }); location.reload(); } }
        
        const textarea = document.getElementById('userInput');
        function autoAdjustHeight() { this.style.height = 'auto'; this.style.height = this.scrollHeight + 'px'; }
        textarea.addEventListener('input', autoAdjustHeight);
        
        function linkify(html) {
            const urlRegex = /(https?:\\/\\/[^\\s<]+)/g;
            return html.replace(urlRegex, function(url) {
                let clean = url.replace(/[.,;:!?)\\]]+$/, '');
                let trailing = url.slice(clean.length);
                return '<a href="' + clean + '" target="_blank" rel="noopener noreferrer">' + clean + '</a>' + trailing;
            });
        }
        
        function formatMessage(text) {
            let html = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            return linkify(html);
        }
        
        const appEl = document.getElementById('app');
        const inputAreaEl = document.getElementById('inputArea');
        
        function setAppHeight() {
            const vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
            appEl.style.height = vh + 'px';
        }
        
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', setAppHeight);
            window.visualViewport.addEventListener('scroll', setAppHeight);
        } else {
            window.addEventListener('resize', setAppHeight);
        }
        setAppHeight();
        
        textarea.addEventListener('focus', function() {
            setTimeout(function() {
                setAppHeight();
                inputAreaEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                scrollToBottom();
            }, 300);
        });
        
        function handleKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }
        
        function copyResponse(messageId) {
            const content = document.querySelector(`#${messageId} .message-content`);
            if (content) {
                navigator.clipboard.writeText(content.innerText).then(() => {
                    const btn = document.querySelector(`#${messageId} .copy-btn`);
                    const originalText = btn.textContent;
                    btn.textContent = '✅ Copied!';
                    setTimeout(() => { btn.textContent = originalText; }, 2000);
                });
            }
        }
        
        async function regenerateResponse(messageId, userMessage) {
            if (isGenerating) return;
            isGenerating = true;
            document.getElementById('typing').style.display = 'block';
            const content = document.querySelector(`#${messageId} .message-content`);
            try {
                const res = await fetch('/regenerate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: currentUser.email, message: userMessage })
                });
                const data = await res.json();
                content.innerHTML = formatMessage(data.response);
                if (data.image) {
                    const img = document.createElement('img');
                    img.src = data.image;
                    img.className = 'chat-image';
                    img.alt = 'Generated image';
                    content.appendChild(img);
                }
                if (messageStore[messageId]) messageStore[messageId].answer = data.response;
            } catch(e) { console.error(e); }
            document.getElementById('typing').style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }
        
        function editMessage(messageId) {
            const div = document.getElementById(messageId);
            const content = div.querySelector('.message-content');
            const editInput = div.querySelector('.edit-message-input');
            const editActions = div.querySelector('.edit-actions');
            if (content.style.display !== 'none') {
                content.style.display = 'none';
                editInput.value = content.innerText;
                editInput.classList.add('active');
                editActions.classList.add('active');
                editInput.focus();
            }
        }
        
        async function saveEdit(messageId) {
            const div = document.getElementById(messageId);
            const editInput = div.querySelector('.edit-message-input');
            const content = div.querySelector('.message-content');
            const editActions = div.querySelector('.edit-actions');
            const newText = editInput.value.trim();
            if (!newText) return;

            content.innerText = newText;
            content.style.display = 'block';
            editInput.classList.remove('active');
            editActions.classList.remove('active');

            // Find the AI response that immediately follows this edited user message
            let aiDiv = div.nextElementSibling;
            while (aiDiv && !aiDiv.classList.contains('ai-message')) {
                aiDiv = aiDiv.nextElementSibling;
            }

            if (!currentUser || isGenerating) return;
            isGenerating = true;
            document.getElementById('typing').style.display = 'block';

            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: newText, email: currentUser.email })
                });
                const data = await res.json();
                if (aiDiv) {
                    const aiContent = aiDiv.querySelector('.message-content');
                    aiContent.innerHTML = formatMessage(data.response);
                    if (data.image) {
                        const img = document.createElement('img');
                        img.src = data.image;
                        img.className = 'chat-image';
                        img.alt = 'Generated image';
                        aiContent.appendChild(img);
                    }
                    if (messageStore[aiDiv.id]) {
                        messageStore[aiDiv.id].question = newText;
                        messageStore[aiDiv.id].answer = data.response;
                    }
                    const regenBtn = aiDiv.querySelector('.message-actions button:nth-child(2)');
                    if (regenBtn) regenBtn.setAttribute('onclick', `regenerateResponse('${aiDiv.id}', '${escapeJs(newText)}')`);
                } else {
                    addMessage(data.response, 'ai', 'msg-' + (++messageCounter), newText);
                }
                loadHistory();
            } catch (e) {
                console.error(e);
            }
            document.getElementById('typing').style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }
        
        function cancelEdit(messageId) {
            const div = document.getElementById(messageId);
            const content = div.querySelector('.message-content');
            const editInput = div.querySelector('.edit-message-input');
            const editActions = div.querySelector('.edit-actions');
            content.style.display = 'block';
            editInput.classList.remove('active');
            editActions.classList.remove('active');
        }
        
        async function continueGenerating(messageId) {
            if (isGenerating) return;
            isGenerating = true;
            document.getElementById('typing').style.display = 'block';
            const content = document.querySelector(`#${messageId} .message-content`);
            const userMessage = (messageStore[messageId] && messageStore[messageId].question) || getLastUserMessage();
            try {
                const res = await fetch('/continue_generating', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: currentUser.email, message: userMessage })
                });
                const data = await res.json();
                content.innerHTML += formatMessage(data.response);
                if (messageStore[messageId]) messageStore[messageId].answer = content.innerText;
            } catch(e) { console.error(e); }
            document.getElementById('typing').style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }
        
        function getLastUserMessage() {
            const messages = document.querySelectorAll('.user-message');
            if (messages.length > 0) return messages[messages.length-1].querySelector('.message-content').innerText;
            return '';
        }
        
        async function shareConversation() {
            if (!currentUser) return;
            try {
                const res = await fetch('/share_conversation?email=' + encodeURIComponent(currentUser.email));
                const data = await res.json();
                const shareText = data.conversation.map(item => `User: ${item.user}\\nYama: ${item.ai}\\n`).join('\\n');
                await navigator.clipboard.writeText(shareText);
                alert('✅ Conversation copied to clipboard!');
            } catch(e) { alert('Could not share conversation.'); }
        }
        
        let pendingDislikeMessageId = null;
        
        async function sendFeedback(messageId, feedbackType, category) {
            const entry = messageStore[messageId] || {};
            try {
                await fetch('/feedback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: currentUser.email,
                        feedback_type: feedbackType,
                        category: category,
                        question: entry.question || '',
                        answer: entry.answer || ''
                    })
                });
            } catch(e) { console.error(e); }
        }
        
        function submitLikeFeedback(messageId) {
            const div = document.getElementById(messageId);
            const likeBtn = div.querySelector('.like-btn');
            const dislikeBtn = div.querySelector('.dislike-btn');
            likeBtn.classList.toggle('liked');
            dislikeBtn.classList.remove('disliked');
            sendFeedback(messageId, 'like', null);
        }
        
        function openDislikeModal(messageId) {
            pendingDislikeMessageId = messageId;
            document.getElementById('dislikeModal').classList.add('show');
        }
        
        function closeDislikeModal() {
            document.getElementById('dislikeModal').classList.remove('show');
            pendingDislikeMessageId = null;
        }
        
        function submitDislikeFeedback() {
            const messageId = pendingDislikeMessageId;
            if (!messageId) return;
            const selected = document.querySelector('input[name="dislikeReason"]:checked');
            const category = selected ? selected.value : 'Other';
            const div = document.getElementById(messageId);
            const dislikeBtn = div.querySelector('.dislike-btn');
            const likeBtn = div.querySelector('.like-btn');
            dislikeBtn.classList.add('disliked');
            likeBtn.classList.remove('liked');
            sendFeedback(messageId, 'dislike', category);
            closeDislikeModal();
        }
        
        let currentController = null;
        
        function setSendButtonState(generating) {
            const btn = document.querySelector('.submit-btn');
            if (generating) {
                btn.innerHTML = '<svg class="submit-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
                btn.setAttribute('aria-label', 'Stop generating');
            } else {
                btn.innerHTML = '<svg class="submit-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>';
                btn.setAttribute('aria-label', 'Send message');
            }
        }
        
        function stopGenerating() {
            if (currentController) {
                currentController.abort();
                currentController = null;
            }
            isGenerating = false;
            setSendButtonState(false);
            document.getElementById('typing').style.display = 'none';
        }
        
        async function sendMessage() {
            if (!currentUser) { alert('Please sign in first!'); return; }
            if (isGenerating) { stopGenerating(); return; }
            const message = textarea.value.trim();
            if (!message) return;
            if (!hasMessages) {
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
            }
            const messageId = 'msg-' + (++messageCounter);
            addMessage(message, 'user', messageId);
            textarea.value = '';
            textarea.style.height = 'auto';
            document.getElementById('typing').style.display = 'block';
            isGenerating = true;
            setSendButtonState(true);
            currentController = new AbortController();
            scrollToBottom();
            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message, email: currentUser.email }),
                    signal: currentController.signal
                });
                const data = await res.json();
                const aiMessageId = 'msg-' + (++messageCounter);
                addMessage(data.response, 'ai', aiMessageId, message, data.image);
                loadHistory();
            } catch(e) {
                if (e.name === 'AbortError') {
                    addMessage('_Generation stopped._', 'ai', 'msg-' + (++messageCounter));
                } else {
                    console.error(e);
                }
            } finally {
                document.getElementById('typing').style.display = 'none';
                isGenerating = false;
                setSendButtonState(false);
                currentController = null;
                scrollToBottom();
            }
        }
        
        function addMessage(text, sender, messageId, userMessage = '', image = null) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + sender + '-message';
            div.id = messageId;
            const wrapper = document.createElement('div');
            wrapper.className = 'message-wrapper';
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = formatMessage(text);
            if (image) {
                const img = document.createElement('img');
                img.src = image;
                img.className = 'chat-image';
                img.alt = 'Generated image';
                content.appendChild(img);
            }
            wrapper.appendChild(content);
            if (sender === 'user') {
                const editInput = document.createElement('input');
                editInput.type = 'text';
                editInput.className = 'edit-message-input';
                editInput.value = text;
                wrapper.appendChild(editInput);
                const editActions = document.createElement('div');
                editActions.className = 'edit-actions';
                editActions.innerHTML = `<button class="save-edit" onclick="saveEdit('${messageId}')">Save</button><button class="cancel-edit" onclick="cancelEdit('${messageId}')">Cancel</button>`;
                wrapper.appendChild(editActions);
            }
            const actions = document.createElement('div');
            actions.className = 'message-actions';
            if (sender === 'ai') {
                messageStore[messageId] = { question: userMessage || getLastUserMessage(), answer: text };
                actions.innerHTML = `
                    <button class="copy-btn" onclick="copyResponse('${messageId}')">📋 Copy</button>
                    <button onclick="regenerateResponse('${messageId}', '${escapeJs(userMessage || getLastUserMessage())}')">🔄 Regenerate</button>
                    <button onclick="continueGenerating('${messageId}')">📝 Continue</button>
                    <button onclick="shareConversation()">📤 Share</button>
                    <button class="speak-btn" onclick="speakMessage('${messageId}')">🔊 Speak</button>
                    <button class="like-btn" onclick="submitLikeFeedback('${messageId}')">👍</button>
                    <button class="dislike-btn" onclick="openDislikeModal('${messageId}')">👎</button>
                `;
            } else {
                actions.innerHTML = `<button onclick="editMessage('${messageId}')">✏️ Edit</button>`;
            }
            wrapper.appendChild(actions);
            div.appendChild(wrapper);
            messages.appendChild(div);
            scrollToBottom();
        }
        
        function escapeJs(text) { return text.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'").replace(/"/g, '\\\\"'); }
        
        function scrollToBottom() { const messages = document.getElementById('messages'); messages.scrollTop = messages.scrollHeight; }
        
        async function handleFileUpload(event) {
            if (!currentUser) { alert('Please sign in first!'); event.target.value = ''; return; }
            const file = event.target.files[0];
            if (!file) return;
            if (!hasMessages) {
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
            }
            addMessage('📎 Uploading "' + file.name + '"...', 'user');
            document.getElementById('typing').style.display = 'block';
            scrollToBottom();
            const formData = new FormData();
            formData.append('file', file);
            formData.append('email', currentUser.email);
            try {
                const res = await fetch('/upload', { method: 'POST', body: formData });
                const data = await res.json();
                addMessage(data.message || 'Upload failed.', 'ai', 'msg-' + (++messageCounter), '', data.image);
            } catch (err) {
                addMessage('⚠️ Upload failed, please try again.', 'ai', 'msg-' + (++messageCounter));
            }
            document.getElementById('typing').style.display = 'none';
            event.target.value = '';
            scrollToBottom();
        }
        
        // ===== Voice Input (Web Speech API) =====
        let recognition = null;
        let isListening = false;
        const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognitionAPI) {
            recognition = new SpeechRecognitionAPI();
            recognition.continuous = false;
            recognition.interimResults = false;
            recognition.lang = 'en-US';

            recognition.onresult = function(event) {
                const transcript = event.results[0][0].transcript;
                textarea.value = transcript;
                autoAdjustHeight.call(textarea);
                sendMessage();
            };
            recognition.onend = function() {
                isListening = false;
                document.getElementById('micBtn').classList.remove('listening');
            };
            recognition.onerror = function() {
                isListening = false;
                document.getElementById('micBtn').classList.remove('listening');
            };
        }

        function toggleVoice() {
            if (!recognition) {
                alert('Voice input is not supported in this browser. Try Chrome or Edge.');
                return;
            }
            const micBtn = document.getElementById('micBtn');
            if (isListening) {
                recognition.stop();
                isListening = false;
                micBtn.classList.remove('listening');
            } else {
                recognition.start();
                isListening = true;
                micBtn.classList.add('listening');
            }
        }

        // ===== Voice Output (SpeechSynthesis) =====
        function speakMessage(messageId) {
            if (!('speechSynthesis' in window)) {
                alert('Voice output is not supported in this browser.');
                return;
            }
            const content = document.querySelector(`#${messageId} .message-content`);
            if (!content) return;
            const text = content.innerText;
            window.speechSynthesis.cancel();
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = 'en-US';
            utterance.rate = 1.0;
            window.speechSynthesis.speak(utterance);
        }

        loadHistory();
        textarea.focus();
    </script>
</body>
</html>'''

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("\n" + "="*55)
    print("🏛️ YAMA AI - COMPLETE UPGRADE")
    print("="*55)
    print(f"🌐 Running on port: {port}")
    print("="*55)
    print("✅ Enhanced Search Quality")
    print("✅ AI Response Formatter")
    print("✅ Step-by-Step Math Solutions")
    print("✅ Physics Formula Solver (step-by-step)")
    print("✅ Biology Solver (Punnett squares, growth models)")
    print("✅ Chemistry Engine (ChemPy + PubChem)")
    print("✅ Periodic Table Integration")
    print("✅ Developer Utilities")
    print("✅ Language Tools")
    print("✅ Time & Date Utilities")
    print("✅ Cybersecurity Tools")
    print("✅ QR Code Generator")
    print("✅ Health Calculators")
    print("✅ Intent Router")
    print("✅ Context Memory")
    print("✅ Analytics")
    print("✅ Friendly Conversations")
    print("✅ Performance Optimizations")
    print("✅ Fixed Response Pipeline")
    print("✅ Fixed Copy/Edit/Regenerate/Continue button IDs")
    print("✅ Fixed critical `import datetime` name-collision bug")
    print("="*55)
    print("🏛️ Developed by: Riishil M Mehta")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
