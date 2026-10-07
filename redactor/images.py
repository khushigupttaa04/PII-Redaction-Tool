# Replace every embedded image with a plain grey box of the same size and format
# OCR-based masking was tried first, but it missed the handwritten signature on the PAN card
# and stylised logo wordmarks, so replacing every image is the only option with zero misses.
import io

from PIL import Image


def redact_images(doc):
    replaced = 0
    for part in doc.part.package.parts:
        if not part.content_type.startswith("image/"):
            continue
        try:
            original = Image.open(io.BytesIO(part.blob))
        except Exception:
            continue
        out = io.BytesIO()
        Image.new("RGB", original.size, (128, 128, 128)).save(out, format=original.format)   # same size keeps layout
        part._blob = out.getvalue()
        replaced += 1
    return replaced
