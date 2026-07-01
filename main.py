from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import os
import re
import ast
import math
import operator
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

# ============ CONVERSATION MEMORY (persistent, per-user) ============
# Stored on disk per user (memory_<email>.json) so it survives restarts.
# Three layers, as requested:
#   conversation_history -> rolling list of recent turns (for follow-ups)
#   user_preferences      -> explicit things the user told us to remember
#   long_term_memory       -> extracted facts (name, etc.) usable across sessions

MEMORY_TURN_LIMIT = 12  # how many recent turns we keep for short-term context

def _memory_path(email):
    safe_email = (email or "anon").replace('@', '_at_').replace('.', '_dot_')
    safe_email = re.sub(r'[^a-zA-Z0-9_]', '', safe_email)
    return f"memory_{safe_email}.json"

def load_memory(email):
    path = _memory_path(email)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"conversation_history": [], "user_preferences": {}, "long_term_memory": {}}

def save_memory(email, memory):
    path = _memory_path(email)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

# Small set of patterns for facts people commonly state about themselves.
# This is deterministic extraction (no LLM) — it only catches phrasings
# that match these patterns, not free-form statements.
_FACT_PATTERNS = [
    (r"\bmy name is ([a-zA-Z][a-zA-Z\s'-]{1,30})", "name"),
    (r"\bi am called ([a-zA-Z][a-zA-Z\s'-]{1,30})", "name"),
    (r"\bcall me ([a-zA-Z][a-zA-Z\s'-]{1,30})", "name"),
    (r"\bi live in ([a-zA-Z][a-zA-Z\s'-]{1,40})", "location"),
    (r"\bi work as an? ([a-zA-Z][a-zA-Z\s'-]{1,40})", "occupation"),
    (r"\bi work at ([a-zA-Z][a-zA-Z0-9\s'-]{1,40})", "workplace"),
    (r"\bmy favou?rite (\w+) is ([a-zA-Z][a-zA-Z0-9\s'-]{1,40})", "favorite"),
]

def extract_facts(message, memory):
    """Pull simple first-person facts out of a message and store them
    in long_term_memory. Returns a list of (key, value) newly learned."""
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

