"""Convert supported knowledge files into editable Markdown previews."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Protocol

from app.services.knowledge_metadata import detect_knowledge_metadata

MAX_FILE_BYTES = 20 * 1024 * 1024
SUPPORTED_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class KnowledgeImportError(Exception):
    """Base error for safe file-preview failures."""


class UnsupportedKnowledgeFileError(KnowledgeImportError):
    """Raised when the uploaded format is unsupported."""


class InvalidKnowledgeFileError(KnowledgeImportError):
    """Raised when a file's content does not match its extension."""


class KnowledgeFileTooLargeError(KnowledgeImportError):
    """Raised when an upload exceeds the configured size limit."""


class OCRUnavailableError(KnowledgeImportError):
    """Raised when OCR is required but no provider is installed."""


class OCRProcessingError(KnowledgeImportError):
    """Raised when an OCR provider cannot process an image."""


class OCRProvider(Protocol):
    def extract_text(self, image_bytes: bytes) -> str:
        """Return recognized text from an encoded image."""


@dataclass(frozen=True)
class KnowledgeFilePreview:
    source_key: str
    title: str
    source_kind: str
    original_filename: str
    content: str
    applicability: str
    effective_date: date | None
    page_count: int
    warnings: list[str]


class _UnavailableOCRProvider:
    def extract_text(self, image_bytes: bytes) -> str:
        del image_bytes
        raise OCRUnavailableError(
            "当前环境未安装 OCR 引擎，请安装 rapidocr-onnxruntime 后重试"
        )


class _RapidOCRProvider:
    def __init__(self) -> None:
        self._engine = None
        self._lock = Lock()

    def extract_text(self, image_bytes: bytes) -> str:
        try:
            import numpy as np
            from PIL import Image
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OCRUnavailableError(
                "当前环境未安装 OCR 引擎，请安装 rapidocr-onnxruntime 后重试"
            ) from exc

        with self._lock:
            if self._engine is None:
                self._engine = RapidOCR()
            try:
                image = Image.open(BytesIO(image_bytes)).convert("RGB")
                pixels = np.asarray(image)
                result, _ = self._engine(pixels)
            except Exception as exc:
                raise OCRProcessingError("图片 OCR 识别失败，请人工录入内容") from exc

        if not result:
            return ""
        lines = []
        for row in result:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                text = str(row[1]).strip()
                if text:
                    lines.append(text)
        return "\n".join(lines)


_OCR_PROVIDER: OCRProvider | None = None
_OCR_PROVIDER_LOCK = Lock()


def get_ocr_provider() -> OCRProvider:
    """Return the process-wide OCR provider used by the preview endpoint."""
    global _OCR_PROVIDER
    if _OCR_PROVIDER is None:
        with _OCR_PROVIDER_LOCK:
            if _OCR_PROVIDER is None:
                try:
                    import rapidocr_onnxruntime  # noqa: F401
                except ImportError:
                    _OCR_PROVIDER = _UnavailableOCRProvider()
                else:
                    _OCR_PROVIDER = _RapidOCRProvider()
    return _OCR_PROVIDER


def preview_knowledge_file(
    filename: str | None,
    data: bytes,
    ocr_provider: OCRProvider,
) -> KnowledgeFilePreview:
    safe_name = Path(filename or "").name
    if not safe_name:
        raise InvalidKnowledgeFileError("请选择需要转换的知识文件")
    if len(data) > MAX_FILE_BYTES:
        raise KnowledgeFileTooLargeError("文件不能超过 20 MB")
    if not data:
        raise InvalidKnowledgeFileError("文件内容为空")

    extension = Path(safe_name).suffix.lower()
    if extension == ".doc":
        raise UnsupportedKnowledgeFileError(
            "暂不支持旧版 .doc，请另存为 .docx 后上传"
        )
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedKnowledgeFileError(
            "仅支持 TXT、Markdown、DOCX、PDF、PNG、JPG、JPEG、WEBP 文件"
        )

    title = Path(safe_name).stem.strip() or "未命名资料"
    if extension in {".txt", ".md", ".markdown"}:
        source_kind = "text"
        content = _decode_text(data)
        page_count = 0
        warnings: list[str] = []
    elif extension == ".docx":
        source_kind = "word"
        _require_signature(data, b"PK\x03\x04", "DOCX")
        content = _docx_to_markdown(data)
        page_count = 0
        warnings = []
    elif extension == ".pdf":
        source_kind = "pdf"
        _require_signature(data, b"%PDF-", "PDF")
        content, page_count, warnings = _pdf_to_markdown(data, ocr_provider)
    else:
        source_kind = "image"
        _validate_image(data)
        content = _ocr_markdown(data, ocr_provider)
        page_count = 1
        warnings = ["图片已使用 OCR 转换，请人工校对识别结果。"]

    if not content.strip():
        raise InvalidKnowledgeFileError("未能从文件中提取到可编辑文字")
    normalized_content = content.strip()
    source_key = f"file-{hashlib.sha256(data).hexdigest()}"
    metadata = detect_knowledge_metadata(
        normalized_content,
        source_key=source_key,
    )
    return KnowledgeFilePreview(
        source_key=source_key,
        title=title[:300],
        source_kind=source_kind,
        original_filename=safe_name[:500],
        content=normalized_content,
        applicability=metadata.applicability,
        effective_date=metadata.effective_date,
        page_count=page_count,
        warnings=warnings,
    )


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding).replace("\r\n", "\n").strip()
        except UnicodeDecodeError:
            continue
    raise InvalidKnowledgeFileError("文本文件编码无法识别，请转换为 UTF-8")


