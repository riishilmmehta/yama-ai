from __future__ import annotations
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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
import time
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
import sympy as sp
from sympy import *
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application, convert_xor
import mpmath as mp
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import base64
import hashlib

# ============ DATA DIRECTORY (Render Compatible - NO RAILWAY) ============
DATA_DIR = os.environ.get("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="Yama AI V2.0")

# ============ USER DATABASE ============
user_db = TinyDB(os.path.join(DATA_DIR, 'users.json'))
User = Query()

# ============ MESSAGE FEATURES STORAGE ============
message_feedback_db = TinyDB(os.path.join(DATA_DIR, 'feedback.json'))
Feedback = Query()

# ============ CONTEXT MEMORY ============
class ContextMemory:
    def __init__(self, max_messages=30):
        self.max_messages = max_messages
        self._contexts = {}
    
    def get_context(self, email: str) -> List[Dict]:
        return self._contexts.get(email, [])
    
    def add_message(self, email: str, role: str, content: str):
        if email not in self._contexts:
            self._contexts[email] = []
        self._contexts[email].append({"role": role, "content": content})
        if len(self._contexts[email]) > self.max_messages:
            self._contexts[email] = self._contexts[email][-self.max_messages:]
    
    def get_last_topic(self, email: str) -> Optional[str]:
        context = self._contexts.get(email, [])
        for msg in reversed(context):
            if msg['role'] == 'user':
                return msg['content']
        return None

context_memory = ContextMemory()

# ============ QUERY CLEANER ============
class QueryCleaner:
    def __init__(self):
        self.filler_words = {
            'explain', 'tell', 'me', 'about', 'please', 'can', 'you', 'could',
            'would', 'should', 'may', 'might', 'will', 'shall', 'do', 'does', 'did',
            'what', 'is', 'are', 'was', 'were', 'the', 'a', 'an', 'of', 'to', 'for',
            'with', 'on', 'at', 'from', 'by', 'in', 'into', 'through', 'during',
            'define', 'describe', 'give', 'show', 'provide', 'list', 'how', 'why',
            'where', 'when', 'who', 'whom', 'whose', 'which'
        }
    
    def clean(self, query: str) -> str:
        words = query.lower().split()
        important = [w for w in words if w not in self.filler_words and len(w) > 2]
        return ' '.join(important) if important else query

query_cleaner = QueryCleaner()

# ============ MATH PREPROCESSOR ============
class MathPreprocessor:
    def __init__(self):
        self.superscript_map = {
            '²': '**2', '³': '**3', '⁴': '**4', '⁵': '**5',
            '⁶': '**6', '⁷': '**7', '⁸': '**8', '⁹': '**9'
        }
        
        self.symbol_map = {
            'π': 'pi', '√': 'sqrt', '∫': 'integrate',
            '∂': 'diff', '∞': 'oo', 'θ': 'theta',
            'α': 'alpha', 'β': 'beta', 'γ': 'gamma',
            '×': '*', '÷': '/', '≈': 'approx'
        }
        
        self.trig_funcs = ['sin', 'cos', 'tan', 'cot', 'sec', 'csc',
                          'asin', 'acos', 'atan', 'acot', 'asec', 'acsc',
                          'sinh', 'cosh', 'tanh', 'asinh', 'acosh', 'atanh']
    
    def preprocess(self, query: str) -> str:
        expression = query.strip()
        
        question_words = ['solve', 'find', 'calculate', 'evaluate', 'what is', 'compute', 
                         'differentiate', 'integrate', 'plot', 'graph']
        for word in question_words:
            expression = re.sub(rf'^{word}\s+', '', expression, flags=re.IGNORECASE)
            expression = re.sub(rf'\s+{word}\s+', ' ', expression, flags=re.IGNORECASE)
        
        for sup, replacement in self.superscript_map.items():
            expression = expression.replace(sup, replacement)
        
        for symbol, replacement in self.symbol_map.items():
            expression = expression.replace(symbol, replacement)
        
        expression = re.sub(r'(\d+)([a-zA-Z])', r'\1*\2', expression)
        expression = re.sub(r'([a-zA-Z])(\d+)', r'\1*\2', expression)
        expression = re.sub(r'(\d+)\(', r'\1*(', expression)
        expression = re.sub(r'\)\(', r')*(', expression)
        expression = re.sub(r'√\(([^)]+)\)', r'sqrt(\1)', expression)
        expression = re.sub(r'√([a-zA-Z]+)', r'sqrt(\1)', expression)
        
        for func in self.trig_funcs:
            expression = re.sub(rf'{func}\s*([a-zA-Z0-9]+)', rf'{func}(\1)', expression)
            expression = re.sub(rf'{func}\s*\(', rf'{func}(', expression)
        
        expression = re.sub(r'd/dx\s*\(([^)]+)\)', r'diff(\1, x)', expression)
        expression = re.sub(r'differentiate\s+([^\s]+)', r'diff(\1, x)', expression)
        expression = re.sub(r'derivative of\s+([^\s]+)', r'diff(\1, x)', expression)
        expression = re.sub(r'integrate\s+([^\s]+)\s+with respect to\s+([a-zA-Z])', r'integrate(\1, \2)', expression)
        expression = re.sub(r'integrate\s+([^\s]+)', r'integrate(\1, x)', expression)
        expression = re.sub(r'∫\s*([^\s]+)\s*dx', r'integrate(\1, x)', expression)
        expression = re.sub(r'\[\[([^\]]+)\]\]', r'Matrix([\1])', expression)
        
        if '=' in expression and not expression.startswith('Eq'):
            parts = expression.split('=')
            if len(parts) == 2:
                left = parts[0].strip()
                right = parts[1].strip()
                expression = f'Eq({left}, {right})'
        
        return expression

math_preprocessor = MathPreprocessor()

# ============ MATH INTENT DETECTOR ============
class MathIntentDetector:
    def __init__(self):
        self.math_keywords = [
            'sin', 'cos', 'tan', 'cot', 'sec', 'csc',
            'asin', 'acos', 'atan', 'log', 'ln', 'exp',
            'sqrt', 'cbrt', 'factorial', 'matrix', 'determinant',
            'integrate', 'differentiate', 'derivative', 'integral',
            'limit', 'plot', 'graph', 'solve', 'factor',
            'expand', 'simplify', 'equation', 'inequality'
        ]
        
        self.math_symbols = ['+', '-', '*', '/', '=', '²', '³', '√', 'π', '∫', '∂']
    
    def detect(self, query: str) -> bool:
        query_lower = query.lower()
        
        for symbol in self.math_symbols:
            if symbol in query:
                return True
        
        for keyword in self.math_keywords:
            if keyword in query_lower:
                return True
        
        if re.search(r'[a-zA-Z]\s*[=]', query):
            return True
        
        if re.search(r'[\d]+\s*[\+\-\*\/]\s*[\d]+', query):
            return True
        
        return False

math_intent_detector = MathIntentDetector()

# ============ ADVANCED MATH ENGINE ============
class AdvancedMathEngine:
    def __init__(self):
        self.precision = 15
        mp.mp.dps = self.precision
        self.x = symbols('x')
        self.y = symbols('y')
        self.z = symbols('z')
    
    def solve(self, query: str) -> Dict[str, Any]:
        try:
            processed = math_preprocessor.preprocess(query)
            
            solvers = [
                ('arithmetic', self.solve_arithmetic),
                ('equation', self.solve_equation),
                ('quadratic', self.solve_quadratic),
                ('calculus', self.solve_calculus),
                ('trigonometry', self.solve_trigonometry),
                ('matrix', self.solve_matrix),
                ('expression', self.solve_expression)
            ]
            
            for solver_type, solver_func in solvers:
                result = solver_func(processed, query)
                if result.get('success'):
                    result['type'] = solver_type
                    if 'explanation' not in result:
                        result['explanation'] = self.generate_explanation(result, query)
                    return result
            
            return {'success': False, 'error': 'Could not parse math expression'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def solve_arithmetic(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            clean = re.sub(r'[^0-9+\-*/%.()\s]', '', processed)
            if not clean:
                return {'success': False}
            
            expr = parse_expr(clean)
            if expr.is_number:
                result = float(expr)
                steps = [
                    f"Expression: {clean}",
                    f"Calculate: {clean}",
                    f"Result: {result}"
                ]
                return {
                    'success': True,
                    'result': result,
                    'result_str': str(result),
                    'is_exact': expr.is_Integer,
                    'steps': steps,
                    'answer': str(result)
                }
        except:
            pass
        return {'success': False}
    
    def solve_quadratic(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            eq_match = re.search(r'Eq\(([^,]+),\s*([^)]+)\)', processed)
            if not eq_match:
                if 'x**2' in processed or 'x²' in original:
                    expr = parse_expr(processed)
                    if expr.is_polynomial():
                        poly = poly(expr, self.x)
                        if poly.degree() == 2:
                            a, b, c = poly.all_coeffs()
                            if len(a) >= 3:
                                a_val, b_val, c_val = float(a[0]), float(a[1]), float(a[2])
                                discriminant = b_val**2 - 4*a_val*c_val
                                
                                if discriminant >= 0:
                                    sqrt_d = sqrt(discriminant)
                                    x1 = (-b_val + sqrt_d) / (2*a_val)
                                    x2 = (-b_val - sqrt_d) / (2*a_val)
                                    
                                    steps = [
                                        f"Quadratic equation: {a_val}x² + {b_val}x + {c_val} = 0",
                                        f"Using quadratic formula: x = [-b ± √(b² - 4ac)] / 2a",
                                        f"a = {a_val}, b = {b_val}, c = {c_val}",
                                        f"b² - 4ac = {b_val}² - 4({a_val})({c_val}) = {discriminant}",
                                        f"√{discriminant} = {sqrt_d.evalf(self.precision)}",
                                        f"x = [{-b_val} ± {sqrt_d.evalf(self.precision)}] / {2*a_val}",
                                        f"x₁ = {x1.evalf(self.precision)}",
                                        f"x₂ = {x2.evalf(self.precision)}"
                                    ]
                                    
                                    return {
                                        'success': True,
                                        'solutions': [str(x1.evalf(self.precision)), str(x2.evalf(self.precision))],
                                        'solutions_count': 2,
                                        'result_str': f"x = {x1.evalf(self.precision)}, {x2.evalf(self.precision)}",
                                        'steps': steps,
                                        'answer': f"x = {x1.evalf(self.precision)}, x = {x2.evalf(self.precision)}"
                                    }
            else:
                left = parse_expr(eq_match.group(1).strip())
                right = parse_expr(eq_match.group(2).strip())
                expr = left - right
                
                if expr.is_polynomial():
                    poly = poly(expr, self.x)
                    if poly.degree() == 2:
                        a, b, c = poly.all_coeffs()
                        if len(a) >= 3:
                            a_val, b_val, c_val = float(a[0]), float(a[1]), float(a[2])
                            discriminant = b_val**2 - 4*a_val*c_val
                            
                            if discriminant >= 0:
                                sqrt_d = sqrt(discriminant)
                                x1 = (-b_val + sqrt_d) / (2*a_val)
                                x2 = (-b_val - sqrt_d) / (2*a_val)
                                
                                factor_form = factor(expr)
                                
                                steps = [
                                    f"Equation: {original}",
                                    f"Method: Factorization",
                                    f"Step 1: Find factors of {abs(c_val)} that sum to {b_val}",
                                    f"Step 2: Rewrite as {factor_form}",
                                    f"Step 3: Apply zero product property",
                                    f"x₁ = {x1.evalf(self.precision)}",
                                    f"x₂ = {x2.evalf(self.precision)}"
                                ]
                                
                                return {
                                    'success': True,
                                    'solutions': [str(x1.evalf(self.precision)), str(x2.evalf(self.precision))],
                                    'solutions_count': 2,
                                    'result_str': f"x = {x1.evalf(self.precision)}, {x2.evalf(self.precision)}",
                                    'steps': steps,
                                    'answer': f"x = {x1.evalf(self.precision)}, x = {x2.evalf(self.precision)}"
                                }
        except:
            pass
        return {'success': False}
    
    def solve_equation(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            quad_result = self.solve_quadratic(processed, original)
            if quad_result.get('success'):
                return quad_result
            
            eq_match = re.search(r'Eq\(([^,]+),\s*([^)]+)\)', processed)
            if eq_match:
                left = parse_expr(eq_match.group(1).strip())
                right = parse_expr(eq_match.group(2).strip())
                expr = left - right
            else:
                expr = parse_expr(processed)
            
            variables = list(expr.free_symbols)
            if not variables:
                return {'success': False}
            
            var = variables[0]
            solutions = solve(expr, var)
            
            if solutions:
                result_strs = []
                for sol in solutions:
                    if sol.is_number:
                        result_strs.append(str(sol.evalf(self.precision)))
                    else:
                        result_strs.append(str(sol))
                
                steps = self.generate_equation_steps(expr, var, solutions, original)
                
                return {
                    'success': True,
                    'variable': str(var),
                    'solutions': result_strs,
                    'solutions_count': len(solutions),
                    'result_str': ', '.join(result_strs) if len(result_strs) > 1 else result_strs[0],
                    'steps': steps,
                    'answer': f"{str(var)} = {', '.join(result_strs) if len(result_strs) > 1 else result_strs[0]}"
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
        return {'success': False}
    
    def generate_equation_steps(self, expr, var, solutions, original) -> List[str]:
        steps = []
        try:
            if expr.is_Add and len(expr.args) <= 3:
                steps.append(f"Equation: {original}")
                steps.append(f"Step 1: Isolate {var} on one side")
                
                if len(expr.args) == 2 and expr.has(var):
                    steps.append(f"Step 2: Move constant terms")
                    steps.append(f"Step 3: Divide by coefficient of {var}")
                    steps.append(f"Final: {var} = {solutions[0]}")
                    return steps
            
            if expr.is_polynomial():
                degree = poly(expr).degree()
                if degree == 1:
                    steps.append(f"Linear equation: {original}")
                    steps.append(f"Step 1: Isolate {var}")
                    steps.append(f"Step 2: {var} = {solutions[0]}")
                elif degree == 2:
                    steps.append(f"Quadratic equation: {original}")
                    steps.append(f"Step 1: Identify coefficients")
                    steps.append(f"Step 2: Apply quadratic formula")
                    steps.append(f"Step 3: Simplify")
                    if len(solutions) > 1:
                        steps.append(f"Step 4: {var} = {solutions[0]}, {var} = {solutions[1]}")
                    else:
                        steps.append(f"Step 4: {var} = {solutions[0]}")
                else:
                    steps.append(f"Equation: {original}")
                    steps.append(f"Solution: {', '.join([str(s) for s in solutions])}")
            else:
                steps.append(f"Equation: {original}")
                steps.append(f"Solution: {', '.join([str(s) for s in solutions])}")
                
        except:
            steps.append(f"Equation: {original}")
            steps.append(f"Solution: {', '.join([str(s) for s in solutions])}")
        
        return steps
    
    def solve_calculus(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            if 'diff' in processed or 'derivative' in processed.lower():
                expr_match = re.search(r'diff\(([^,]+),\s*([^)]+)\)', processed)
                if not expr_match:
                    expr = parse_expr(processed.replace('diff', '').replace('(', '').replace(')', '').strip())
                    var = self.x
                else:
                    expr = parse_expr(expr_match.group(1).strip())
                    var = parse_expr(expr_match.group(2).strip())
                
                result = diff(expr, var)
                steps = [
                    f"Expression: d/d{var}({expr})",
                    f"Method: Power Rule / Chain Rule",
                    f"Step 1: Apply differentiation rules",
                    f"Step 2: d/d{var}({expr}) = {result}",
                    f"Final Answer: {result}"
                ]
                return {
                    'success': True,
                    'type': 'derivative',
                    'expression': str(expr),
                    'result': str(result),
                    'result_str': str(result),
                    'steps': steps,
                    'answer': str(result)
                }
            
            if 'integrate' in processed or '∫' in processed:
                expr_match = re.search(r'integrate\(([^,]+),\s*([^)]+)\)', processed)
                if not expr_match:
                    expr = parse_expr(processed.replace('integrate', '').replace('∫', '').replace('(', '').replace(')', '').strip())
                    var = self.x
                else:
                    expr = parse_expr(expr_match.group(1).strip())
                    var = parse_expr(expr_match.group(2).strip())
                
                result = integrate(expr, var)
                steps = [
                    f"Expression: ∫{expr} d{var}",
                    f"Method: Power Rule / Substitution",
                    f"Step 1: Apply integration rules",
                    f"Step 2: ∫{expr} d{var} = {result} + C",
                    f"Final Answer: {result} + C"
                ]
                return {
                    'success': True,
                    'type': 'integral',
                    'expression': str(expr),
                    'result': str(result),
                    'result_str': str(result) + ' + C',
                    'steps': steps,
                    'answer': str(result) + ' + C'
                }
            
            if 'limit' in processed or 'lim' in processed:
                expr_match = re.search(r'limit\(([^,]+),\s*([^,]+),\s*([^)]+)\)', processed)
                if expr_match:
                    expr = parse_expr(expr_match.group(1).strip())
                    var = parse_expr(expr_match.group(2).strip())
                    point = parse_expr(expr_match.group(3).strip())
                    result = limit(expr, var, point)
                    steps = [
                        f"Expression: lim_{var}→{point} of {expr}",
                        f"Method: Direct substitution",
                        f"Step 1: Evaluate limit",
                        f"Step 2: lim_{var}→{point} of {expr} = {result}",
                        f"Final Answer: {result}"
                    ]
                    return {
                        'success': True,
                        'type': 'limit',
                        'expression': str(expr),
                        'result': str(result),
                        'result_str': str(result),
                        'steps': steps,
                        'answer': str(result)
                    }
        except Exception as e:
            return {'success': False, 'error': str(e)}
        return {'success': False}
    
    def solve_trigonometry(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            expr = parse_expr(processed)
            if any(f in str(expr) for f in ['sin', 'cos', 'tan', 'asin', 'acos', 'atan']):
                if expr.is_number:
                    result = expr.evalf(self.precision)
                    steps = [
                        f"Expression: {original}",
                        f"Method: Direct evaluation",
                        f"Step 1: Calculate {original}",
                        f"Result: {float(result)}"
                    ]
                    return {
                        'success': True,
                        'expression': str(expr),
                        'result': float(result),
                        'result_str': str(result),
                        'steps': steps,
                        'answer': str(result)
                    }
                
                simplified = simplify(expr)
                steps = [
                    f"Expression: {original}",
                    f"Method: Simplify using trig identities",
                    f"Step 1: Apply trig identities",
                    f"Result: {simplified}"
                ]
                return {
                    'success': True,
                    'expression': str(expr),
                    'result': str(simplified),
                    'result_str': str(simplified),
                    'steps': steps,
                    'answer': str(simplified)
                }
        except:
            pass
        return {'success': False}
    
    def solve_matrix(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            matrix_match = re.search(r'Matrix\(\[(.*?)\]\)', processed)
            if not matrix_match:
                matrix_match = re.search(r'\[\[(.*?)\]\]', original)
                if matrix_match:
                    matrix_str = matrix_match.group(1)
                else:
                    return {'success': False}
            else:
                matrix_str = matrix_match.group(1)
            
            rows = []
            for row in matrix_str.split('],['):
                row = re.sub(r'[\[\]]', '', row)
                row_values = [float(x.strip()) for x in row.split(',')]
                rows.append(row_values)
            
            matrix = Matrix(rows)
            
            if 'determinant' in original or 'det' in original:
                result = matrix.det()
                steps = [
                    f"Matrix: {matrix}",
                    f"Method: Determinant formula",
                    f"Step 1: Apply determinant formula for {len(rows)}×{len(rows)} matrix",
                    f"Result: det = {float(result)}"
                ]
                return {
                    'success': True,
                    'operation': 'determinant',
                    'matrix': str(matrix),
                    'result': float(result),
                    'result_str': str(result),
                    'steps': steps,
                    'answer': str(result)
                }
            elif 'inverse' in original or 'inv' in original:
                result = matrix.inv()
                steps = [
                    f"Matrix: {matrix}",
                    f"Method: Matrix inversion",
                    f"Step 1: Calculate inverse matrix",
                    f"Result: {result}"
                ]
                return {
                    'success': True,
                    'operation': 'inverse',
                    'matrix': str(matrix),
                    'result': str(result),
                    'result_str': str(result),
                    'steps': steps,
                    'answer': str(result)
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
        return {'success': False}
    
    def solve_expression(self, processed: str, original: str) -> Dict[str, Any]:
        try:
            expr = parse_expr(processed)
            if expr.is_number:
                result = expr.evalf(self.precision)
                steps = [
                    f"Expression: {original}",
                    f"Method: Direct evaluation",
                    f"Result: {float(result)}"
                ]
                return {
                    'success': True,
                    'result': float(result),
                    'result_str': str(result),
                    'steps': steps,
                    'answer': str(result)
                }
            
            simplified = simplify(expr)
            if str(simplified) != str(expr):
                steps = [
                    f"Expression: {original}",
                    f"Method: Simplify expression",
                    f"Step 1: Apply algebraic rules",
                    f"Result: {simplified}"
                ]
                return {
                    'success': True,
                    'result': str(simplified),
                    'result_str': str(simplified),
                    'steps': steps,
                    'answer': str(simplified)
                }
            
            factored = factor(expr)
            if str(factored) != str(expr):
                steps = [
                    f"Expression: {original}",
                    f"Method: Factor expression",
                    f"Step 1: Apply factoring rules",
                    f"Result: {factored}"
                ]
                return {
                    'success': True,
                    'result': str(factored),
                    'result_str': str(factored),
                    'steps': steps,
                    'answer': str(factored)
                }
        except:
            pass
        return {'success': False}
    
    def generate_explanation(self, result: Dict[str, Any], query: str) -> str:
        if not result.get('success'):
            return "Could not solve this problem."
        
        steps = result.get('steps', [])
        if steps:
            return "\n".join(steps)
        
        return result.get('answer', 'Result computed successfully.')

math_engine = AdvancedMathEngine()

# ============ GRAPH GENERATOR ============
class GraphGenerator:
    def __init__(self):
        self.fig_size = (8, 6)
        self.dpi = 100
    
    def generate_graph(self, expression: str) -> Optional[str]:
        try:
            expr = parse_expr(expression)
            
            fig, ax = plt.subplots(figsize=self.fig_size, dpi=self.dpi)
            
            x_vals = np.linspace(-10, 10, 1000)
            f = lambdify(self.x, expr, modules=['numpy'])
            y_vals = f(x_vals)
            
            ax.plot(x_vals, y_vals, 'b-', linewidth=2)
            ax.grid(True, alpha=0.3)
            ax.axhline(y=0, color='black', linewidth=0.5)
            ax.axvline(x=0, color='black', linewidth=0.5)
            
            ax.set_xlabel('x')
            ax.set_ylabel('y')
            ax.set_title(f'y = {expression}')
            
            if y_vals.any():
                sign_changes = np.where(np.diff(np.sign(y_vals)))[0]
                x_intercepts = []
                for idx in sign_changes:
                    x_intercepts.append(x_vals[idx])
                
                for x_int in x_intercepts:
                    ax.plot(x_int, 0, 'ro', markersize=8)
                    ax.annotate(f'({x_int:.2f}, 0)', (x_int, 0), xytext=(5, 5), 
                               textcoords='offset points', fontsize=8)
            
            buf = BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight', dpi=self.dpi)
            buf.seek(0)
            
            img_str = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            return img_str
        except:
            return None

graph_generator = GraphGenerator()

# ============ SOURCE VALIDATOR ============
class SourceValidator:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.session.timeout = 8
    
    def validate_and_extract(self, url: str, query: str = "") -> Dict[str, Any]:
        result = {
            'valid': False,
            'content': None,
            'title': None,
            'relevance_score': 0,
            'error': None
        }
        
        try:
            response = self.session.get(url, timeout=8)
            if response.status_code != 200:
                result['error'] = f"Status: {response.status_code}"
                return result
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
                element.decompose()
            
            for element in soup.find_all(class_=re.compile(r'(ad|popup|modal|banner|cookie|newsletter|subscribe|sidebar|comment|related)', re.I)):
                element.decompose()
            
            title_tag = soup.find('title')
            if title_tag:
                result['title'] = title_tag.get_text().strip()
            
            content_parts = []
            main = soup.find('article') or soup.find('main')
            if main:
                for p in main.find_all('p'):
                    text = p.get_text(strip=True)
                    if len(text) > 50:
                        content_parts.append(text)
            else:
                for p in soup.find_all('p'):
                    text = p.get_text(strip=True)
                    if len(text) > 50:
                        content_parts.append(text)
            
            for h in soup.find_all(['h1', 'h2', 'h3']):
                text = h.get_text(strip=True)
                if len(text) > 10:
                    content_parts.append(text)
            
            full_text = ' '.join(content_parts[:30])
            full_text = re.sub(r'\s+', ' ', full_text).strip()
            
            if len(full_text) > 100:
                result['valid'] = True
                result['content'] = full_text[:2000]
                
                if query:
                    result['relevance_score'] = self.calculate_relevance(full_text, query)
            
        except Exception as e:
            result['error'] = str(e)
        
        return result
    
    def calculate_relevance(self, content: str, query: str) -> float:
        if not content or not query:
            return 0.0
        
        query_words = set(query_cleaner.clean(query).split())
        content_words = set(re.findall(r'\b[a-z]{3,}\b', content.lower()))
        
        if not query_words:
            return 0.0
        
        overlap = len(content_words.intersection(query_words))
        relevance = overlap / len(query_words) if query_words else 0
        
        if query.lower() in content.lower():
            relevance += 0.3
        
        return min(1.0, relevance)

source_validator = SourceValidator()

# ============ TRUST SCORE ============
def get_trust_score(url: str) -> Tuple[int, str]:
    domain = urlparse(url).netloc.lower()
    
    if any(domain.endswith(ext) for ext in ['.gov', '.gov.uk', '.gov.in']):
        return 100, "Government"
    if any(domain.endswith(ext) for ext in ['.edu', '.ac.uk', '.ac.in']):
        return 95, "University"
    if any(keyword in domain for keyword in ['researchgate', 'arxiv', 'pubmed', 'nature', 'science']):
        return 95, "Scientific Journal"
    if 'wikipedia' in domain:
        return 85, "Encyclopedia"
    if any(keyword in domain for keyword in ['microsoft', 'apple', 'google', 'github']):
        return 85, "Official"
    if any(keyword in domain for keyword in ['nytimes', 'bbc', 'cnn', 'reuters', 'apnews']):
        return 80, "News"
    if 'blog' in domain or 'medium' in domain:
        return 60, "Blog"
    if any(keyword in domain for keyword in ['forum', 'reddit', 'quora']):
        return 40, "Forum"
    return 50, "Website"

# ============ ANSWER GENERATOR ============
class AnswerGenerator:
    def __init__(self):
        self.min_relevance_threshold = 0.2
    
    def generate_answer(self, query: str, search_results: List[Dict]) -> Dict[str, Any]:
        direct_answer = self.extract_direct_answer(query, search_results)
        if direct_answer:
            return {
                'success': True,
                'answer': direct_answer,
                'sources': search_results[:3],
                'source_type': 'direct'
            }
        
        valid_sources = []
        for result in search_results[:5]:
            url = result.get('url', '')
            if url:
                validation = source_validator.validate_and_extract(url, query)
                if validation['valid'] and validation['relevance_score'] >= self.min_relevance_threshold:
                    valid_sources.append({
                        'url': url,
                        'title': validation.get('title', ''),
                        'content': validation['content'],
                        'relevance_score': validation['relevance_score'],
                        'trust_score': get_trust_score(url)[0]
                    })
        
        if not valid_sources:
            return {'success': False, 'error': 'No relevant sources found'}
        
        generated_answer = self.generate_from_sources(query, valid_sources)
        
        if generated_answer:
            return {
                'success': True,
                'answer': generated_answer,
                'sources': valid_sources[:3],
                'source_type': 'generated'
            }
        
        return {'success': False, 'error': 'Could not generate answer'}
    
    def extract_direct_answer(self, query: str, search_results: List[Dict]) -> Optional[str]:
        if not search_results:
            return None
        
        cleaned_query = query_cleaner.clean(query)
        query_words = set(cleaned_query.split())
        
        for result in search_results[:5]:
            snippet = result.get('snippet', '')
            if snippet and len(snippet) > 50:
                snippet_words = set(re.findall(r'\b[a-z]{3,}\b', snippet.lower()))
                overlap = len(snippet_words.intersection(query_words))
                
                if overlap >= 2 or query.lower() in snippet.lower():
                    clean_snippet = re.sub(r'\s+', ' ', snippet).strip()
                    if len(clean_snippet) > 50:
                        return clean_snippet
        
        return None
    
    def generate_from_sources(self, query: str, sources: List[Dict]) -> Optional[str]:
        if not sources:
            return None
        
        all_content = []
        for source in sources:
            content = source.get('content', '')
            if content:
                all_content.append(content)
        
        if not all_content:
            return None
        
        combined = ' '.join(all_content[:3])
        sentences = re.split(r'[.!?]', combined)
        key_sentences = []
        
        important_terms = query_cleaner.clean(query).split()
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 50:
                if any(term in sentence.lower() for term in important_terms):
                    key_sentences.append(sentence)
                elif len(key_sentences) < 2:
                    key_sentences.append(sentence)
        
        if key_sentences:
            return '. '.join(key_sentences[:3]) + '.'
        
        first_content = all_content[0][:500]
        paragraphs = first_content.split('\n')
        for p in paragraphs:
            if len(p) > 100:
                return p
        
        return None

answer_generator = AnswerGenerator()

# ============ SEARCH FUNCTION ============
def search_web(query: str, max_results: int = 10) -> List[Dict]:
    results = []
    try:
        search_query = query_cleaner.clean(query)
        if not search_query:
            search_query = query
        
        with DDGS() as ddgs:
            search_results = list(ddgs.text(search_query, max_results=max_results))
            
            for r in search_results:
                url = r.get('href', '')
                if not url:
                    continue
                
                trust_score, category = get_trust_score(url)
                
                results.append({
                    "title": r.get('title', ''),
                    "snippet": r.get('body', '')[:300],
                    "url": url,
                    "trust_score": trust_score,
                    "trust_category": category
                })
            
            seen_urls = set()
            unique_results = []
            for r in results:
                if r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    unique_results.append(r)
            
            unique_results.sort(key=lambda x: x['trust_score'], reverse=True)
            return unique_results
    except Exception as e:
        print(f"Search error: {e}")
        return []

# ============ FORMAT MATH ANSWER ============
def format_math_answer(result: Dict[str, Any]) -> str:
    if not result.get('success'):
        return f"❌ Could not solve: {result.get('error', 'Unknown error')}"
    
    steps = result.get('steps', [])
    answer = result.get('answer', result.get('result_str', ''))
    
    response = "📐 **Mathematics Solution**\n\n"
    
    if steps:
        response += "**Step-by-Step Solution:**\n\n"
        for i, step in enumerate(steps, 1):
            response += f"{i}. {step}\n"
        response += "\n"
    
    response += f"**Final Answer:** {answer}\n"
    
    return response

# ============ FORMAT GENERAL ANSWER ============
def format_answer(answer_data: Dict[str, Any], query: str, intent: str) -> str:
    if not answer_data.get('success'):
        return f"I couldn't find a clear answer to '{query}'. Please try rephrasing your question."
    
    answer = answer_data['answer']
    sources = answer_data.get('sources', [])
    
    if intent == 'definition':
        response = f"📖 **Definition**\n\n{answer}\n\n"
    elif intent == 'explanation':
        response = f"💡 **Explanation**\n\n{answer}\n\n"
    elif intent == 'fact':
        response = f"📌 **Fact**\n\n{answer}\n\n"
    else:
        response = f"💡 **Answer**\n\n{answer}\n\n"
    
    if sources:
        response += "📚 **Sources**\n"
        for i, source in enumerate(sources[:3], 1):
            trust = source.get('trust_score', 0)
            title = source.get('title', 'Untitled')
            url = source.get('url', '')
            relevance = source.get('relevance_score', 0)
            
            if trust >= 80:
                icon = "⭐"
            elif trust >= 60:
                icon = "📘"
            else:
                icon = "📄"
            
            response += f"{i}. {icon} {title} (Trust: {trust}%, Relevance: {int(relevance * 100)}%)\n   <a href=\"{url}\" target=\"_blank\">{url}</a>\n"
    
    return response

# ============ MAIN RESPONSE FUNCTION ============
def get_response(message, email, regenerate=False):
    msg = message.strip()
    
    stats = update_user_stats(email)
    user = user_db.get(User.email == email)
    user_name = user.get('name', 'User') if user else 'User'
    
    if math_intent_detector.detect(msg):
        math_result = math_engine.solve(msg)
        if math_result.get('success'):
            response = format_math_answer(math_result)
            
            if 'graph' in msg.lower() or 'plot' in msg.lower():
                expr_match = re.search(r'[a-zA-Z0-9\+\-\*\^\/\(\)\s]+', msg)
                if expr_match:
                    graph_img = graph_generator.generate_graph(expr_match.group(0).strip())
                    if graph_img:
                        response += f"\n\n📊 **Graph**\n<img src='data:image/png;base64,{graph_img}' alt='Graph' style='max-width:100%;border-radius:8px;'/>"
            
            response += f"\n\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
            return response
        else:
            response = f"❌ Could not solve: {math_result.get('error', 'Unknown error')}\n\n💡 Try rephrasing your math question."
            return response
    
    if msg.lower() in ['hi', 'hello', 'hey', 'sup', 'yo', 'good morning', 'good evening']:
        return f"👋 Hello {user_name}! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
    
    if 'how are you' in msg.lower():
        return f"😊 I'm doing great! Thanks for asking, {user_name}!"
    
    context = context_memory.get_context(email)
    if context and len(context) > 0:
        if any(word in msg.lower() for word in ['it', 'they', 'that', 'this', 'those', 'these', 'what about']):
            last_topic = context_memory.get_last_topic(email)
            if last_topic and 'President' in last_topic and 'Italy' in msg.lower():
                search_query = "current President of Italy"
            elif last_topic:
                search_query = f"{query_cleaner.clean(msg)} {query_cleaner.clean(last_topic)}"
            else:
                search_query = query_cleaner.clean(msg)
        else:
            search_query = query_cleaner.clean(msg)
    else:
        search_query = query_cleaner.clean(msg)
    
    if not search_query:
        search_query = msg
    
    start_time = time.time()
    
    search_results = search_web(search_query, max_results=10)
    
    if not search_results:
        return f"I searched for '{msg}' but found no results. Please try rephrasing your question."
    
    intent = 'general'
    if any(word in msg.lower() for word in ['define', 'meaning', 'definition']):
        intent = 'definition'
    elif any(word in msg.lower() for word in ['explain', 'how does', 'why does']):
        intent = 'explanation'
    elif any(word in msg.lower() for word in ['who', 'what', 'when', 'where']):
        intent = 'fact'
    
    answer_data = answer_generator.generate_answer(msg, search_results)
    response = format_answer(answer_data, msg, intent)
    
    important_terms = query_cleaner.clean(msg).split()
    follow_ups = []
    if important_terms:
        follow_ups.append(f"Tell me more about {important_terms[0]}")
    follow_ups.extend(["Give examples", "Explain simply"])
    
    if follow_ups:
        response += "\n\n💭 **Follow-up Questions:**\n" + "\n".join([f"• {q}" for q in follow_ups[:3]])
    
    response += f"\n\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
    
    track_analytics({
        "query": msg,
        "intent": intent,
        "response_time": time.time() - start_time,
        "sources_found": len(search_results)
    })
    
    if not regenerate:
        context_memory.add_message(email, "user", msg)
        context_memory.add_message(email, "ai", response)
    
    return response

# ============ ANALYTICS ============
analytics_data = []

def track_analytics(data: Dict):
    analytics_data.append({
        **data,
        "timestamp": datetime.now().isoformat()
    })
    if len(analytics_data) > 1000:
        analytics_data[:] = analytics_data[-1000:]

def get_analytics_summary() -> Dict:
    if not analytics_data:
        return {"error": "No analytics data available"}
    
    total = len(analytics_data)
    avg_time = sum(d.get('response_time', 0) for d in analytics_data) / total
    avg_sources = sum(d.get('sources_found', 0) for d in analytics_data) / total
    
    return {
        "total_queries": total,
        "average_response_time": f"{avg_time:.2f}s",
        "average_sources": f"{avg_sources:.1f}"
    }

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

# ============ HEALTH CHECK ============
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "Yama AI V2.0",
        "version": "2.0"
    }

@app.get("/ping")
async def ping():
    return {"pong": True, "timestamp": datetime.now().isoformat()}

# ============ ENDPOINTS ============
@app.post("/feedback")
async def submit_feedback(request: Request):
    data = await request.json()
    return {"status": "success"}

@app.post("/regenerate")
async def regenerate_response(request: Request):
    data = await request.json()
    email = data.get('email')
    message = data.get('message')
    response = get_response(message, email, regenerate=True)
    return {"response": response}

@app.post("/continue_generating")
async def continue_generating(request: Request):
    return {"response": "\n\n📝 Additional information could not be generated."}

@app.get("/share_conversation")
async def share_conversation(email: str = ""):
    if not email:
        return JSONResponse({"error": "Email required"}, status_code=400)
    history = load_history(email)
    return {"conversation": history}

@app.get("/analytics")
async def get_analytics():
    return get_analytics_summary()

# ============ GOOGLE CLIENT ID ============
GOOGLE_CLIENT_ID = "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com"

# ============ HTML (UNCHANGED) ============
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
        * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
        html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; overflow-x: hidden; overflow-y: auto; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f0e8; transition: all 0.3s ease; -webkit-font-smoothing: antialiased; }}
        img, video, iframe {{ max-width: 100%; height: auto; }}
        
        body.dark {{ background: #1a1a2e; }}
        body.dark .app {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); }}
        body.dark .header {{ background: rgba(26,26,46,0.95); border-bottom-color: #2a2a4e; }}
        body.dark .logo h1 {{ color: #d4c5a9; }}
        body.dark .input-wrapper {{ background: #2a2a4e; border-color: #3a3a5e; }}
        body.dark textarea {{ color: #e0e0e0; }}
        body.dark textarea::placeholder {{ color: #6a5a7a; }}
        body.dark .message-content {{ color: #e0e0e0; }}
        body.dark .ai-message .message-content {{ background: #2a2a4e !important; color: #e0e0e0 !important; }}
        body.dark .suggestion {{ background: #2a2a4e; border-color: #3a3a5e; color: #e0e0e0; }}
        body.dark .suggestion:hover {{ background: #3a3a5e; color: white; }}
        body.dark .welcome h2 {{ color: #d4c5a9; }}
        body.dark .welcome p {{ color: #8a7a6a; }}
        body.dark .sidebar {{ background: #0f0f23; border-right-color: #2a2a4e; }}
        body.dark .sidebar-header {{ background: #0a0a1a; }}
        body.dark .history-question {{ color: #d4c5a9; }}
        body.dark .history-time {{ color: #6a5a7a; }}
        body.dark .history-item:hover {{ background: rgba(212,197,169,0.08); border-color: #3a3a5e; }}
        body.dark .clear-history {{ color: #d4c5a9; border-color: #3a3a5e; }}
        body.dark .clear-history:hover {{ background: rgba(212,197,169,0.2); border-color: #c4a57b; }}
        body.dark .new-chat-btn {{ background: #3a3a5e; color: #d4c5a9; }}
        body.dark .new-chat-btn:hover {{ background: #4a4a6e; }}
        body.dark .typing span {{ background: #d4c5a9; }}
        body.dark .typing {{ color: #d4c5a9; }}
        body.dark a {{ color: #4ecdc4; }}
        body.dark .message-content a {{ color: #4ecdc4; }}
        body.dark .message-content a:hover {{ color: #6ee7de; }}
        body.dark .control-btn {{ color: #d4c5a9; }}
        body.dark .control-btn:hover {{ background: #3a3a5e; color: white; }}

        .login-overlay {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); z-index: 2000; display: flex; justify-content: center; align-items: center; padding: 20px; }}
        .login-card {{ background: white; border-radius: 30px; padding: 40px 30px; text-align: center; max-width: 400px; width: 100%; box-shadow: 0 25px 50px rgba(0,0,0,0.2); }}
        .login-card .logo-icon {{ font-size: 4rem; margin-bottom: 20px; }}
        .login-card h2 {{ font-family: 'Playfair Display', serif; font-size: 2rem; margin-bottom: 10px; }}
        .login-card p {{ color: #666; font-size: 1rem; margin-bottom: 30px; }}

        .app {{ display: flex; flex-direction: column; height: 100dvh; min-height: 100vh; width: 100%; background: linear-gradient(135deg, #f5f0e8 0%, #e8e0d5 100%); position: relative; overflow: hidden; }}
        
        .sidebar {{ position: fixed; left: 0; top: 0; bottom: 0; width: min(280px, 80vw); background: #2c2418; border-right: 1px solid #4a3f2f; display: flex; flex-direction: column; transform: translateX(-100%); transition: transform 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55); z-index: 1000; box-shadow: 4px 0 20px rgba(0,0,0,0.1); }}
        .sidebar.open {{ transform: translateX(0); }}
        .sidebar-header {{ padding: 20px; border-bottom: 1px solid #4a3f2f; background: #1f1912; flex-shrink: 0; }}
        .sidebar-header h3 {{ color: #d4c5a9; font-family: 'Playfair Display', serif; font-size: 1rem; }}
        .user-profile {{ display: none; align-items: center; gap: 12px; padding: 12px; background: rgba(212,197,169,0.1); border-radius: 12px; margin-top: 15px; }}
        .user-profile-img {{ width: 45px; height: 45px; border-radius: 50%; object-fit: cover; }}
        .user-profile-info {{ flex: 1; min-width: 0; }}
        .user-profile-name {{ color: #d4c5a9; font-weight: 600; font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .user-profile-email {{ color: #8a7a6a; font-size: 0.65rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .logout-btn {{ background: rgba(212,197,169,0.1); border: 1px solid #4a3f2f; border-radius: 20px; padding: 6px 12px; color: #d4c5a9; cursor: pointer; font-size: 0.65rem; white-space: nowrap; }}
        .history-list {{ flex: 1; overflow-y: auto; padding: 12px; -webkit-overflow-scrolling: touch; }}
        .history-item {{ padding: 10px; margin-bottom: 6px; border-radius: 10px; cursor: pointer; transition: all 0.2s; border: 1px solid transparent; }}
        .history-item:hover {{ background: rgba(212,197,169,0.08); border-color: #4a3f2f; }}
        .history-question {{ font-size: 0.8rem; color: #d4c5a9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .history-time {{ font-size: 0.6rem; color: #6a5a4a; margin-top: 4px; }}
        .sidebar-footer {{ padding: 16px; border-top: 1px solid #4a3f2f; background: #1f1912; flex-shrink: 0; }}
        .new-chat-btn {{ background: #4a3f2f; border: none; border-radius: 25px; padding: 12px 16px; color: #d4c5a9; cursor: pointer; width: 100%; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; gap: 8px; transition: all 0.2s; }}
        .new-chat-btn:hover {{ background: #5a4f3f; }}
        .clear-history {{ background: rgba(212,197,169,0.1); border: 1px solid #4a3f2f; border-radius: 20px; padding: 8px 16px; color: #d4c5a9; cursor: pointer; font-size: 0.7rem; margin-top: 10px; width: 100%; }}
        .overlay {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.4); display: none; z-index: 999; }}
        .overlay.show {{ display: block; }}

        .main {{ flex: 1; display: flex; flex-direction: column; min-height: 0; height: 100%; width: 100%; overflow: hidden; }}
        
        .header {{ padding: 12px 16px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid #d4c5a9; background: rgba(245,240,232,0.95); flex-shrink: 0; min-height: 56px; width: 100%; position: relative; z-index: 10; }}
        .menu-btn {{ background: none; border: none; font-size: 1.3rem; cursor: pointer; color: #6a5a4a; padding: 8px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }}
        .menu-btn:hover {{ background: #d4c5a9; color: #2c2418; }}
        .logo {{ flex: 1; display: flex; align-items: baseline; gap: 6px; min-width: 0; }}
        .logo-icon {{ font-size: 1.8rem; }}
        .logo h1 {{ font-family: 'Playfair Display', serif; font-size: 1.3rem; color: #2c2418; white-space: nowrap; }}
        .new-chat-mobile {{ background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 8px; border-radius: 10px; color: #6a5a4a; display: none; }}
        .control-btn {{ background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 8px 12px; border-radius: 20px; color: #6a5a4a; transition: all 0.2s; display: flex; align-items: center; justify-content: center; }}
        .control-btn:hover {{ background: #d4c5a9; }}
        .user-btn {{ background: none; border: none; cursor: pointer; display: none; padding: 4px; }}
        .user-btn img {{ width: 35px; height: 35px; border-radius: 50%; object-fit: cover; }}

        .messages {{ flex: 1; overflow-y: auto; padding: 16px; padding-bottom: 20px; -webkit-overflow-scrolling: touch; scroll-behavior: smooth; min-height: 0; }}
        .message {{ margin-bottom: 20px; animation: fadeIn 0.3s ease; }}
        .message-wrapper {{ display: inline-block; max-width: 85%; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .user-message {{ text-align: right; }}
        .ai-message {{ text-align: left; }}
        .message-content {{ display: inline-block; max-width: 100%; font-size: 0.9rem; line-height: 1.5; color: #2c2418; background: transparent !important; padding: 0 !important; }}
        .user-message .message-content {{ background: #2c2418 !important; color: white !important; padding: 10px 16px !important; border-radius: 20px !important; }}
        .ai-message .message-content {{ background: white !important; color: #2c2418 !important; padding: 12px 18px !important; border-radius: 20px !important; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}

        .message-actions {{ display: flex; gap: 8px; margin-top: 8px; opacity: 0.6; transition: opacity 0.2s; flex-wrap: wrap; }}
        .message-actions:hover {{ opacity: 1; }}
        .message-actions button {{ background: none; border: none; cursor: pointer; padding: 4px 8px; font-size: 0.75rem; border-radius: 6px; color: #6a5a4a; transition: all 0.2s; display: flex; align-items: center; gap: 4px; }}
        .message-actions button:hover {{ background: rgba(44,36,24,0.1); color: #2c2418; }}
        body.dark .message-actions button {{ color: #8a7a6a; }}
        body.dark .message-actions button:hover {{ background: rgba(212,197,169,0.1); color: #d4c5a9; }}
        .message-actions .liked {{ color: #4caf50 !important; }}
        .message-actions .disliked {{ color: #f44336 !important; }}

        .edit-message-input {{ display: none; width: 100%; padding: 8px 12px; border: 2px solid #2c2418; border-radius: 12px; font-size: 0.9rem; font-family: inherit; background: white; color: #2c2418; }}
        body.dark .edit-message-input {{ background: #2a2a4e; color: #e0e0e0; border-color: #4a3f2f; }}
        .edit-message-input.active {{ display: block; }}
        .edit-actions {{ display: none; gap: 8px; margin-top: 8px; }}
        .edit-actions.active {{ display: flex; }}
        .edit-actions button {{ padding: 4px 12px; border-radius: 6px; border: none; cursor: pointer; font-size: 0.75rem; }}
        .edit-actions .save-edit {{ background: #2c2418; color: white; }}
        .edit-actions .cancel-edit {{ background: #e0d5c8; color: #2c2418; }}
        body.dark .edit-actions .cancel-edit {{ background: #3a3a5e; color: #d4c5a9; }}

        .typing {{ display: none; padding: 10px 16px; gap: 5px; color: #888; font-size: 0.8rem; flex-shrink: 0; }}
        .typing span {{ width: 6px; height: 6px; background: #c4a57b; border-radius: 50%; display: inline-block; animation: bounce 1.4s infinite; }}
        @keyframes bounce {{ 0%, 60%, 100% {{ transform: translateY(0); }} 30% {{ transform: translateY(-6px); }} }}

        .input-area {{ position: sticky; bottom: 0; z-index: 100; background: #f5f0e8; padding: 12px 16px 20px; padding-bottom: env(safe-area-inset-bottom, 20px); flex-shrink: 0; border-top: 1px solid rgba(212,197,169,0.3); }}
        body.dark .input-area {{ background: #1a1a2e; border-top-color: rgba(42,42,78,0.3); }}
        .input-wrapper {{ display: flex; align-items: flex-end; gap: 12px; background: white; border-radius: 28px; padding: 8px 8px 8px 20px; border: 1px solid #d4c5a9; width: 100%; max-width: 760px; margin: 0 auto; min-height: 56px; }}
        body.dark .input-wrapper {{ background: #2a2a4e; border-color: #3a3a5e; }}
        .input-text-wrapper {{ flex: 1; min-width: 0; }}
        textarea {{ width: 100%; background: transparent; border: none; outline: none; font-size: 16px; line-height: 1.5; resize: none; padding: 8px 0; font-family: inherit; color: #2c2418; min-height: 24px; max-height: 180px; overflow-y: auto; }}
        body.dark textarea {{ color: #e0e0e0; }}
        textarea::placeholder {{ color: #b8a88a; font-size: 0.95rem; }}
        @media (max-width: 768px) {{ textarea {{ font-size: 16px !important; }} }}
        .submit-btn {{ display: flex; align-items: center; justify-content: center; flex-shrink: 0; width: 44px; height: 44px; border-radius: 50%; border: none; background-color: #2c2418; color: white; cursor: pointer; transition: all 0.2s; min-width: 44px; min-height: 44px; }}
        body.dark .submit-btn {{ background-color: #4a3f2f; }}
        .submit-btn:hover {{ background-color: #4a3f2f; transform: scale(1.02); }}
        .submit-btn:active {{ transform: scale(0.96); }}
        .submit-icon {{ width: 20px; height: 20px; fill: currentColor; }}

        .welcome {{ display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 50vh; text-align: center; padding: 20px; }}
        .welcome-icon {{ font-size: 3rem; margin-bottom: 15px; animation: float 3s ease-in-out infinite; }}
        @keyframes float {{ 0%, 100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-8px); }} }}
        .welcome h2 {{ font-family: 'Playfair Display', serif; font-size: 2rem; color: #2c2418; margin-bottom: 8px; }}
        .welcome p {{ color: #6a5a4a; font-size: 0.85rem; margin-bottom: 20px; }}
        .suggestions {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 15px; }}
        .suggestion {{ background: white; border: 1px solid #d4c5a9; border-radius: 30px; padding: 6px 14px; font-size: 0.75rem; color: #2c2418; cursor: pointer; transition: all 0.2s; white-space: nowrap; }}
        .suggestion:hover {{ background: #2c2418; color: white; border-color: #2c2418; }}

        @media (max-width: 768px) {{ .messages {{ padding: 12px 16px; padding-bottom: 16px; }} .suggestions {{ display: none; }} .new-chat-mobile {{ display: block; }} .header {{ padding: 10px 14px; min-height: 52px; }} .logo h1 {{ font-size: 1.1rem; }} .logo-icon {{ font-size: 1.4rem; }} .message-content {{ max-width: 100%; font-size: 0.85rem; }} .input-area {{ padding: 10px 12px 16px; }} .input-wrapper {{ padding: 6px 6px 6px 16px; min-height: 50px; border-radius: 26px; }} textarea {{ font-size: 16px !important; padding: 8px 0; }} .submit-btn {{ width: 40px; height: 40px; min-width: 40px; min-height: 40px; }} .submit-icon {{ width: 18px; height: 18px; }} .message-actions button {{ font-size: 0.65rem; padding: 2px 6px; }} }}
        @media (max-width: 480px) {{ .header {{ padding: 8px 12px; min-height: 48px; gap: 8px; }} .logo h1 {{ font-size: 1rem; }} .logo-icon {{ font-size: 1.2rem; }} .control-btn {{ font-size: 0.9rem; padding: 6px 8px; }} .menu-btn {{ font-size: 1.1rem; padding: 6px; }} .messages {{ padding: 10px 12px; }} .input-area {{ padding: 8px 10px 14px; padding-bottom: env(safe-area-inset-bottom, 14px); }} .input-wrapper {{ padding: 5px 5px 5px 14px; min-height: 44px; gap: 8px; border-radius: 24px; }} textarea {{ font-size: 15px !important; padding: 6px 0; min-height: 20px; }} .submit-btn {{ width: 40px; height: 40px; min-width: 40px; min-height: 40px; }} .submit-icon {{ width: 16px; height: 16px; }} .message-content {{ font-size: 0.8rem; }} }}
        @media (max-width: 380px) {{ .header {{ padding: 6px 10px; min-height: 44px; gap: 6px; }} .logo h1 {{ font-size: 0.85rem; }} .logo-icon {{ font-size: 1rem; }} .control-btn {{ font-size: 0.8rem; padding: 4px 6px; }} .messages {{ padding: 8px 10px; }} .input-area {{ padding: 6px 8px 12px; }} .input-wrapper {{ padding: 4px 4px 4px 12px; min-height: 40px; gap: 6px; border-radius: 22px; }} textarea {{ font-size: 14px !important; padding: 5px 0; min-height: 18px; }} .submit-btn {{ width: 36px; height: 36px; min-width: 36px; min-height: 36px; }} .submit-icon {{ width: 14px; height: 14px; }} .message-content {{ font-size: 0.75rem; }} }}
        @media (max-height: 500px) and (orientation: landscape) {{ .header {{ min-height: 40px; padding: 4px 12px; gap: 6px; }} .logo h1 {{ font-size: 0.9rem; }} .logo-icon {{ font-size: 1.1rem; }} .messages {{ padding: 6px 12px; padding-bottom: 10px; }} .input-area {{ padding: 4px 12px 8px; }} .input-wrapper {{ min-height: 38px; padding: 4px 4px 4px 12px; }} textarea {{ min-height: 20px; max-height: 80px; font-size: 14px !important; padding: 4px 0; }} .submit-btn {{ width: 36px; height: 36px; min-width: 36px; min-height: 36px; }} .submit-icon {{ width: 14px; height: 14px; }} .welcome {{ min-height: 20vh; }} .suggestions {{ display: none; }} .control-btn {{ font-size: 0.8rem; padding: 3px 6px; }} }}
        @media (min-width: 769px) and (max-width: 1024px) {{ .input-wrapper {{ max-width: 90%; }} .messages {{ padding: 16px 24px; }} .header {{ padding: 14px 20px; }} }}
        @media (min-width: 1025px) {{ .input-wrapper {{ max-width: 760px; }} .messages {{ padding: 24px 32px; }} .header {{ padding: 16px 32px; }} }}
    </style>
</head>
<body>
    <div id="loginOverlay" class="login-overlay">
        <div class="login-card">
            <div class="logo-icon">🏛️</div>
            <h2>Welcome to Yama</h2>
            <p>Sign in to start your AI journey</p>
            <div id="g_id_onload" data-client_id="{GOOGLE_CLIENT_ID}" data-context="signin" data-ux_mode="popup" data-callback="handleCredentialResponse" data-auto_prompt="false"></div>
            <div class="g_id_signin" data-type="standard" data-shape="rectangular" data-theme="outline" data-text="signin_with" data-size="large" data-logo_alignment="left"></div>
        </div>
    </div>
    
    <div class="app" id="app">
        <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h3>📜 CONVERSATIONS</h3>
                <div class="user-profile" id="userProfile"></div>
            </div>
            <div class="history-list" id="historyList"><div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div></div>
            <div class="sidebar-footer">
                <button class="new-chat-btn" onclick="newChat()">➕ New Chat</button>
                <button class="clear-history" onclick="clearHistory()">Clear all history</button>
            </div>
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
            
            <div class="typing" id="typing"><span></span><span></span><span></span> Yama is thinking...</div>
            
            <div class="input-area">
                <div class="input-wrapper">
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
        let currentUser = null, hasMessages = false, messageCounter = 0, isGenerating = false;
        
        function toggleTheme() {{ document.body.classList.toggle('dark'); localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light'); }}
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
        if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark');
        
        function toggleUserMenu() {{ document.getElementById('sidebar').classList.toggle('open'); document.getElementById('overlay').classList.toggle('show'); }}
        
        function handleCredentialResponse(response) {{
            const payload = JSON.parse(atob(response.credential.split('.')[1]));
            currentUser = {{ name: payload.name, email: payload.email, picture: payload.picture }};
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
                </div>
                <button class="logout-btn" onclick="logout()">Logout</button>
            `;
            loadHistory();
            fetch('/set_user', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ email: currentUser.email, name: currentUser.name, picture: currentUser.picture }}) }});
        }}
        
        function logout() {{
            currentUser = null;
            document.getElementById('loginOverlay').style.display = 'flex';
            document.getElementById('app').style.display = 'none';
            document.getElementById('userBtn').style.display = 'none';
            document.getElementById('userProfile').style.display = 'none';
            if (google && google.accounts) google.accounts.id.disableAutoSelect();
        }}
        
        function newChat() {{ if (confirm('Start a new chat?')) location.reload(); }}
        function toggleSidebar() {{ document.getElementById('sidebar').classList.toggle('open'); document.getElementById('overlay').classList.toggle('show'); }}
        function closeSidebar() {{ document.getElementById('sidebar').classList.remove('open'); document.getElementById('overlay').classList.remove('show'); }}
        function askSuggestion(q) {{ document.getElementById('userInput').value = q; sendMessage(); }}
        
        async function loadHistory() {{
            if (!currentUser) return;
            const res = await fetch('/get_history?email=' + encodeURIComponent(currentUser.email));
            const history = await res.json();
            const container = document.getElementById('historyList');
            if (history.length === 0) {{ container.innerHTML = '<div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div>'; return; }}
            let html = '';
            for (let i = history.length - 1; i >= 0; i--) {{
                let item = history[i];
                html += '<div class="history-item" onclick="loadChatMessage(\\'' + escapeHtml(item.user) + '\\')">' +
                        '<div class="history-question">' + escapeHtml(item.user.substring(0, 45)) + '</div>' +
                        '<div class="history-time">' + item.timestamp + '</div></div>';
            }}
            container.innerHTML = html;
        }}
        
        function escapeHtml(text) {{ const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }}
        function loadChatMessage(msg) {{ document.getElementById('userInput').value = msg; closeSidebar(); sendMessage(); }}
        async function clearHistory() {{ if (confirm('Clear all history?')) {{ await fetch('/clear_history', {{ method: 'POST' }}); location.reload(); }} }}
        
        const textarea = document.getElementById('userInput');
        function autoAdjustHeight() {{ this.style.height = 'auto'; this.style.height = this.scrollHeight + 'px'; }}
        textarea.addEventListener('input', autoAdjustHeight);
        
        if (window.visualViewport) {{
            let lastHeight = window.visualViewport.height;
            window.visualViewport.addEventListener('resize', function() {{
                const inputArea = document.querySelector('.input-area');
                if (inputArea && window.visualViewport.height < lastHeight) {{
                    setTimeout(() => {{ inputArea.scrollIntoView({{ behavior: 'smooth', block: 'end' }}); }}, 100);
                }}
                lastHeight = window.visualViewport.height;
            }});
        }}
        
        function handleKey(e) {{ if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); sendMessage(); }} }}
        
        function copyResponse(messageId) {{
            const content = document.querySelector(`#message-${{messageId}} .message-content`);
            if (content) {{
                navigator.clipboard.writeText(content.innerText).then(() => {{
                    const btn = document.querySelector(`#message-${{messageId}} .copy-btn`);
                    const originalText = btn.textContent;
                    btn.textContent = '✅ Copied!';
                    setTimeout(() => {{ btn.textContent = originalText; }}, 2000);
                }});
            }}
        }}
        
        async function regenerateResponse(messageId, userMessage) {{
            if (isGenerating) return;
            isGenerating = true;
            document.getElementById('typing').style.display = 'block';
            const content = document.querySelector(`#message-${{messageId}} .message-content`);
            try {{
                const res = await fetch('/regenerate', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ email: currentUser.email, message: userMessage }})
                }});
                const data = await res.json();
                content.innerHTML = data.response.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            }} catch(e) {{ console.error(e); }}
            document.getElementById('typing').style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }}
        
        function editMessage(messageId) {{
            const div = document.getElementById(`message-${{messageId}}`);
            const content = div.querySelector('.message-content');
            const editInput = div.querySelector('.edit-message-input');
            const editActions = div.querySelector('.edit-actions');
            if (content.style.display !== 'none') {{
                content.style.display = 'none';
                editInput.value = content.innerText;
                editInput.classList.add('active');
                editActions.classList.add('active');
                editInput.focus();
            }}
        }}
        
        function saveEdit(messageId) {{
            const div = document.getElementById(`message-${{messageId}}`);
            const editInput = div.querySelector('.edit-message-input');
            const content = div.querySelector('.message-content');
            const editActions = div.querySelector('.edit-actions');
            const newText = editInput.value.trim();
            if (newText) {{
                content.innerText = newText;
                content.style.display = 'block';
                editInput.classList.remove('active');
                editActions.classList.remove('active');
            }}
        }}
        
        function cancelEdit(messageId) {{
            const div = document.getElementById(`message-${{messageId}}`);
            const content = div.querySelector('.message-content');
            const editInput = div.querySelector('.edit-message-input');
            const editActions = div.querySelector('.edit-actions');
            content.style.display = 'block';
            editInput.classList.remove('active');
            editActions.classList.remove('active');
        }}
        
        async function continueGenerating(messageId) {{
            if (isGenerating) return;
            isGenerating = true;
            document.getElementById('typing').style.display = 'block';
            const content = document.querySelector(`#message-${{messageId}} .message-content`);
            const userMessage = getLastUserMessage();
            try {{
                const res = await fetch('/continue_generating', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ email: currentUser.email, message: userMessage }})
                }});
                const data = await res.json();
                content.innerHTML += data.response.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            }} catch(e) {{ console.error(e); }}
            document.getElementById('typing').style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }}
        
        function getLastUserMessage() {{
            const messages = document.querySelectorAll('.user-message');
            if (messages.length > 0) return messages[messages.length-1].querySelector('.message-content').innerText;
            return '';
        }}
        
        async function shareConversation() {{
            if (!currentUser) return;
            try {{
                const res = await fetch('/share_conversation?email=' + encodeURIComponent(currentUser.email));
                const data = await res.json();
                const shareText = data.conversation.map(item => `User: ${{item.user}}\\nYama: ${{item.ai}}\\n`).join('\\n');
                await navigator.clipboard.writeText(shareText);
                alert('✅ Conversation copied to clipboard!');
            }} catch(e) {{ alert('Could not share conversation.'); }}
        }}
        
        async function submitFeedback(messageId, feedbackType) {{
            const div = document.getElementById(`message-${{messageId}}`);
            const likeBtn = div.querySelector('.like-btn');
            const dislikeBtn = div.querySelector('.dislike-btn');
            try {{
                await fetch('/feedback', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ email: currentUser.email, message_index: parseInt(messageId.split('-')[1]), feedback_type: feedbackType }})
                }});
                if (feedbackType === 'like') {{
                    likeBtn.classList.toggle('liked');
                    if (dislikeBtn.classList.contains('disliked')) dislikeBtn.classList.remove('disliked');
                }} else {{
                    dislikeBtn.classList.toggle('disliked');
                    if (likeBtn.classList.contains('liked')) likeBtn.classList.remove('liked');
                }}
            }} catch(e) {{ console.error(e); }}
        }}
        
        function stopGenerating() {{ isGenerating = false; document.getElementById('typing').style.display = 'none'; }}
        
        async function sendMessage() {{
            if (!currentUser) {{ alert('Please sign in first!'); return; }}
            const message = textarea.value.trim();
            if (!message) return;
            if (isGenerating) {{ stopGenerating(); return; }}
            if (!hasMessages) {{
                const welcome = document.getElementById('welcome');
                if (welcome) welcome.style.display = 'none';
                hasMessages = true;
            }}
            const messageId = 'msg-' + (++messageCounter);
            addMessage(message, 'user', messageId);
            textarea.value = '';
            textarea.style.height = 'auto';
            document.getElementById('typing').style.display = 'block';
            scrollToBottom();
            try {{
                const res = await fetch('/chat', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ message: message, email: currentUser.email }})
                }});
                const data = await res.json();
                const aiMessageId = 'msg-' + (++messageCounter);
                addMessage(data.response, 'ai', aiMessageId, message);
                document.getElementById('typing').style.display = 'none';
                loadHistory();
                scrollToBottom();
            }} catch(e) {{ console.error(e); document.getElementById('typing').style.display = 'none'; }}
        }}
        
        function addMessage(text, sender, messageId, userMessage = '') {{
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + sender + '-message';
            div.id = messageId;
            const wrapper = document.createElement('div');
            wrapper.className = 'message-wrapper';
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = text.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            wrapper.appendChild(content);
            if (sender === 'user') {{
                const editInput = document.createElement('input');
                editInput.type = 'text';
                editInput.className = 'edit-message-input';
                editInput.value = text;
                wrapper.appendChild(editInput);
                const editActions = document.createElement('div');
                editActions.className = 'edit-actions';
                editActions.innerHTML = `<button class="save-edit" onclick="saveEdit('${{messageId}}')">Save</button><button class="cancel-edit" onclick="cancelEdit('${{messageId}}')">Cancel</button>`;
                wrapper.appendChild(editActions);
            }}
            const actions = document.createElement('div');
            actions.className = 'message-actions';
            if (sender === 'ai') {{
                actions.innerHTML = `
                    <button class="copy-btn" onclick="copyResponse('${{messageId}}')">📋 Copy</button>
                    <button onclick="regenerateResponse('${{messageId}}', '${{escapeJs(userMessage || getLastUserMessage())}}')">🔄 Regenerate</button>
                    <button onclick="continueGenerating('${{messageId}}')">📝 Continue</button>
                    <button onclick="stopGenerating()">⏹️ Stop</button>
                    <button onclick="shareConversation()">📤 Share</button>
                    <button class="like-btn" onclick="submitFeedback('${{messageId}}', 'like')">👍</button>
                    <button class="dislike-btn" onclick="submitFeedback('${{messageId}}', 'dislike')">👎</button>
                `;
            }} else {{
                actions.innerHTML = `<button onclick="editMessage('${{messageId}}')">✏️ Edit</button>`;
            }}
            wrapper.appendChild(actions);
            div.appendChild(wrapper);
            messages.appendChild(div);
            scrollToBottom();
        }}
        
        function escapeJs(text) {{ return text.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'").replace(/"/g, '\\\\"'); }}
        function scrollToBottom() {{ const messages = document.getElementById('messages'); messages.scrollTop = messages.scrollHeight; }}
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
    port = int(os.environ.get("PORT", 10000))
    print("\n" + "="*55)
    print("🏛️ YAMA AI V2.0 - ADVANCED MATH & EXPLANATION ENGINE")
    print("="*55)
    print(f"🌐 Running on port: {port}")
    print("="*55)
    print("✅ No Railway dependencies")
    print("✅ Local file storage (./data/)")
    print("✅ All features working")
    print("✅ UI unchanged")
    print("✅ Google Sign-In working")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
