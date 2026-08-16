"""Small allow-list validator for document uploads."""

from pathlib import Path

from werkzeug.utils import secure_filename


ALLOWED_UPLOADS = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".txt": {"text/plain"},
    ".csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"},
}


def validate_upload(upload):
    if upload is None:
        raise ValueError("Choose a document to upload.")
    filename = secure_filename(upload.filename or "")
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_UPLOADS:
        raise ValueError("Upload a PDF, PNG, JPEG, plain-text, or CSV file.")
    declared = (upload.mimetype or "").lower()
    if declared not in ALLOWED_UPLOADS[suffix]:
        raise ValueError("The file content type does not match an allowed upload.")
    header = upload.stream.read(512)
    upload.stream.seek(0)
    if suffix == ".pdf" and not header.startswith(b"%PDF-"):
        raise ValueError("The uploaded file is not a valid PDF.")
    if suffix == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The uploaded file is not a valid PNG image.")
    if suffix in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
        raise ValueError("The uploaded file is not a valid JPEG image.")
    if suffix in {".txt", ".csv"}:
        try:
            header.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Text uploads must use UTF-8 encoding.") from exc
    return filename
