"""
Diagram Service — Generates diagrams as image or PDF outputs.
"""

import os
import asyncio
import logging
import tempfile
import shutil
from typing import Dict, Any

from google import genai
from google.genai import types

from core.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    return genai.Client(api_key=settings.GEMINI_API_KEY)


TIKZ_DIAGRAM_PROMPT = """You are an expert at generating TikZ diagrams for LaTeX. Generate a diagram based on:

Subject: {subject}
Diagram type: {diagram_type}
Description: {description}

Output COMPLETE, COMPILABLE LaTeX code that generates this diagram.
Use: \\documentclass[tikz,border=10pt]{{standalone}} or \\documentclass{{article}} with geometry.
Required packages: tikz, pgfplots, amsmath, amssymb, xcolor.
If the diagram involves 3D: use tikz-3dplot.
If the diagram involves plots: use pgfplots with axis environment.
If the diagram involves geometry: use tkz-euclide or basic tikz.
If the diagram involves circuits: use circuitikz.

Make it publication-quality with proper labels, colors, and annotations.
The LaTeX code MUST compile without errors. Output ONLY the LaTeX code, nothing else."""

MERMAID_DIAGRAM_PROMPT = """You are an expert at generating Mermaid diagrams. Generate a diagram based on:

Subject: {subject}
Diagram type: {diagram_type}
Description: {description}

Output ONLY valid Mermaid diagram syntax. No explanation, no markdown fences.
Supported types: flowchart, sequenceDiagram, classDiagram, stateDiagram, erDiagram, gantt, pie, mindmap.
Make it detailed and well-structured."""

MATPLOTLIB_PROMPT = """You are an expert at generating Python matplotlib code. Generate a plot based on:

Subject: {subject}
Plot type: {plot_type}
Description: {description}

Output ONLY valid Python code that:
1. Imports matplotlib.pyplot and numpy
2. Creates the plot with proper labels, title, colors, grid
3. Saves to a file using plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')
4. Uses the variable OUTPUT_PATH (it will be set before execution)

Make it publication-quality. Output ONLY the Python code, nothing else."""