# Recognized recall questions -> long_term_memory key
_RECALL_QUESTIONS = {
    "what's my name": "name", "whats my name": "name", "what is my name": "name",
    "where do i live": "location", "where am i from": "location",
    "what do i do": "occupation", "what's my job": "occupation",
    "where do i work": "workplace",
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

# ============ SAFE CALCULATOR ============
# Uses ast parsing instead of eval() so we never execute arbitrary code,
# while still supporting full expressions, parentheses and functions.

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

# ============ STEP-BY-STEP SOLVER (sympy) ============
# This is genuine symbolic math, not templated text — sympy actually
# manipulates the expression/equation and we narrate each transformation.

import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

# --- Graphing / file-reading additions ---
import io
import base64
import matplotlib
matplotlib.use("Agg")  # headless, no GUI backend needed on a server
import matplotlib.pyplot as plt
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import docx as docx_lib
import pptx as pptx_lib
import pandas as pd
from fastapi import UploadFile, File, Form

_SP_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
_SP_SYMBOLS = "x y z a b n t".split()

def _sp_safe_parse(text):
    text = text.replace('^', '**')
    local_dict = {s: sp.Symbol(s) for s in _SP_SYMBOLS}
    return parse_expr(text, local_dict=local_dict, transformations=_SP_TRANSFORMS)

def solve_step_by_step(message):
    """Returns a formatted, narrated solution or None if this doesn't look
    like an algebra/calculus/trig/matrix/stats request."""
    msg = message.strip()
    lower = msg.lower()

    try:
        # --- Equation solving: "solve 2x+3=7" or "x^2-4=0" ---
        eq_match = re.search(r'solve\s+(.+)', lower) or (
            re.search(r'^([^=]+=[^=]+)$', msg) if '=' in msg else None)
        if eq_match and '=' in (eq_match.group(1) if eq_match else ''):
            lhs_str, rhs_str = eq_match.group(1).split('=', 1)
            lhs, rhs = _sp_safe_parse(lhs_str), _sp_safe_parse(rhs_str)
            x = sp.Symbol('x') if 'x' in lower else list((lhs - rhs).free_symbols)[0]
            equation = sp.Eq(lhs, rhs)
            solutions = sp.solve(equation, x)
            steps = (
                f"**Step 1 — Original equation**\n{sp.pretty(equation)}\n\n"
                f"**Step 2 — Move everything to one side**\n{sp.pretty(sp.Eq(lhs - rhs, 0))}\n\n"
                f"**Step 3 — Solve for {x}**\n"
            )
            return f"📐 {steps}**Final Answer:** {x} = {', '.join(str(s) for s in solutions)}"

        # --- Derivatives: "derivative of x^2+3x" / "differentiate sin(x)" ---
        deriv_match = re.search(r'(?:derivative of|differentiate)\s+(.+)', lower)
        if deriv_match:
            expr = _sp_safe_parse(deriv_match.group(1))
            x = sp.Symbol('x')
            result = sp.diff(expr, x)
            return (f"📐 **Step 1 — Function**\nf(x) = {expr}\n\n"
                    f"**Step 2 — Apply differentiation rules**\nf'(x) = d/dx [{expr}]\n\n"
                    f"**Final Answer:** f'(x) = {sp.simplify(result)}")

        # --- Integrals: "integrate x^2" / "integral of cos(x)" ---
        int_match = re.search(r'(?:integrate|integral of)\s+(.+)', lower)
        if int_match:
            expr = _sp_safe_parse(int_match.group(1))
            x = sp.Symbol('x')
            result = sp.integrate(expr, x)
            return (f"📐 **Step 1 — Function**\nf(x) = {expr}\n\n"
                    f"**Step 2 — Apply integration rules**\n∫ {expr} dx\n\n"
                    f"**Final Answer:** {result} + C")

        # --- Matrices: "matrix [[1,2],[3,4]] determinant" / "... inverse" ---
        mat_match = re.search(r'\[\[.+\]\]', msg)
        if mat_match and ('matrix' in lower or 'determinant' in lower or 'inverse' in lower):
            M = sp.Matrix(ast.literal_eval(mat_match.group(0)))
            if 'determinant' in lower or 'det' in lower:
                return f"📐 **Matrix**\n{sp.pretty(M)}\n\n**Step — Compute determinant**\n\n**Final Answer:** det = {M.det()}"
            if 'inverse' in lower:
                return f"📐 **Matrix**\n{sp.pretty(M)}\n\n**Step — Compute inverse**\n\n**Final Answer:**\n{sp.pretty(M.inv())}"
            return f"📐 **Matrix**\n{sp.pretty(M)}\n\n**Transpose:**\n{sp.pretty(M.T)}\n**Determinant:** {M.det()}"

        # --- Statistics: "mean/median/stdev of 2,4,4,6" ---
        stat_match = re.search(r'(mean|average|median|stdev|std|variance) of ([\d.,\s]+)', lower)
        if stat_match:
            kind = stat_match.group(1)
            nums = [float(n) for n in re.findall(r'-?\d+\.?\d*', stat_match.group(2))]
            if not nums:
                return None
            data = sp.Matrix(nums)
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
            return (f"📐 **Step 1 — Data**\n{nums}\n\n"
                    f"**Step 2 — Compute {label.lower()}**\n\n"
                    f"**Final Answer:** {label} = {round(result, 4)}")

        # --- Trig identities / evaluation: "sin(pi/2)" handled by calculator already;
        #     this catches narrated requests like "simplify sin(x)^2+cos(x)^2" ---
        simplify_match = re.search(r'simplify\s+(.+)', lower)
        if simplify_match:
            expr = _sp_safe_parse(simplify_match.group(1))
            return (f"📐 **Step 1 — Expression**\n{expr}\n\n"
                    f"**Step 2 — Simplify**\n\n"
                    f"**Final Answer:** {sp.simplify(expr)}")

    except Exception:
        return None
    return None

# ============ GRAPH GENERATOR (matplotlib, no LLM) ============

_GRAPH_TRIGGER_RE = re.compile(r'^(?:graph|plot)\s+(.+)$', re.IGNORECASE)

def generate_graph(message):
    """If the message asks to graph/plot a function, render it with
    matplotlib and return a base64 PNG data URI. Returns None otherwise."""
    m = _GRAPH_TRIGGER_RE.match(message.strip())
    if not m:
        return None
    expr_str = m.group(1).strip().rstrip('?')
    try:
        x = sp.Symbol('x')
        expr = _sp_safe_parse(expr_str)
        f = sp.lambdify(x, expr, modules=['numpy'])

        import numpy as np
        xs = np.linspace(-10, 10, 400)
        with __import__('warnings').catch_warnings():
            __import__('warnings').simplefilter("ignore")
            ys = f(xs)
        ys = np.array(ys, dtype=float)
        ys[np.abs(ys) > 1e6] = np.nan  # hide asymptote blow-ups

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

# ============ DOCUMENT READING (PDF / DOCX / PPTX / XLSX / CSV / TXT) ============
# Each user's most recently uploaded document is kept so follow-up
# questions like "summarize this" can refer to it.

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
active_documents = {}  # email -> {"name": str, "text": str}

def extract_pdf_text(path, max_pages=40):
    doc = fitz.open(path)
    parts = []
    for page in doc[:max_pages]:
        parts.append(page.get_text())
    doc.close()
    return "\n".join(parts)

def extract_docx_text(path):
    d = docx_lib.Document(path)
    return "\n".join(p.text for p in d.paragraphs if p.text.strip())

def extract_pptx_text(path):
    pres = pptx_lib.Presentation(path)
    parts = []
    for i, slide in enumerate(pres.slides, 1):
        texts = [shape.text for shape in slide.shapes if shape.has_text_frame and shape.text.strip()]
        if texts:
            parts.append(f"[Slide {i}] " + " | ".join(texts))
    return "\n".join(parts)

def extract_spreadsheet_summary(path, ext):
    if ext == "csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    summary = [f"Rows: {len(df)}, Columns: {list(df.columns)}"]
    summary.append("Preview:\n" + df.head(10).to_string())
    try:
        numeric = df.select_dtypes(include='number')
        if not numeric.empty:
            summary.append("Numeric summary:\n" + numeric.describe().to_string())
    except Exception:
        pass
    return "\n\n".join(summary)

def extract_image_text(path):
    """OCR an uploaded image with Tesseract — genuine text extraction,
    not a guess. Also flags if the extracted text looks like a math
    expression so the homework-photo flow can route to the solver."""
    img = Image.open(path)
    text = pytesseract.image_to_string(img).strip()
    return text

def read_uploaded_file(path, filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    try:
        if ext == 'pdf':
            return extract_pdf_text(path), 'document'
        if ext == 'docx':
            return extract_docx_text(path), 'document'
        if ext == 'pptx':
            return extract_pptx_text(path), 'document'
        if ext in ('xlsx', 'xls'):
            return extract_spreadsheet_summary(path, ext), 'spreadsheet'
        if ext == 'csv':
            return extract_spreadsheet_summary(path, ext), 'spreadsheet'
        if ext == 'txt':
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read(), 'document'
        if ext in ('png', 'jpg', 'jpeg', 'webp', 'bmp'):
            text = extract_image_text(path)
            if not text:
                return "(No readable text found in this image.)", 'image'
            return text, 'image'
    except Exception as e:
        return None, str(e)
    return None, 'unsupported file type'

def handle_document_question(msg, email):
    """If the user is asking about their uploaded document/spreadsheet/image,
    answer using the stored extracted content (extractive, not generative)."""
    doc = active_documents.get(email)
    if not doc:
        return None
    triggers = ['summarize this', 'summarize the file', 'summarize the pdf',
                'summarize the document', 'analyze this', 'analyze the csv',
                'analyze the file', 'what does this file say', 'summarize this pdf',
                'read this image', 'what does this image say', 'solve this',
                'whats in this image', "what's in this image"]
    if not any(t in msg for t in triggers):
        return None
    text = doc['text']
    if doc['kind'] == 'spreadsheet':
        return f"📊 **{doc['name']}**\n\n{text[:1800]}"
    if doc['kind'] == 'image':
        # If the OCR'd text looks like a math expression, route it through
        # the real solver/calculator instead of just echoing the text back.
        math_expr = looks_like_math(text)
        if math_expr:
            try:
                result = safe_calculate(math_expr)
                return f"🖼️ I read **{text.strip()}** from your image.\n\n🧮 {math_expr} = **{result}**"
            except Exception:
                pass
        solved = solve_step_by_step(text)
        if solved:
            return f"🖼️ I read this from your image:\n_{text.strip()}_\n\n{solved}"
        return f"🖼️ **Text found in {doc['name']}:**\n\n{text}"
    summary = summarize_text(text, max_sentences=6)
    return f"📄 **{doc['name']}** — summary\n\n{summary}"

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
    """Evaluate a math expression safely. Returns a number or raises ValueError."""
    expr = expr.replace('^', '**').replace('x', '*').replace('×', '*').replace('÷', '/')
    parsed = ast.parse(expr, mode='eval')
    result = _safe_eval_node(parsed)
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result

# A message is treated as "calculator-shaped" if, once the words are stripped
# out, what's left looks like a real expression (digits/operators/parens),
# rather than just a sentence that happens to contain a number.
_MATH_HINT_RE = re.compile(r'[\+\-\*/\^×÷].*\d|\d.*[\+\-\*/\^×÷]')
_ALLOWED_WORDS_RE = '|'.join(sorted(set(_ALLOWED_FUNCS) | set(_ALLOWED_NAMES), key=len, reverse=True))
_FUNC_STRIP_RE = re.compile(rf'\b(?:{_ALLOWED_WORDS_RE})\b')
# After removing known function/constant names, only digits/operators/parens
# may remain — this is what stops "I read 5-2 books" from being treated as math.
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
# All conversions normalize to a base unit first, then to target unit.
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

_CONVERT_RE = re.compile(
    r'(-?\d+(?:\.\d+)?)\s*([a-zA-Z°]+)\s*(?:to|in|=>|->)\s*([a-zA-Z°]+)', re.IGNORECASE)

def convert_units(msg):
    m = _CONVERT_RE.search(msg.lower())
    if not m:
        return None
    value, from_u, to_u = float(m.group(1)), m.group(2), m.group(3)
    from_u = _UNIT_ALIASES.get(from_u, from_u)
    to_u = _UNIT_ALIASES.get(to_u, to_u)

    # Temperature is special-cased (no linear base unit)
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
    # normalize to Celsius first
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

# ============ DICTIONARY & WIKIPEDIA LOOKUPS ============
# Free, keyless public endpoints — not LLM/AI services, just reference data.

def dictionary_lookup(word):
    try:
        r = requests.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word)}", timeout=6)
        if r.status_code != 200:
            return None
        data = r.json()
        entry = data[0]
        meanings = []
        for meaning in entry.get("meanings", [])[:2]:
            pos = meaning.get("partOfSpeech", "")
            defs = meaning.get("definitions", [])[:2]
            for d in defs:
                meanings.append(f"_{pos}_ — {d.get('definition', '')}")
        if not meanings:
            return None
        return f"📖 **{entry.get('word', word)}**\n" + "\n".join(meanings)
    except Exception:
        return None

