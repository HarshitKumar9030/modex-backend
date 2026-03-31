"""
Study Service — Generates educational content: study packs, worksheets,
exams, flashcards, formula sheets, and revision notes.

All outputs go through LaTeX compilation for professional PDF output.
Uses Gemini to generate content, then compiles via pdf_service.
"""

import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional

from google import genai
from google.genai import types

from core.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    return genai.Client(api_key=settings.GEMINI_API_KEY)


# ── LaTeX Templates ───────────────────────────────────────────────

STUDY_SCHEDULE_PROMPT = """You are an expert academic tutor. Generate a detailed study schedule.

Topic: {topic}
Duration: {duration}
Level: {level}

Output COMPLETE, COMPILABLE LaTeX code for a professional study schedule PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr, tabularx, booktabs.
Do NOT use tcolorbox, tikz, or pgfplots. Use \\colorbox and \\fbox for colored boxes instead.
Include clear time blocks, learning objectives per block, key concepts to cover, and quick-review checkpoints.
Make it visually clean with sections, colored headers, and tables.
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

FORMULA_SHEET_PROMPT = """You are an expert academic tutor. Generate a comprehensive formula/cheat sheet.

Topic: {topic}
Level: {level}
Style: {style}

Output COMPLETE, COMPILABLE LaTeX code for a professional formula sheet PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, multicol, fancyhdr, array.
Do NOT use tcolorbox or tikz. Use \\colorbox and \\fbox for colored boxes instead.
Organize formulas by sub-topic in colored boxes. Include variable definitions.
Use compact layout (small margins, multicol). Every formula must be correct.
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

REVISION_NOTES_PROMPT = """You are an expert academic tutor. Generate thorough revision notes.

Topic: {topic}
Level: {level}
Source material: {source_text}

Output COMPLETE, COMPILABLE LaTeX code for professional revision notes PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr, mdframed.
Do NOT use tcolorbox or tikz. Use \\colorbox, \\fbox, or mdframed environments for colored boxes instead.
Include: key definitions, theorems, worked examples, common pitfalls, exam tips.
Use colored definition/theorem boxes, numbered examples, and clear section structure.
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

PRACTICE_QUESTIONS_PROMPT = """You are an expert academic tutor. Generate practice questions with worked solutions.

Topic: {topic}
Level: {level}
Difficulty: {difficulty}
Number of questions: {count}

Output COMPLETE, COMPILABLE LaTeX code for a practice question set PDF.
Use: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem packages.
Structure:
1. Questions section (numbered, with marks allocation)
2. Detailed worked solutions section (step-by-step)
Include a mix of calculation, proof, and application questions.
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

FLASHCARDS_PROMPT = """You are an expert academic tutor. Generate flashcard content.

Topic: {topic}
Level: {level}
Count: {count}

Output COMPLETE, COMPILABLE LaTeX code for printable flashcards PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, array, tabularx.
Do NOT use tcolorbox or tikz. Use \\fbox and \\colorbox for card borders and backgrounds.
Create cards with:
- Front: Question or term (in a bordered box using \\fbox)
- Back: Answer or definition (in a colored box using \\colorbox)
Layout: 2 cards per row, 4 per page. Use tabular for layout, \\hrule for cut lines.
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

WORKSHEET_PROMPT = """You are an expert teacher. Generate a worksheet.

Topic: {topic}
Level: {level}
Difficulty: {difficulty}
Type: {worksheet_type}
Include answer key: {include_answers}

Output COMPLETE, COMPILABLE LaTeX code for a professional worksheet PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr.
Do NOT use tcolorbox, tikz, or pgfplots.
Include: title header with space for name/date, instructions, numbered questions with
adequate working space, and if requested, a detachable answer key section.
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

EXAM_PROMPT = """You are an expert examiner. Generate an exam paper.

Topic: {topic}
Level: {level}
Difficulty: {difficulty}
Duration: {duration}
Total marks: {total_marks}
Sections: {sections}

Output COMPLETE, COMPILABLE LaTeX code for a professional exam paper PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr.
Do NOT use tcolorbox, tikz, pgfplots, or lastpage.
Include:
- Cover page with instructions, duration, total marks
- Multiple sections (MCQ, short answer, long answer) with marks per question
- Proper formatting: headers, footers with page numbers
- Separate marking scheme / answer key at the end
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

STUDY_PACK_PROMPT = """You are an expert academic tutor. Generate a COMPLETE study pack.

Topic: {topic}
Duration: {duration}
Level: {level}

Output COMPLETE, COMPILABLE LaTeX code for a comprehensive study pack PDF that contains ALL of the following sections in ONE document:

