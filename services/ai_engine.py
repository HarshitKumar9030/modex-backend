"""
AI Engine — Uses Gemini 2.5 Flash to interpret user instructions
and decide which file operation to perform.

Uses structured output (JSON schema) so the model returns a clean
OperationDecision that maps directly to our service calls.
"""

import json
import logging
from typing import List, Optional, Dict, Any

from google import genai
from google.genai import types

from core.config import settings

logger = logging.getLogger(__name__)

# ── Gemini client (singleton) ─────────────────────────────────────

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


# ── System prompt ─────────────────────────────────────────────────
SYSTEM_INSTRUCTION = """You are Modex, an AI file-processing assistant. Users upload files (PDFs, images, audio) and describe what they want done in natural language.

Your job: interpret the user's request and return a structured JSON operation.

You can handle PDFs, images, audio AND text/document files (txt, md, csv, json, html, xml, rtf, log).

## Available Operations

### PDF Operations
- compress_pdf: params → { target_size_kb?, quality?: "low"|"medium"|"high" }
- merge_pdf: (requires multiple PDF files) params → {}
- split_pdf: params → { pages?: [1,2,3], ranges?: "1-3,5,7-10" }
- rotate_pdf: params → { degrees: 90|180|270, pages?: [1,2] }
- pdf_to_images: params → { format?: "png"|"jpg", dpi?: 150 }
- pdf_pages_to_images: Render full PDF pages as images (text + charts + everything). params → { format?: "png"|"jpg", dpi?: 200, pages?: [1,2] }
- extract_pdf_images: Extract embedded images (photos, charts, diagrams) from within a PDF. params → { format?: "png"|"jpg", min_size?: 50 }
- images_to_pdf: (requires image files) params → {}

### Image Operations
- compress_image: params → { target_size_kb?, quality?: 1-100, format? }
- resize_image: params → { width?, height?, scale?, maintain_aspect?: true }
- crop_image: params → { left, top, right, bottom } or { x, y, width, height }
- convert_image: params → { format: "png"|"jpg"|"webp"|"bmp"|"tiff"|"gif", quality?: 1-100 }

### Audio Operations
- compress_audio: params → { target_size_kb?, bitrate?: "128k"|"64k", format?: "mp3" }
- convert_audio: params → { format: "mp3"|"wav"|"ogg"|"flac"|"aac"|"m4a", bitrate?: "192k" }
- trim_audio: params → { start_sec?, end_sec?, start_ms?, end_ms? }
- adjust_audio_volume: params → { change_db?: float, normalize?: bool }

### Document Operations
- document_to_pdf: (for txt, md, csv, json, html, xml, rtf, log files) params → {}

## Rules
1. Match the user's intent to the correct operation(s).
2. Extract parameters from the user's message (e.g. "compress to 100kb" → target_size_kb: 100).
3. Set file_ids to the IDs of files provided in context. If not clear, use all uploaded files.
4. If the request is ambiguous or you need more info, set needs_clarification=true and put your question in explanation.
5. If no matching operation exists, set operation="unknown" and explain in explanation.
6. Be smart about unit conversions: "100kb" = 100, "1mb" = 1024, "500 bytes" = 0.5.
7. For "make it smaller" type requests without a specific target, use quality-based compression.
8. Always populate the explanation field with a brief human-readable summary of what you'll do.
9. For "extract images from PDF" / "get photos from PDF" / "pull charts from PDF" → use extract_pdf_images.
10. For "convert PDF to images" / "screenshot PDF pages" / "render pages" → use pdf_pages_to_images.
11. The older pdf_to_images is a legacy alias — prefer pdf_pages_to_images for full-page renders and extract_pdf_images for embedded content.
12. **Multi-operations**: If the user requests MULTIPLE operations (e.g. "compress and convert to png", "resize to 800px wide and convert to webp"), populate the `operations` array with each step in order. Each step has its own operation, file_ids, and params. The main `operation` field should be set to "multi_operation" and `params` should be empty.
13. For single operations, leave `operations` as an empty array and use `operation`/`params` as before.
"""

# ── JSON schema for structured output ─────────────────────────────

OPERATION_SCHEMA = {
    "type": "object",
    "required": ["operation", "file_ids", "params", "explanation", "needs_clarification"],
    "properties": {
        "operation": {
            "type": "string",
            "description": "Operation name from the available operations list, or 'multi_operation' for chained ops"
        },
        "file_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "IDs of files to process"
        },
        "params": {
            "type": "object",
            "description": "Operation-specific parameters (empty if multi_operation)"
        },
        "explanation": {
            "type": "string",
            "description": "Brief explanation of what will be done, or a clarifying question"
        },
        "needs_clarification": {
            "type": "boolean",
            "description": "True if more info is needed from the user"
        },
        "operations": {
            "type": "array",
            "description": "Ordered list of operations for multi-operation requests. Empty for single ops.",
            "items": {
                "type": "object",
                "required": ["operation", "params"],
                "properties": {
                    "operation": {"type": "string"},
                    "file_ids": {"type": "array", "items": {"type": "string"}},
                    "params": {"type": "object"}
                }
            }
        }
    }
}


async def interpret_request(
    user_message: str,
    file_descriptions: List[Dict[str, Any]],
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Send the user's message + file context to Gemini and get back a structured
    operation decision.

    Args:
        user_message: The user's natural-language instruction.
        file_descriptions: List of dicts with file info:
            [{"id": "...", "filename": "...", "type": "pdf|image|audio", "size_kb": 123}]
        conversation_history: Previous messages for multi-turn context.

    Returns:
        Parsed OperationDecision as a dict.
    """
    client = get_client()

    # Build the context message with file info
    file_context = "## Uploaded Files\n"
    if file_descriptions:
        for f in file_descriptions:
            file_context += f"- ID: {f['id']} | Name: {f['filename']} | Type: {f['type']} | Size: {f['size_kb']:.1f} KB\n"
    else:
        file_context += "No files uploaded yet.\n"

    # Build conversation contents
    contents = []

    # Add conversation history for multi-turn
    if conversation_history:
        for msg in conversation_history[-10:]:  # Last 10 messages for context
            role = "user" if msg["role"] == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg["content"])]
                )
            )

    # Current user message with file context
    full_message = f"{file_context}\n## User Request\n{user_message}"
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=full_message)]
        )
    )

    try:
        response = client.models.generate_content(
            model=settings.AI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_json_schema=OPERATION_SCHEMA,
                temperature=0.1,  # Low temp for deterministic operations
                max_output_tokens=settings.AI_MAX_TOKENS,
            ),
        )

        result = json.loads(response.text)
        logger.info(f"AI decision: {result['operation']} — {result['explanation']}")
        return result

    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return {
            "operation": "unknown",
            "file_ids": [],
            "params": {},
            "explanation": f"I encountered an error processing your request: {str(e)}. Please try rephrasing.",
            "needs_clarification": True,
        }