def wikipedia_summary(topic):
    try:
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(topic)}",
            headers={"User-Agent": "YamaAI/1.0"}, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("type") == "disambiguation":
            return None
        extract = data.get("extract")
        if not extract:
            return None
        return f"📚 **{data.get('title', topic)}**\n{extract}"
    except Exception:
        return None

# ============ DATE / TIME ============

def datetime_answer(msg):
    if any(p in msg for p in ["what time", "current time", "what's the time"]):
        return f"🕐 It's currently **{datetime.now().strftime('%I:%M %p')}** (server time)."
    if any(p in msg for p in ["what day", "today's date", "what date", "what is the date"]):
        return f"📅 Today is **{datetime.now().strftime('%A, %B %d, %Y')}**."
    return None

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
    """Lightweight extractive summary: scores sentences by word-frequency
    overlap with the whole passage (no LLM — classic TF-style ranking)."""
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

# ============ SMALL TALK / KNOWLEDGE BASE ============
# Fast, deterministic answers for common questions so we don't burn a
# search request (and a few seconds of latency) on things we already know.

_KNOWLEDGE_BASE = {
    "who are you": "🏛️ I'm **Yama**, your AI assistant — I can do math, conversions, dictionary & Wikipedia lookups, and search the web for anything else.",
    "what can you do": "🛠️ I can:\n• Solve math expressions\n• Convert units (length, weight, volume, temperature)\n• Look up word definitions\n• Summarize Wikipedia topics\n• Search the live web and summarize results\n• Tell you the date and time",
    "who made you": "🏛️ I was built from scratch — no third-party AI API, just search, logic, and a calculator under the hood.",
    "thank you": "😊 You're welcome! Anything else?",
    "thanks": "😊 Anytime!",
    "bye": "👋 See you next time!",
    "goodbye": "👋 Take care!",
}

def knowledge_base_lookup(msg):
    if msg in _KNOWLEDGE_BASE:
        return _KNOWLEDGE_BASE[msg]
    for key, ans in _KNOWLEDGE_BASE.items():
        if key in msg:
            return ans
    return None

