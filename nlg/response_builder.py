import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

try:
    from jinja2 import Environment, FileSystemLoader
    JINJA_AVAILABLE = True
except ImportError:
    JINJA_AVAILABLE = False
    logger.warning("jinja2 is not installed. Template rendering will fall back to basic formatting.")

class ResponseBuilder:
    def __init__(self, template_dir: str = "nlg/templates"):
        self.template_dir = template_dir
        self.env = None
        
        if JINJA_AVAILABLE:
            # Assumes running from root of yama-ai
            if not os.path.exists(self.template_dir):
                os.makedirs(self.template_dir, exist_ok=True)
            self.env = Environment(loader=FileSystemLoader(self.template_dir))

    def build_response(self, intent: str, tool_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Takes raw tool output and formats it using a template for the given intent.
        Returns the structured dictionary expected by the frontend.
        """
        # Ensure we always return the standard structure
        response = {
            "direct_answer": tool_result.get("direct_answer", ""),
            "key_points": tool_result.get("key_points", []),
            "sources": tool_result.get("sources", [])
        }
        
        if not JINJA_AVAILABLE or not self.env:
            return response
            
        template_name = f"{intent}.jinja"
        template_path = os.path.join(self.template_dir, template_name)
        
        # If there's a specific template for this intent, use it to format the direct answer
        if os.path.exists(template_path):
            try:
                template = self.env.get_template(template_name)
                # Render the template with tool result data
                formatted_answer = template.render(**tool_result)
                if formatted_answer.strip():
                    response["direct_answer"] = formatted_answer.strip()
            except Exception as e:
                logger.error(f"Error rendering template {template_name}: {e}")
                
        return response

builder = ResponseBuilder()

def build_response(intent: str, tool_result: Dict[str, Any]) -> Dict[str, Any]:
    return builder.build_response(intent, tool_result)
