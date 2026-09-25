"""Text from a training file attached in the editor's Claude panel (PowerPoint,
Word, PDF or plain text), so Claude can use an existing training as its start.

Office files are zip archives of XML; the text is pulled out with a simple
pattern match (no XML parser, so nothing in the file can make the app fetch or
expand anything). Sizes are capped against oversized or booby-trapped files.

Pictures in the file are saved as uploads (through the `save` callback) and shown
to Claude in place as "[Picture: /media/...]" lines, so it can keep each one on
the slide it belongs to. Ones nobody saves into a lesson are swept after a day."""
import hashlib
import html
import io
import posixpath
import re
import zipfile

MAX_FILE_BYTES = 25 * 1024**2
MAX_PART_BYTES = 8 * 1024**2
MAX_CHARS = 60000
PICTURE_TYPES = (".jpg", ".jpeg", ".png", ".gif", ".webp")
MAX_PICTURES = 20
MAX_PICTURE_BYTES = 15 * 1024**2
MIN_PICTURE_BYTES = 2048          # smaller ones are bullets, icons and lines
KINDS = {".pptx": "PowerPoint", ".docx": "Word", ".pdf": "PDF", ".txt": "text", ".md": "text"}


class Unreadable(ValueError):
    pass


class _Pictures:
    """Saves the pictures a file uses, once each, and hands back their links."""

    def __init__(self, save):
        self.save, self.links = save, {}

    def add(self, ext, data):
        ext = ext.lower()
        if not self.save or ext not in PICTURE_TYPES or not MIN_PICTURE_BYTES <= len(data) <= MAX_PICTURE_BYTES:
            return None
        key = hashlib.sha1(data).hexdigest()
        if key not in self.links:
            if len(self.links) >= MAX_PICTURES:
                return None
            self.links[key] = self.save(ext, data)
        return self.links[key]

    def from_zip(self, z, member):
        if member not in z.namelist() or z.getinfo(member).file_size > MAX_PICTURE_BYTES:
            return None
        return self.add(posixpath.splitext(member)[1], z.read(member))


def _picture_line(link):
    return f"[Picture: {link}]"


def _image_rels(z, part):
    """rId -> zip path of each picture a part (a slide, a Word body) links to."""
    folder, name = posixpath.split(part)
    rels = f"{folder}/_rels/{name}.rels"
    if rels not in z.namelist():
        return {}
    out = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", _read_part(z, rels)):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if attrs.get("Type", "").endswith("/image") and attrs.get("TargetMode") != "External" and attrs.get("Id"):
            out[attrs["Id"]] = posixpath.normpath(posixpath.join(folder, html.unescape(attrs.get("Target", ""))))
    return out


def _embeds(xml):
    return re.findall(r'r:embed="([^"]+)"', xml)


def _xml_text(xml, para_tag, text_tag, picture=None):
    out = []
    for para in re.findall(rf"<{para_tag}[ >].*?</{para_tag}>", xml, re.S):
        bits = re.findall(rf"<{text_tag}(?: [^>]*)?>([^<]*)</{text_tag}>", para)
        line = html.unescape("".join(bits)).strip()
        if line:
            out.append(line)
        for rid in _embeds(para) if picture else []:
            link = picture(rid)
            if link:
                out.append(_picture_line(link))
    return "\n".join(out)


def _read_part(z, name):
    if z.getinfo(name).file_size > MAX_PART_BYTES:
        raise Unreadable("That file has a part too big to read.")
    return z.read(name).decode("utf-8", "replace")


def _pptx(data, pics):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        slides = []
        for i, name in enumerate(names, 1):
            xml = _read_part(z, name)
            text = _xml_text(xml, "a:p", "a:t")
            rels = _image_rels(z, name)
            links = [pics.from_zip(z, rels[rid]) for rid in dict.fromkeys(_embeds(xml)) if rid in rels]
            text = "\n".join([text, *(_picture_line(l) for l in links if l)]).strip()
            notes_name = name.replace("slides/slide", "notesSlides/notesSlide")
            notes = _xml_text(_read_part(z, notes_name), "a:p", "a:t") if notes_name in z.namelist() else ""
            part = f"--- Slide {i} ---\n{text or '(no text: pictures only)'}"
            if notes:
                part += f"\n[Speaker notes] {notes}"
            slides.append(part)
    return "\n\n".join(slides), len(names)


def _docx(data, pics):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "word/document.xml" not in z.namelist():
            raise Unreadable("That doesn't look like a Word file.")
        rels = _image_rels(z, "word/document.xml")
        picture = lambda rid: pics.from_zip(z, rels[rid]) if rid in rels else None
        return _xml_text(_read_part(z, "word/document.xml"), "w:p", "w:t", picture), None


def _pdf_pictures(page, pics):
    links = []
    try:
        images = list(page.images)
    except Exception:  # an image format pypdf can't unpack
        return links
    for img in images:
        try:
            link = pics.add(posixpath.splitext(img.name)[1], img.data)
        except Exception:
            continue
        if link:
            links.append(link)
    return links


def _pdf(data, pics):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        links = _pdf_pictures(page, pics)
        text = "\n".join([text, *(_picture_line(l) for l in links)]).strip()
        pages.append(f"--- Page {i} ---\n{text or '(no text: pictures only)'}")
        if sum(len(p) for p in pages) > MAX_CHARS:
            break
    return "\n\n".join(pages), len(reader.pages)


def extract(filename, data, save=None):
    """Returns {"name", "kind", "text", "parts", "truncated", "pictures"}; raises
    Unreadable. save(ext, bytes) -> link stores a picture; without it pictures are skipped."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext in (".ppt", ".doc"):
        raise Unreadable(f"'{filename}' is an old Office format. Open it and Save As .{ext[1:]}x, then attach that.")
    if ext in (".key", ".pages"):
        raise Unreadable("Export it from Keynote or Pages as PowerPoint (.pptx) or PDF, then attach that.")
    if ext not in KINDS:
        raise Unreadable(f"'{filename}' can't be read. Attach a PowerPoint (.pptx), Word (.docx), PDF or text file.")
    if len(data) > MAX_FILE_BYTES:
        raise Unreadable("That file is over 25 MB. Try a PDF export, or split it.")
    pics = _Pictures(save)
    try:
        if ext == ".pptx":
            text, parts = _pptx(data, pics)
        elif ext == ".docx":
            text, parts = _docx(data, pics)
        elif ext == ".pdf":
            text, parts = _pdf(data, pics)
        else:
            text, parts = data.decode("utf-8", "replace"), None
    except Unreadable:
        raise
    except Exception as e:  # damaged or password-protected files
        raise Unreadable(f"'{filename}' couldn't be opened ({e.__class__.__name__}). Is it password-protected?")
    text = text.strip()
    if not pics.links and not re.search(r"\w", re.sub(r"--- (Slide|Page) \d+ ---|\(no text: pictures only\)", "", text)):
        raise Unreadable(f"'{filename}' has no text the app can read (it may be pictures only).")
    return {"name": filename, "kind": KINDS[ext], "text": text[:MAX_CHARS], "parts": parts,
            "truncated": len(text) > MAX_CHARS, "pictures": len(pics.links)}
