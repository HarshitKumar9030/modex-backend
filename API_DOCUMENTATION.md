# Modex Backend API Documentation

> **Version:** 1.0.0  
> **Base URL:** `http://localhost:8000/api/v1`  
> **Powered by:** Gemini 2.5 Flash  
> **Auto-docs:** `http://localhost:8000/docs` (Swagger) | `http://localhost:8000/redoc` (ReDoc)

---

## Overview

Modex is a **chat-based unified file processing tool**. Users interact through a conversation interface — they upload files (PDFs, images, audio) as attachments and describe what they want in natural language. The AI interprets the request and executes the operation automatically.

### Key Concepts

| Concept | Description |
|---|---|
| **Conversation** | A chat session. All files and messages belong to a conversation. Expires after 24 hours. |
| **Message** | A single chat message — either `user` or `assistant` role. |
| **File** | An uploaded file (PDF, image, or audio). Can have an `output` file after processing. |
| **Operation** | A file operation (compress, resize, convert, etc.) determined by the AI. |

### Data Retention Policy (STRICT)

- All data (conversations, messages, files) **auto-deletes after 24 hours**.
- Files are deleted from disk on expiry.
- After a user downloads a processed file, it's marked as "exported".
- Users can manually delete conversations at any time.
- A background job runs every 30 minutes to purge expired data.

---

## Workflow (How the Frontend Should Work)

```
1. Create a conversation         → POST /conversations
2. Upload files as attachments   → POST /conversations/{id}/files
3. Send a chat message           → POST /conversations/{id}/chat
   (AI interprets + processes)
4. Display result to user
5. User downloads output file    → GET /conversations/{id}/files/{file_id}/output
6. Repeat steps 2-5 as needed
```

### Example Flow

```
User creates conversation
User uploads "report.pdf"
User sends: "Compress this to 100kb"
→ AI returns: "I'll compress report.pdf to approximately 100KB"
→ Backend compresses the PDF
→ Response includes the processed file info with download link
User downloads the compressed file
```

---

## API Endpoints

### Health Check

#### `GET /api/v1/health`

Check if the API is running.

**Response:**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "data_retention_hours": 24
}
```

---

### Conversations

#### `POST /api/v1/conversations`

Create a new conversation session.

**Request Body (optional):**
```json
{
  "title": "My PDF project"
}
```

**Response (201):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "My PDF project",
  "created_at": "2026-03-05T10:00:00Z",
  "updated_at": "2026-03-05T10:00:00Z",
  "expires_at": "2026-03-06T10:00:00Z"
}
```

---

#### `GET /api/v1/conversations`

List all active (non-expired) conversations.

**Query Params:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max conversations to return |
| `offset` | int | 0 | Pagination offset |

**Response (200):**
```json
{
  "conversations": [
    {
      "id": "...",
      "title": "My PDF project",
      "created_at": "...",
      "updated_at": "...",
      "expires_at": "..."
    }
  ],
  "total": 1
}
```

---

#### `GET /api/v1/conversations/{conversation_id}`

Get a specific conversation.

**Response (200):** Same as ConversationOut above.  
**Response (404):** `{"detail": "Conversation not found"}`

---

#### `DELETE /api/v1/conversations/{conversation_id}`

Delete a conversation and ALL its data (messages + files) immediately.

**Response (204):** No content.  
**Response (404):** `{"detail": "Conversation not found"}`

---

### Messages

#### `GET /api/v1/conversations/{conversation_id}/messages`

Get all messages in a conversation (ordered by time).

**Response (200):**
```json
[
  {
    "id": "msg-uuid",
    "role": "user",
    "content": "Compress this PDF to 100kb",
    "file_ids": "[\"file-uuid-1\"]",
    "created_at": "2026-03-05T10:01:00Z"
  },
  {
    "id": "msg-uuid-2",
    "role": "assistant",
    "content": "I'll compress report.pdf to approximately 100KB.\n\n**Result:** Compressed PDF: 98.5 KB",
    "file_ids": null,
    "created_at": "2026-03-05T10:01:02Z"
  }
]
```

---

### Chat (Main Interaction Endpoint)

#### `POST /api/v1/conversations/{conversation_id}/chat`

**This is the core endpoint.** Send a user message, and the AI will interpret it, execute file operations, and return the result.

