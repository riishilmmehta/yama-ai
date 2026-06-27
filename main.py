from fastapi import FastAPI, Request, HTTPException
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
import time
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
import sympy as sp
from sympy import symbols, Eq, solve, diff, integrate, limit, Matrix, sin, cos, tan, asin, acos, atan, log, ln, exp, sqrt, cbrt, factorial, pi, E, I, oo
from sympy.parsing.sympy_parser import parse_expr
import mpmath as mp
import numpy as np
from urllib.parse import urlparse
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

app = FastAPI(title="Yama AI")

# ============ USER DATABASE ============
user_db = TinyDB('users.json')
User = Query()

# ============ MESSAGE FEATURES STORAGE ============
message_feedback_db = TinyDB('feedback.json')
Feedback = Query()

# ============ LEARNING DASHBOARD ============
learning_db = TinyDB('learning.json')
Learning = Query()

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
    
    def clear_context(self, email: str):
        if email in self._contexts:
            self._contexts[email] = []

context_memory = ContextMemory()

# ============ SOURCE VALIDATION SYSTEM (Phase 17.1) ============
class SourceValidator:
    def __init__(self):
        self.validated_cache = {}
        self.broken_urls = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def validate_url(self, url: str, timeout: int = 5) -> Dict[str, Any]:
        """Validate if URL is accessible and returns valid content"""
        # Check cache
        url_hash = hashlib.md5(url.encode()).hexdigest()
        if url_hash in self.validated_cache:
            cache_entry = self.validated_cache[url_hash]
            # Cache for 1 hour
            if datetime.now() - cache_entry['timestamp'] < timedelta(hours=1):
                return cache_entry['result']
        
        result = {
            'valid': False,
            'status_code': None,
            'title': None,
            'content_length': 0,
            'error': None,
            'redirects': False
        }
        
        try:
            response = self.session.head(url, timeout=timeout, allow_redirects=True)
            result['status_code'] = response.status_code
            
            # Check if redirected
            if response.history:
                result['redirects'] = True
                final_url = response.url
                if final_url != url:
                    # Check if final URL is valid
                    final_check = self.validate_url(final_url, timeout)
                    if final_check['valid']:
                        result['valid'] = True
                        result['redirects'] = True
                        result['final_url'] = final_url
                        # Cache and return
                        self.validated_cache[url_hash] = {
                            'result': result,
                            'timestamp': datetime.now()
                        }
                        return result
            
            # Check status code
            if response.status_code == 200:
                # Get content length
                result['content_length'] = int(response.headers.get('Content-Length', 0))
                
                # If content length is too small, try GET
                if result['content_length'] < 100:
                    get_response = self.session.get(url, timeout=timeout, stream=True)
                    if get_response.status_code == 200:
                        content = get_response.text[:500]
                        soup = BeautifulSoup(content, 'html.parser')
                        title = soup.find('title')
                        if title and title.get_text().strip():
                            result['title'] = title.get_text().strip()
                            result['valid'] = True
                            result['content_length'] = len(content)
                else:
                    result['valid'] = True
                    
                # Try to get title if not already set
                if result['valid'] and not result['title']:
                    try:
                        get_response = self.session.get(url, timeout=timeout, stream=True)
                        if get_response.status_code == 200:
                            content = get_response.text[:1000]
                            soup = BeautifulSoup(content, 'html.parser')
                            title = soup.find('title')
                            if title:
                                result['title'] = title.get_text().strip()
                    except:
                        pass
            else:
                result['error'] = f"Status code: {response.status_code}"
                
        except requests.exceptions.Timeout:
            result['error'] = "Timeout"
        except requests.exceptions.ConnectionError:
            result['error'] = "Connection error"
        except Exception as e:
            result['error'] = str(e)
        
        # Cache result
        self.validated_cache[url_hash] = {
            'result': result,
            'timestamp': datetime.now()
        }
        
        if not result['valid']:
            self.broken_urls.add(url)
        
        return result
    
    def is_broken(self, url: str) -> bool:
        """Check if URL is known to be broken"""
        return url in self.broken_urls
    
    def validate_sources(self, sources: List[Dict]) -> List[Dict]:
        """Validate multiple sources and return only valid ones"""
        validated_sources = []
        
        for source in sources:
            url = source.get('url', '')
            if not url:
                continue
            
            # Skip if known broken
            if self.is_broken(url):
                continue
            
            # Validate
            validation = self.validate_url(url)
            if validation['valid']:
                source['validation'] = validation
                source['valid'] = True
                if validation.get('title'):
                    source['page_title'] = validation['title']
                validated_sources.append(source)
            else:
                source['valid'] = False
                source['validation_error'] = validation.get('error', 'Unknown error')
                # Log broken source for learning
                log_broken_source(url, validation.get('error', 'Unknown error'))
        
        return validated_sources

source_validator = SourceValidator()

# ============ TRUSTED SOURCE RANKING (Phase 17.2) ============
def get_trust_score(url: str) -> Tuple[int, str, str]:
    """Get trust score for a URL"""
    url_lower = url.lower()
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Highest Priority - 90-100
    if any(domain.endswith(ext) for ext in ['.gov', '.gov.uk', '.gov.in']):
        return 100, "Government", "Highest"
    if any(domain.endswith(ext) for ext in ['.edu', '.ac.uk', '.ac.in']):
        return 95, "Educational", "Highest"
    if any(keyword in domain for keyword in ['researchgate', 'arxiv', 'pubmed', 'sciencedirect', 'springer', 'ieee', 'nature', 'science']):
        return 95, "Scientific Journal", "Highest"
    
    # High Priority - 80-89
    if any(domain.endswith(ext) for ext in ['.org', '.org.uk']):
        return 85, "Organization", "High"
    if any(keyword in domain for keyword in ['wikipedia', 'britannica', 'encyclopedia']):
        return 85, "Encyclopedia", "High"
    if any(keyword in domain for keyword in ['microsoft', 'apple', 'google', 'amazon', 'facebook', 'twitter', 'github', 'stackoverflow']):
        return 85, "Official Company", "High"
    
    # Medium Priority - 60-79
    if any(keyword in domain for keyword in ['nytimes', 'washingtonpost', 'bbc', 'cnn', 'reuters', 'apnews', 'bloomberg', 'wsj', 'theguardian', 'economist']):
        return 80, "Major News", "Medium"
    if any(keyword in domain for keyword in ['blog', 'medium', 'wordpress', 'substack']):
        return 60, "Blog", "Medium"
    
    # Lower Priority - Below 60
    if any(keyword in domain for keyword in ['forum', 'reddit', 'quora', 'yahoo']):
        return 40, "Forum", "Low"
    
    # Unknown
    return 50, "Website", "Medium"

# ============ MULTI-SOURCE VERIFICATION (Phase 17.3) ============
def verify_factual_claim(claim: str, sources: List[Dict]) -> Dict[str, Any]:
    """Verify factual claims across multiple sources"""
    if not sources:
        return {'verified': False, 'confidence': 0, 'matches': 0}
    
    # Extract key claims from sources
    claims_from_sources = []
    for source in sources:
        snippet = source.get('snippet', '').lower()
        # Extract potential factual statements
        statements = re.findall(r'[^.!?]*[.!?]', snippet)
        for stmt in statements:
            if len(stmt.strip()) > 20:
                claims_from_sources.append(stmt.strip())
    
    # Count matching claims
    claim_hash = hashlib.md5(claim.lower().encode()).hexdigest()
    matches = 0
    
    for source_claim in claims_from_sources[:10]:
        # Simple similarity check
        if claim.lower() in source_claim or source_claim in claim.lower():
            matches += 1
    
    total_sources = len(sources)
    match_ratio = matches / total_sources if total_sources > 0 else 0
    
    # Determine verification status
    if match_ratio >= 0.7:
        status = "verified"
        confidence = 90 + (match_ratio * 10)
    elif match_ratio >= 0.4:
        status = "partially_verified"
        confidence = 60 + (match_ratio * 30)
    else:
        status = "unverified"
        confidence = 30 + (match_ratio * 30)
    
    return {
        'verified': status == 'verified',
        'status': status,
        'confidence': round(min(100, confidence), 1),
        'matches': matches,
        'total_sources': total_sources,
        'match_ratio': match_ratio
    }

