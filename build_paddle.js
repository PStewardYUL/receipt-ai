const fs = require('fs');
let content = '';
content += '"""\n';
content += 'PaddleOCR + CLIP for receipt processing.\n';
content += 'Primary OCR system using PaddleOCR with Ollama fallback.\n';
content += '\n';
content += 'This module provides a unified interface that can use either:\n';
content += '1. PaddleOCR + CLIP (when available) - fast local OCR\n';
content += '2. Ollama vision LLM (fallback) - slower but more accurate\n';
content += '"""\n';

fs.writeFileSync('backend/services/paddle_ocr.js', content);
console.log('done');