1. STUDY SCHEDULE — Time-blocked plan for the given duration
2. FORMULA SHEET — All key formulas organized by sub-topic in colored boxes
3. REVISION NOTES — Key concepts, definitions, theorems with examples
4. PRACTICE QUESTIONS — 10-15 mixed difficulty problems
5. WORKED SOLUTIONS — Step-by-step solutions to all practice questions
6. FLASHCARD SUMMARY — Quick Q&A pairs for rapid revision

Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr, multicol, tabularx, booktabs, array, hyperref.
Do NOT use tcolorbox, tikz, pgfplots, or lastpage. Use \\colorbox and \\fbox for colored boxes instead.
Use \\newpage between major sections. Professional formatting with colored boxes (\\colorbox), clean headers.
The LaTeX code MUST compile without errors. Output ONLY the LaTeX code, nothing else."""

CLEANUP_NOTES_PROMPT = """You are an expert academic editor. Clean up and restructure the following messy/rough notes into polished, well-organized study material.

Raw input:
{source_text}

Output COMPLETE, COMPILABLE LaTeX code for a polished notes PDF.
Use ONLY these packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr, mdframed.
Do NOT use tcolorbox or tikz. Use \\colorbox, \\fbox, or mdframed for colored boxes.
- Fix any mathematical notation errors
- Organize content into clear sections and subsections
- Add formatted definition/theorem boxes
- Correct spelling and grammar
- Add proper equation numbering
- Include a table of contents
The LaTeX code must compile without errors. Output ONLY the LaTeX code, nothing else."""

TEMPLATE_MAP = {
    "lecture_notes": "Generate professional lecture notes with numbered definitions, theorems in colored boxes, proofs, and examples.",
    "cheat_sheet": "Generate a compact cheat sheet with small margins, multi-column layout, all key formulas and shortcuts in colored mini-boxes.",
    "assignment": "Generate an assignment handout with numbered questions, space for working, marks allocation, and submission instructions.",
    "academic_report": "Generate a clean academic report template with abstract, introduction, methodology, results, conclusion sections.",
    "study_planner": "Generate a study planner with weekly/daily blocks, goal setting section, progress tracking, and topic checklist.",
}

TEMPLATE_PROMPT = """You are an expert document designer. Generate a {template_name}.

Topic: {topic}
Level: {level}
Additional instructions: {extra}

Style description: {style_desc}

Output COMPLETE, COMPILABLE LaTeX code for this document.
Use ONLY these standard packages: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr, multicol, tabularx, booktabs, array, hyperref.
Do NOT use tcolorbox, tikz, or pgfplots. Use \\colorbox and \\fbox for colored boxes.
Professional formatting. The LaTeX code MUST compile without errors. Output ONLY the LaTeX code, nothing else."""

CUSTOM_PDF_PROMPT = """You are an expert LaTeX document generator.

User request:
{user_prompt}

