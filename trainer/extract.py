"""Text from a training file attached in the editor's Claude panel (PowerPoint,
Word, PDF or plain text), so Claude can use an existing training as its start.

Office files are zip archives of XML; the text is pulled out with a simple
pattern match (no XML parser, so nothing in the file can make the app fetch or
expand anything). Sizes are capped against oversized or booby-trapped files."""
import html
import io
import re
import zipfile

MAX_FILE_BYTES = 25 * 1024**2
MAX_PART_BYTES = 8 * 1024**2
MAX_CHARS = 60000
KINDS = {".pptx": "PowerPoint", ".docx": "Word", ".pdf": "PDF", ".txt": "text", ".md": "text"}


class Unreadable(ValueError):
    pass


def _xml_text(xml, para_tag, text_tag):
    out = []
    for para in re.findall(rf"<{para_tag}[ >].*?</{para_tag}>", xml, re.S):
        bits = re.findall(rf"<{text_tag}(?: [^>]*)?>([^<]*)</{text_tag}>", para)
        line = html.unescape("".join(bits)).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _read_part(z, name):
    if z.getinfo(name).file_size > MAX_PART_BYTES:
        raise Unreadable("That file has a part too big to read.")
    return z.read(name).decode("utf-8", "replace")


def _pptx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        slides = []
        for i, name in enumerate(names, 1):
            text = _xml_text(_read_part(z, name), "a:p", "a:t")
            notes_name = name.replace("slides/slide", "notesSlides/notesSlide")
            notes = _xml_text(_read_part(z, notes_name), "a:p", "a:t") if notes_name in z.namelist() else ""
            part = f"--- Slide {i} ---\n{text or '(no text: pictures only)'}"
            if notes:
                part += f"\n[Speaker notes] {notes}"
            slides.append(part)
    return "\n\n".join(slides), len(names)


def _docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "word/document.xml" not in z.namelist():
            raise Unreadable("That doesn't look like a Word file.")
        return _xml_text(_read_part(z, "word/document.xml"), "w:p", "w:t"), None


def _pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        pages.append(f"--- Page {i} ---\n{(page.extract_text() or '').strip() or '(no text: pictures only)'}")
        if sum(len(p) for p in pages) > MAX_CHARS:
            break
    return "\n\n".join(pages), len(reader.pages)


def extract(filename, data):
    """Returns {"name", "kind", "text", "parts", "truncated"}; raises Unreadable."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext in (".ppt", ".doc"):
        raise Unreadable(f"'{filename}' is an old Office format. Open it and Save As .{ext[1:]}x, then attach that.")
    if ext in (".key", ".pages"):
        raise Unreadable("Export it from Keynote or Pages as PowerPoint (.pptx) or PDF, then attach that.")
    if ext not in KINDS:
        raise Unreadable(f"'{filename}' can't be read. Attach a PowerPoint (.pptx), Word (.docx), PDF or text file.")
    if len(data) > MAX_FILE_BYTES:
        raise Unreadable("That file is over 25 MB. Try a PDF export, or split it.")
    try:
        if ext == ".pptx":
            text, parts = _pptx(data)
        elif ext == ".docx":
            text, parts = _docx(data)
        elif ext == ".pdf":
            text, parts = _pdf(data)
        else:
            text, parts = data.decode("utf-8", "replace"), None
    except Unreadable:
        raise
    except Exception as e:  # damaged or password-protected files
        raise Unreadable(f"'{filename}' couldn't be opened ({e.__class__.__name__}). Is it password-protected?")
    text = text.strip()
    if not re.search(r"\w", re.sub(r"--- (Slide|Page) \d+ ---|\(no text: pictures only\)", "", text)):
        raise Unreadable(f"'{filename}' has no text the app can read (it may be pictures only).")
    return {"name": filename, "kind": KINDS[ext], "text": text[:MAX_CHARS], "parts": parts,
            "truncated": len(text) > MAX_CHARS}