class DiagramService:
    """Generates diagrams using LaTeX, Mermaid, or Matplotlib."""

    @staticmethod
    async def _generate_content(prompt: str) -> str:
        client = _get_client()

        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=settings.AI_MODEL,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction="You are a diagram code generator. Output ONLY valid code. No explanations, no markdown fences.",
                    temperature=0.1,
                    max_output_tokens=settings.AI_MAX_TOKENS,
                ),
            ),
            timeout=60,
        )

        if not response.text:
            raise ValueError("Empty response from AI")

        code = response.text.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            code = "\n".join(lines)
        return code

    @staticmethod
    async def generate_tikz_diagram(output_path: str, params: Dict[str, Any]) -> str:
        """Generate a diagram via LaTeX and save as image or PDF."""
        subject = params.get("subject", params.get("topic", "mathematics"))
        diagram_type = params.get("diagram_type", "general")
        description = params.get("description", params.get("topic", "a clear, labeled diagram"))

        prompt = TIKZ_DIAGRAM_PROMPT.format(
            subject=subject, diagram_type=diagram_type, description=description
        )
        latex = await DiagramService._generate_content(prompt)

        ext = os.path.splitext(output_path)[1].lower()
        if ext == ".pdf":
            from services.pdf_service import PDFService
            await PDFService.generate_latex_pdf(latex, output_path, {})
        else:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_pdf = os.path.join(temp_dir, "diagram.pdf")
                from services.pdf_service import PDFService
                await PDFService.generate_latex_pdf(latex, temp_pdf, {})
                await DiagramService._render_pdf_to_image(temp_pdf, output_path)
        return "Generated diagram successfully."

    @staticmethod
    async def generate_matplotlib_plot(output_path: str, params: Dict[str, Any]) -> str:
        """Generate a plot and save as image or PDF."""
        subject = params.get("subject", params.get("topic", "mathematics"))
        plot_type = params.get("plot_type", params.get("diagram_type", "line plot"))
        description = params.get("description", params.get("topic", "a clear, labeled plot"))

        prompt = MATPLOTLIB_PROMPT.format(
            subject=subject, plot_type=plot_type, description=description
        )
        code = await DiagramService._generate_content(prompt)

        # Replace OUTPUT_PATH placeholder and execute
        code = code.replace("OUTPUT_PATH", repr(output_path))
        code = code.replace("output_path", repr(output_path))

        # Ensure it saves to our path
        if "savefig" not in code:
            code += f"\nimport matplotlib.pyplot as plt\nplt.savefig({repr(output_path)}, dpi=150, bbox_inches='tight')"

        try:
            # Execute in a temp namespace
            exec_globals = {"__builtins__": __builtins__}
            await asyncio.to_thread(exec, code, exec_globals)

            if not os.path.exists(output_path):
                raise FileNotFoundError("Matplotlib did not produce output file")

            return "Generated diagram successfully."
        except Exception as e:
            logger.error(f"Matplotlib execution failed: {e}")
            logger.info("Falling back to LaTeX renderer...")
            return await DiagramService.generate_tikz_diagram(output_path, params)

    @staticmethod
    async def generate_diagram(output_path: str, params: Dict[str, Any]) -> str:
        """Auto-select the best diagram engine and generate."""
        engine = params.get("engine", "tikz").lower()
        diagram_type = params.get("diagram_type", "general").lower()

        # Auto-detect best engine
        flowchart_types = ["flowchart", "sequence", "class", "state", "er", "gantt", "pie", "mindmap"]
        plot_types = ["line", "bar", "scatter", "histogram", "heatmap", "contour", "surface", "plot"]

        if engine == "mermaid" or diagram_type in flowchart_types:
            # Generate mermaid, then fall back to TikZ (mermaid needs mmdc CLI)
            if shutil.which("mmdc"):
                return await DiagramService._generate_mermaid_diagram(output_path, params)
            else:
                logger.info("mmdc not available, using TikZ fallback for flowchart")
                return await DiagramService.generate_tikz_diagram(output_path, params)
        elif engine == "matplotlib" or diagram_type in plot_types:
            return await DiagramService.generate_matplotlib_plot(output_path, params)
        else:
            return await DiagramService.generate_tikz_diagram(output_path, params)

    @staticmethod
    async def _generate_mermaid_diagram(output_path: str, params: Dict[str, Any]) -> str:
        """Generate a Mermaid diagram and render it with mmdc."""
        subject = params.get("subject", params.get("topic", ""))
        diagram_type = params.get("diagram_type", "flowchart")
        description = params.get("description", params.get("topic", ""))

        prompt = MERMAID_DIAGRAM_PROMPT.format(
            subject=subject, diagram_type=diagram_type, description=description
        )
        mermaid_code = await DiagramService._generate_content(prompt)

        with tempfile.NamedTemporaryFile(suffix=".mmd", mode="w", delete=False) as f:
            f.write(mermaid_code)
            mmd_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                "mmdc", "-i", mmd_path, "-o", output_path, "-t", "default",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(f"mmdc failed: {stderr.decode()}")
            return "Generated diagram successfully."
        except Exception as e:
            logger.warning(f"Mermaid failed: {e}, falling back to LaTeX renderer")
            return await DiagramService.generate_tikz_diagram(output_path, params)
        finally:
            os.unlink(mmd_path)

    @staticmethod
    async def _render_pdf_to_image(pdf_path: str, output_path: str) -> None:
        """Render the first page of a PDF to an image file."""
        from pdf2image import convert_from_path

        ext = os.path.splitext(output_path)[1].lower()
        image_format = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
        pages = await asyncio.to_thread(convert_from_path, pdf_path, dpi=220, first_page=1, last_page=1)
        if not pages:
            raise ValueError("No pages were produced while rendering the diagram")
        await asyncio.to_thread(pages[0].save, output_path, image_format)
