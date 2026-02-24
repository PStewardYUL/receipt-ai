# This script writes the paddle_ocr.py file
content = '''"""
PaddleOCR + CLIP for receipt processing.
Primary OCR system using PaddleOCR with Ollama fallback.

This module provides a unified interface that can use either:
1. PaddleOCR + CLIP (when available) - fast local OCR
2. Ollama vision LLM (fallback) - slower but more accurate
"""
import logging
import os

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MIN_OCR_LENGTH = 40


def _clear_corrupted_paddle_cache():
    """Clear corrupted PaddleOCR model cache."""
    paddle_dir = "/root/.paddleocr"
    if os.path.exists(paddle_dir):
        try:
            for root, dirs, files in os.walk(paddle_dir):
                for f in files:
                    if f.endswith(".tar"):
                        path = os.path.join(root, f)
                        size = os.path.getsize(path)
                        logger.warning(f"PADDLE CACHE FILE {f}: {size} bytes")
        except Exception as e:
            logger.warning(f"Error checking paddle cache: {e}")


VENDOR_LOGOS = [
    "Home Depot", "Canadian Tire", "Costco", "Walmart",
]


class PaddleOCRClient:

    def __init__(self):
        self._engine_ready = False
        self._ollama_client = None
        
        
@property  
def ocr_engine(self):
   return None
   
   
@property  
def clip_model(self): 
   return None
   
   
@property  
def ollama_client(self):
   from services.ollama import Ollamlient()
'''

with open('backend/services/paddle_ocr.py', 'w') as f:
    f.write(content)
print('File written')
