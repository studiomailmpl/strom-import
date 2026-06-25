"""
PDF text and image extraction services.
Ported from app.py — extract_pdf_text, extract_pdf_pages_as_images, detect_invoice_currency.
"""

import base64
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 25


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    finally:
        doc.close()


def extract_pdf_pages_as_images(pdf_bytes: bytes, dpi: int = 200) -> list[str]:
    """Convert each PDF page to a base64-encoded PNG image for Claude Vision.
    Returns list of base64 strings (without data URI prefix).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        images = []
        zoom = dpi / 72  # 72 is default DPI
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            images.append(b64)
        if len(images) > MAX_PDF_PAGES:
            logger.warning(f"PDF has {len(images)} pages, truncating to {MAX_PDF_PAGES}")
            images = images[:MAX_PDF_PAGES]
        return images
    finally:
        doc.close()


def detect_invoice_currency(pdf_text: str) -> str:
    """Detect the currency of an invoice. Returns 'EUR' or 'DKK'."""
    text_lower = pdf_text.lower()
    # Explicit currency markers
    if "total dkk" in text_lower or "currency: dkk" in text_lower:
        return "DKK"
    if "drawn in: euro" in text_lower or "price(eur)" in text_lower or "amount(eur)" in text_lower:
        return "EUR"
    # Check for DKK symbol or text
    if " dkk" in text_lower and " eur" not in text_lower:
        return "DKK"
    # Default: EUR (most brand invoices are in EUR)
    return "EUR"
