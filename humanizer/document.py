"""
High-Fidelity Document Processing Module.
Supports .docx with run-level style preservation (bold, italics, citations, sub/superscript, colors),
along with PDF extraction, TXT handling, and persistent session checkpointing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator, Optional

import docx
from docx import Document
from docx.shared import RGBColor

from humanizer.config import HumanizerConfig

logger = logging.getLogger(__name__)

_SKIP_STYLES = {"code", "verbatim", "source code", "header", "footer"}
REF_LINE = re.compile(r"^\s*\[\d+\]")
CAPTION_LINE = re.compile(r"^\s*(Figure|Fig\.|Table)\s*\d", re.IGNORECASE)


class Checkpoint:
    """Per-session persistent state for a humanize run."""
    DIR = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "humanizer_pro" / "sessions"

    def __init__(
        self,
        sid: str,
        input_text: str,
        cfg_key: dict,
        total: int,
        mode: str = "text",
        source_path: Optional[str] = None,
    ) -> None:
        self.sid = sid
        self.path = self.DIR / f"{sid}.json"
        self.total = total
        self.mode = mode
        self.source_path = source_path
        self._blocks: dict[str, str] = {}
        self._save_lock = threading.Lock()
        self._created_at = datetime.now().isoformat(timespec="seconds")
        self._load_existing(input_text, cfg_key)
        self._input = input_text
        self._cfg_key = cfg_key

    def _load_existing(self, input_text: str, cfg_key: dict) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("input") == input_text and d.get("cfg") == cfg_key:
                self._blocks = dict(d.get("blocks") or {})
                self._created_at = d.get("created_at", self._created_at)
        except Exception:
            pass

    def get(self, idx: int) -> Optional[str]:
        return self._blocks.get(str(idx))

    def done_count(self) -> int:
        return len(self._blocks)

    def clear(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass

    def put(self, idx: int, text: str) -> None:
        with self._save_lock:
            self._blocks[str(idx)] = text
            try:
                self.DIR.mkdir(parents=True, exist_ok=True)
                payload = {
                    "version": 2,
                    "sid": self.sid,
                    "mode": self.mode,
                    "source_path": self.source_path,
                    "input": self._input,
                    "cfg": self._cfg_key,
                    "total": self.total,
                    "blocks": self._blocks,
                    "created_at": self._created_at,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except Exception as e:
                logger.warning(f"Checkpoint save failed: {e}")

    @staticmethod
    def compute_id(input_text: str, cfg_key: dict) -> str:
        import hashlib
        h = hashlib.sha1()
        h.update(input_text.encode("utf-8", errors="replace"))
        h.update(json.dumps(cfg_key, sort_keys=True).encode("utf-8"))
        return h.hexdigest()[:16]


@dataclass
class RunStyle:
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    subscript: Optional[bool] = None
    superscript: Optional[bool] = None
    font_name: Optional[str] = None
    font_size: Optional[object] = None
    font_color: Optional[RGBColor] = None
    style_name: Optional[str] = None


def _extract_run_style(run) -> RunStyle:
    rs = RunStyle(
        bold=run.bold,
        italic=run.italic,
        underline=run.underline,
        subscript=run.font.subscript if run.font else None,
        superscript=run.font.superscript if run.font else None,
    )
    if run.font:
        rs.font_name = run.font.name
        rs.font_size = run.font.size
        if run.font.color and run.font.color.rgb:
            rs.font_color = run.font.color.rgb
    if run.style:
        try:
            rs.style_name = run.style.name
        except Exception:
            pass
    return rs


def _apply_run_style(run, rs: RunStyle) -> None:
    if rs.bold is not None:
        run.bold = rs.bold
    if rs.italic is not None:
        run.italic = rs.italic
    if rs.underline is not None:
        run.underline = rs.underline
    if run.font:
        if rs.subscript is not None:
            run.font.subscript = rs.subscript
        if rs.superscript is not None:
            run.font.superscript = rs.superscript
        if rs.font_name:
            run.font.name = rs.font_name
        if rs.font_size:
            run.font.size = rs.font_size
        if rs.font_color:
            run.font.color.rgb = rs.font_color
    if rs.style_name:
        try:
            run.style = rs.style_name
        except Exception:
            pass


def _tag_runs(para) -> tuple[str, dict[str, dict]]:
    """Encodes styled runs as semantic tags and protected placeholders.

    Runs containing inline drawings, math equations, or embedded objects
    are stored in the vault as locked elements regardless of whether they
    carry visible text.  This prevents their XML from being silently lost
    when _reconstruct_runs clears and rewrites paragraph runs.
    """
    tagged_parts = []
    vault: dict[str, dict] = {}
    counter = 0

    _COMPLEX_MARKERS = ("<w:drawing", "<w:pict", "<m:oMath", "<w:object")

    for run in para.runs:
        t = run.text
        xml_str = run._element.xml
        has_complex = any(m in xml_str for m in _COMPLEX_MARKERS)

        # Runs with no visible text but complex content must be preserved.
        if not t and has_complex:
            tag = f"__RUN_LOCKED_{counter}__"
            vault[tag] = {
                "text": "",
                "style": _extract_run_style(run),
                "locked": True,
                "_elem": run._element,  # keep the real lxml element
            }
            tagged_parts.append(tag)
            counter += 1
            continue

        if not t:
            continue

        is_cite = bool(
            run.font and (run.font.superscript or run.font.subscript)
            or re.fullmatch(r"\[[a-zA-Z0-9,\-\s]{1,15}\]", t.strip())
        )

        if has_complex or is_cite:
            tag = f"__RUN_LOCKED_{counter}__"
            vault[tag] = {
                "text": t,
                "style": _extract_run_style(run),
                "locked": True,
                "_elem": run._element if has_complex else None,
            }
            tagged_parts.append(tag)
            counter += 1
        elif run.bold and run.italic:
            tag_open = f"<bi id='{counter}'>"
            tag_close = f"</bi id='{counter}'>"
            vault[f"bi_{counter}"] = {"style": _extract_run_style(run), "locked": False}
            tagged_parts.append(f"{tag_open}{t}{tag_close}")
            counter += 1
        elif run.bold:
            tag_open = f"<b id='{counter}'>"
            tag_close = f"</b id='{counter}'>"
            vault[f"b_{counter}"] = {"style": _extract_run_style(run), "locked": False}
            tagged_parts.append(f"{tag_open}{t}{tag_close}")
            counter += 1
        elif run.italic:
            tag_open = f"<i id='{counter}'>"
            tag_close = f"</i id='{counter}'>"
            vault[f"i_{counter}"] = {"style": _extract_run_style(run), "locked": False}
            tagged_parts.append(f"{tag_open}{t}{tag_close}")
            counter += 1
        else:
            tagged_parts.append(t)

    return "".join(tagged_parts), vault


def _reconstruct_runs(para, rewritten_text: str, vault: dict[str, dict], base_style: RunStyle) -> None:
    """Clears and rebuilds paragraph runs faithfully preserving all inline styles,
    complex elements (drawings, math equations), and citations in exact sequential position."""
    p_elem = para._element

    # 1. Collect all complex elements to preserve
    complex_elems = {
        v["_elem"] for v in vault.values()
        if v.get("locked") and v.get("_elem") is not None
    }

    # 2. Cleanly detach all existing runs from the paragraph XML
    for r in list(para.runs):
        p_elem.remove(r._element)

    attached_elems: set = set()
    attached_tokens: set[str] = set()

    # 3. Match both styled semantic tags AND locked run placeholders in sequential order
    token_pattern = re.compile(
        r"(<(?P<tag>b|i|bi)\s+id=['\"](?P<id>\d+)['\"]>(?P<content>.*?)</(?P=tag)(?:\s+id=['\"](?P=id)['\"])?>)|"
        r"(?P<lock>__RUN_(?:LOCKED|PLACEHOLDER)_\d+__)",
        re.DOTALL | re.IGNORECASE,
    )

    last_idx = 0
    for m in token_pattern.finditer(rewritten_text):
        start, end = m.span()
        if start > last_idx:
            chunk = rewritten_text[last_idx:start]
            chunk = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", chunk)
            if chunk:
                r = para.add_run(chunk)
                _apply_run_style(r, base_style)

        if m.group("lock"):
            tok = m.group("lock")
            attached_tokens.add(tok)
            v = vault.get(tok, {})
            elem = v.get("_elem")
            if elem is not None:
                p_elem.append(elem)
                attached_elems.add(elem)
            else:
                txt = v.get("text", "")
                st = v.get("style", base_style)
                if txt:
                    r = para.add_run(txt)
                    _apply_run_style(r, st)
        else:
            tag_type = m.group("tag").lower()
            tag_id = m.group("id")
            tag_content = m.group("content")
            clean_content = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", tag_content)
            key = f"{tag_type}_{tag_id}"
            st = vault.get(key, {}).get("style", base_style)
            if clean_content:
                r = para.add_run(clean_content)
                _apply_run_style(r, st)

        last_idx = end

    if last_idx < len(rewritten_text):
        trailing = rewritten_text[last_idx:]
        trailing = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", trailing)
        if trailing:
            r = para.add_run(trailing)
            _apply_run_style(r, base_style)

    # 4. Fallback: if no runs were added at all, write plain text
    if not para.runs and not attached_elems:
        plain = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", rewritten_text)
        if plain:
            r = para.add_run(plain)
            _apply_run_style(r, base_style)

    # 5. Safety: ensure any complex element not matched in text is safely attached
    for elem in complex_elems:
        if elem not in attached_elems:
            p_elem.append(elem)
            attached_elems.add(elem)

    # 6. Guaranteed Citation & Locked Text Retention:
    # If the LLM omitted any locked text or citation token, restore it to prevent loss
    for tok, v in vault.items():
        if v.get("locked") and tok not in attached_tokens:
            elem = v.get("_elem")
            if elem is not None:
                if elem not in attached_elems:
                    p_elem.append(elem)
                    attached_elems.add(elem)
            else:
                txt = v.get("text", "")
                if txt:
                    prefix = " " if not txt.startswith((" ", "[", "(", ",")) else ""
                    r = para.add_run(f"{prefix}{txt}")
                    _apply_run_style(r, v.get("style", base_style))
                    attached_tokens.add(tok)


def should_skip_paragraph(para) -> bool:
    t = para.text.strip()
    if not t:
        return True
    style = (para.style.name or "").lower() if para.style else ""
    if any(s in style for s in _SKIP_STYLES):
        return True
    if REF_LINE.match(t) or CAPTION_LINE.match(t):
        return True
    return False


def _iter_table_paragraphs(table, seen_p_ids: Optional[set[int]] = None) -> Generator:
    """Recursively yield all paragraphs from a table and any nested tables,
    skipping duplicate paragraph instances caused by merged table cells."""
    if seen_p_ids is None:
        seen_p_ids = set()
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                p_id = id(p._element)
                if p_id not in seen_p_ids:
                    seen_p_ids.add(p_id)
                    yield p
            for nested_table in cell.tables:
                yield from _iter_table_paragraphs(nested_table, seen_p_ids)


def iter_document_paragraphs(doc: Document) -> Generator:
    """Yield all paragraphs from main body and recursively throughout all tables without duplicates."""
    seen_p_ids: set[int] = set()
    for p in doc.paragraphs:
        p_id = id(p._element)
        if p_id not in seen_p_ids:
            seen_p_ids.add(p_id)
            yield p
    for table in doc.tables:
        yield from _iter_table_paragraphs(table, seen_p_ids)


def extract_text_from_pdf(path: Path | str) -> str:
    """Extract clean text from a PDF file using PyMuPDF (fitz), pypdf, or pdfplumber."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"PDF file not found: {p}")

    # 1. Try PyMuPDF (fitz) - fastest and highest fidelity
    try:
        import fitz
        doc = fitz.open(str(p))
        pages = []
        for page in doc:
            txt = page.get_text()
            if txt and txt.strip():
                pages.append(txt.strip())
        doc.close()
        if pages:
            return "\n\n".join(pages)
    except Exception as e:
        logger.debug(f"PyMuPDF extraction failed: {e}")

    # 2. Try pypdf fallback
    try:
        import pypdf
        reader = pypdf.PdfReader(str(p))
        pages = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt and txt.strip():
                pages.append(txt.strip())
        if pages:
            return "\n\n".join(pages)
    except Exception as e:
        logger.debug(f"pypdf extraction failed: {e}")

    # 3. Try pdfplumber fallback
    try:
        import pdfplumber
        with pdfplumber.open(str(p)) as pdf:
            pages = [page.extract_text() for page in pdf.pages if page.extract_text()]
            if pages:
                return "\n\n".join(pages)
    except Exception as e:
        logger.debug(f"pdfplumber extraction failed: {e}")

    raise RuntimeError(f"Could not extract text from PDF: {p.name}")


def extract_text_from_file(path: Path | str) -> str:
    """Unified file text extractor for .docx, .pdf, and .txt files."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(p)
    elif suffix == ".docx":
        doc = docx.Document(str(p))
        return "\n\n".join(para.text for para in iter_document_paragraphs(doc) if para.text.strip())
    else:
        return p.read_text(encoding="utf-8", errors="replace")