**Request Body:**
```json
{
  "message": "Compress this PDF to 100kb",
  "file_ids": ["file-uuid-1"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | Natural language instruction (1-5000 chars) |
| `file_ids` | string[] | No | Specific file IDs to operate on. If omitted, AI uses all files in conversation. |

**Response (200):**
```json
{
  "message": {
    "id": "msg-uuid",
    "role": "assistant",
    "content": "I'll compress report.pdf to approximately 100KB.\n\n**Result:** Compressed PDF: 98.5 KB",
    "file_ids": null,
    "created_at": "2026-03-05T10:01:02Z"
  },
  "processed_files": [
    {
      "id": "file-uuid-1",
      "original_filename": "report.pdf",
      "mime_type": "application/pdf",
      "file_size": 524288,
      "file_type": "pdf",
      "status": "completed",
      "operation": "compress_pdf",
      "error_message": null,
      "created_at": "2026-03-05T10:00:30Z",
      "has_output": true
    }
  ]
}
```

**Important for Frontend:**
- If `processed_files` has items with `has_output: true`, show a download button.
- The `message.content` may contain markdown (bold, newlines). Render it.
- If the AI needs clarification, it will ask in the message content — no files will be processed.

---

### Example Chat Messages (Frontend Should Support)

| User Message | What Happens |
|---|---|
| `"Compress this PDF to 100kb"` | compress_pdf with target_size_kb=100 |
| `"Make this image smaller, maybe 800px wide"` | resize_image with width=800 |
| `"Convert to PNG"` | convert_image with format=png |
| `"Merge all PDFs"` | merge_pdf on all PDF files |
| `"Split into individual pages"` | split_pdf, creates one file per page |
| `"Rotate 90 degrees"` | rotate_pdf with degrees=90 |
| `"Convert to MP3"` | convert_audio with format=mp3 |
| `"Trim from 10s to 30s"` | trim_audio with start_sec=10, end_sec=30 |
| `"Make it louder"` | adjust_audio_volume with change_db=6 |
| `"Compress the audio to 500kb"` | compress_audio with target_size_kb=500 |
| `"Convert images to a PDF"` | images_to_pdf |
| `"Extract images from PDF"` | pdf_to_images |

---

### Files

#### `POST /api/v1/conversations/{conversation_id}/files`

Upload one or more files to a conversation.

**Request:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `files` | File[] | One or more files (max 50MB each) |

**Supported File Types:**
- **PDF:** `.pdf`
- **Images:** `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tiff`, `.gif`
- **Audio:** `.mp3`, `.wav`, `.ogg`, `.flac`, `.aac`, `.m4a`

**Response (201):**
```json
{
  "files": [
    {
      "id": "file-uuid-1",
      "original_filename": "report.pdf",
      "mime_type": "application/pdf",
      "file_size": 524288,
      "file_type": "pdf",
      "status": "uploaded",
      "operation": null,
      "error_message": null,
      "created_at": "2026-03-05T10:00:30Z",
      "has_output": false
    }
  ],
  "message": "Successfully uploaded 1 file(s)"
}
```

**Errors:**
- `404`: Conversation not found
- `400`: No valid files (wrong type or too large)

---

#### `GET /api/v1/conversations/{conversation_id}/files`

List all files in a conversation.

**Response (200):** Array of FileOut objects (same structure as above).

---

#### `GET /api/v1/conversations/{conversation_id}/files/{file_id}/download`

Download the **original** uploaded file.

**Response:** Binary file download with proper filename and MIME type.  
**Errors:**
- `404`: File not found
- `410`: File deleted (data retention)

---

#### `GET /api/v1/conversations/{conversation_id}/files/{file_id}/output`

Download the **processed/output** file (result of an operation).  
The file is automatically marked as "exported" after download.

**Response:** Binary file download (filename prefixed with `modex_`).  
**Errors:**
- `404`: File not found or no output available

---

## Data Models

### Conversation
```typescript
interface Conversation {
  id: string;           // UUID
  title: string | null;
  created_at: string;   // ISO 8601
  updated_at: string;   // ISO 8601
  expires_at: string;   // ISO 8601 — auto-delete after this time
}
```

### Message
```typescript
interface Message {
  id: string;           // UUID
  role: "user" | "assistant";
  content: string;      // May contain markdown
  file_ids: string | null;  // JSON string of file ID array, or null
  created_at: string;   // ISO 8601
}
```

### File
```typescript
interface FileInfo {
  id: string;               // UUID
  original_filename: string;
  mime_type: string;
  file_size: number;        // bytes
  file_type: "pdf" | "image" | "audio";
  status: "uploaded" | "processing" | "completed" | "failed";
  operation: string | null; // e.g. "compress_pdf", "resize_image"
  error_message: string | null;
  created_at: string;       // ISO 8601
  has_output: boolean;      // true if a processed file is available for download
}
```

### ChatRequest
```typescript
interface ChatRequest {
  message: string;          // 1-5000 chars, natural language instruction
  file_ids?: string[];      // optional, specific files to operate on
}
```

### ChatResponse
```typescript
interface ChatResponse {
  message: Message;
  processed_files: FileInfo[];  // files that were processed (may be empty)
}
```

---

## Frontend Implementation Notes

### 1. Chat Interface Layout
- Left sidebar: conversation list (from `GET /conversations`)
- Main area: chat messages (from `GET /conversations/{id}/messages`)
- Bottom: message input + file upload button
- Each assistant message may include download buttons for processed files

### 2. File Upload UX
- Drag & drop or click-to-browse
- Show upload progress
- After upload, show file chips/tags in the chat area (filename, size, type icon)
- These files are then referenced when user sends a message

### 3. Message Rendering
- User messages: plain text with file attachment indicators
- Assistant messages: render as **markdown** (they may include bold, newlines, results)
- If `processed_files` is non-empty and has `has_output: true`, show download buttons

### 4. File Status Indicators
- `uploaded` → ⬆️ ready
- `processing` → ⏳ spinner
- `completed` → ✅ done (show download if has_output)
- `failed` → ❌ error (show error_message)

### 5. Expiry
- Show `expires_at` as a countdown or time remaining
- Conversations auto-expire — fetch fresh list on page load
- Prompt user to download before expiry

### 6. Error Handling
- 404: conversation/file not found → redirect or show error
- 410: file deleted (retention policy) → show "file expired" message
- 400: bad upload → show validation error
- 500: server error → show generic error + retry button

### 7. CORS
The backend allows requests from `http://localhost:3000` and `http://localhost:5173` by default. Update `.env` `CORS_ORIGINS` for production.

