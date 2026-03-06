"""
Image processing service.

Operations:
  - compress_image      : Reduce image file size (target_size_kb or quality)
  - resize_image        : Resize to given dimensions
  - crop_image          : Crop to given box
  - convert_image       : Convert between formats (png, jpg, webp, bmp, tiff, gif)
  - remove_background   : Remove image background (AI-powered)
"""

import os
import io
import logging
from typing import Dict, Any

from PIL import Image

from core.config import settings

logger = logging.getLogger(__name__)

# Map of output formats
FORMAT_MAP = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "gif": "GIF",
}


class ImageService:

    # ── Compress ──────────────────────────────────────────────────

    @staticmethod
    async def compress_image(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Compress an image. Params:
          - target_size_kb (int, optional): target file size in KB
          - quality (int, optional): 1-100, default 80
          - format (str, optional): output format, default keeps original
        """
        target_kb = params.get("target_size_kb")
        quality = params.get("quality", 80)
        fmt = params.get("format")

        try:
            img = Image.open(input_path)

            # Determine output format
            if fmt:
                pil_format = FORMAT_MAP.get(fmt.lower(), "JPEG")
                if not output_path.lower().endswith(f".{fmt.lower()}"):
                    base = os.path.splitext(output_path)[0]
                    output_path = f"{base}.{fmt.lower()}"
            else:
                ext = os.path.splitext(input_path)[1].lstrip(".").lower()
                pil_format = FORMAT_MAP.get(ext, "JPEG")

            # Convert RGBA → RGB for JPEG
            if pil_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            if target_kb:
                _compress_to_target(img, output_path, pil_format, target_kb)
            else:
                img.save(output_path, format=pil_format, quality=quality, optimize=True)

            final_size = os.path.getsize(output_path) / 1024
            return f"Compressed image to {final_size:.1f} KB"

        except Exception as e:
            logger.error(f"Image compress failed: {e}")
            raise ValueError(f"Failed to compress image: {e}")

    # ── Resize ────────────────────────────────────────────────────

    @staticmethod
    async def resize_image(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Resize an image. Params:
          - width (int, optional)
          - height (int, optional)
          - scale (float, optional): e.g. 0.5 for half size
          - maintain_aspect (bool, default True)
        """
        try:
            img = Image.open(input_path)
            orig_w, orig_h = img.size

            scale = params.get("scale")
            width = params.get("width")
            height = params.get("height")
            maintain = params.get("maintain_aspect", True)

            if scale:
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
            elif width and height:
                if maintain:
                    img.thumbnail((width, height), Image.LANCZOS)
                    img.save(output_path, quality=95, optimize=True)
                    return f"Resized from {orig_w}x{orig_h} to {img.size[0]}x{img.size[1]}"
                else:
                    new_w, new_h = width, height
            elif width:
                ratio = width / orig_w
                new_w = width
                new_h = int(orig_h * ratio)
            elif height:
                ratio = height / orig_h
                new_h = height
                new_w = int(orig_w * ratio)
            else:
                raise ValueError("Provide width, height, or scale")

            resized = img.resize((new_w, new_h), Image.LANCZOS)

            # Preserve format
            ext = os.path.splitext(input_path)[1].lstrip(".").lower()
            pil_format = FORMAT_MAP.get(ext, "PNG")
            if pil_format == "JPEG" and resized.mode in ("RGBA", "P", "LA"):
                resized = resized.convert("RGB")

            resized.save(output_path, format=pil_format, quality=95, optimize=True)
            return f"Resized from {orig_w}x{orig_h} to {new_w}x{new_h}"

        except Exception as e:
            logger.error(f"Image resize failed: {e}")
            raise ValueError(f"Failed to resize image: {e}")

    # ── Crop ──────────────────────────────────────────────────────

    @staticmethod
    async def crop_image(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Crop an image. Params:
          - left (int): pixels from left
          - top (int): pixels from top
          - right (int): pixels from left to right edge
          - bottom (int): pixels from top to bottom edge
        Or:
          - x, y, width, height (alternative box definition)
        """
        try:
            img = Image.open(input_path)

            if "left" in params:
                box = (params["left"], params["top"], params["right"], params["bottom"])
            elif "x" in params:
                x, y = params["x"], params["y"]
                w, h = params["width"], params["height"]
                box = (x, y, x + w, y + h)
            else:
                raise ValueError("Provide crop coordinates (left/top/right/bottom or x/y/width/height)")

            cropped = img.crop(box)

            ext = os.path.splitext(input_path)[1].lstrip(".").lower()
            pil_format = FORMAT_MAP.get(ext, "PNG")
            if pil_format == "JPEG" and cropped.mode in ("RGBA", "P", "LA"):
                cropped = cropped.convert("RGB")

            cropped.save(output_path, format=pil_format, quality=95, optimize=True)
            return f"Cropped to {cropped.size[0]}x{cropped.size[1]}"

        except Exception as e:
            logger.error(f"Image crop failed: {e}")
            raise ValueError(f"Failed to crop image: {e}")

    # ── Convert ───────────────────────────────────────────────────

    @staticmethod
    async def convert_image(input_path: str, output_path: str, params: Dict[str, Any]) -> tuple[str, str]:
        """
        Convert image format. Params:
          - format (str): target format, e.g. "png", "jpg", "webp"
          - quality (int, optional): 1-100 for lossy formats

        Returns:
          (message, actual_output_path)
        """
        target_format = params.get("format", "png").lower()
        quality = params.get("quality", 90)

        try:
            img = Image.open(input_path)
            pil_format = FORMAT_MAP.get(target_format)
            if not pil_format:
                raise ValueError(f"Unsupported format: {target_format}")

            # Fix output extension
            base = os.path.splitext(output_path)[0]
            output_path = f"{base}.{target_format}"

            if pil_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            save_kwargs = {"format": pil_format, "optimize": True}
            if pil_format in ("JPEG", "WEBP"):
                save_kwargs["quality"] = quality

            img.save(output_path, **save_kwargs)
            return f"Converted to {target_format.upper()} ({os.path.getsize(output_path) / 1024:.1f} KB)", output_path

        except Exception as e:
            logger.error(f"Image convert failed: {e}")
            raise ValueError(f"Failed to convert image: {e}")

    # ── Remove Background ─────────────────────────────────────────

    @staticmethod
    async def remove_background(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Remove the background from an image. Params:
          - model (str, optional): rembg model name, default "u2net"
        Always outputs PNG (transparency support).
        """
        from rembg import remove as rembg_remove

        try:
            img = Image.open(input_path).convert("RGBA")
            result = rembg_remove(img)

            # Always save as PNG for transparency
            base = os.path.splitext(output_path)[0]
            output_path = f"{base}.png"
            result.save(output_path, format="PNG")

            return f"Background removed — saved as PNG ({os.path.getsize(output_path) / 1024:.1f} KB)"

        except ImportError:
            raise ValueError("Background removal is not available — rembg is not installed")
        except Exception as e:
            logger.error(f"Background removal failed: {e}")
            raise ValueError(f"Failed to remove background: {e}")


# ── Private helpers ───────────────────────────────────────────────

def _compress_to_target(img: Image.Image, output_path: str, pil_format: str, target_kb: float):
    """Binary-search quality to hit a target file size."""
    if pil_format not in ("JPEG", "WEBP"):
        # For lossless formats, just save optimised
        img.save(output_path, format=pil_format, optimize=True)
        return

    lo, hi = 5, 95
    best_quality = lo

    for _ in range(10):  # max 10 iterations
        mid = (lo + hi) // 2
        buf = io.BytesIO()

        save_img = img
        if pil_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
            save_img = img.convert("RGB")

        save_img.save(buf, format=pil_format, quality=mid, optimize=True)
        size_kb = buf.tell() / 1024

        if size_kb <= target_kb:
            best_quality = mid
            lo = mid + 1
        else:
            hi = mid - 1

    # Save final
    save_img = img
    if pil_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
        save_img = img.convert("RGB")
    save_img.save(output_path, format=pil_format, quality=best_quality, optimize=True)
