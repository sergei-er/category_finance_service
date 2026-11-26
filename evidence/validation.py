import hashlib
import zipfile
from pathlib import Path

from django.core.exceptions import ValidationError

MAX_SIZE = 10 * 1024 * 1024


def inspect_upload(upload):
    if not upload.size or upload.size > MAX_SIZE:
        raise ValidationError("Размер файла должен быть от 1 байта до 10 МиБ")
    suffix = Path(upload.name).suffix.lower()
    head = upload.read(16)
    upload.seek(0)
    mime = None
    if suffix == ".pdf" and head.startswith(b"%PDF-"):
        mime = "application/pdf"
    elif suffix == ".txt":
        try:
            text = upload.read().decode("utf-8")
            if "\x00" in text:
                raise ValueError
            mime = "text/plain"
        except (UnicodeError, ValueError):
            raise ValidationError("Текст должен быть UTF-8 без нулевых байтов") from None
    elif suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(upload) as archive:
                entries = archive.infolist()
                names = {e.filename for e in entries}
                expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                if (
                    len(entries) > 1000
                    or sum(e.file_size for e in entries) > 50 * 1024 * 1024
                    or any("vbaproject" in n.lower() or ".." in n.split("/") for n in names)
                    or expected not in names
                    or "[Content_Types].xml" not in names
                ):
                    raise ValueError
            mime = "application/vnd.openxmlformats-officedocument." + (
                "wordprocessingml.document" if suffix == ".docx" else "spreadsheetml.sheet"
            )
        except (zipfile.BadZipFile, ValueError):
            raise ValidationError("Некорректный или неподдерживаемый Office-файл") from None
    if mime is None:
        raise ValidationError(
            "Допускаются PDF, TXT UTF-8, DOCX и XLSX; формат должен соответствовать содержимому"
        )
    upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    name = (
        Path(upload.name.replace("\\", "/")).name.replace("\r", "").replace("\n", "")[:180]
        or "document"
    )
    return name, mime, digest.hexdigest()
