"""
PDF processing service.

Operations:
  - compress_pdf     : Reduce PDF file size (target_size_kb or quality)
  - merge_pdf        : Merge multiple PDFs into one
  - split_pdf        : Split a PDF into individual pages or page ranges
  - rotate_pdf       : Rotate pages by N degrees
  - pdf_to_images    : Convert each page to an image
  - images_to_pdf    : Convert images into a single PDF
  - document_to_pdf  : Convert text/markdown/csv/json/html/xml/etc to PDF
  - watermark_pdf    : Add text watermark to PDF pages
  - protect_pdf      : Password-protect a PDF
  - unlock_pdf       : Remove password from a PDF
  - ocr_pdf          : OCR a scanned PDF to make it searchable
"""

import os
import io
import html
import logging
from typing import List, Optional, Dict, Any

from PyPDF2 import PdfReader, PdfWriter
import pikepdf
import img2pdf
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
from reportlab.lib.enums import TA_LEFT

from pdf2image import convert_from_path

from core.config import settings

logger = logging.getLogger(__name__)


def _looks_like_pdf_bytes(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


class PDFService:

    # ── Compress ──────────────────────────────────────────────────

    @staticmethod
    async def compress_pdf(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Compress a PDF. Params:
          - target_size_kb (int, optional): target file size in KB
          - quality (str): "low", "medium", "high" — maps to compression levels
        """
        target_kb = params.get("target_size_kb")
        quality = params.get("quality", "medium")

        try:
            with pikepdf.open(input_path) as pdf:
                # Remove unreferenced objects and compress streams
                pdf.remove_unreferenced_resources()
                
                # Compress images inside the PDF
                for page in pdf.pages:
                    _compress_pdf_images(page, quality)

                pdf.save(
                    output_path,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    recompress_flate=True,
                )

            # If target size specified, do iterative compression
            if target_kb:
                current_size = os.path.getsize(output_path) / 1024
                if current_size > target_kb:
                    _iterative_compress(input_path, output_path, target_kb)

            final_size = os.path.getsize(output_path) / 1024
            return f"Compressed PDF: {final_size:.1f} KB"

        except Exception as e:
            logger.error(f"PDF compress failed: {e}")
            raise ValueError(f"Failed to compress PDF: {e}")

    # ── Merge ─────────────────────────────────────────────────────

    @staticmethod
    async def merge_pdf(input_paths: List[str], output_path: str, params: Dict[str, Any]) -> str:
        """Merge multiple PDFs into one."""
        try:
            writer = PdfWriter()
            total_pages = 0
            for path in input_paths:
                reader = PdfReader(path)
                for page in reader.pages:
                    writer.add_page(page)
                    total_pages += 1

            with open(output_path, "wb") as f:
                writer.write(f)

            return f"Merged {len(input_paths)} PDFs into one ({total_pages} pages)"

        except Exception as e:
            logger.error(f"PDF merge failed: {e}")
            raise ValueError(f"Failed to merge PDFs: {e}")

    # ── Split ─────────────────────────────────────────────────────

    @staticmethod
    async def split_pdf(input_path: str, output_dir: str, params: Dict[str, Any]) -> List[str]:
        """
        Split a PDF. Params:
          - pages (list[int], optional): specific page numbers (1-indexed)
          - ranges (str, optional): e.g. "1-3,5,7-10"
        Returns list of output file paths.
        """
        try:
            reader = PdfReader(input_path)
            total = len(reader.pages)
            pages_to_extract = _parse_page_ranges(params, total)
            output_paths = []

            base_name = os.path.splitext(os.path.basename(input_path))[0]

            for i, page_num in enumerate(pages_to_extract):
                writer = PdfWriter()
                writer.add_page(reader.pages[page_num - 1])
                out_path = os.path.join(output_dir, f"{base_name}_page_{page_num}.pdf")
                with open(out_path, "wb") as f:
                    writer.write(f)
                output_paths.append(out_path)

            return output_paths

        except Exception as e:
            logger.error(f"PDF split failed: {e}")
            raise ValueError(f"Failed to split PDF: {e}")

    # ── Rotate ────────────────────────────────────────────────────

    @staticmethod
    async def rotate_pdf(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Rotate PDF pages. Params:
          - degrees (int): 90, 180, 270
          - pages (list[int], optional): specific pages to rotate (1-indexed), default all
        """
        degrees = params.get("degrees", 90)
        try:
            reader = PdfReader(input_path)
            writer = PdfWriter()
            target_pages = params.get("pages")

            for i, page in enumerate(reader.pages):
                if target_pages is None or (i + 1) in target_pages:
                    page.rotate(degrees)
                writer.add_page(page)

            with open(output_path, "wb") as f:
                writer.write(f)

            rotated_count = len(target_pages) if target_pages else len(reader.pages)
            return f"Rotated {rotated_count} pages by {degrees}°"

        except Exception as e:
            logger.error(f"PDF rotate failed: {e}")
            raise ValueError(f"Failed to rotate PDF: {e}")

    # ── PDF → Images ──────────────────────────────────────────────

    @staticmethod
    async def pdf_to_images(input_path: str, output_dir: str, params: Dict[str, Any]) -> List[str]:
        """
        Convert PDF pages to images. Params:
          - format (str): "png" or "jpg", default "png"
          - dpi (int): resolution, default 150
        """
        fmt = params.get("format", "png")
        dpi = params.get("dpi", 150)

        try:
            reader = PdfReader(input_path)
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_paths = []

            # Use pikepdf to render — extract images from each page
            with pikepdf.open(input_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    # Extract all images from this page
                    for j, (name, raw) in enumerate(page.images.items()):
                        pil_image = Image.open(io.BytesIO(raw.read_raw_bytes()))
                        out_path = os.path.join(output_dir, f"{base_name}_p{i+1}_{j}.{fmt}")
                        pil_image.save(out_path, format=fmt.upper(), dpi=(dpi, dpi))
                        output_paths.append(out_path)

            if not output_paths:
                return [f"No extractable images found in PDF (PDF has {len(reader.pages)} pages)"]

            return output_paths

        except Exception as e:
            logger.error(f"PDF to images failed: {e}")
            raise ValueError(f"Failed to convert PDF to images: {e}")

    # ── PDF pages → Images (full render) ──────────────────────────

    @staticmethod
    async def pdf_pages_to_images(input_path: str, output_dir: str, params: Dict[str, Any]) -> List[str]:
        """
        Render each PDF page as a full image (including text, charts, diagrams).
        Uses pdf2image (poppler) for high-fidelity rendering.
        Params:
          - format (str): "png" or "jpg", default "png"
          - dpi (int): resolution, default 200
          - pages (list[int], optional): specific pages (1-indexed)
        """
        fmt = params.get("format", "png")
        dpi = params.get("dpi", 200)
        target_pages = params.get("pages")

        try:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_paths = []

            convert_kwargs = {"dpi": dpi, "fmt": fmt}
            if target_pages:
                convert_kwargs["first_page"] = min(target_pages)
                convert_kwargs["last_page"] = max(target_pages)

            images = convert_from_path(input_path, **convert_kwargs)

            for i, pil_image in enumerate(images):
                page_num = (target_pages[i] if target_pages and i < len(target_pages)
                            else (convert_kwargs.get("first_page", 1) + i))
                out_path = os.path.join(output_dir, f"{base_name}_page{page_num}.{fmt}")
                pil_image.save(out_path, format=fmt.upper())
                output_paths.append(out_path)

            return output_paths if output_paths else [f"No pages rendered from PDF"]

        except Exception as e:
            logger.error(f"PDF pages to images failed: {e}")
            raise ValueError(f"Failed to render PDF pages as images: {e}")

    # ── Extract embedded images from PDF ──────────────────────────

    @staticmethod
    async def extract_pdf_images(input_path: str, output_dir: str, params: Dict[str, Any]) -> List[str]:
        """
        Extract all embedded images (photos, charts, diagrams) from PDF.
        Uses pikepdf to pull raw image streams, with size filtering.
        Params:
          - format (str): output format, default "png"
          - min_size (int): minimum pixel dimension to keep (skip tiny icons), default 50
        """
        fmt = params.get("format", "png")
        min_size = params.get("min_size", 50)

        try:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_paths = []

            with pikepdf.open(input_path) as pdf:
                img_idx = 0
                for page_num, page in enumerate(pdf.pages, 1):
                    if not hasattr(page, "images"):
                        continue
                    for name, raw in page.images.items():
                        try:
                            pil_image = Image.open(io.BytesIO(raw.read_raw_bytes()))
                            w, h = pil_image.size
                            # Filter out tiny decorative images
                            if w < min_size or h < min_size:
                                continue
                            out_path = os.path.join(
                                output_dir,
                                f"{base_name}_img_p{page_num}_{img_idx}.{fmt}"
                            )
                            if fmt.upper() == "JPEG" and pil_image.mode in ("RGBA", "P", "LA"):
                                pil_image = pil_image.convert("RGB")
                            pil_image.save(out_path, format=fmt.upper())
                            output_paths.append(out_path)
                            img_idx += 1
                        except Exception:
                            continue

            if not output_paths:
                # Fallback: render pages as images so user still gets something
                return await PDFService.pdf_pages_to_images(input_path, output_dir, params)

            return output_paths

        except Exception as e:
            logger.error(f"Extract PDF images failed: {e}")
            raise ValueError(f"Failed to extract images from PDF: {e}")

    # ── Images → PDF ──────────────────────────────────────────────

    @staticmethod
    async def images_to_pdf(image_paths: List[str], output_path: str, params: Dict[str, Any]) -> str:
        """Convert a list of images into a single PDF."""
        try:
            valid_paths = [p for p in image_paths if os.path.exists(p)]
            if not valid_paths:
                raise ValueError("No valid image files provided")

            # Use img2pdf for lossless conversion
            pdf_bytes = img2pdf.convert(valid_paths)
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)

            return f"Created PDF from {len(valid_paths)} images"

        except Exception as e:
            logger.error(f"Images to PDF failed: {e}")
            raise ValueError(f"Failed to convert images to PDF: {e}")

    # ── Document → PDF ────────────────────────────────────────────

    @staticmethod
    async def document_to_pdf(input_path: str, output_path: str, source_ext: str, params: Dict[str, Any]) -> str:
        """
        Convert a text-based document to PDF.
        Supports: txt, md, csv, json, html, xml, rtf, log
        """
        try:
            with open(input_path, "r", encoding="utf-8", errors="replace") as f:
                raw_text = f.read()

            if not raw_text.strip():
                raise ValueError("File is empty — nothing to convert.")

            if source_ext == "md":
                return await PDFService._markdown_to_pdf(raw_text, output_path, params)
            elif source_ext == "html":
                return await PDFService._html_to_pdf(raw_text, output_path, params)
            else:
                # Plain text path: txt, csv, json, xml, rtf, log
                return await PDFService._text_to_pdf(raw_text, output_path, source_ext, params)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Document to PDF failed: {e}")
            raise ValueError(f"Failed to convert document to PDF: {e}")

    @staticmethod
    async def _text_to_pdf(text: str, output_path: str, source_ext: str, params: Dict[str, Any]) -> str:
        """Render plain text content into a clean PDF."""
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )

        styles = getSampleStyleSheet()
        code_style = ParagraphStyle(
            "CodeBlock",
            parent=styles["Code"],
            fontSize=9,
            leading=13,
            fontName="Courier",
            spaceAfter=4,
        )

        story = []
        # Title
        title_ext = source_ext.upper()
        story.append(Paragraph(f"<b>{title_ext} Document</b>", styles["Heading2"]))
        story.append(Spacer(1, 6 * mm))

        # For structured formats, use monospaced font
        if source_ext in ("json", "xml", "csv", "log"):
            for line in text.splitlines():
                safe = html.escape(line) or "&nbsp;"
                story.append(Preformatted(safe, code_style))
        else:
            # Regular text
            for line in text.splitlines():
                if line.strip() == "":
                    story.append(Spacer(1, 3 * mm))
                else:
                    safe = html.escape(line)
                    story.append(Paragraph(safe, styles["Normal"]))

        doc.build(story)
        size_kb = os.path.getsize(output_path) / 1024
        return f"Converted {source_ext.upper()} to PDF ({size_kb:.1f} KB)"

    @staticmethod
    async def _markdown_to_pdf(md_text: str, output_path: str, params: Dict[str, Any]) -> str:
        """Render Markdown content into a styled PDF."""
        try:
            import markdown as md_lib
        except ImportError:
            # Fallback: treat as plain text
            return await PDFService._text_to_pdf(md_text, output_path, "md", params)

        # Convert markdown to HTML
        html_content = md_lib.markdown(
            md_text,
            extensions=["tables", "fenced_code", "codehilite", "toc", "nl2br"],
        )

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )

        styles = getSampleStyleSheet()
        story = []

        # Parse the HTML into paragraphs for reportlab
        # Split on block-level tags
        import re
        blocks = re.split(r"(<h[1-6].*?</h[1-6]>|<p>.*?</p>|<pre>.*?</pre>|<ul>.*?</ul>|<ol>.*?</ol>|<table>.*?</table>|<blockquote>.*?</blockquote>|<hr\s*/?>)", html_content, flags=re.DOTALL)

        heading_map = {
            "h1": styles["Heading1"],
            "h2": styles["Heading2"],
            "h3": styles["Heading3"],
        }
        code_style = ParagraphStyle(
            "MDCode",
            parent=styles["Code"],
            fontSize=9,
            leading=13,
            fontName="Courier",
            backColor="#f5f5f5",
            spaceAfter=6,
        )

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            # Detect heading level
            heading_match = re.match(r"<(h[1-6]).*?>(.*?)</\1>", block, re.DOTALL)
            if heading_match:
                level = heading_match.group(1)
                content = heading_match.group(2)
                style = heading_map.get(level, styles["Heading3"])
                story.append(Paragraph(content, style))
                story.append(Spacer(1, 2 * mm))
                continue

            # Pre/code blocks
            if block.startswith("<pre"):
                code_text = re.sub(r"<.*?>", "", block)
                code_text = html.escape(code_text) if "&" not in code_text else code_text
                story.append(Preformatted(code_text, code_style))
                story.append(Spacer(1, 3 * mm))
                continue

            # Horizontal rule
            if block.startswith("<hr"):
                story.append(Spacer(1, 4 * mm))
                continue

            # Everything else: render as paragraph (reportlab handles basic HTML)
            # Strip wrapping p tags but keep inline formatting
            inner = re.sub(r"^<p>|</p>$", "", block, flags=re.DOTALL).strip()
            if inner:
                story.append(Paragraph(inner, styles["Normal"]))
                story.append(Spacer(1, 2 * mm))

        if not story:
            story.append(Paragraph("(empty document)", styles["Normal"]))

        doc.build(story)
        size_kb = os.path.getsize(output_path) / 1024
        return f"Converted Markdown to PDF ({size_kb:.1f} KB)"

    @staticmethod
    async def _html_to_pdf(html_text: str, output_path: str, params: Dict[str, Any]) -> str:
        """Render HTML content into a PDF. Uses reportlab's built-in HTML paragraph support."""
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )

        styles = getSampleStyleSheet()
        story = []

        import re
        # Strip <html>, <head>, <body> wrappers and <style>/<script> blocks
        clean = re.sub(r"<(head|style|script).*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"</?(?:html|body|!DOCTYPE)[^>]*>", "", clean, flags=re.IGNORECASE)

        # Split into block-level chunks
        blocks = re.split(r"(<(?:h[1-6]|p|div|pre|ul|ol|table|blockquote|hr)[^>]*>.*?</(?:h[1-6]|p|div|pre|ul|ol|table|blockquote)>|<hr\s*/?>)", clean, flags=re.DOTALL | re.IGNORECASE)

        for block in blocks:
            block = block.strip()
            if not block:
                continue
            try:
                story.append(Paragraph(block, styles["Normal"]))
                story.append(Spacer(1, 2 * mm))
            except Exception:
                # If reportlab rejects the HTML, fall back to escaped text
                safe = html.escape(re.sub(r"<.*?>", "", block))
                if safe.strip():
                    story.append(Paragraph(safe, styles["Normal"]))

        if not story:
            story.append(Paragraph("(empty document)", styles["Normal"]))

        doc.build(story)
        size_kb = os.path.getsize(output_path) / 1024
        return f"Converted HTML to PDF ({size_kb:.1f} KB)"

    # ── Watermark ─────────────────────────────────────────────────

    @staticmethod
    async def watermark_pdf(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Add a text watermark to every page. Params:
          - text (str): watermark text, default "CONFIDENTIAL"
          - opacity (float): 0.0-1.0, default 0.15
          - angle (int): rotation degrees, default 45
          - font_size (int): default 60
        """
        from reportlab.lib.colors import Color

        text = params.get("text", "CONFIDENTIAL")
        opacity = max(0.01, min(1.0, params.get("opacity", 0.15)))
        angle = params.get("angle", 45)
        font_size = params.get("font_size", 60)

        try:
            reader = PdfReader(input_path)
            page_w = float(reader.pages[0].mediabox.width)
            page_h = float(reader.pages[0].mediabox.height)

            # Create watermark overlay PDF in memory
            watermark_buf = io.BytesIO()
            from reportlab.pdfgen import canvas as rl_canvas
            c = rl_canvas.Canvas(watermark_buf, pagesize=(page_w, page_h))
            c.saveState()
            c.setFont("Helvetica-Bold", font_size)
            c.setFillColor(Color(0.5, 0.5, 0.5, alpha=opacity))
            c.translate(page_w / 2, page_h / 2)
            c.rotate(angle)
            c.drawCentredString(0, 0, text)
            c.restoreState()
            c.save()
            watermark_buf.seek(0)

            watermark_reader = PdfReader(watermark_buf)
            watermark_page = watermark_reader.pages[0]

            writer = PdfWriter()
            for page in reader.pages:
                page.merge_page(watermark_page)
                writer.add_page(page)

            with open(output_path, "wb") as f:
                writer.write(f)

            return f"Added watermark '{text}' to {len(reader.pages)} pages"

        except Exception as e:
            logger.error(f"PDF watermark failed: {e}")
            raise ValueError(f"Failed to watermark PDF: {e}")

    # ── Password protect ──────────────────────────────────────────

    @staticmethod
    async def protect_pdf(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Password-protect a PDF. Params:
          - password (str): the password to set
          - owner_password (str, optional): separate owner password for editing
        """
        password = params.get("password", "modex123")
        owner_password = params.get("owner_password", password)

        try:
            with pikepdf.open(input_path) as pdf:
                encryption = pikepdf.Encryption(
                    owner=owner_password,
                    user=password,
                    R=6,  # AES-256
                )
                pdf.save(output_path, encryption=encryption)

            return f"PDF protected with password (AES-256 encryption)"

        except Exception as e:
            logger.error(f"PDF protect failed: {e}")
            raise ValueError(f"Failed to protect PDF: {e}")

    # ── Unlock / remove password ──────────────────────────────────

    @staticmethod
    async def unlock_pdf(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Remove password from a PDF. Params:
          - password (str): the current password
        """
        password = params.get("password", "")

        try:
            with pikepdf.open(input_path, password=password) as pdf:
                pdf.save(output_path)

            return "PDF unlocked — password removed"

        except pikepdf.PasswordError:
            raise ValueError("Incorrect password. Please provide the correct PDF password.")
        except Exception as e:
            logger.error(f"PDF unlock failed: {e}")
            raise ValueError(f"Failed to unlock PDF: {e}")

    # ── OCR (scanned PDF → searchable PDF) ────────────────────────

    @staticmethod
    async def ocr_pdf(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        OCR a scanned PDF: render pages as images, run Tesseract, produce a searchable PDF.
        Params:
          - language (str): Tesseract language code, default "eng"
          - dpi (int): render resolution, default 300
        """
        import pytesseract

        language = params.get("language", "eng")
        dpi = params.get("dpi", 300)

        try:
            images = convert_from_path(input_path, dpi=dpi)
            if not images:
                raise ValueError("Could not render any pages from the PDF")

            pdf_pages = []
            for img in images:
                page_pdf = pytesseract.image_to_pdf_or_hocr(img, lang=language, extension="pdf")
                pdf_pages.append(page_pdf)

            # Merge OCR'd pages into single PDF
            writer = PdfWriter()
            for page_bytes in pdf_pages:
                reader = PdfReader(io.BytesIO(page_bytes))
                for page in reader.pages:
                    writer.add_page(page)

            with open(output_path, "wb") as f:
                writer.write(f)

            return f"OCR complete — {len(images)} pages made searchable (lang: {language})"

        except ImportError:
            raise ValueError("OCR is not available — pytesseract is not installed")
        except Exception as e:
            logger.error(f"PDF OCR failed: {e}")
            raise ValueError(f"Failed to OCR PDF: {e}")

    @staticmethod
    async def generate_latex_pdf(latex_code: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Generates a PDF layout from raw LaTeX code.
        Will attempt to use local pdflatex first (for privacy and speed),
        and fall back to a public LaTeX compilation API if latex is not installed.
        If compilation fails, automatically extracts the error and asks Gemini
        to fix the LaTeX, then retries once.
        """
        import asyncio
        import shutil
        import httpx
        from tempfile import TemporaryDirectory

        MAX_ATTEMPTS = 2
        current_latex = latex_code

        for attempt in range(MAX_ATTEMPTS):
            # 1. Try local pdflatex first
            local_log = ""
            if shutil.which("pdflatex"):
                with TemporaryDirectory() as temp_dir:
                    tex_file = os.path.join(temp_dir, "document.tex")
                    with open(tex_file, "w", encoding="utf-8") as f:
                        f.write(current_latex)

                    try:
                        process = await asyncio.create_subprocess_exec(
                            "pdflatex", "-interaction=nonstopmode", "document.tex",
                            cwd=temp_dir,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await process.communicate()

                        pdf_file = os.path.join(temp_dir, "document.pdf")
                        if process.returncode == 0 and os.path.exists(pdf_file):
                            with open(pdf_file, "rb") as compiled_pdf:
                                pdf_bytes = compiled_pdf.read()
                            if _looks_like_pdf_bytes(pdf_bytes):
                                with open(output_path, "wb") as f:
                                    f.write(pdf_bytes)
                                return "Generated document successfully."

                        local_log = stdout.decode("utf-8", errors="replace")[:4000]
                        logger.warning(
                            "Local pdflatex did not produce a valid PDF (attempt %d). returncode=%s",
                            attempt + 1, process.returncode,
                        )
                    except Exception as e:
                        logger.warning(f"Local pdflatex failed: {e}. Falling back to API.")

            # 2. Fall back to texlive.net API
            cloud_log = ""
            try:
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    data = {
                        "filename[]": "document.tex",
                        "engine": "pdflatex",
                        "return": "pdf",
                    }
                    files = {
                        "filecontents[]": ("document.tex", current_latex.encode("utf-8"))
                    }
                    response = await client.post("https://texlive.net/cgi-bin/latexcgi", data=data, files=files)
                    response.raise_for_status()

                    if _looks_like_pdf_bytes(response.content):
                        with open(output_path, "wb") as f:
                            f.write(response.content)
                        return "Generated document successfully."

                    # Compilation failed — capture the log for auto-fix
                    cloud_log = response.text[:4000]
                    logger.warning("Cloud compiler returned log instead of PDF (attempt %d)", attempt + 1)
            except Exception as e:
                logger.warning(f"Cloud API call failed (attempt {attempt + 1}): {e}")

            # 3. If this is not the last attempt, auto-fix the LaTeX using Gemini
            if attempt < MAX_ATTEMPTS - 1:
                error_log = cloud_log or local_log
                error_snippet = _extract_latex_errors(error_log)
                if error_snippet:
                    logger.info("Attempting auto-fix of LaTeX (errors: %s)", error_snippet[:200])
                    fixed = await _autofix_latex(current_latex, error_snippet)
                    if fixed and fixed != current_latex:
                        current_latex = fixed
                        continue
                # No useful error or autofix identical — don't bother retrying
                break

        raise ValueError("Failed to compile LaTeX to PDF. Ensure the LaTeX code is valid and doesn't contain errors.")

# ── Private helpers ───────────────────────────────────────────────


import re as _re


def _extract_latex_errors(log_text: str) -> str:
    """Pull the meaningful error lines out of a pdflatex / texlive log."""
    if not log_text:
        return ""
    error_lines: list[str] = []
    for line in log_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("!") or "Error" in stripped or "Undefined control sequence" in stripped:
            error_lines.append(stripped)
        elif stripped.startswith("l.") and error_lines:
            error_lines.append(stripped)
    if not error_lines:
        # Fallback: grab last 15 lines which usually contain the error
        tail = log_text.strip().splitlines()[-15:]
        return "\n".join(tail)
    return "\n".join(error_lines[:20])


async def _autofix_latex(broken_latex: str, error_snippet: str) -> str:
    """Ask Gemini to fix broken LaTeX code based on the compiler error."""
    import asyncio
    from google import genai
    from google.genai import types
    from core.config import settings

    prompt = (
        "The following LaTeX code failed to compile. Fix ALL errors and return "
        "ONLY the corrected, complete LaTeX code (from \\documentclass to \\end{document}). "
        "Do NOT wrap in markdown fences. Do NOT include explanations.\n\n"
        "IMPORTANT RULES FOR THE FIX:\n"
        "- Use ONLY standard packages available in TeX Live (article, amsmath, amssymb, geometry, "
        "xcolor, enumitem, fancyhdr, multicol, hyperref, graphicx, tabularx, booktabs, array).\n"
        "- AVOID tcolorbox, tikz, pgfplots, and other heavy packages unless absolutely needed.\n"
        "- Replace tcolorbox colored boxes with simple \\fbox or \\colorbox commands.\n"
        "- Replace tikz drawings with plain text or tabular layouts.\n"
        "- Ensure every \\begin has a matching \\end.\n"
        "- Do NOT use any undefined commands.\n\n"
        f"COMPILER ERRORS:\n{error_snippet}\n\n"
        f"BROKEN LATEX CODE:\n{broken_latex}"
    )

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=settings.AI_MODEL,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=settings.AI_MAX_TOKENS,
                ),
            ),
            timeout=60,
        )

        if not response.text:
            return broken_latex

        fixed = response.text.strip()
        # Strip markdown fences if present
        if fixed.startswith("```"):
            lines = fixed.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            fixed = "\n".join(lines)

        return fixed
    except Exception as e:
        logger.warning(f"Auto-fix LaTeX via Gemini failed: {e}")
        return broken_latex


def _compress_pdf_images(page, quality: str):
    """Recompress images inside a PDF page."""
    quality_map = {"low": 30, "medium": 55, "high": 80}
    jpeg_quality = quality_map.get(quality, 55)

    try:
        if hasattr(page, 'images'):
            for name, raw in page.images.items():
                try:
                    pil_img = Image.open(io.BytesIO(raw.read_raw_bytes()))
                    buf = io.BytesIO()
                    pil_img = pil_img.convert("RGB")
                    pil_img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                    raw.write(buf.getvalue())
                except Exception:
                    pass  # Skip images that can't be recompressed
    except Exception:
        pass


def _iterative_compress(input_path: str, output_path: str, target_kb: float):
    """Try progressively lower quality to hit target size."""
    for quality in [50, 35, 20, 10]:
        try:
            with pikepdf.open(input_path) as pdf:
                for page in pdf.pages:
                    _compress_pdf_images(page, "low")
                pdf.save(
                    output_path,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    recompress_flate=True,
                )
            if os.path.getsize(output_path) / 1024 <= target_kb:
                break
        except Exception:
            break


def _parse_page_ranges(params: Dict[str, Any], total_pages: int) -> List[int]:
    """Parse page numbers from params."""
    if "pages" in params and params["pages"]:
        return [p for p in params["pages"] if 1 <= p <= total_pages]

    if "ranges" in params and params["ranges"]:
        pages = set()
        for part in params["ranges"].split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                for p in range(int(start), int(end) + 1):
                    if 1 <= p <= total_pages:
                        pages.add(p)
            else:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p)
        return sorted(pages)

    # Default: all pages
    return list(range(1, total_pages + 1))