def _require_signature(data: bytes, signature: bytes, label: str) -> None:
    if not data.startswith(signature):
        raise InvalidKnowledgeFileError(f"文件内容与 {label} 格式不匹配")


def _docx_to_markdown(data: bytes) -> str:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(BytesIO(data))
    except Exception as exc:
        raise InvalidKnowledgeFileError("DOCX 文件内容损坏或无法解析") from exc

    blocks: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            markdown = _paragraph_markdown(paragraph)
            if markdown:
                blocks.append(markdown)
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            markdown = _table_markdown(table)
            if markdown:
                blocks.append(markdown)
    return "\n\n".join(blocks)


def _paragraph_markdown(paragraph) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""
    style_name = (paragraph.style.name if paragraph.style is not None else "") or ""
    heading = _heading_level(style_name)
    if heading is not None:
        return f"{'#' * heading} {text}"
    if style_name.startswith("List Bullet"):
        return f"- {text}"
    if style_name.startswith("List Number"):
        return f"1. {text}"
    return text


def _heading_level(style_name: str) -> int | None:
    normalized = style_name.strip().lower()
    if normalized == "title":
        return 1
    if normalized.startswith("heading "):
        suffix = normalized.removeprefix("heading ").strip()
        if suffix.isdigit():
            return min(max(int(suffix), 1), 6)
    return None


def _table_markdown(table) -> str:
    rows = []
    for row in table.rows:
        rows.append([_table_cell(cell.text) for cell in row.cells])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def _table_cell(value: str) -> str:
    return " ".join(value.replace("|", r"\|").split())


def _pdf_to_markdown(
    data: bytes,
    ocr_provider: OCRProvider,
) -> tuple[str, int, list[str]]:
    try:
        import pymupdf

        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise InvalidKnowledgeFileError("PDF 文件内容损坏或无法解析") from exc

    try:
        pages = [page.get_text("text").strip() for page in document]
        page_count = len(pages)
        warnings: list[str] = []
        if not any(_has_extractable_text(text) for text in pages):
            warnings.append("PDF 未包含可复制文字，已使用 OCR，请人工校对识别结果。")
            pages = [
                _ocr_markdown(_render_pdf_page(page), ocr_provider)
                for page in document
            ]
        elif any(not _has_extractable_text(text) for text in pages):
            warnings.append("部分 PDF 页面无可复制文字，已对这些页面使用 OCR。")
            converted = []
            for page, text in zip(document, pages, strict=True):
                if _has_extractable_text(text):
                    converted.append(text)
                else:
                    converted.append(_ocr_markdown(_render_pdf_page(page), ocr_provider))
            pages = converted
    finally:
        document.close()

    non_empty = [text for text in pages if text.strip()]
    if page_count == 1 and len(non_empty) == 1:
        content = non_empty[0].strip()
    else:
        content = "\n\n".join(
            f"## 第 {index} 页\n\n{text.strip()}"
            for index, text in enumerate(pages, start=1)
            if text.strip()
        )
    return content, page_count, warnings


def _has_extractable_text(value: str) -> bool:
    return len("".join(value.split())) >= 8


def _render_pdf_page(page) -> bytes:
    try:
        import pymupdf

        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        return pixmap.tobytes("png")
    except Exception as exc:
        raise InvalidKnowledgeFileError("扫描版 PDF 页面渲染失败") from exc


def _validate_image(data: bytes) -> None:
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise InvalidKnowledgeFileError("图片文件内容损坏或格式不受支持") from exc


def _ocr_markdown(data: bytes, ocr_provider: OCRProvider) -> str:
    try:
        text = ocr_provider.extract_text(data)
    except KnowledgeImportError:
        raise
    except Exception as exc:
        raise OCRProcessingError("OCR 识别失败，请人工录入内容") from exc
    if not text.strip():
        raise InvalidKnowledgeFileError("OCR 未识别到可用文字，请上传更清晰的图片")
    return text.replace("\r\n", "\n").strip()