Generate EXACTLY what the user asked for as a PDF-ready LaTeX document.
Requirements:
- Output COMPLETE, COMPILABLE LaTeX code only.
- Start with \\documentclass and end with \\end{{document}}.
- Do NOT add extra sections not requested.
- Do NOT add commentary, markdown fences, or explanations.
- Preserve requested scope, detail level, and formatting intent as closely as possible.
"""


class StudyService:
    """Generates educational content via Gemini + LaTeX pipeline."""

    @staticmethod
    async def _generate_latex(prompt: str) -> str:
        """Send prompt to Gemini and return raw LaTeX code."""
        client = _get_client()

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=settings.AI_MODEL,
                    contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                    config=types.GenerateContentConfig(
                        system_instruction="You are a LaTeX document generator. Output ONLY valid, complete, compilable LaTeX code. No explanations, no markdown fences, just raw LaTeX starting with \\documentclass and ending with \\end{document}. CRITICAL: Do NOT use tcolorbox, tikz, pgfplots, or lastpage packages. Use only standard packages: amsmath, amssymb, geometry, xcolor, enumitem, fancyhdr, multicol, tabularx, booktabs, array, hyperref, mdframed. For colored boxes use \\colorbox or \\fbox. IMPORTANT: Follow the prompt exactly and include only requested sections/content. Do not add extra sections, bonus material, or assumptions beyond the prompt.",
                        temperature=0.2,
                        max_output_tokens=settings.AI_MAX_TOKENS,
                    ),
                ),
                timeout=settings.STUDY_GEN_TIMEOUT_SECONDS,
            )

            if not response.text:
                raise ValueError("Empty response from AI")

            latex = response.text.strip()
            # Strip markdown fences if AI wraps it
            if latex.startswith("```"):
                lines = latex.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                latex = "\n".join(lines)

            return latex

        except Exception as e:
            logger.error(f"Failed to generate LaTeX: {e}")
            raise ValueError(f"Failed to generate content: {e}")

    @staticmethod
    async def _compile_and_save(latex_code: str, output_path: str) -> str:
        """Compile LaTeX to PDF via pdf_service."""
        from services.pdf_service import PDFService
        return await PDFService.generate_latex_pdf(latex_code, output_path, {})

    # ── Public API ────────────────────────────────────────────────

    @staticmethod
    async def generate_study_pack(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        duration = params.get("duration", "3 hours")
        level = params.get("level", "intermediate")

        prompt = STUDY_PACK_PROMPT.format(topic=topic, duration=duration, level=level)
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated complete study pack for '{topic}' ({duration}, {level} level)."

    @staticmethod
    async def generate_study_schedule(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        duration = params.get("duration", "3 hours")
        level = params.get("level", "intermediate")

        prompt = STUDY_SCHEDULE_PROMPT.format(topic=topic, duration=duration, level=level)
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated study schedule for '{topic}' ({duration})."

    @staticmethod
    async def generate_formula_sheet(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        level = params.get("level", "intermediate")
        style = params.get("style", "comprehensive")

        prompt = FORMULA_SHEET_PROMPT.format(topic=topic, level=level, style=style)
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated formula sheet for '{topic}'."

    @staticmethod
    async def generate_revision_notes(output_path: str, params: Dict[str, Any], source_text: str = "") -> str:
        topic = params.get("topic", "General Mathematics")
        level = params.get("level", "intermediate")

        prompt = REVISION_NOTES_PROMPT.format(
            topic=topic, level=level,
            source_text=source_text[:8000] if source_text else "No source material provided — generate from your knowledge."
        )
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated revision notes for '{topic}'."

    @staticmethod
    async def generate_practice_questions(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        level = params.get("level", "intermediate")
        difficulty = params.get("difficulty", "mixed")
        count = params.get("count", 10)

        prompt = PRACTICE_QUESTIONS_PROMPT.format(
            topic=topic, level=level, difficulty=difficulty, count=count
        )
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated {count} practice questions with worked solutions for '{topic}'."

    @staticmethod
    async def generate_flashcards(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        level = params.get("level", "intermediate")
        count = params.get("count", 20)

        prompt = FLASHCARDS_PROMPT.format(topic=topic, level=level, count=count)
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated {count} flashcards for '{topic}'."

    @staticmethod
    async def generate_worksheet(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        level = params.get("level", "intermediate")
        difficulty = params.get("difficulty", "mixed")
        worksheet_type = params.get("worksheet_type", "practice")
        include_answers = params.get("include_answers", True)

        prompt = WORKSHEET_PROMPT.format(
            topic=topic, level=level, difficulty=difficulty,
            worksheet_type=worksheet_type,
            include_answers="Yes, include answer key" if include_answers else "No answer key"
        )
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated {difficulty} {worksheet_type} worksheet for '{topic}'."

    @staticmethod
    async def generate_exam(output_path: str, params: Dict[str, Any]) -> str:
        topic = params.get("topic", "General Mathematics")
        level = params.get("level", "intermediate")
        difficulty = params.get("difficulty", "mixed")
        duration = params.get("duration", "2 hours")
        total_marks = params.get("total_marks", 100)
        sections = params.get("sections", "MCQ, Short Answer, Long Answer")

        prompt = EXAM_PROMPT.format(
            topic=topic, level=level, difficulty=difficulty,
            duration=duration, total_marks=total_marks, sections=sections
        )
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated exam paper for '{topic}' ({total_marks} marks, {duration})."

    @staticmethod
    async def cleanup_notes(output_path: str, params: Dict[str, Any], source_text: str = "") -> str:
        if not source_text:
            raise ValueError("No source text provided to clean up.")

        prompt = CLEANUP_NOTES_PROMPT.format(source_text=source_text[:15000])
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return "Cleaned up and restructured notes into polished PDF."

    @staticmethod
    async def generate_from_template(output_path: str, params: Dict[str, Any]) -> str:
        template_name = params.get("template", "lecture_notes")
        topic = params.get("topic", "General")
        level = params.get("level", "intermediate")
        extra = params.get("extra_instructions", "")

        style_desc = TEMPLATE_MAP.get(template_name, TEMPLATE_MAP["lecture_notes"])

        prompt = TEMPLATE_PROMPT.format(
            template_name=template_name.replace("_", " "),
            topic=topic, level=level, extra=extra, style_desc=style_desc
        )
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return f"Generated {template_name.replace('_', ' ')} for '{topic}'."

    @staticmethod
    async def generate_custom_pdf(output_path: str, params: Dict[str, Any]) -> str:
        """Generate a custom PDF by following the user's prompt exactly."""
        user_prompt = str(params.get("prompt", "")).strip()
        if not user_prompt:
            raise ValueError("No prompt provided for custom PDF generation.")

        prompt = CUSTOM_PDF_PROMPT.format(user_prompt=user_prompt)
        latex = await StudyService._generate_latex(prompt)
        await StudyService._compile_and_save(latex, output_path)
        return "Generated custom PDF from your prompt."

    @staticmethod
    async def formula_ocr(image_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """Extract math formulas from an image and produce clean LaTeX PDF."""
        import base64
        client = _get_client()

        with open(image_path, "rb") as f:
            image_data = f.read()

        b64 = base64.b64encode(image_data).decode("utf-8")
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=settings.AI_MODEL,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_bytes(data=image_data, mime_type=mime),
                                types.Part.from_text(text="Extract ALL mathematical formulas, equations, and text from this image. Convert everything into clean, well-organized LaTeX. Output COMPLETE, COMPILABLE LaTeX code starting with \\documentclass and ending with \\end{document}. Use amsmath, amssymb packages. Format nicely with numbered equations and clear sections."),
                            ]
                        )
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=settings.AI_MAX_TOKENS,
                    ),
                ),
                timeout=60,
            )

            if not response.text:
                raise ValueError("Could not extract formulas from image")

            latex = response.text.strip()
            if latex.startswith("```"):
                lines = latex.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                latex = "\n".join(lines)

            from services.pdf_service import PDFService
            await PDFService.generate_latex_pdf(latex, output_path, {})
            return "Extracted formulas from image and generated clean LaTeX PDF."

        except Exception as e:
            logger.error(f"Formula OCR failed: {e}")
            raise ValueError(f"Failed to extract formulas: {e}")

    @staticmethod
    async def synthesize_files(file_paths: List[Dict[str, str]], output_path: str, params: Dict[str, Any]) -> str:
        """Combine content from multiple files into one synthesized document."""
        from PyPDF2 import PdfReader
        client = _get_client()

        action = params.get("action", "combine")
        topic = params.get("topic", "")

        # Extract text from all sources
        all_text = []
        for fp in file_paths:
            path = fp["path"]
            fname = fp["filename"]
            ftype = fp["type"]

            try:
                if ftype == "pdf":
                    reader = PdfReader(path)
                    text = ""
                    for page in reader.pages[:30]:  # cap at 30 pages
                        text += (page.extract_text() or "") + "\n"
                    all_text.append(f"=== Source: {fname} ===\n{text[:5000]}")
                elif ftype == "document":
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        all_text.append(f"=== Source: {fname} ===\n{f.read(5000)}")
            except Exception as e:
                logger.warning(f"Could not read {fname}: {e}")

        if not all_text:
            raise ValueError("Could not extract content from any of the uploaded files.")

        combined = "\n\n".join(all_text)

        action_prompts = {
            "combine": f"Combine ALL the content below into ONE comprehensive, well-structured revision booklet. Merge overlapping topics, remove duplicates, organize by theme.",
            "compare": f"Compare and contrast the content from these sources. Highlight similarities, differences, and unique points from each source.",
            "extract_formulas": f"Extract ONLY the mathematical formulas, equations, and key definitions from all sources below. Organize them by topic.",
            "summarize": f"Create a master summary of all the content below. Include the most important concepts, formulas, and key points from every source.",
        }

        action_text = action_prompts.get(action, action_prompts["combine"])
        if topic:
            action_text += f" Focus on: {topic}"

        prompt = f"""{action_text}

Source material:
{combined[:20000]}

Output COMPLETE, COMPILABLE LaTeX code for a professional PDF document.
Use: \\documentclass{{article}}, amsmath, amssymb, geometry, xcolor, enumitem, tcolorbox, fancyhdr packages.
Professional formatting with sections, colored boxes for key concepts.
The LaTeX code MUST compile without errors. Output ONLY the LaTeX code, nothing else."""

        latex = await StudyService._generate_latex(prompt)
        from services.pdf_service import PDFService
        await PDFService.generate_latex_pdf(latex, output_path, {})
        return f"Synthesized {len(file_paths)} files into a single document ({action})."