# ============ CONFIDENCE SCORING (Phase 17.4) ============
def calculate_confidence(source_quality: float, validation_results: List, agreement: Dict, verification: Dict) -> Dict[str, Any]:
    """Calculate overall confidence score"""
    
    # Source quality factor (40%)
    quality_factor = source_quality / 100 * 0.4
    
    # Validation factor (20%)
    valid_sources = sum(1 for v in validation_results if v.get('valid', False))
    total_sources = len(validation_results) if validation_results else 1
    validation_factor = (valid_sources / total_sources) * 0.2
    
    # Agreement factor (20%)
    agreement_factor = (agreement.get('confidence', 0) / 100) * 0.2
    
    # Verification factor (20%)
    verification_factor = (verification.get('confidence', 0) / 100) * 0.2
    
    total_confidence = (quality_factor + validation_factor + agreement_factor + verification_factor) * 100
    total_confidence = min(100, max(0, total_confidence))
    
    # Determine level
    if total_confidence >= 95:
        level = "Very High"
        description = "Extremely reliable. Multiple high-quality, verified sources confirm this information."
    elif total_confidence >= 80:
        level = "High"
        description = "Reliable. Good quality sources with consistent information."
    elif total_confidence >= 60:
        level = "Moderate"
        description = "Moderately reliable. Sources are available but quality varies."
    else:
        level = "Low"
        description = "Low confidence. Limited or conflicting sources. Please verify independently."
    
    return {
        'score': round(total_confidence, 1),
        'level': level,
        'description': description,
        'factors': {
            'source_quality': round(quality_factor * 100, 1),
            'validation': round(validation_factor * 100, 1),
            'agreement': round(agreement_factor * 100, 1),
            'verification': round(verification_factor * 100, 1)
        }
    }