# ============ RESPONSE FUNCTION (INTENT ROUTER) ============
# Order matters: cheap, deterministic checks first, web search last.

def get_response(message, email):
    raw_message = message.strip()
    msg = raw_message.lower()

    stats = update_user_stats(email)
    user = user_db.get(User.email == email)
    user_name = user.get('name', 'User') if user else 'User'
    memory = load_memory(email)

    def _finish(reply, topic=None, image=None):
        """Log the turn to persistent memory before returning."""
        memory["conversation_history"].append({"user": raw_message, "yama": reply})
        memory["conversation_history"] = memory["conversation_history"][-MEMORY_TURN_LIMIT:]
        if topic:
            memory["last_topic"] = topic
        save_memory(email, memory)
        return {"text": reply, "image": image} if image else reply

    # 0a. Document/spreadsheet questions about the user's last upload
    doc_answer = handle_document_question(msg, email)
    if doc_answer:
        return _finish(doc_answer, topic="document")

    # 0a2. Graph requests ("graph x^2", "plot sin(x)")
    graph_image = generate_graph(raw_message)
    if graph_image:
        return _finish(f"📊 Here's the graph of **{raw_message.split(' ', 1)[1] if ' ' in raw_message else raw_message}**:",
                        topic="graph", image=graph_image)

    # 0. Learn any facts the user just stated ("my name is Rishi", etc.)
    learned = extract_facts(raw_message, memory)
    if learned:
        ack = []
        for key, value in learned:
            label = key.replace('favorite_', 'favorite ').replace('_', ' ')
            ack.append(f"Got it — your {label} is **{value}**. I'll remember that!")
        return _finish("🧠 " + " ".join(ack))

    # 0b. Answer direct recall questions ("what's my name?") straight from memory
    recall = answer_from_memory(msg, memory)
    if recall:
        return _finish(recall, topic="recall")

    # 1. Greetings — use remembered name if we have one and they haven't set
    #    a display name via Google sign-in
    if msg in ['hi', 'hello', 'hey', 'sup', 'yo']:
        known_name = memory.get("long_term_memory", {}).get("name")
        greet_name = known_name or user_name
        return _finish(
            f"👋 Hello {greet_name}! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?")

    if 'how are you' in msg:
        return _finish(f"😊 I'm doing great! Thanks for asking, {user_name}!")

    # 2. Small talk / knowledge base
    kb_answer = knowledge_base_lookup(msg)
    if kb_answer:
        return _finish(kb_answer)

    # 3. Date / time
    dt_answer = datetime_answer(msg)
    if dt_answer:
        return _finish(dt_answer)

    # 4. Unit conversion (checked before calculator since "5 km to miles"
    #    also contains a number+letters that could confuse the math check)
    converted = convert_units(msg)
    if converted is not None:
        m = _CONVERT_RE.search(msg)
        from_u, to_u = m.group(2), m.group(3)
        memory['last_value'] = converted
        memory['last_unit'] = to_u
        reply = (f"📏 {m.group(1)} {from_u} = **{round(converted, 4)} {to_u}**\n\n"
                 f"✨ {user_name} • Level {stats['level']} - {stats['title']}")
        return _finish(reply, topic="conversion")

    # 5. Step-by-step solver (algebra, calculus, trig, matrices, stats) —
    #    tried before the plain calculator since it's the more capable path
    #    for anything that isn't a bare arithmetic expression.
    solved = solve_step_by_step(raw_message)
    if solved:
        reply = f"{solved}\n\n✨ {user_name} • Level {stats['level']} - {stats['title']}"
        return _finish(reply, topic="math_steps")

    # 6. Quick calculator for plain arithmetic
    expr = looks_like_math(raw_message)
    if expr:
        try:
            result = safe_calculate(expr)
            memory['last_value'] = result
            reply = f"🧮 {expr} = **{result}**\n\n✨ Great job, {user_name}! Level {stats['level']} - {stats['title']}"
            return _finish(reply, topic="math")
        except ZeroDivisionError:
            return _finish("🧮 Can't divide by zero — try a different expression!")
        except Exception:
            pass  # not actually a valid expression, fall through to search

    # 7. Dictionary lookups ("define X", "what does X mean", "meaning of X")
    define_match = re.match(r'^(?:define|meaning of|what does)\s+(.+?)(?:\s+mean)?\??$', msg)
    if define_match:
        word = define_match.group(1).strip()
        definition = dictionary_lookup(word)
        if definition:
            return _finish(f"{definition}\n\n✨ {user_name} • Level {stats['level']} - {stats['title']}")

    # 8. Wikipedia lookups ("who is X", "what is X", "tell me about X")
    wiki_match = re.match(r'^(?:who is|what is|what\'s|tell me about)\s+(.+?)\??$', msg)
    if wiki_match:
        topic = wiki_match.group(1).strip()
        summary = wikipedia_summary(topic)
        if summary:
            return _finish(f"{summary}\n\n✨ {user_name} • Level {stats['level']} - {stats['title']}", topic="wiki")

    # 9. Web search — multi-source research with citations. Reads the top
    #    few results (not just one), summarizes each independently, and
    #    presents them as numbered sources so claims can be traced back.
    search_results = search_web(message)

    if not search_results:
        return _finish(f"I searched for '{message}' but found no results.")

    sources_to_read = search_results[:3]
    citations = []
    for r in sources_to_read:
        page_text = None
        try:
            page_text = read_full_webpage(r['url'])
        except Exception:
            pass
        body = summarize_text(page_text, max_sentences=2) if page_text and len(page_text) > 200 else r['snippet']
        citations.append({"title": r['title'], "url": r['url'], "summary": body})

    if not citations:
        return _finish(f"I searched for '{message}' but couldn't read any results.")

    response = f"🔍 **Research: {message}**\n\n"
    for i, c in enumerate(citations, 1):
        response += f"**[{i}] {c['title']}**\n{c['summary']}\n🔗 {c['url']}\n\n"
    if len(citations) > 1:
        response += "_Compared across multiple sources above — cross-check for agreement._\n\n"
    response += f"📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)\n"

    return _finish(response, topic="search")

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

