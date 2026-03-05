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

### Content Analysis Operations (read-only, no file output)
- summarize: Summarize the content of a PDF, document, or image. params → { detail?: "brief"|"detailed" }
- answer_about_content: Answer a specific question about the file's content. params → { question: "the user's question" }
- extract_text: Extract all text from a PDF or document and return it. params → {}
- describe_image: Describe what is in an image. params → { detail?: "brief"|"detailed" }

## Rules
1. Match the user's intent to the correct operation(s).
2. Extract parameters from the user's message (e.g. "compress to 100kb" → target_size_kb: 100).
3. Set file_ids to the IDs of files provided in context. If not clear, use all uploaded files.
4. If the request is ambiguous or you need more info, set needs_clarification=true and put your question in explanation.
5. If no matching operation exists, set operation="unknown" and explain in explanation.
14. For "summarize", "what does this say", "tell me about this file", "explain this document" → use summarize.
15. For specific questions about file content ("what is the revenue?", "how many pages?", "who is the author?") → use answer_about_content with the user's question in params.
16. For "extract text", "get text from PDF", "copy all text" → use extract_text.
17. For "describe this image", "what's in this picture" → use describe_image.
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


# ── Content analysis operations ─────────────────────────────────

CONTENT_OPERATIONS = {"summarize", "answer_about_content", "extract_text", "describe_image"}


def _extract_text_from_pdf(path: str, max_chars: int = 50000) -> str:
    """Extract text from a PDF using PyPDF2."""
    from PyPDF2 import PdfReader

    reader = PdfReader(path)
    pages_text = []
    total = 0
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if total + len(text) > max_chars:
            text = text[: max_chars - total]
            pages_text.append(f"--- Page {i + 1} ---\n{text}")
            break
        pages_text.append(f"--- Page {i + 1} ---\n{text}")
        total += len(text)
    return "\n".join(pages_text)


def _read_document_text(path: str, max_chars: int = 50000) -> str:
    """Read text from a document file."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(max_chars)


async def analyze_file_content(
    operation: str,
    files: List[Dict[str, Any]],
    params: Dict[str, Any],
    user_message: str,
) -> str:
    """
    Perform a content analysis operation on the given files and return
    a natural-language response (no output file produced).
    """
    client = get_client()

    # Collect content from files
    parts: list = []
    for f in files:
        ftype = f["type"]
        path = f["path"]
        fname = f["filename"]

        if ftype == "pdf":
            text = _extract_text_from_pdf(path)
            if not text.strip():
                parts.append(f"[{fname}: PDF has no extractable text (scanned/image-only)]")
            else:
                parts.append(f"Content of {fname}:\n{text}")
        elif ftype == "document":
            text = _read_document_text(path)
            parts.append(f"Content of {fname}:\n{text}")
        elif ftype == "image":
            # Use Gemini multimodal: upload image bytes
            import pathlib
            img_path = pathlib.Path(path)
            mime_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
                ".tiff": "image/tiff",
            }
            mime = mime_map.get(img_path.suffix.lower(), "image/png")
            img_bytes = img_path.read_bytes()
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
        else:
            parts.append(f"[{fname}: unsupported file type for content analysis]")

    if not parts:
        return "No files found to analyse. Please upload a file first."

    # Build the analysis prompt
    detail = params.get("detail", "detailed")

    if operation == "summarize":
        task = f"Provide a {'brief, concise' if detail == 'brief' else 'thorough and detailed'} summary of the following file content. Use clear sections and bullet points where appropriate."
    elif operation == "answer_about_content":
        question = params.get("question", user_message)
        task = f"Answer the following question about the file content: {question}"
    elif operation == "extract_text":
        task = "Extract and return ALL the text content from the file exactly as it appears, preserving structure."
    elif operation == "describe_image":
        task = f"Provide a {'brief' if detail == 'brief' else 'detailed'} description of what is shown in the image(s)."
    else:
        task = user_message

    # Build contents list: text parts as strings, image parts as-is
    content_parts = []
    for p in parts:
        if isinstance(p, str):
            content_parts.append(types.Part.from_text(text=p))
        else:
            content_parts.append(p)
    content_parts.append(types.Part.from_text(text=f"\n\nTask: {task}"))

    try:
        response = client.models.generate_content(
            model=settings.AI_MODEL,
            contents=[types.Content(role="user", parts=content_parts)],
            config=types.GenerateContentConfig(
                system_instruction="You are Modex, an AI file-processing assistant. The user has asked you to analyse their file content. Respond directly, clearly, and helpfully. Use markdown formatting.",
                temperature=0.3,
                max_output_tokens=settings.AI_MAX_TOKENS,
            ),
        )
        return response.text or "I could not generate a response for this content."
    except Exception as e:
        logger.error(f"Content analysis error: {e}")
        return f"I encountered an error analysing the file: {str(e)}"
