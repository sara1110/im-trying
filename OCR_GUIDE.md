# OCR Guide for Scanned Annual Reports

If a bank's annual report is image-based (scanned PDF), text extraction via `pymupdf4llm` / `PyMuPDF` may return very little data. Use OCR fallback:

## 1) Install OCR dependencies

```bash
pip install pytesseract pdf2image pillow
# Linux system package (example Debian/Ubuntu)
sudo apt-get install -y tesseract-ocr poppler-utils
```

For Indonesian language OCR quality:

```bash
sudo apt-get install -y tesseract-ocr-ind
```

## 2) OCR fallback flow

1. Detect low text density from extracted markdown (e.g., fewer than ~2,000 chars).
2. Convert each PDF page to image (`pdf2image.convert_from_bytes`).
3. Run OCR per page with `pytesseract.image_to_string(image, lang="ind+eng")`.
4. Concatenate OCR text and pass it into the same extraction functions (`extract_metric`, unit detection, currency conversion).

## 3) Minimal OCR helper function

```python
from pdf2image import convert_from_bytes
import pytesseract


def ocr_pdf_bytes(pdf_bytes: bytes) -> str:
    pages = convert_from_bytes(pdf_bytes, dpi=300)
    texts = []
    for img in pages:
        texts.append(pytesseract.image_to_string(img, lang="ind+eng"))
    return "\n".join(texts)
```

## 4) Practical tips

- Use `dpi=300` or `dpi=400` for better table readability.
- Preprocess images (grayscale + threshold) for noisy scans.
- OCR can misread separators (`.` and `,`) so keep robust numeric normalization.
- Always keep evidence lines in output notes for manual verification.