---

## Running the Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1     # Windows
pip install -r requirements.txt

# Set your Gemini API key
# Edit .env → GEMINI_API_KEY=your-key-here

# Start the server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Requirements
- Python 3.10+
- FFmpeg (required for audio processing — `pydub` depends on it)
  - Windows: `winget install FFmpeg` or download from https://ffmpeg.org
  - The `ffmpeg` and `ffprobe` binaries must be in PATH

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | (required) | Google Gemini API key |
| `DATABASE_URL` | `sqlite+aiosqlite:///./modex.db` | Database connection string |
| `UPLOAD_DIR` | `./storage/uploads` | Directory for uploaded files |
| `OUTPUT_DIR` | `./storage/outputs` | Directory for processed output files |
| `MAX_FILE_SIZE_MB` | `50` | Maximum upload size per file |
| `ALLOWED_EXTENSIONS` | `pdf,png,jpg,...` | Comma-separated allowed file extensions |
| `CORS_ORIGINS` | `http://localhost:3000,...` | Comma-separated allowed CORS origins |
| `DATA_RETENTION_HOURS` | `24` | Hours before auto-deletion |

---

## Architecture

```
backend/
├── main.py                    # FastAPI app entry point
├── requirements.txt
├── .env                       # Environment variables
├── .env.example
├── core/
│   ├── config.py              # Settings (Pydantic)
│   ├── database.py            # SQLAlchemy async engine + session
│   └── data_retention.py      # Cleanup scheduler + file purge
├── models/
│   ├── schemas.py             # SQLAlchemy ORM models (Conversation, Message, FileRecord)
│   └── api_models.py          # Pydantic request/response models
├── services/
│   ├── ai_engine.py           # Gemini 2.5 Flash integration (interprets user requests)
│   ├── chat_service.py        # Orchestrates chat flow (message → AI → operation → response)
│   ├── file_service.py        # File upload, download, operation dispatch
│   ├── pdf_service.py         # PDF operations (compress, merge, split, rotate, convert)
│   ├── image_service.py       # Image operations (compress, resize, crop, convert)
│   └── audio_service.py       # Audio operations (compress, convert, trim, volume)
├── api/
│   └── routes/
│       ├── conversations.py   # Conversation + chat endpoints
│       └── files.py           # File upload + download endpoints
└── storage/                   # Auto-created, gitignored
    ├── uploads/               # Raw uploaded files (per conversation)
    └── outputs/               # Processed output files (per conversation)
```

### Flow Diagram

```
User sends chat message
        ↓
[conversations.py] POST /chat
        ↓
[chat_service.py] Orchestrates:
  1. Saves user message to DB
  2. Gathers file context
  3. Calls AI Engine
        ↓
[ai_engine.py] Sends to Gemini 2.5 Flash:
  - System prompt with all available operations
  - File descriptions (id, name, type, size)
  - Conversation history
  - User message
  → Returns structured JSON: {operation, file_ids, params, explanation}
        ↓
[file_service.py] Dispatches to correct service:
  → pdf_service.py / image_service.py / audio_service.py
        ↓
Saves result, returns to user
```