# ============ MATHEMATICS ENGINE ============
class MathematicsEngine:
    def __init__(self):
        self.precision = 15
        mp.mp.dps = self.precision
    
    def detect_math_intent(self, query: str) -> Dict[str, Any]:
        query_lower = query.lower().strip()
        
        math_patterns = {
            'arithmetic': r'[\d\+\-\*\/\(\)\.\^\%\s]+',
            'equation': r'[a-zA-Z]+\s*[\+\-\*\/\^]?\s*[=]',
            'solve': r'(solve|find|calculate|what is|evaluate|compute)',
            'derivative': r'(derivative|differentiate|diff|d/dx)',
            'integral': r'(integral|integrate|∫)',
            'limit': r'(limit|lim)',
            'matrix': r'(matrix|determinant|eigenvalue|eigenvector)',
            'statistics': r'(mean|median|mode|variance|std|standard deviation|correlation)',
            'probability': r'(probability|permutation|combination|binomial|normal distribution)',
            'trigonometry': r'(sin|cos|tan|asin|acos|atan|csc|sec|cot)',
            'unit_conversion': r'(convert|to|in|from|meters|kilometers|miles|feet|inches|kg|grams|pounds|liters|gallons|°c|°f|kelvin|celsius|fahrenheit)',
            'graph': r'(graph|plot|chart|visualize|draw)',
            'step_by_step': r'(step|explain|show work|solution steps|detailed)'
        }
        
        detected_types = []
        for math_type, pattern in math_patterns.items():
            if re.search(pattern, query_lower, re.IGNORECASE):
                detected_types.append(math_type)
        
        if re.search(r'[\d]+\s*[\+\-\*\/]\s*[\d]+', query):
            if 'arithmetic' not in detected_types:
                detected_types.append('arithmetic')
        
        if re.search(r'[a-zA-Z]\s*[=]', query):
            if 'equation' not in detected_types:
                detected_types.append('equation')
        
        return {
            'is_math': len(detected_types) > 0,
            'types': detected_types,
            'raw_query': query
        }
    
    def parse_natural_language(self, query: str) -> str:
        query_clean = query.lower().strip()
        
        number_words = {
            'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
            'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
            'ten': '10', 'twenty': '20', 'thirty': '30', 'forty': '40', 'fifty': '50',
            'sixty': '60', 'seventy': '70', 'eighty': '80', 'ninety': '90',
            'hundred': '100', 'thousand': '1000', 'million': '1000000'
        }
        
        for word, digit in number_words.items():
            query_clean = query_clean.replace(word, digit)
        
        replacements = {
            'plus': '+', 'minus': '-', 'times': '*', 'multiplied by': '*',
            'divided by': '/', 'over': '/', 'square': '**2', 'cube': '**3',
            'squared': '**2', 'cubed': '**3', 'to the power of': '**',
            'power': '**', 'equals': '=', 'is equal to': '=', 'is': '=',
            'x': 'x', 'y': 'y', 'z': 'z', 'theta': 'θ', 'pi': 'π',
            'sqrt': 'sqrt', 'root': 'sqrt', 'log': 'log', 'ln': 'ln',
            'sin': 'sin', 'cos': 'cos', 'tan': 'tan',
            'asin': 'asin', 'acos': 'acos', 'atan': 'atan',
            'derivative of': 'd/dx', 'integral of': '∫', 'limit of': 'lim'
        }
        
        for word, replacement in replacements.items():
            query_clean = query_clean.replace(word, replacement)
        
        remove_words = ['what', 'is', 'the', 'of', 'find', 'calculate', 'compute', 'solve', 'evaluate']
        for word in remove_words:
            query_clean = query_clean.replace(word, '').strip()
        
        return query_clean
    
    def solve_arithmetic(self, expression: str) -> Dict[str, Any]:
        try:
            expr = expression.replace('×', '*').replace('÷', '/').replace('x', '*')
            result = parse_expr(expr)
            if result.is_number:
                return {
                    'success': True,
                    'result': float(result),
                    'result_str': str(result.evalf(self.precision)),
                    'is_exact': result.is_Integer
                }
        except:
            pass
        
        try:
            result = mp.mpf(expression)
            return {
                'success': True,
                'result': float(result),
                'result_str': str(result),
                'is_exact': False
            }
        except:
            return {'success': False, 'error': 'Could not parse arithmetic expression'}
    
    def solve_equation(self, equation: str) -> Dict[str, Any]:
        try:
            if '=' in equation:
                left, right = equation.split('=')
                left_expr = parse_expr(left.strip())
                right_expr = parse_expr(right.strip())
                expr = left_expr - right_expr
            else:
                expr = parse_expr(equation)
            
            variables = list(expr.free_symbols)
            if not variables:
                return self.solve_arithmetic(equation)
            
            var = variables[0]
            solutions = solve(expr, var)
            
            if solutions:
                result_strs = []
                for sol in solutions:
                    if sol.is_number:
                        result_strs.append(str(sol.evalf(self.precision)))
                    else:
                        result_strs.append(str(sol))
                
                return {
                    'success': True,
                    'variable': str(var),
                    'solutions': result_strs,
                    'solutions_count': len(solutions),
                    'result_str': ', '.join(result_strs) if len(result_strs) > 1 else result_strs[0],
                    'solution_type': 'multiple' if len(solutions) > 1 else 'single'
                }
            else:
                return {'success': False, 'error': 'No real solutions found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def solve_calculus(self, query: str, expression: str) -> Dict[str, Any]:
        try:
            expr = parse_expr(expression)
            var = symbols('x')
            
            for sym in expr.free_symbols:
                var = sym
                break
            
            if 'derivative' in query or 'diff' in query or 'd/dx' in query:
                result = diff(expr, var)
                return {
                    'success': True,
                    'type': 'derivative',
                    'expression': str(expr),
                    'result': str(result),
                    'result_str': str(result)
                }
            elif 'integral' in query or '∫' in query:
                result = integrate(expr, var)
                return {
                    'success': True,
                    'type': 'integral',
                    'expression': str(expr),
                    'result': str(result),
                    'result_str': str(result) + ' + C'
                }
            elif 'limit' in query or 'lim' in query:
                limit_point = 0
                limit_match = re.search(r'->\s*([\d]+)', query)
                if limit_match:
                    limit_point = float(limit_match.group(1))
                result = limit(expr, var, limit_point)
                return {
                    'success': True,
                    'type': 'limit',
                    'expression': str(expr),
                    'limit_point': limit_point,
                    'result': str(result),
                    'result_str': str(result)
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
        
        return {'success': False, 'error': 'Could not parse calculus expression'}
    
    def solve_trigonometry(self, expression: str) -> Dict[str, Any]:
        try:
            expr = parse_expr(expression)
            result = expr.evalf(self.precision)
            return {
                'success': True,
                'expression': str(expr),
                'result': float(result),
                'result_str': str(result)
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def solve_matrix(self, query: str) -> Dict[str, Any]:
        try:
            matrix_match = re.search(r'\[(.*?)\]', query)
            if not matrix_match:
                return {'success': False, 'error': 'Could not find matrix in query'}
            
            matrix_str = matrix_match.group(1)
            rows = []
            for row in matrix_str.split(';'):
                row_values = [float(x.strip()) for x in row.split(',')]
                rows.append(row_values)
            
            matrix = Matrix(rows)
            
            if 'determinant' in query or 'det' in query:
                result = matrix.det()
                return {
                    'success': True,
                    'operation': 'determinant',
                    'matrix': str(matrix),
                    'result': float(result),
                    'result_str': str(result)
                }
            elif 'inverse' in query:
                result = matrix.inv()
                return {
                    'success': True,
                    'operation': 'inverse',
                    'matrix': str(matrix),
                    'result': str(result),
                    'result_str': str(result)
                }
            elif 'transpose' in query:
                result = matrix.T
                return {
                    'success': True,
                    'operation': 'transpose',
                    'matrix': str(matrix),
                    'result': str(result),
                    'result_str': str(result)
                }
            elif 'eigenvalue' in query or 'eigen' in query:
                eigenvals = matrix.eigenvals()
                return {
                    'success': True,
                    'operation': 'eigenvalues',
                    'matrix': str(matrix),
                    'result': str(eigenvals),
                    'result_str': str(eigenvals)
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
        
        return {'success': False, 'error': 'Could not parse matrix expression'}
    
    def solve_statistics(self, query: str, numbers: List[float]) -> Dict[str, Any]:
        try:
            if not numbers:
                return {'success': False, 'error': 'No numbers provided'}
            
            n = len(numbers)
            mean = sum(numbers) / n
            variance = sum((x - mean) ** 2 for x in numbers) / n
            std_dev = variance ** 0.5
            sorted_nums = sorted(numbers)
            
            if n % 2 == 0:
                median = (sorted_nums[n//2 - 1] + sorted_nums[n//2]) / 2
            else:
                median = sorted_nums[n//2]
            
            from collections import Counter
            counter = Counter(numbers)
            mode = [k for k, v in counter.items() if v == max(counter.values())]
            
            result = {}
            if 'mean' in query or 'average' in query:
                result['mean'] = mean
            if 'median' in query:
                result['median'] = median
            if 'mode' in query:
                result['mode'] = mode
            if 'variance' in query or 'var' in query:
                result['variance'] = variance
            if 'std' in query or 'standard deviation' in query:
                result['standard_deviation'] = std_dev
            
            result_str = ', '.join([f"{k}: {v}" for k, v in result.items()])
            
            return {
                'success': True,
                'operation': 'statistics',
                'data_points': n,
                'results': result,
                'result_str': result_str
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def solve_unit_conversion(self, query: str) -> Dict[str, Any]:
        try:
            conversion_pattern = r'(\d+\.?\d*)\s*([a-zA-Z]+)\s*(?:to|in|from)\s*([a-zA-Z]+)'
            match = re.search(conversion_pattern, query, re.IGNORECASE)
            
            if not match:
                return {'success': False, 'error': 'Could not parse conversion'}
            
            value = float(match.group(1))
            from_unit = match.group(2).lower()
            to_unit = match.group(3).lower()
            
            conversions = {
                'length': {
                    'meters': 1, 'kilometers': 1000, 'miles': 1609.34,
                    'feet': 0.3048, 'inches': 0.0254, 'centimeters': 0.01,
                    'millimeters': 0.001
                },
                'weight': {
                    'kilograms': 1, 'grams': 0.001, 'pounds': 0.453592,
                    'ounces': 0.0283495
                },
                'volume': {
                    'liters': 1, 'gallons': 3.78541, 'milliliters': 0.001,
                    'cubic_meters': 1000
                },
                'temperature': {
                    'celsius': 'celsius', 'fahrenheit': 'fahrenheit',
                    'kelvin': 'kelvin'
                }
            }
            
            from_unit_found = None
            to_unit_found = None
            category = None
            
            for cat, units in conversions.items():
                if from_unit in units:
                    from_unit_found = units[from_unit]
                    category = cat
                if to_unit in units:
                    to_unit_found = units[to_unit]
            
            if category == 'temperature':
                result = self.convert_temperature(value, from_unit, to_unit)
                return {
                    'success': True,
                    'operation': 'unit_conversion',
                    'from_value': value,
                    'from_unit': from_unit,
                    'to_unit': to_unit,
                    'result': result,
                    'result_str': f"{value} {from_unit} = {result} {to_unit}"
                }
            elif from_unit_found and to_unit_found:
                result = value * (from_unit_found / to_unit_found)
                return {
                    'success': True,
                    'operation': 'unit_conversion',
                    'from_value': value,
                    'from_unit': from_unit,
                    'to_unit': to_unit,
                    'result': result,
                    'result_str': f"{value} {from_unit} = {result} {to_unit}"
                }
            
            return {'success': False, 'error': 'Unsupported conversion'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def convert_temperature(self, value: float, from_unit: str, to_unit: str) -> float:
        from_unit = from_unit.lower()
        to_unit = to_unit.lower()
        
        if from_unit in ['celsius', 'c']:
            celsius = value
        elif from_unit in ['fahrenheit', 'f']:
            celsius = (value - 32) * 5/9
        elif from_unit in ['kelvin', 'k']:
            celsius = value - 273.15
        else:
            return value
        
        if to_unit in ['celsius', 'c']:
            return celsius
        elif to_unit in ['fahrenheit', 'f']:
            return celsius * 9/5 + 32
        elif to_unit in ['kelvin', 'k']:
            return celsius + 273.15
        else:
            return value
    
    def extract_numbers(self, text: str) -> List[float]:
        numbers = re.findall(r'\d+\.?\d*', text)
        return [float(num) for num in numbers]
    
    def solve_math(self, query: str) -> Dict[str, Any]:
        detection = self.detect_math_intent(query)
        
        if not detection['is_math']:
            return {'success': False, 'is_math': False}
        
        parsed = self.parse_natural_language(query)
        step_by_step = 'step' in query.lower() or 'explain' in query.lower()
        graph_requested = 'graph' in query.lower() or 'plot' in query.lower()
        
        result = {'success': False, 'is_math': True}
        
        if 'arithmetic' in detection['types']:
            arith_result = self.solve_arithmetic(parsed)
            if arith_result['success']:
                result = arith_result
                result['type'] = 'arithmetic'
        
        if 'equation' in detection['types'] and not result['success']:
            eq_result = self.solve_equation(parsed)
            if eq_result['success']:
                result = eq_result
                result['type'] = 'equation'
        
        if ('derivative' in detection['types'] or 'integral' in detection['types'] or 'limit' in detection['types']):
            calc_result = self.solve_calculus(query, parsed)
            if calc_result['success']:
                result = calc_result
        
        if 'trigonometry' in detection['types'] and not result['success']:
            trig_result = self.solve_trigonometry(parsed)
            if trig_result['success']:
                result = trig_result
                result['type'] = 'trigonometry'
        
        if 'matrix' in detection['types'] and not result['success']:
            matrix_result = self.solve_matrix(query)
            if matrix_result['success']:
                result = matrix_result
        
        if 'unit_conversion' in detection['types'] and not result['success']:
            conv_result = self.solve_unit_conversion(query)
            if conv_result['success']:
                result = conv_result
        
        if 'statistics' in detection['types'] and not result['success']:
            numbers = self.extract_numbers(query)
            if numbers:
                stat_result = self.solve_statistics(query, numbers)
                if stat_result['success']:
                    result = stat_result
        
        if step_by_step and result['success']:
            result['step_by_step'] = True
        
        if graph_requested and result['success']:
            result['graph'] = True
        
        return result

math_engine = MathematicsEngine()

# ============ SEARCH IMPROVEMENTS ============
def search_web_improved(query: str, max_results: int = 10) -> List[Dict]:
    results = []
    try:
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))
            
            for r in search_results:
                url = r.get('href', '')
                trust_score, category, trust_level = get_trust_score(url)
                
                results.append({
                    "title": r.get('title', ''),
                    "snippet": r.get('body', '')[:300],
                    "url": url,
                    "trust_score": trust_score,
                    "trust_category": category,
                    "trust_level": trust_level,
                    "content_richness": min(1.0, len(r.get('body', '')) / 500)
                })
            
            seen_urls = set()
            unique_results = []
            for r in results:
                if r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    unique_results.append(r)
            
            # Sort by trust score and richness
            unique_results.sort(key=lambda x: (x['trust_score'] + x['content_richness'] * 50), reverse=True)
            return unique_results
    except Exception as e:
        print(f"Search error: {e}")
        return []

def read_full_webpage_improved(url: str) -> Optional[str]:
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
            element.decompose()
        
        for element in soup.find_all(class_=re.compile(r'(ad|popup|modal|banner|cookie|newsletter|subscribe)', re.I)):
            element.decompose()
        
        for element in soup.find_all(['ul', 'ol']):
            if element.find_parent(['nav', 'header']):
                element.decompose()
        
        content_parts = []
        article = soup.find('article') or soup.find('main')
        
        if article:
            for p in article.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 50 and not any(x in text.lower() for x in ['advertisement', 'sponsored', 'cookie']):
                    content_parts.append(text)
        else:
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 50 and not any(x in text.lower() for x in ['advertisement', 'sponsored', 'cookie']):
                    content_parts.append(text)
        
        for h in soup.find_all(['h1', 'h2', 'h3']):
            text = h.get_text(strip=True)
            if len(text) > 10:
                content_parts.append(f"**{text}**")
        
        for li in soup.find_all('li'):
            text = li.get_text(strip=True)
            if len(text) > 20:
                content_parts.append(f"• {text}")
        
        full_text = ' '.join(content_parts[:50])
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        return full_text[:3000]
    except:
        return None

# ============ MAKE URLS CLICKABLE ============
def make_urls_clickable(text: str) -> str:
    url_pattern = r'(https?://[^\s]+)'
    
    def replace_url(match):
        url = match.group(1)
        url_clean = url.rstrip('.,;:!?')
        return f'<a href="{url_clean}" target="_blank" rel="noopener noreferrer">{url_clean}</a>'
    
    return re.sub(url_pattern, replace_url, text)

# ============ FORMAT MATH ANSWER ============
def format_math_answer(result: Dict[str, Any], query: str) -> str:
    if not result['success']:
        return f"❌ Could not solve: {result.get('error', 'Unknown error')}\n\n💡 Try rephrasing your math question."
    
    math_type = result.get('type', 'arithmetic')
    
    if math_type == 'arithmetic':
        answer = f"🧮 **Calculation Result**\n\n"
        answer += f"**Answer:** {result['result_str']}\n"
        if result.get('is_exact', False):
            answer += f"✅ Exact value"
        else:
            answer += f"📊 Approximate value (rounded to {math_engine.precision} decimal places)"
        return answer
    
    elif math_type == 'equation':
        answer = f"📐 **Equation Solution**\n\n"
        answer += f"**Variable:** {result.get('variable', 'x')}\n"
        answer += f"**Solution{'s' if result.get('solutions_count', 1) > 1 else ''}:** {result['result_str']}\n"
        if result.get('solutions_count', 1) > 1:
            answer += f"\n📊 Found {result['solutions_count']} solutions"
        return answer
    
    elif math_type == 'derivative':
        answer = f"📈 **Derivative**\n\n"
        answer += f"**Original:** {result.get('expression', '')}\n"
        answer += f"**Derivative:** {result['result_str']}\n"
        return answer
    
    elif math_type == 'integral':
        answer = f"∫ **Integral**\n\n"
        answer += f"**Original:** {result.get('expression', '')}\n"
        answer += f"**Integral:** {result['result_str']}\n"
        return answer
    
    elif math_type == 'limit':
        answer = f"📊 **Limit**\n\n"
        answer += f"**Expression:** {result.get('expression', '')}\n"
        answer += f"**As x → {result.get('limit_point', 0)}:** {result['result_str']}\n"
        return answer
    
    elif math_type == 'trigonometry':
        answer = f"📐 **Trigonometric Calculation**\n\n"
        answer += f"**Expression:** {result.get('expression', '')}\n"
        answer += f"**Result:** {result['result_str']}\n"
        return answer
    
    elif math_type == 'matrix':
        answer = f"🔢 **Matrix Operation**\n\n"
        answer += f"**Operation:** {result.get('operation', 'matrix')}\n"
        answer += f"**Result:**\n{result['result_str']}\n"
        return answer
    
    elif math_type == 'unit_conversion':
        answer = f"📏 **Unit Conversion**\n\n"
        answer += f"**{result['result_str']}**\n"
        return answer
    
    elif math_type == 'statistics':
        answer = f"📊 **Statistical Calculation**\n\n"
        answer += f"**Data Points:** {result.get('data_points', 0)}\n"
        answer += f"**Results:**\n"
        for key, value in result.get('results', {}).items():
            answer += f"  • {key}: {value}\n"
        return answer
    
    return f"📊 **Result**\n\n{result.get('result_str', '')}"

# ============ LEARNING DASHBOARD (Phase 17.9) ============
def log_broken_source(url: str, error: str):
    """Log broken sources for learning"""
    learning_db.insert({
        'type': 'broken_source',
        'url': url,
        'error': error,
        'timestamp': datetime.now().isoformat()
    })

def log_wrong_answer(query: str, wrong_answer: str, correct_answer: str = None):
    """Log wrong answers for learning"""
    learning_db.insert({
        'type': 'wrong_answer',
        'query': query,
        'wrong_answer': wrong_answer,
        'correct_answer': correct_answer,
        'timestamp': datetime.now().isoformat()
    })

def log_feedback(email: str, feedback_type: str, message_index: int):
    """Log user feedback"""
    learning_db.insert({
        'type': 'user_feedback',
        'email': email,
        'feedback_type': feedback_type,
        'message_index': message_index,
        'timestamp': datetime.now().isoformat()
    })

def get_learning_metrics() -> Dict[str, Any]:
    """Get learning dashboard metrics"""
    broken_sources = learning_db.search(Learning.type == 'broken_source')
    wrong_answers = learning_db.search(Learning.type == 'wrong_answer')
    feedbacks = learning_db.search(Learning.type == 'user_feedback')
    
    total_broken = len(broken_sources)
    total_wrong = len(wrong_answers)
    total_feedback = len(feedbacks)
    
    likes = sum(1 for f in feedbacks if f.get('feedback_type') == 'like')
    dislikes = sum(1 for f in feedbacks if f.get('feedback_type') == 'dislike')
    
    satisfaction = (likes / total_feedback * 100) if total_feedback > 0 else 0
    
    return {
        'total_broken_sources': total_broken,
        'total_wrong_answers': total_wrong,
        'total_feedback': total_feedback,
        'likes': likes,
        'dislikes': dislikes,
        'user_satisfaction': f"{satisfaction:.1f}%",
        'recent_broken': broken_sources[-5:] if broken_sources else [],
        'recent_wrong': wrong_answers[-5:] if wrong_answers else []
    }

# ============ ANSWER QUALITY REVIEWER (Phase 17.6) ============
def review_answer(answer: str, query: str, sources: List[Dict], confidence: Dict) -> Dict[str, Any]:
    """Review answer quality before sending"""
    issues = []
    warnings = []
    
    # Check if answer is empty
    if not answer or len(answer.strip()) < 10:
        issues.append("Answer is too short or empty")
    
    # Check if answer contains hallucination markers
    hallucination_markers = ['probably', 'maybe', 'perhaps', 'could be', 'might be', 'i think']
    for marker in hallucination_markers:
        if marker in answer.lower():
            warnings.append(f"Contains uncertain language: '{marker}'")
    
    # Check if answer has sources
    if not sources:
        warnings.append("No sources provided")
    
    # Check confidence
    if confidence.get('score', 0) < 50:
        warnings.append(f"Low confidence score: {confidence.get('score', 0)}%")
    
    # Check if answer addresses the query
    query_words = set(query.lower().split())
    answer_words = set(answer.lower().split())
    overlap = len(query_words.intersection(answer_words))
    if overlap < 3 and len(query_words) > 3:
        warnings.append("Answer may not be directly relevant to the query")
    
    # Determine if answer passes review
    passes = len(issues) == 0
    
    return {
        'passes': passes,
        'issues': issues,
        'warnings': warnings,
        'quality_score': max(0, 100 - (len(issues) * 20) - (len(warnings) * 5)),
        'needs_regeneration': len(issues) > 0
    }

# ============ ADVANCED ANSWER GENERATION ============
def generate_advanced_answer(query: str, search_results: List[Dict], context_history: List[Dict]) -> str:
    # Check for math first
    math_result = math_engine.solve_math(query)
    if math_result.get('success', False):
        return format_math_answer(math_result, query)
    
    if not search_results:
        return f"I searched for '{query}' but found no results. Please try rephrasing your question."
    
    # Validate sources (Phase 17.1)
    validated_sources = source_validator.validate_sources(search_results[:10])
    
    if not validated_sources:
        return "⚠️ Found sources but none could be validated. Please try again with a different query."
    
    # Read valid sources
    source_contents = []
    for i, source in enumerate(validated_sources[:5]):
        url = source['url']
        content = read_full_webpage_improved(url)
        if content and len(content) > 100:
            source_contents.append({
                "url": url,
                "title": source.get('title', ''),
                "content": content,
                "trust_score": source.get('trust_score', 50),
                "trust_category": source.get('trust_category', 'Website'),
                "trust_level": source.get('trust_level', 'Medium')
            })
    
    # Multi-source verification (Phase 17.3)
    verification = verify_factual_claim(query, validated_sources)
    
    # Calculate agreement
    agreement = analyze_source_agreement(validated_sources)
    
    # Calculate confidence (Phase 17.4)
    avg_trust = sum(s.get('trust_score', 50) for s in validated_sources) / len(validated_sources) if validated_sources else 50
    validation_results = [s.get('validation', {}) for s in validated_sources]
    confidence = calculate_confidence(avg_trust, validation_results, agreement, verification)
    
    # Generate answer
    answer_parts = []
    
    # Quick Answer
    quick_answer = generate_quick_answer(query, source_contents)
    answer_parts.append(f"📌 **Quick Answer**\n{quick_answer}\n")
    
    # Detailed Explanation
    detailed_explanation = generate_detailed_explanation(query, source_contents)
    if detailed_explanation:
        answer_parts.append(f"📖 **Detailed Explanation**\n{detailed_explanation}\n")
    
    # Key Facts
    key_facts = generate_key_facts(query, source_contents)
    if key_facts:
        facts_text = "\n".join([f"• {fact}" for fact in key_facts[:5]])
        answer_parts.append(f"📊 **Key Facts**\n{facts_text}\n")
    
    # Analysis
    analysis = generate_analysis(query, source_contents, agreement)
    if analysis:
        answer_parts.append(f"🔍 **Analysis**\n{analysis}\n")
    
    # Confidence Score
    confidence_info = generate_confidence_info(confidence, verification)
    answer_parts.append(confidence_info)
    
    # Sources (only valid ones)
    source_cards = generate_source_cards(validated_sources[:5])
    if source_cards:
        answer_parts.append(f"🔗 **Verified Sources**\n{source_cards}")
    
    # Add warning if needed
    if confidence['score'] < 60:
        answer_parts.insert(0, "⚠️ **Low Confidence Warning:** The information below has limited verification. Please verify independently.\n")
    
    full_answer = "\n".join(answer_parts)
    
    # Review answer quality (Phase 17.6)
    review = review_answer(full_answer, query, validated_sources, confidence)
    
    # If answer quality is poor, add note
    if not review['passes']:
        full_answer += "\n\n⚠️ **Note:** This answer has quality issues and may need verification."
    
    return make_urls_clickable(full_answer)

def generate_quick_answer(query: str, sources: List[Dict]) -> str:
    if not sources:
        return "No information found."
    
    first_content = sources[0]['content'] if sources else ""
    sentences = first_content.split('.')
    for sentence in sentences:
        if len(sentence.strip()) > 30:
            return sentence.strip() + '.'
    
    return "Information found but could not generate a quick answer."

def generate_detailed_explanation(query: str, sources: List[Dict]) -> str:
    if not sources:
        return ""
    
    combined_content = ""
    for source in sources[:3]:
        combined_content += source['content'] + " "
    
    paragraphs = combined_content.split('. ')
    key_paragraphs = []
    
    for p in paragraphs:
        if len(p) > 100 and not any(x in p.lower() for x in ['advertisement', 'cookie']):
            key_paragraphs.append(p)
    
    if key_paragraphs:
        explanation = ". ".join(key_paragraphs[:3])
        if not explanation.endswith('.'):
            explanation += '.'
        return explanation
    
    return "Detailed explanation could not be generated."

def generate_key_facts(query: str, sources: List[Dict]) -> List[str]:
    facts = []
    seen_facts = set()
    
    for source in sources[:5]:
        content = source['content']
        sentences = re.split(r'[.!?]', content)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 30 and len(sentence) < 150:
                if (re.search(r'\d+', sentence) or 
                    any(word in sentence.lower() for word in ['is', 'are', 'was', 'were', 'has', 'have']) or
                    any(word in sentence.lower() for word in ['percent', 'million', 'billion', 'year'])):
                    
                    fact = sentence.strip()
                    if fact and fact not in seen_facts:
                        seen_facts.add(fact)
                        facts.append(fact)
        
        if len(facts) >= 5:
            break
    
    return facts[:5]

def generate_analysis(query: str, sources: List[Dict], agreement: Dict) -> str:
    analysis_parts = []
    
    # Source quality analysis
    trust_scores = [s.get('trust_score', 50) for s in sources if 'trust_score' in s]
    if trust_scores:
        avg_trust = sum(trust_scores) / len(trust_scores)
        if avg_trust >= 80:
            analysis_parts.append(f"High-quality sources with average trust score of {avg_trust:.0f}%.")
        elif avg_trust >= 60:
            analysis_parts.append(f"Moderate-quality sources with average trust score of {avg_trust:.0f}%.")
        else:
            analysis_parts.append(f"Source quality is mixed with average trust score of {avg_trust:.0f}%.")
    
    # Agreement analysis
    if agreement.get('total_sources', 0) > 1:
        if agreement.get('level') == 'High':
            analysis_parts.append(f"Strong agreement across {agreement['total_sources']} sources.")
        elif agreement.get('level') == 'Medium':
            analysis_parts.append(f"Mixed agreement among {agreement['total_sources']} sources.")
        else:
            analysis_parts.append(f"Limited agreement between sources.")
    
    return " ".join(analysis_parts)

def generate_confidence_info(confidence: Dict, verification: Dict) -> str:
    return f"""🔬 **Confidence: {confidence['score']:.1f}%** ({confidence['level']})
{confidence['description']}

**Verification:** {verification.get('status', 'unknown')}
**Sources matched:** {verification.get('matches', 0)}/{verification.get('total_sources', 0)}

**Confidence Factors:**
• Source Quality: {confidence['factors']['source_quality']:.1f}%
• Validation: {confidence['factors']['validation']:.1f}%
• Agreement: {confidence['factors']['agreement']:.1f}%
• Verification: {confidence['factors']['verification']:.1f}%"""

def generate_source_cards(sources: List[Dict]) -> str:
    cards = []
    for i, source in enumerate(sources[:5], 1):
        trust = source.get('trust_score', 0)
        category = source.get('trust_category', 'Unknown')
        level = source.get('trust_level', 'Medium')
        url = source['url']
        status = "✅ Verified" if source.get('valid', False) else "⚠️ Unverified"
        
        # Get trust indicator
        if trust >= 80:
            indicator = "⭐"
        elif trust >= 60:
            indicator = "📘"
        else:
            indicator = "📄"
        
        cards.append(f"{i}. {indicator} **{source.get('title', 'Untitled')}** ({level} - {trust}%)\n   {status} | Category: {category}\n   <a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">{url}</a>")
    return "\n\n".join(cards)

def analyze_source_agreement(sources: List[Dict]) -> Dict[str, Any]:
    if not sources:
        return {"confidence": 0, "agreement": "No sources", "confident_sources": 0}
    
    fact_groups = defaultdict(list)
    
    for source in sources:
        snippet = source.get('snippet', '').lower()
        claims = set()
        sentences = snippet.split('.')
        for sentence in sentences:
            if len(sentence.strip()) > 20 and any(word in sentence for word in ['is', 'are', 'was', 'were', 'has', 'have']):
                claims.add(sentence.strip())
        
        for claim in claims:
            if claim:
                fact_groups[claim].append(source['url'])
    
    total_claims = len(fact_groups)
    agreed_claims = {k: v for k, v in fact_groups.items() if len(v) >= 3}
    disagreeing_claims = {k: v for k, v in fact_groups.items() if len(v) == 1}
    
    total_sources = len(sources)
    
    if total_sources == 0:
        confidence = 0
    else:
        avg_trust = sum(s.get('trust_score', 50) for s in sources) / total_sources
        trust_factor = avg_trust / 100
        
        if agreed_claims:
            agreement_factor = min(1.0, len(agreed_claims) / total_sources)
        else:
            agreement_factor = 0.5 if len(sources) > 1 else 0.3
        
        source_count_factor = min(1.0, total_sources / 8)
        
        confidence = (trust_factor * 0.4 + agreement_factor * 0.4 + source_count_factor * 0.2) * 100
        confidence = max(0, min(100, confidence))
    
    if confidence >= 85:
        level = "High"
    elif confidence >= 60:
        level = "Medium"
    else:
        level = "Low"
    
    return {
        "confidence": round(confidence, 1),
        "level": level,
        "total_sources": total_sources,
        "agreement": f"{len(agreed_claims)} facts agreed by {len(sources)} sources",
        "agreed_facts": len(agreed_claims),
        "disagreements": len(disagreeing_claims)
    }

# ============ FOLLOW-UP ENGINE ============
def generate_follow_ups(query: str) -> List[str]:
    follow_ups = []
    query_lower = query.lower()
    
    follow_ups.append("Explain simply")
    follow_ups.append("Give examples")
    follow_ups.append("Real-world use cases")
    
    if any(word in query_lower for word in ['what', 'who', 'when', 'where']):
        follow_ups.append("Why is this important?")
        follow_ups.append("Latest developments")
    
    if any(word in query_lower for word in ['how', 'why']):
        follow_ups.append("What are the benefits?")
        follow_ups.append("What are the drawbacks?")
    
    if any(word in query_lower for word in ['technology', 'software', 'programming', 'code', 'python', 'javascript']):
        follow_ups.append("How does it compare to alternatives?")
        follow_ups.append("What are the best practices?")
    
    if any(word in query_lower for word in ['business', 'company', 'market']):
        follow_ups.append("What is the market impact?")
        follow_ups.append("Who are the key players?")
    
    if any(word in query_lower for word in ['health', 'medicine', 'fitness']):
        follow_ups.append("What are the health benefits?")
        follow_ups.append("Are there any risks?")
    
    if any(word in query_lower for word in ['history', 'historical', 'ancient']):
        follow_ups.append("What is the historical significance?")
        follow_ups.append("How did it develop over time?")
    
    if len(follow_ups) < 3:
        follow_ups.extend(["Tell me more", "What are the implications?", "Is this widely used?"])
    
    return list(dict.fromkeys(follow_ups))[:6]

# ============ RESOLVE REFERENCES ============
def resolve_references(message: str, context: List[Dict]) -> str:
    if not context:
        return message
    
    user_messages = [msg['content'] for msg in context if msg['role'] == 'user'][-3:]
    if not user_messages:
        return message
    
    resolved = message
    last_message = user_messages[-1] if user_messages else ""
    
    words = last_message.split()
    potential_entities = []
    current_entity = []
    
    for word in words:
        if word[0].isupper() and len(word) > 1:
            current_entity.append(word)
        elif current_entity and word in ['is', 'are', 'was', 'were', 'has', 'have', 'had']:
            if current_entity:
                potential_entities.append(' '.join(current_entity))
                current_entity = []
        elif current_entity and len(current_entity) < 5:
            current_entity.append(word)
        else:
            if current_entity:
                potential_entities.append(' '.join(current_entity))
                current_entity = []
    
    if current_entity:
        potential_entities.append(' '.join(current_entity))
    
    for word in last_message.split():
        if word[0].isupper() and len(word) > 2 and word not in potential_entities:
            potential_entities.append(word)
    
    potential_entities = list(dict.fromkeys(potential_entities))
    
    pronouns = {
        r'\bit\b': potential_entities[0] if potential_entities else 'it',
        r'\bthey\b': potential_entities[0] if potential_entities else 'they',
        r'\bthem\b': potential_entities[0] if potential_entities else 'them',
        r'\bthis\b': potential_entities[0] if potential_entities else 'this',
        r'\bthat\b': potential_entities[0] if potential_entities else 'that',
        r'\bthese\b': potential_entities[0] if potential_entities else 'these',
        r'\bthose\b': potential_entities[0] if potential_entities else 'those'
    }
    
    for pronoun, entity in pronouns.items():
        resolved = re.sub(pronoun, entity, resolved, flags=re.IGNORECASE)
    
    return resolved

# ============ ANALYTICS SYSTEM ============
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
    
    total_queries = len(analytics_data)
    avg_response_time = sum(d.get('response_time', 0) for d in analytics_data) / total_queries if total_queries > 0 else 0
    avg_sources = sum(d.get('sources_found', 0) for d in analytics_data) / total_queries if total_queries > 0 else 0
    avg_quality = sum(sum(d.get('quality_scores', [0])) / len(d.get('quality_scores', [1])) for d in analytics_data if d.get('quality_scores')) / total_queries if total_queries > 0 else 0
    avg_context = sum(d.get('context_used', 0) for d in analytics_data) / total_queries if total_queries > 0 else 0
    math_queries = sum(1 for d in analytics_data if d.get('is_math', False))
    
    return {
        "total_queries": total_queries,
        "average_response_time": f"{avg_response_time:.2f}s",
        "average_sources_per_query": f"{avg_sources:.1f}",
        "average_source_quality": f"{avg_quality:.1f}%",
        "average_context_used": f"{avg_context:.1f} messages",
        "math_queries_solved": math_queries
    }

# ============ RESPONSE FUNCTION ============
def get_response(message, email, regenerate=False):
    msg = message.strip().lower()
    
    stats = update_user_stats(email)
    user = user_db.get(User.email == email)
    user_name = user.get('name', 'User') if user else 'User'
    
    # Check for math intent FIRST
    math_detection = math_engine.detect_math_intent(message)
    if math_detection['is_math']:
        math_result = math_engine.solve_math(message)
        if math_result.get('success', False):
            response = format_math_answer(math_result, message)
            follow_ups = generate_follow_ups(message)
            if follow_ups:
                follow_up_text = "\n\n💭 **Related Questions:**\n" + "\n".join([f"• {q}" for q in follow_ups[:3]])
                response += follow_up_text
            response += f"\n\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
            
            track_analytics({
                "query": message,
                "is_math": True,
                "math_type": math_result.get('type', 'unknown'),
                "response_time": 0.1
            })
            
            if not regenerate:
                context_memory.add_message(email, "user", message)
                context_memory.add_message(email, "ai", response)
            return response
    
    # Greetings
    if msg in ['hi', 'hello', 'hey', 'sup', 'yo']:
        return f"👋 Hello {user_name}! You are a **{stats['title']}** (Level {stats['level']}) with {stats['count']} messages!\n\nHow can I help you today?"
    
    if 'how are you' in msg:
        return f"😊 I'm doing great! Thanks for asking, {user_name}!"
    
    # Resolve references using context
    context = context_memory.get_context(email)
    resolved_message = resolve_references(message, context)
    
    start_time = time.time()
    
    search_results = search_web_improved(resolved_message, max_results=10)
    
    response = generate_advanced_answer(resolved_message, search_results, context)
    
    follow_ups = generate_follow_ups(resolved_message)
    if follow_ups:
        follow_up_text = "\n\n💭 **Follow-up Questions:**\n" + "\n".join([f"• {q}" for q in follow_ups[:4]])
        response += follow_up_text
    
    response += f"\n\n📊 **{user_name}'s Stats:** Level {stats['level']} - {stats['title']} ({stats['count']} messages)"
    
    response_time = time.time() - start_time
    track_analytics({
        "query": resolved_message,
        "is_math": False,
        "response_time": response_time,
        "sources_found": len(search_results),
        "quality_scores": [s.get('trust_score', 0) for s in search_results[:5]],
        "context_used": len(context)
    })
    
    if not regenerate:
        context_memory.add_message(email, "user", message)
        context_memory.add_message(email, "ai", response)
    
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

# ============ MESSAGE FEATURES ENDPOINTS ============
@app.post("/feedback")
async def submit_feedback(request: Request):
    data = await request.json()
    email = data.get('email')
    message_index = data.get('message_index')
    feedback_type = data.get('feedback_type')
    
    if not email or message_index is None:
        return JSONResponse({"error": "Missing required fields"}, status_code=400)
    
    message_feedback_db.insert({
        "email": email,
        "message_index": message_index,
        "feedback_type": feedback_type,
        "timestamp": datetime.now().isoformat()
    })
    
    # Log feedback for learning
    log_feedback(email, feedback_type, message_index)
    
    return {"status": "success"}

@app.post("/regenerate")
async def regenerate_response(request: Request):
    data = await request.json()
    email = data.get('email')
    message = data.get('message')
    
    if not email or not message:
        return JSONResponse({"error": "Missing required fields"}, status_code=400)
    
    response = get_response(message, email, regenerate=True)
    return {"response": response}

@app.post("/continue_generating")
async def continue_generating(request: Request):
    data = await request.json()
    email = data.get('email')
    message = data.get('message')
    
    if not email or not message:
        return JSONResponse({"error": "Missing required fields"}, status_code=400)
    
    context = context_memory.get_context(email)
    search_results = search_web_improved(message, max_results=10)
    
    extra_content = "\n\n📝 **Additional Information:**\n"
    
    for i, result in enumerate(search_results[:3], 1):
        content = read_full_webpage_improved(result['url'])
        if content and len(content) > 100:
            extra_content += f"\n**Source {i}:** {content[:500]}...\n"
    
    return {"response": extra_content}

@app.get("/share_conversation")
async def share_conversation(email: str = ""):
    if not email:
        return JSONResponse({"error": "Email required"}, status_code=400)
    
    history = load_history(email)
    return {"conversation": history}

@app.get("/learning_dashboard")
async def learning_dashboard():
    """Admin endpoint for learning metrics"""
    metrics = get_learning_metrics()
    return JSONResponse(metrics)

# ============ GOOGLE CLIENT ID ============
GOOGLE_CLIENT_ID = "46152262032-41laiprrsbes52knkch3hlji7reqc6eb.apps.googleusercontent.com"

# ============ COMPLETE HTML (UNCHANGED) ============
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
        
        /* ========== MESSAGE ACTION BUTTONS ========== */
        .message-actions {{
            display: flex;
            gap: 8px;
            margin-top: 8px;
            opacity: 0.6;
            transition: opacity 0.2s;
            flex-wrap: wrap;
        }}
        
        .message-actions:hover {{
            opacity: 1;
        }}
        
        .message-actions button {{
            background: none;
            border: none;
            cursor: pointer;
            padding: 4px 8px;
            font-size: 0.75rem;
            border-radius: 6px;
            color: #6a5a4a;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        
        .message-actions button:hover {{
            background: rgba(44,36,24,0.1);
            color: #2c2418;
        }}
        
        body.dark .message-actions button {{
            color: #8a7a6a;
        }}
        
        body.dark .message-actions button:hover {{
            background: rgba(212,197,169,0.1);
            color: #d4c5a9;
        }}
        
        .message-actions .liked {{
            color: #4caf50 !important;
        }}
        
        .message-actions .disliked {{
            color: #f44336 !important;
        }}
        
        .ai-message .message-wrapper {{
            display: inline-block;
            max-width: 85%;
            text-align: left;
        }}
        
        .ai-message .message-content {{
            background: white !important;
            color: #2c2418 !important;
            padding: 12px 18px !important;
            border-radius: 20px !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            display: inline-block;
            width: 100%;
        }}
        
        .user-message .message-wrapper {{
            display: inline-block;
            max-width: 85%;
            text-align: right;
        }}
        
        .user-message .message-content {{
            background: #2c2418 !important;
            color: white !important;
            padding: 10px 16px !important;
            border-radius: 20px !important;
            display: inline-block;
            width: 100%;
        }}
        
        .edit-message-input {{
            display: none;
            width: 100%;
            padding: 8px 12px;
            border: 2px solid #2c2418;
            border-radius: 12px;
            font-size: 0.9rem;
            font-family: inherit;
            background: white;
            color: #2c2418;
        }}
        
        body.dark .edit-message-input {{
            background: #2a2a4e;
            color: #e0e0e0;
            border-color: #4a3f2f;
        }}
        
        .edit-message-input.active {{
            display: block;
        }}
        
        .edit-actions {{
            display: none;
            gap: 8px;
            margin-top: 8px;
        }}
        
        .edit-actions.active {{
            display: flex;
        }}
        
        .edit-actions button {{
            padding: 4px 12px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-size: 0.75rem;
        }}
        
        .edit-actions .save-edit {{
            background: #2c2418;
            color: white;
        }}
        
        .edit-actions .cancel-edit {{
            background: #e0d5c8;
            color: #2c2418;
        }}
        
        body.dark .edit-actions .cancel-edit {{
            background: #3a3a5e;
            color: #d4c5a9;
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
        
        /* ========== APP ========== */
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
        
        /* ========== SIDEBAR ========== */
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
        
        /* ========== MAIN ========== */
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
        
        /* ========== MESSAGES ========== */
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
        
        .message-wrapper {{
            display: inline-block;
            max-width: 85%;
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
            max-width: 100%;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #2c2418;
            background: transparent !important;
            padding: 0 !important;
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
        
        /* ========== INPUT AREA ========== */
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
        
        /* ========== RESPONSIVE ========== */
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
                max-width: 100%;
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
            .message-actions button {{
                font-size: 0.65rem;
                padding: 2px 6px;
            }}
        }}
        
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
        let messageCounter = 0;
        let isGenerating = false;
        let currentMessageIndex = 0;
        let messageHistory = [];
        
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
                container.innerHTML = '<div style="color:#6a5a4a;text-align:center;padding:20px;">No conversations yet</div>';
                return;
            }}
            let html = '';
            for (let i = history.length - 1; i >= 0; i--) {{
                let item = history[i];
                html += '<div class="history-item" onclick="loadChatMessage(\\'' + escapeHtml(item.user) + '\\')">' +
                        '<div class="history-question">' + escapeHtml(item.user.substring(0, 45)) + '</div>' +
                        '<div class="history-time">' + item.timestamp + '</div>' +
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
        
        function autoAdjustHeight() {{
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        }}
        
        textarea.addEventListener('input', autoAdjustHeight);
        
        if (window.visualViewport) {{
            let lastHeight = window.visualViewport.height;
            window.visualViewport.addEventListener('resize', function() {{
                const inputArea = document.querySelector('.input-area');
                if (inputArea && window.visualViewport.height < lastHeight) {{
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
        
        // ============ MESSAGE FEATURES ============
        
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
            
            const typing = document.getElementById('typing');
            typing.style.display = 'block';
            
            const messageDiv = document.getElementById(`message-${{messageId}}`);
            const content = messageDiv.querySelector('.message-content');
            
            try {{
                const res = await fetch('/regenerate', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ 
                        email: currentUser.email, 
                        message: userMessage 
                    }})
                }});
                const data = await res.json();
                content.innerHTML = data.response.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            }} catch (error) {{
                console.error('Regenerate error:', error);
            }}
            
            typing.style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }}
        
        function editMessage(messageId) {{
            const messageDiv = document.getElementById(`message-${{messageId}}`);
            const content = messageDiv.querySelector('.message-content');
            const editInput = messageDiv.querySelector('.edit-message-input');
            const editActions = messageDiv.querySelector('.edit-actions');
            
            if (content.style.display !== 'none') {{
                content.style.display = 'none';
                editInput.value = content.innerText;
                editInput.classList.add('active');
                editActions.classList.add('active');
                editInput.focus();
            }}
        }}
        
        function saveEdit(messageId) {{
            const messageDiv = document.getElementById(`message-${{messageId}}`);
            const editInput = messageDiv.querySelector('.edit-message-input');
            const content = messageDiv.querySelector('.message-content');
            const editActions = messageDiv.querySelector('.edit-actions');
            
            const newText = editInput.value.trim();
            if (newText) {{
                content.innerText = newText;
                content.style.display = 'block';
                editInput.classList.remove('active');
                editActions.classList.remove('active');
                updateMessageInHistory(messageId, newText);
            }}
        }}
        
        function cancelEdit(messageId) {{
            const messageDiv = document.getElementById(`message-${{messageId}}`);
            const content = messageDiv.querySelector('.message-content');
            const editInput = messageDiv.querySelector('.edit-message-input');
            const editActions = messageDiv.querySelector('.edit-actions');
            
            content.style.display = 'block';
            editInput.classList.remove('active');
            editActions.classList.remove('active');
        }}
        
        function updateMessageInHistory(messageId, newText) {{
            // Update stored message
        }}
        
        async function continueGenerating(messageId) {{
            if (isGenerating) return;
            isGenerating = true;
            
            const typing = document.getElementById('typing');
            typing.style.display = 'block';
            
            const messageDiv = document.getElementById(`message-${{messageId}}`);
            const content = messageDiv.querySelector('.message-content');
            const userMessage = getLastUserMessage();
            
            try {{
                const res = await fetch('/continue_generating', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ 
                        email: currentUser.email, 
                        message: userMessage 
                    }})
                }});
                const data = await res.json();
                content.innerHTML += data.response.replace(/\\n/g, '<br>').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            }} catch (error) {{
                console.error('Continue error:', error);
            }}
            
            typing.style.display = 'none';
            isGenerating = false;
            scrollToBottom();
        }}
        
        function getLastUserMessage() {{
            const messages = document.querySelectorAll('.user-message');
            if (messages.length > 0) {{
                const lastUserMsg = messages[messages.length - 1];
                return lastUserMsg.querySelector('.message-content').innerText;
            }}
            return '';
        }}
        
        async function shareConversation() {{
            if (!currentUser) return;
            try {{
                const res = await fetch('/share_conversation?email=' + encodeURIComponent(currentUser.email));
                const data = await res.json();
                const shareText = data.conversation.map(item => 
                    `User: ${{item.user}}\\nYama: ${{item.ai}}\\n`
                ).join('\\n');
                
                await navigator.clipboard.writeText(shareText);
                alert('✅ Conversation copied to clipboard!');
            }} catch (error) {{
                console.error('Share error:', error);
                alert('Could not share conversation.');
            }}
        }}
        
        async function submitFeedback(messageId, feedbackType) {{
            const messageDiv = document.getElementById(`message-${{messageId}}`);
            const likeBtn = messageDiv.querySelector('.like-btn');
            const dislikeBtn = messageDiv.querySelector('.dislike-btn');
            
            try {{
                await fetch('/feedback', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        email: currentUser.email,
                        message_index: parseInt(messageId.split('-')[1]),
                        feedback_type: feedbackType
                    }})
                }});
                
                if (feedbackType === 'like') {{
                    likeBtn.classList.toggle('liked');
                    if (dislikeBtn.classList.contains('disliked')) {{
                        dislikeBtn.classList.remove('disliked');
                    }}
                }} else {{
                    dislikeBtn.classList.toggle('disliked');
                    if (likeBtn.classList.contains('liked')) {{
                        likeBtn.classList.remove('liked');
                    }}
                }}
            }} catch (error) {{
                console.error('Feedback error:', error);
            }}
        }}
        
        function stopGenerating() {{
            isGenerating = false;
            document.getElementById('typing').style.display = 'none';
        }}
        
        // ============ SEND MESSAGE ============
        async function sendMessage() {{
            if (!currentUser) {{ alert('Please sign in first!'); return; }}
            const message = textarea.value.trim();
            if (!message) return;
            
            if (isGenerating) {{
                stopGenerating();
                return;
            }}
            
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
            }} catch (error) {{
                console.error('Send message error:', error);
                document.getElementById('typing').style.display = 'none';
            }}
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
                editActions.innerHTML = `
                    <button class="save-edit" onclick="saveEdit('${{messageId}}')">Save</button>
                    <button class="cancel-edit" onclick="cancelEdit('${{messageId}}')">Cancel</button>
                `;
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
                actions.innerHTML = `
                    <button onclick="editMessage('${{messageId}}')">✏️ Edit</button>
                `;
            }}
            
            wrapper.appendChild(actions);
            div.appendChild(wrapper);
            messages.appendChild(div);
            scrollToBottom();
        }}
        
        function escapeJs(text) {{
            return text.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'").replace(/"/g, '\\\\"');
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

@app.get("/analytics")
async def get_analytics():
    return get_analytics_summary()

if __name__ == "__main__":
    print("\n" + "="*55)
    print("🏛️ YAMA AI - HIGH ACCURACY INTELLIGENCE SYSTEM")
    print("="*55)
    print("🌐 Open: http://localhost:8000")
    print("="*55)
    print("✅ PHASE 17.1 - SOURCE VALIDATION SYSTEM")
    print("✅ PHASE 17.2 - TRUSTED SOURCE RANKING")
    print("✅ PHASE 17.3 - MULTI-SOURCE VERIFICATION")
    print("✅ PHASE 17.4 - CONFIDENCE SCORING")
    print("✅ PHASE 17.5 - SPECIALIZED KNOWLEDGE ENGINES")
    print("✅ PHASE 17.6 - ANSWER QUALITY REVIEWER")
    print("✅ PHASE 17.7 - HALLUCINATION PREVENTION")
    print("✅ PHASE 17.8 - CITATION SYSTEM")
    print("✅ PHASE 17.9 - CONTINUOUS LEARNING DASHBOARD")
    print("="*55)
    print("🎯 Goal: Accuracy > Speed")
    print("🎯 Goal: Verification > Guessing")
    print("🎯 Goal: Trusted Sources > Random Sources")
    print("🎯 Goal: Evidence > Assumptions")
    print("="*55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=10000)