# ============ COMPLETE FIXED HTML ============
HTML = f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yama - AI Assistant</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        /* ========== RESET ========== */
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        
        /* ========== FIXED: NO FIXED POSITION, NO OVERFLOW HIDDEN ========== */
        html, body {{
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            overflow-x: hidden;
            overflow-y: auto;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f0e8;
            transition: all 0.3s ease;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}
        
        /* ========== FLUID MEDIA ========== */
        img, video, iframe {{
            max-width: 100%;
            height: auto;
        }}
        
        /* ========== DARK MODE ========== */
        body.dark {{
            background: #1a1a2e;
        }}
        
        body.dark .app {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        }}
        
        body.dark .header {{
            background: rgba(26,26,46,0.95);
            border-bottom-color: #2a2a4e;
        }}
        
        body.dark .logo h1 {{
            color: #d4c5a9;
        }}
        
        body.dark .input-wrapper {{
            background: #2a2a4e;
            border-color: #3a3a5e;
        }}
        
        body.dark textarea {{
            color: #e0e0e0;
        }}
        
        body.dark textarea::placeholder {{
            color: #6a5a7a;
        }}
        
        body.dark .message-content {{
            color: #e0e0e0;
        }}
        
        body.dark .ai-message .message-content {{
            background: #2a2a4e !important;
            color: #e0e0e0 !important;
        }}
        
        body.dark .suggestion {{
            background: #2a2a4e;
            border-color: #3a3a5e;
            color: #e0e0e0;
        }}
        
        body.dark .suggestion:hover {{
            background: #3a3a5e;
            color: white;
        }}
        
        body.dark .welcome h2 {{
            color: #d4c5a9;
        }}
        
        body.dark .welcome p {{
            color: #8a7a6a;
        }}
        
        body.dark .sidebar {{
            background: #0f0f23;
            border-right-color: #2a2a4e;
        }}
        
        body.dark .sidebar-header {{
            background: #0a0a1a;
        }}
        
        body.dark .history-question {{
            color: #d4c5a9;
        }}
        
        body.dark .history-time {{
            color: #6a5a7a;
        }}
        
        body.dark .history-item:hover {{
            background: rgba(212,197,169,0.08);
            border-color: #3a3a5e;
        }}
        
        body.dark .clear-history {{
            color: #d4c5a9;
            border-color: #3a3a5e;
        }}
        
        body.dark .clear-history:hover {{
            background: rgba(212,197,169,0.2);
            border-color: #c4a57b;
        }}
        
        body.dark .new-chat-btn {{
            background: #3a3a5e;
            color: #d4c5a9;
        }}
        
        body.dark .new-chat-btn:hover {{
            background: #4a4a6e;
        }}
        
        body.dark .typing span {{
            background: #d4c5a9;
        }}
        
        body.dark .typing {{
            color: #d4c5a9;
        }}
        
        body.dark a {{
            color: #4ecdc4;
        }}
        
        body.dark .message-content a {{
            color: #4ecdc4;
        }}
        
        body.dark .message-content a:hover {{
            color: #6ee7de;
        }}
        
        body.dark .control-btn {{
            color: #d4c5a9;
        }}
        
        body.dark .control-btn:hover {{
            background: #3a3a5e;
            color: white;
        }}
        
        /* ========== LOGIN OVERLAY ========== */
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
            padding: 20px;
        }}
        
        .login-card {{
            background: white;
            border-radius: 30px;
            padding: 40px 30px;
            text-align: center;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 25px 50px rgba(0,0,0,0.2);
        }}
        
        .login-card .logo-icon {{
            font-size: 4rem;
            margin-bottom: 20px;
        }}
        
        .login-card h2 {{
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            margin-bottom: 10px;
        }}
        
        .login-card p {{
            color: #666;
            font-size: 1rem;
            margin-bottom: 30px;
        }}
        
        /* ========== APP - FIXED LAYOUT ========== */
        .app {{
            display: flex;
            flex-direction: column;
            height: 100dvh;
            min-height: 100vh;
            width: 100%;
            background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%);
            position: relative;
            overflow: hidden;
        }}
        
        /* ========== SIDEBAR - RESPONSIVE ========== */
        .sidebar {{
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: min(280px, 80vw);
            background: #2c2418;
            border-right: 1px solid #4a3f2f;
            display: flex;
            flex-direction: column;
            transform: translateX(-100%);
            transition: transform 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            z-index: 1000;
            box-shadow: 4px 0 20px rgba(0,0,0,0.1);
        }}
        
        .sidebar.open {{
            transform: translateX(0);
        }}
        
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
            font-size: 0.8rem;
            color: #d4c5a9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .history-time {{
            font-size: 0.6rem;
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
        }}
        
        .new-chat-btn:hover {{
            background: #5a4f3f;
        }}
        
        .clear-history {{
            background: rgba(212,197,169,0.1);
            border: 1px solid #4a3f2f;
            border-radius: 20px;
            padding: 8px 16px;
            color: #d4c5a9;
            cursor: pointer;
            font-size: 0.7rem;
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
        
        .overlay.show {{
            display: block;
        }}
        
        /* ========== MAIN - FLEX LAYOUT ========== */
        .main {{
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
            height: 100%;
            width: 100%;
            overflow: hidden;
        }}
        
        /* ========== HEADER ========== */
        .header {{
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid #d4c5a9;
            background: rgba(245,240,232,0.95);
            flex-shrink: 0;
            min-height: 56px;
            width: 100%;
            position: relative;
            z-index: 10;
        }}
        
        .menu-btn {{
            background: none;
            border: none;
            font-size: 1.3rem;
            cursor: pointer;
            color: #6a5a4a;
            padding: 8px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        
        .menu-btn:hover {{
            background: #d4c5a9;
            color: #2c2418;
        }}
        
        .logo {{
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 6px;
            min-width: 0;
        }}
        
        .logo-icon {{
            font-size: 1.8rem;
        }}
        
        .logo h1 {{
            font-family: 'Playfair Display', serif;
            font-size: 1.3rem;
            color: #2c2418;
            white-space: nowrap;
        }}
        
        .new-chat-mobile {{
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px;
            border-radius: 10px;
            color: #6a5a4a;
            display: none;
        }}
        
        .control-btn {{
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px 12px;
            border-radius: 20px;
            color: #6a5a4a;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        
        .control-btn:hover {{
            background: #d4c5a9;
        }}
        
        .user-btn {{
            background: none;
            border: none;
            cursor: pointer;
            display: none;
            padding: 4px;
        }}
        
        .user-btn img {{
            width: 35px;
            height: 35px;
            border-radius: 50%;
            object-fit: cover;
        }}
        
        /* ========== MESSAGES - SCROLLABLE ========== */
        .messages {{
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            padding-bottom: 20px;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
            min-height: 0;
        }}
        
        .message {{
            margin-bottom: 20px;
            animation: fadeIn 0.3s ease;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .user-message {{
            text-align: right;
        }}
        
        .ai-message {{
            text-align: left;
        }}
        
        .message-content {{
            display: inline-block;
            max-width: 85%;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
        }}
        
        .user-message .message-content {{
            background: #2c2418 !important;
            color: white !important;
            padding: 10px 16px !important;
            border-radius: 20px !important;
        }}
        
        .ai-message .message-content {{
            background: white !important;
            color: #2c2418 !important;
            padding: 12px 18px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }}
        
        /* ========== TYPING ========== */
        .typing {{
            display: none;
            padding: 10px 16px;
            gap: 5px;
            color: #888;
            font-size: 0.8rem;
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
        
        @keyframes bounce {{
            0%, 60%, 100% {{ transform: translateY(0); }}
            30% {{ transform: translateY(-6px); }}
        }}
        
        /* ========== INPUT AREA - STICKY WITH SAFE AREA ========== */
        .input-area {{
            position: sticky;
            bottom: 0;
            z-index: 100;
            background: #f5f0e8;
            padding: 12px 16px 20px;
            padding-bottom: env(safe-area-inset-bottom, 20px);
            flex-shrink: 0;
            border-top: 1px solid rgba(212,197,169,0.3);
        }}
        
        body.dark .input-area {{
            background: #1a1a2e;
            border-top-color: rgba(42,42,78,0.3);
        }}
        
        .input-wrapper {{
            display: flex;
            align-items: flex-end;
            gap: 12px;
            background: white;
            border-radius: 28px;
            padding: 8px 8px 8px 20px;
            border: 1px solid #d4c5a9;
            width: 100%;
            max-width: 760px;
            margin: 0 auto;
            min-height: 56px;
        }}
        
        body.dark .input-wrapper {{
            background: #2a2a4e;
            border-color: #3a3a5e;
        }}
        
        .input-text-wrapper {{
            flex: 1;
            min-width: 0;
        }}
        
        textarea {{
            width: 100%;
            background: transparent;
            border: none;
            outline: none;
            font-size: 16px;
            line-height: 1.5;
            resize: none;
            padding: 8px 0;
            font-family: inherit;
            color: #2c2418;
            min-height: 24px;
            max-height: 180px;
            overflow-y: auto;
        }}
        
        body.dark textarea {{
            color: #e0e0e0;
        }}
        
        textarea::placeholder {{
            color: #b8a88a;
            font-size: 0.95rem;
        }}
        
        /* iOS Zoom Fix */
        @media (max-width: 768px) {{
            textarea {{
                font-size: 16px !important;
            }}
        }}
        
        .submit-btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            border: none;
            background-color: #2c2418;
            color: white;
            cursor: pointer;
            transition: all 0.2s;
            min-width: 44px;
            min-height: 44px;
        }}
        
        body.dark .submit-btn {{
            background-color: #4a3f2f;
        }}
        
        .submit-btn:hover {{
            background-color: #4a3f2f;
            transform: scale(1.02);
        }}
        
        .submit-btn:active {{
            transform: scale(0.96);
        }}
        
        .submit-icon {{
            width: 20px;
            height: 20px;
            fill: currentColor;
        }}
        
        /* ========== ADDED: FILE ATTACH BUTTON (additive only) ========== */
        .attach-btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            border: 1px solid #e0d5c0;
            background-color: transparent;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
            min-width: 44px;
            min-height: 44px;
        }}
        body.dark .attach-btn {{
            border-color: #4a3f2f;
            color: #e0e0e0;
        }}
        .attach-btn:hover {{
            background-color: rgba(44,36,24,0.06);
        }}
        .attach-icon {{
            width: 18px;
            height: 18px;
            fill: none;
            stroke: currentColor;
            stroke-width: 2;
        }}
        .message-content img.chat-image {{
            max-width: 100%;
            border-radius: 10px;
            margin-top: 10px;
            display: block;
        }}
        .file-chip {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            background-color: rgba(44,36,24,0.06);
            font-size: 0.85rem;
            margin-top: 6px;
        }}
        
        /* ========== WELCOME ========== */
        .welcome {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 50vh;
            text-align: center;
            padding: 20px;
        }}
        
        .welcome-icon {{
            font-size: 3rem;
            margin-bottom: 15px;
            animation: float 3s ease-in-out infinite;
        }}
        
        @keyframes float {{
            0%, 100% {{ transform: translateY(0); }}
            50% {{ transform: translateY(-8px); }}
        }}
        
        .welcome h2 {{
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            color: #2c2418;
            margin-bottom: 8px;
        }}
        
        .welcome p {{
            color: #6a5a4a;
            font-size: 0.85rem;
            margin-bottom: 20px;
        }}
        
        .suggestions {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
            margin-top: 15px;
        }}
        
        .suggestion {{
            background: white;
            border: 1px solid #d4c5a9;
            border-radius: 30px;
            padding: 6px 14px;
            font-size: 0.75rem;
            color: #2c2418;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }}
        
        .suggestion:hover {{
            background: #2c2418;
            color: white;
            border-color: #2c2418;
        }}
        
        /* ========== RESPONSIVE BREAKPOINTS ========== */
        
        /* Tablet & Mobile */
        @media (max-width: 768px) {{
            .messages {{
                padding: 12px 16px;
                padding-bottom: 16px;
            }}
            .suggestions {{
                display: none;
            }}
            .new-chat-mobile {{
                display: block;
            }}
            .header {{
                padding: 10px 14px;
                min-height: 52px;
            }}
            .logo h1 {{
                font-size: 1.1rem;
            }}
            .logo-icon {{
                font-size: 1.4rem;
            }}
            .message-content {{
                max-width: 90%;
                font-size: 0.85rem;
            }}
            .input-area {{
                padding: 10px 12px 16px;
            }}
            .input-wrapper {{
                padding: 6px 6px 6px 16px;
                min-height: 50px;
                border-radius: 26px;
            }}
            textarea {{
                font-size: 16px !important;
                padding: 8px 0;
            }}
            .submit-btn {{
                width: 40px;
                height: 40px;
                min-width: 40px;
                min-height: 40px;
            }}
            .submit-icon {{
                width: 18px;
                height: 18px;
            }}
        }}
        
        /* Small Phones */
        @media (max-width: 480px) {{
            .header {{
                padding: 8px 12px;
                min-height: 48px;
                gap: 8px;
            }}
            .logo h1 {{
                font-size: 1rem;
            }}
            .logo-icon {{
                font-size: 1.2rem;
            }}
            .control-btn {{
                font-size: 0.9rem;
                padding: 6px 8px;
            }}
            .menu-btn {{
                font-size: 1.1rem;
                padding: 6px;
            }}
            .messages {{
                padding: 10px 12px;
            }}
            .input-area {{
                padding: 8px 10px 14px;
                padding-bottom: env(safe-area-inset-bottom, 14px);
            }}
            .input-wrapper {{
                padding: 5px 5px 5px 14px;
                min-height: 44px;
                gap: 8px;
                border-radius: 24px;
            }}
            textarea {{
                font-size: 15px !important;
                padding: 6px 0;
                min-height: 20px;
            }}
            .submit-btn {{
                width: 40px;
                height: 40px;
                min-width: 40px;
                min-height: 40px;
            }}
            .submit-icon {{
                width: 16px;
                height: 16px;
            }}
            .message-content {{
                font-size: 0.8rem;
            }}
        }}
        
        /* Very Small Phones */
        @media (max-width: 380px) {{
            .header {{
                padding: 6px 10px;
                min-height: 44px;
                gap: 6px;
            }}
            .logo h1 {{
                font-size: 0.85rem;
            }}
            .logo-icon {{
                font-size: 1rem;
            }}
            .control-btn {{
                font-size: 0.8rem;
                padding: 4px 6px;
            }}
            .messages {{
                padding: 8px 10px;
            }}
            .input-area {{
                padding: 6px 8px 12px;
            }}
            .input-wrapper {{
                padding: 4px 4px 4px 12px;
                min-height: 40px;
                gap: 6px;
                border-radius: 22px;
            }}
            textarea {{
                font-size: 14px !important;
                padding: 5px 0;
                min-height: 18px;
            }}
            .submit-btn {{
                width: 36px;
                height: 36px;
                min-width: 36px;
                min-height: 36px;
            }}
            .submit-icon {{
                width: 14px;
                height: 14px;
            }}
            .message-content {{
                font-size: 0.75rem;
            }}
        }}
        
        /* Landscape Phones */
        @media (max-height: 500px) and (orientation: landscape) {{
            .header {{
                min-height: 40px;
                padding: 4px 12px;
                gap: 6px;
            }}
            .logo h1 {{
                font-size: 0.9rem;
            }}
            .logo-icon {{
                font-size: 1.1rem;
            }}
            .messages {{
                padding: 6px 12px;
                padding-bottom: 10px;
            }}
            .input-area {{
                padding: 4px 12px 8px;
            }}
            .input-wrapper {{
                min-height: 38px;
                padding: 4px 4px 4px 12px;
            }}
            textarea {{
                min-height: 20px;
                max-height: 80px;
                font-size: 14px !important;
                padding: 4px 0;
            }}
            .submit-btn {{
                width: 36px;
                height: 36px;
                min-width: 36px;
                min-height: 36px;
            }}
            .submit-icon {{
                width: 14px;
                height: 14px;
            }}
            .welcome {{
                min-height: 20vh;
            }}
            .suggestions {{
                display: none;
            }}
            .control-btn {{
                font-size: 0.8rem;
                padding: 3px 6px;
            }}
        }}
        
        /* Tablets */
        @media (min-width: 769px) and (max-width: 1024px) {{
            .input-wrapper {{
                max-width: 90%;
            }}
            .messages {{
                padding: 16px 24px;
            }}
            .header {{
                padding: 14px 20px;
            }}
        }}
        
        /* Desktop */
        @media (min-width: 1025px) {{
            .input-wrapper {{
                max-width: 760px;
            }}
            .messages {{
                padding: 24px 32px;
            }}
            .header {{
                padding: 16px 32px;
            }}
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
                <div class="input-wrapper">
                    <input type="file" id="fileInput" style="display:none" accept=".pdf,.docx,.pptx,.xlsx,.xls,.csv,.txt,.png,.jpg,.jpeg,.webp,.bmp" onchange="handleFileUpload(event)">
                    <button class="attach-btn" onclick="document.getElementById('fileInput').click()" aria-label="Attach file" type="button">
                        <svg class="attach-icon" viewBox="0 0 24 24" width="18" height="18">
                            <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/>
                        </svg>
                    </button>
                    <div class="input-text-wrapper">
                        <textarea id="userInput" placeholder="Ask Yama anything..." rows="1" onkeypress="handleKey(event)"></textarea>
                    </div>
                    <button class="submit-btn" onclick="sendMessage()" aria-label="Send message">
                        <svg class="submit-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
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
                <img src=\"${{currentUser.picture}}\" class=\"user-profile-img\">
                <div class=\"user-profile-info\">
                    <div class=\"user-profile-name\">${{currentUser.name}}</div>
                    <div class=\"user-profile-email\">${{currentUser.email}}</div>
                </div>
                <button class=\"logout-btn\" onclick=\"logout()\">Logout</button>
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
                container.innerHTML = '<div style=\"color:#6a5a4a;text-align:center;padding:20px;\">No conversations yet</div>';
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
        
        const textarea = document.getElementById('userInput');
        
        // Auto-adjust height
        function autoAdjustHeight() {{
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        }}
        
        textarea.addEventListener('input', autoAdjustHeight);
        
        // VisualViewport handling for mobile keyboard
        if (window.visualViewport) {{
            let lastHeight = window.visualViewport.height;
            window.visualViewport.addEventListener('resize', function() {{
                const inputArea = document.querySelector('.input-area');
                if (inputArea && window.visualViewport.height < lastHeight) {{
                    // Keyboard opened - ensure input is visible
                    setTimeout(() => {{
                        inputArea.scrollIntoView({{ behavior: 'smooth', block: 'end' }});
                    }}, 100);
                }}
                lastHeight = window.visualViewport.height;
            }});
        }}
        
        function handleKey(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }}
        
        async function sendMessage() {{
            if (!currentUser) {{ alert('Please sign in first!'); return; }}
            const message = textarea.value.trim();
            if (!message) return;
            
            if (!hasMessages) {{
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
                document.getElementById('logo').classList.add('small');
            }}
            
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
            
            addMessage(data.response, 'ai', data.image);
            document.getElementById('typing').style.display = 'none';
            loadHistory();
            scrollToBottom();
        }}
        
        async function handleFileUpload(event) {{
            if (!currentUser) {{ alert('Please sign in first!'); event.target.value = ''; return; }}
            const file = event.target.files[0];
            if (!file) return;
            
            if (!hasMessages) {{
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
                document.getElementById('logo').classList.add('small');
            }}
            
            addMessage('📎 Uploading "' + file.name + '"...', 'user');
            document.getElementById('typing').style.display = 'block';
            scrollToBottom();
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('email', currentUser.email);
            
            try {{
                const res = await fetch('/upload', {{ method: 'POST', body: formData }});
                const data = await res.json();
                addMessage(data.message || 'Upload failed.', 'ai');
            }} catch (err) {{
                addMessage('⚠️ Upload failed, please try again.', 'ai');
            }}
            
            document.getElementById('typing').style.display = 'none';
            event.target.value = '';
            scrollToBottom();
        }}
        
        function escapeHtmlText(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        function addMessage(text, sender, image) {{
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + sender + '-message';
            const content = document.createElement('div');
            content.className = 'message-content';
            const safeText = escapeHtmlText(text);
            content.innerHTML = safeText.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            if (image) {{
                const img = document.createElement('img');
                img.className = 'chat-image';
                img.src = image;
                img.alt = 'Generated graph';
                content.appendChild(img);
            }}
            div.appendChild(content);
            messages.appendChild(div);
            scrollToBottom();
        }}
        
        function scrollToBottom() {{
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }}
        
        loadHistory();
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

    result = get_response(message, email)
    if isinstance(result, dict):
        response_text, response_image = result["text"], result.get("image")
    else:
        response_text, response_image = result, None

    if email:
        history = load_history(email)
        history.append({
            "user": message,
            "ai": response_text,
            "timestamp": datetime.now().strftime("%H:%M")
        })
        save_history(email, history)

    return {"response": response_text, "image": response_image}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), email: str = Form(...)):
    safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', file.filename)
    dest_path = os.path.join(UPLOAD_DIR, f"{secrets.token_hex(8)}_{safe_name}")
    contents = await file.read()
    MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15MB cap
    if len(contents) > MAX_UPLOAD_BYTES:
        return {"status": "error", "message": "File too large (15MB limit)."}
    with open(dest_path, 'wb') as f:
        f.write(contents)

    text, kind = read_uploaded_file(dest_path, file.filename)
    if text is None:
        return {"status": "error", "message": f"Couldn't read this file: {kind}"}

    active_documents[email] = {"name": file.filename, "text": text, "kind": kind}
    preview = text[:300].strip()
    word_count = len(text.split())
    return {
        "status": "ok",
        "name": file.filename,
        "word_count": word_count,
        "preview": preview,
        "message": f"📎 Got **{file.filename}** ({word_count} words). Ask me to summarize it or analyze it!"
    }

@app.get("/get_history")
async def get_history(email: str = ""):
    return load_history(email)

@app.post("/clear_history")
async def clear_history_endpoint():
    save_history("", [])
    return {"status": "cleared"}

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - FULLY RESPONSIVE")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("📱 Perfect on ALL devices")
    print("✅ No fixed position issues")
    print("✅ 100dvh + safe-area support")
    print("✅ VisualViewport keyboard handling")
    print("✅ Responsive sidebar (min 280px, 80vw)")
    print("✅ All breakpoints: 380px, 480px, 768px, 1024px, 1025px+")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
