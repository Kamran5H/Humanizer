"""
Format-preserving document handling.

DOCX paragraphs are converted to a compact tagged string for rewriting and then
rebuilt in place:

* Formatted runs (bold, italic, underline, colour, highlight, fonts ...) become
  ``<b id='3'>text</b>``-style tags whose full ``w:rPr`` is restored afterwards.
* Anything that is not plain text — hyperlinks, citation fields (Zotero,
  Mendeley, EndNote), footnote references, equations, images, content
  controls, tracked changes, bookmarks — becomes an opaque
  ``__RUN_LOCKED_n__`` token and its original XML is re-attached untouched.
* Reference lists, captions, code, tables of contents and equation-only lines
  are skipped entirely.

Also provides text extraction for PDF / TXT / Markdown and a crash-safe
checkpoint so long jobs can resume.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

import docx
from docx.document import Document as _DocxDocument
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.run import Run

logger = logging.getLogger(__name__)


def save_result(target: Path, text: str, formatted_result: Optional[Path] = None) -> None:
    """Save an existing formatted result without rebuilding it from plain text.

    Plain-text edits cannot safely be mapped back to Word runs. Refuse a stale
    formatted export rather than silently dropping edits or document formatting.
    """
    target = Path(target)
    if target.suffix.lower() != ".docx":
        target.write_text(text, encoding="utf-8")
        return
    if formatted_result is not None:
        formatted_result = Path(formatted_result)
        if not formatted_result.is_file():
            raise ValueError("The formatted result is missing. Rewrite the original document again.")
        if text != docx_text(formatted_result):
            raise ValueError("The result was edited as plain text. Save those edits as TXT or Markdown, "
                             "or restore the original result to download its formatted DOCX.")
        if target.resolve() != formatted_result.resolve():
            shutil.copyfile(formatted_result, target)
        return
    document = docx.Document()
    for block in text.split("\n\n"):
        document.add_paragraph(block)
    document.save(str(target))


def save_docx_package(source: Path, document: _DocxDocument, target: Path) -> None:
    """Replace the main document XML; copy every other original package part.

    Rewriting body/table text creates no relationships or new package parts.
    Keeping the source package avoids normalising unrelated styles, headers,
    figures, embedded files, footnotes, and application-specific metadata.
    """
    target = Path(target)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".docx", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temporary, "w") as result:
            result.comment = original.comment
            main_part = str(document.part.partname).lstrip("/")
            for entry in original.infolist():
                result.writestr(entry, document.part.blob if entry.filename == main_part else original.read(entry))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

_SKIP_STYLE_WORDS = ("code", "verbatim", "source", "header", "footer", "toc", "caption",
                     "bibliograph", "reference", "equation", "table of", "index")
REF_LINE = re.compile(r"^\s*(?:\[\d+\]|\d+\.\s+[A-Z][a-z]+,\s+[A-Z]\.)")
CAPTION_LINE = re.compile(r"^\s*(?:Figure|Fig\.|Table|Scheme|Chart|Equation|Eq\.)\s*S?\d", re.IGNORECASE)
REF_HEADINGS = re.compile(r"^\s*(?:\d+\.?\s*)?(?:references?|bibliography|works cited|literature cited|reference list|sources|citations)\s*:?\s*$", re.IGNORECASE)

_W_R = qn("w:r")
_W_PPR = qn("w:pPr")
_W_RPR = qn("w:rPr")
_W_FLDCHAR = qn("w:fldChar")
_W_FLDTYPE = qn("w:fldCharType")
_DROP_TAGS = {qn("w:proofErr"), qn("w:lastRenderedPageBreak")}
_COMPLEX_IN_RUN = ("drawing", "pict", "object", "footnoteReference", "endnoteReference",
                   "commentReference", "sym", "AlternateContent", "ptab", "instrText", "fldChar",
                   "ruby", "fldData")


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------
class Checkpoint:
    """Per-session persistent state so an interrupted job resumes where it stopped."""

    DIR = Path(os.environ.get("HUMANIZER_HOME") or os.environ.get("LOCALAPPDATA")
               or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "humanizer_pro" / "sessions"

    def __init__(self, sid: str, input_text: str, cfg_key: dict, total: int,
                 mode: str = "text", source_path: Optional[str] = None) -> None:
        self.sid = sid
        self.path = self.DIR / f"{sid}.json"
        self.total = total
        self.mode = mode
        self.source_path = source_path
        self._blocks: dict[str, str] = {}
        self._lock = threading.Lock()
        self._created_at = datetime.now().isoformat(timespec="seconds")
        self._input_hash = hashlib.sha256(input_text.encode("utf-8", "replace")).hexdigest()
        self._cfg_key = cfg_key
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("input_hash") == self._input_hash and d.get("cfg") == self._cfg_key:
                self._blocks = {str(k): str(v) for k, v in (d.get("blocks") or {}).items()}
                self._created_at = d.get("created_at", self._created_at)
        except Exception:
            pass

    def get(self, idx: int) -> Optional[str]:
        return self._blocks.get(str(idx))

    def done_count(self) -> int:
        return len(self._blocks)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass

    def put(self, idx: int, text: str) -> None:
        with self._lock:
            self._blocks[str(idx)] = text
            try:
                self.DIR.mkdir(parents=True, exist_ok=True)
                payload = {
                    "version": 3, "sid": self.sid, "mode": self.mode, "source_path": self.source_path,
                    "input_hash": self._input_hash, "cfg": self._cfg_key, "total": self.total,
                    "blocks": self._blocks, "created_at": self._created_at,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except Exception as e:
                logger.warning("Checkpoint save failed: %s", e)

    @staticmethod
    def compute_id(input_text: str, cfg_key: dict) -> str:
        h = hashlib.sha1()
        h.update(input_text.encode("utf-8", errors="replace"))
        h.update(json.dumps(cfg_key, sort_keys=True).encode("utf-8"))
        return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Run styles (kept for API compatibility; rebuilding uses full rPr copies)
# ---------------------------------------------------------------------------
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
    rpr: Optional[object] = None  # deep copy of the source w:rPr element


def _extract_run_style(run) -> RunStyle:
    f = run.font
    rs = RunStyle(bold=run.bold, italic=run.italic, underline=run.underline,
                  subscript=f.subscript if f else None, superscript=f.superscript if f else None)
    if f is not None:
        rs.font_name = f.name
        rs.font_size = f.size
        try:
            if f.color is not None and f.color.type is not None and f.color.rgb is not None:
                rs.font_color = f.color.rgb
        except Exception:
            pass
    rpr = run._element.find(_W_RPR)
    rs.rpr = copy.deepcopy(rpr) if rpr is not None else None
    return rs


def _apply_run_style(run, rs: Optional[RunStyle]) -> None:
    if rs is None:
        return
    if rs.rpr is not None:
        old = run._element.find(_W_RPR)
        if old is not None:
            run._element.remove(old)
        run._element.insert(0, copy.deepcopy(rs.rpr))
        return
    if rs.bold is not None:
        run.bold = rs.bold
    if rs.italic is not None:
        run.italic = rs.italic
    if rs.underline is not None:
        run.underline = rs.underline
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


# ---------------------------------------------------------------------------
# Tagging / reconstruction
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _rpr_key(rpr) -> str:
    if rpr is None:
        return ""
    from lxml import etree  # python-docx depends on lxml
    return etree.tostring(rpr, method="c14n").decode("utf-8")


def _run_is_complex(r_el) -> bool:
    for child in r_el.iter():
        name = _local(child.tag)
        if name in _COMPLEX_IN_RUN:
            return True
        if name == "br" and child.get(qn("w:type")) in ("page", "column"):
            return True
    return False


def _fld_types(r_el) -> list[str]:
    return [fc.get(_W_FLDTYPE) for fc in r_el.iter(_W_FLDCHAR)]


def _tag_name(rpr) -> str:
    if rpr is None:
        return "i"
    names = {_local(c.tag): c for c in rpr}

    def on(n: str) -> bool:
        el = names.get(n)
        return el is not None and el.get(qn("w:val")) not in ("0", "false", "none")

    b, i, u = on("b"), on("i"), on("u")
    if b and i:
        return "bi"
    if b:
        return "b"
    if u and not i:
        return "u"
    return "i"


def _tag_runs(para) -> tuple[str, dict[str, dict]]:
    """Encode a paragraph as tagged text. Returns (tagged_text, vault).

    vault keys: ``__RUN_LOCKED_n__`` -> {"locked": True, "_elems": [...], "text": str}
                ``<tag>_<n>``         -> {"locked": False, "rpr": element|None}
                ``__base__``          -> {"rpr": dominant plain-run rPr}
    """
    p_el = para._element
    children = [c for c in p_el if c.tag != _W_PPR]

    # 1. Group children into segments: ("text", rpr, text, elem) | ("lock", elems, text)
    segments: list[tuple] = []
    field_group: list = []
    field_text: list[str] = []
    depth = 0
    for child in children:
        if child.tag in _DROP_TAGS:
            continue
        if child.tag == _W_R:
            types = _fld_types(child)
            if depth > 0 or "begin" in types:
                field_group.append(child)
                field_text.append(Run(child, para).text)
                depth += types.count("begin") - types.count("end")
                if depth <= 0:
                    depth = 0
                    segments.append(("lock", field_group, "".join(field_text)))
                    field_group, field_text = [], []
                continue
            if _run_is_complex(child):
                segments.append(("lock", [child], Run(child, para).text))
                continue
            text = Run(child, para).text
            if not text:
                continue
            rpr = child.find(_W_RPR)
            run = Run(child, para)
            is_cite = bool(run.font.superscript or run.font.subscript) or bool(
                re.fullmatch(r"\s*\[[\w,\-–\s]{1,15}\]\s*", text))
            if is_cite:
                segments.append(("lock", [child], text))
            else:
                segments.append(("text", rpr, text, child))
        else:
            name = _local(child.tag)
            if name == "bookmarkStart" and child.get(qn("w:name")) == "_GoBack":
                continue
            if depth > 0:
                field_group.append(child)
                continue
            text = "".join(t.text or "" for t in child.iter(qn("w:t")))
            segments.append(("lock", [child], text))
    if field_group:  # unterminated field — keep it whole
        segments.append(("lock", field_group, "".join(field_text)))

    # 2. Dominant (base) formatting = rPr covering the most plain characters.
    weight: dict[str, int] = {}
    rpr_by_key: dict[str, object] = {}
    for seg in segments:
        if seg[0] == "text":
            k = _rpr_key(seg[1])
            weight[k] = weight.get(k, 0) + len(seg[2])
            rpr_by_key.setdefault(k, seg[1])
    base_key = max(weight, key=weight.get) if weight else ""
    vault: dict[str, dict] = {"__base__": {"rpr": copy.deepcopy(rpr_by_key.get(base_key)), "locked": False}}

    # 3. Emit tagged text, merging adjacent runs with identical formatting.
    parts: list[str] = []
    counter = 0
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg[0] == "lock":
            tok = f"__RUN_LOCKED_{counter}__"
            counter += 1
            vault[tok] = {"locked": True, "_elems": list(seg[1]), "_elem": seg[1][0], "text": seg[2]}
            parts.append(tok)
            i += 1
            continue
        key = _rpr_key(seg[1])
        text = seg[2]
        j = i + 1
        while j < len(segments) and segments[j][0] == "text" and _rpr_key(segments[j][1]) == key:
            text += segments[j][2]
            j += 1
        if key == base_key:
            parts.append(text)
        else:
            tag = _tag_name(seg[1])
            vault[f"{tag}_{counter}"] = {"locked": False, "rpr": copy.deepcopy(seg[1]),
                                         "style": RunStyle(rpr=copy.deepcopy(seg[1]))}
            # Keep surrounding whitespace outside the tag so the model sees natural text.
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            core = text.strip()
            if core:
                parts.append(f"{lead}<{tag} id='{counter}'>{core}</{tag}>{trail}")
            else:
                parts.append(text)
            counter += 1
        i = j
    return "".join(parts), vault


_TOKEN_PATTERN = re.compile(
    r"<(?P<tag>bi|b|i|u)\s+id=['\"](?P<id>\d+)['\"]>(?P<content>.*?)</(?P=tag)(?:\s+id=['\"](?P=id)['\"])?>"
    r"|(?P<lock>__RUN_(?:LOCKED|PLACEHOLDER)_\d+__)",
    re.DOTALL | re.IGNORECASE,
)
_STRAY_TAG = re.compile(r"</?(?:bi|b|i|u)\b[^>]*>", re.IGNORECASE)


def _new_run(para, text: str, rpr) -> None:
    if not text:
        return
    r = para.add_run(text)
    if rpr is not None:
        r._element.insert(0, copy.deepcopy(rpr))


def _reconstruct_runs(para, rewritten_text: str, vault: dict[str, dict], base_style: Optional[RunStyle] = None) -> None:
    """Rebuild a paragraph from tagged text, re-attaching every locked element exactly once."""
    p_el = para._element
    base_rpr = (vault.get("__base__") or {}).get("rpr")
    if base_rpr is None and base_style is not None:
        base_rpr = base_style.rpr

    for child in [c for c in p_el if c.tag != _W_PPR]:
        p_el.remove(child)

    used: set[str] = set()
    last = 0
    for m in _TOKEN_PATTERN.finditer(rewritten_text):
        _new_run(para, _STRAY_TAG.sub("", rewritten_text[last:m.start()]), base_rpr)
        if m.group("lock"):
            tok = m.group("lock")
            v = vault.get(tok)
            if v and tok not in used:
                used.add(tok)
                for el in v.get("_elems") or ([v["_elem"]] if v.get("_elem") is not None else []):
                    p_el.append(el)
                if not (v.get("_elems") or v.get("_elem") is not None) and v.get("text"):
                    _new_run(para, v["text"], (v.get("style") or RunStyle()).rpr)
        else:
            key = f"{m.group('tag').lower()}_{m.group('id')}"
            v = vault.get(key) or {}
            rpr = v.get("rpr")
            if rpr is None and v.get("style") is not None:
                rpr = v["style"].rpr
            _new_run(para, _STRAY_TAG.sub("", m.group("content")), rpr if rpr is not None else base_rpr)
        last = m.end()
    _new_run(para, _STRAY_TAG.sub("", rewritten_text[last:]), base_rpr)

    # Anything the rewrite dropped is re-attached so nothing is ever lost.
    for tok, v in vault.items():
        if not v.get("locked") or tok in used:
            continue
        elems = v.get("_elems") or ([v["_elem"]] if v.get("_elem") is not None else [])
        if elems:
            for el in elems:
                p_el.append(el)
        elif v.get("text"):
            _new_run(para, " " + v["text"], (v.get("style") or RunStyle()).rpr)


# ---------------------------------------------------------------------------
# Paragraph selection
# ---------------------------------------------------------------------------
def _style_name(para) -> str:
    try:
        return (para.style.name or "").lower() if para.style is not None else ""
    except Exception:
        return ""


def _is_heading(para) -> bool:
    s = _style_name(para)
    return s.startswith("heading") or s in ("title", "subtitle")


def should_skip_paragraph(para) -> bool:
    t = para.text.strip()
    if not t:
        return True
    style = _style_name(para)
    if any(w in style for w in _SKIP_STYLE_WORDS):
        return True
    if REF_LINE.match(t) or CAPTION_LINE.match(t):
        return True
    letters = sum(ch.isalpha() for ch in t)
    if letters < 0.5 * len(t.replace(" ", "")):
        return True  # equations, numbers, codes
    return False


def _iter_table_paragraphs(table, seen: set[int]) -> Generator:
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                if id(p._element) not in seen:
                    seen.add(id(p._element))
                    yield p
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested, seen)


def iter_document_paragraphs(doc: _DocxDocument) -> Generator:
    """Yield body paragraphs then table paragraphs (recursively, merged cells once)."""
    seen: set[int] = set()
    for p in doc.paragraphs:
        if id(p._element) not in seen:
            seen.add(id(p._element))
            yield p
    for table in doc.tables:
        yield from _iter_table_paragraphs(table, seen)


def select_paragraphs(doc: _DocxDocument, rewrite_headings: bool = False) -> list:
    """Paragraphs that are safe and worthwhile to rewrite (skips everything after a References heading)."""
    chosen = []
    in_refs = False
    for p in doc.paragraphs:
        text = p.text.strip()
        if REF_HEADINGS.match(text) and (len(text) < 40):
            in_refs = True
            continue
        if in_refs:
            if _is_heading(p) and not REF_HEADINGS.match(text):
                in_refs = False
            else:
                continue
        if (rewrite_headings or not _is_heading(p)) and not should_skip_paragraph(p):
            chosen.append(p)
    seen = {id(p._element) for p in doc.paragraphs}
    for table in doc.tables:
        for p in _iter_table_paragraphs(table, seen):
            if not should_skip_paragraph(p):
                chosen.append(p)
    return chosen


def docx_text(path_or_doc) -> str:
    doc = path_or_doc if isinstance(path_or_doc, _DocxDocument) else docx.Document(str(path_or_doc))
    return "\n\n".join(p.text for p in iter_document_paragraphs(doc) if p.text.strip())


# ---------------------------------------------------------------------------
# Plain extraction
# ---------------------------------------------------------------------------
def _clean_pdf_text(txt: str) -> str:
    txt = re.sub(r"(\w)-\n(\w)", r"\1\2", txt)          # de-hyphenate line breaks
    txt = re.sub(r"[ \t]*\n(?!\n)[ \t]*", " ", txt)     # join wrapped lines
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return re.sub(r"[ \t]{2,}", " ", txt).strip()


def extract_text_from_pdf(path: Path | str) -> str:
    """Extract text from a PDF with PyMuPDF, pypdf or pdfplumber (whichever is installed)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"PDF file not found: {p}")
    try:
        import fitz  # type: ignore
        with fitz.open(str(p)) as d:
            pages = [pg.get_text() for pg in d]
        pages = [_clean_pdf_text(x) for x in pages if x and x.strip()]
        if pages:
            return "\n\n".join(pages)
    except Exception as e:
        logger.debug("PyMuPDF extraction failed: %s", e)
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(p))
        pages = [_clean_pdf_text(pg.extract_text() or "") for pg in reader.pages]
        pages = [x for x in pages if x]
        if pages:
            return "\n\n".join(pages)
    except Exception as e:
        logger.debug("pypdf extraction failed: %s", e)
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(p)) as pdf:
            pages = [_clean_pdf_text(pg.extract_text() or "") for pg in pdf.pages]
        pages = [x for x in pages if x]
        if pages:
            return "\n\n".join(pages)
    except Exception as e:
        logger.debug("pdfplumber extraction failed: %s", e)
    raise RuntimeError(f"Could not extract text from PDF: {p.name} (install 'pypdf' or 'pymupdf')")


def extract_text_from_file(path: Path | str) -> str:
    """Extract text from .docx, .pdf, .txt or .md."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(p)
    if suffix == ".docx":
        return docx_text(p)
    raw = p.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


__all__ = [
    "Checkpoint", "RunStyle", "_tag_runs", "_reconstruct_runs", "_extract_run_style",
    "should_skip_paragraph", "iter_document_paragraphs", "select_paragraphs", "docx_text",
    "extract_text_from_pdf", "extract_text_from_file",
]
