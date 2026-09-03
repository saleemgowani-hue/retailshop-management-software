"""
ocr_saas.py — Bill-photo OCR text extraction.

Ported from app.py's extract_text_from_bill_image(). Purely a reading
AID for the shopkeeper — it does NOT auto-fill or auto-save anything.
No tenant logic needed here at all (this never touches the database).

Cloud note (Section M of the analysis): pytesseract needs the SYSTEM
Tesseract binary, not just the Python wrapper — on Streamlit Community
Cloud this requires a packages.txt file with the line "tesseract-ocr"
so it gets apt-installed at build time. Without it, OCR_AVAILABLE below
just evaluates False and the app degrades gracefully (no crash).
"""

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


def extract_text_from_bill_image(uploaded_file):
    """Returns (text: str|None, error: str|None). Never raises — any
    failure (corrupt image, missing Tesseract binary, unsupported
    format) is reported back as a message, not an exception, so a bad
    photo upload can never crash the Stock Purchase page."""
    if not OCR_AVAILABLE:
        return None, "OCR library install nahi hai."
    try:
        img = Image.open(uploaded_file)
        text = pytesseract.image_to_string(img)
        return text.strip(), None
    except Exception as e:
        return None, f"OCR nahi chal paya: {e}"
