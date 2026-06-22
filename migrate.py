"""
migrate.py
---------------------
Migrates reading progress and groups (collections) from ReadEra to Readest.

For each book in the Readest library, the script:
  1. Finds the corresponding book in ReadEra (by title, with Unicode normalization).
  2. Transfers the reading progress (current page / total pages) from ReadEra.
  3. Identifies the book's collection in ReadEra and finds the matching group
     in the Readest groups file; if none exists, creates a new group with a
     random hexadecimal ID in the same format as the existing ones.

Outputs:
  - Migrated Readest library (JSON)
  - Updated Readest groups file (JSON)

Usage:
    python migrate.py [options]

    python migrate.py \\
        --readera     readera-library.json \\
        --readest     readest-library.json \\
        --groups      readest_groups.json  \\
        --out-library readest-library-migrated.json \\
        --out-groups  readest_groups-migrated.json
"""

import os
import re
import sys
import json
import uuid
import glob
import string
import shutil
import difflib
import secrets
import zipfile
import argparse
import subprocess
import platform
import warnings
import posixpath
import unicodedata
import xml.etree.ElementTree as ET
from xml.dom import minidom, Node
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Set, List


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────

def get_library_directory(program_name: str) -> str:
    """
    """
    home = None
    if platform.system() == 'Windows':
        home = os.getenv('USERPROFILE')
    else:
        home = os.getenv('HOME')

    if not home:
        raise EnvironmentError("Could not determine the user's home directory.")

    if program_name.lower() == 'readera':
        warnings.warn(
            f"ReadEra support is not fully tested. Using the in-app directory as a fallback. "
            f"Please upload the ReadEra's library file in './former_libraries/readera/'.",
            UserWarning,
            stacklevel=2
        )
        return os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0])), 
            'former_libraries', 'readera'
        )
    
    if program_name.lower() == 'readest':
        readest_lib_dir = os.path.join(home, 'AppData', 'Roaming', 'com.bilingify.readest', 'Readest', 'Books')

        if os.path.isdir(readest_lib_dir):
            return readest_lib_dir
        else:
            warnings.warn(
                f"Readest's library directory not found in current environment. "
                f"Please upload the Readest's library files in './former_libraries/readest/'.",
                UserWarning,
                stacklevel=2
            )
            return os.path.join(
                os.path.dirname(os.path.abspath(sys.argv[0])), 
                'former_libraries', 'readest'
            )
    else:
        raise ValueError(f"Unknown program name: {program_name}")
    
def get_readest_groups_filepath():
    readest_dir = get_library_directory('readest')
    library_filepath = os.path.join(readest_dir, 'library.json')
    groups_filepath = os.path.join(
        os.path.dirname(os.path.abspath(sys.argv[0])),
        'former_libraries', 'readest', 'groups.json'
    )

    if not os.path.isfile(library_filepath):
        warnings.warn(
            f"Readest library file not found at '{library_filepath}'. "
            f"Using existing groups file if available: '{groups_filepath}'.",
            UserWarning,
            stacklevel=2
        )
        return groups_filepath

    try:
        with open(library_filepath, encoding='utf-8') as f:
            readest_library = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.warn(
            f"Unable to load Readest library from '{library_filepath}': {exc}. "
            f"Using existing groups file if available: '{groups_filepath}'.",
            UserWarning,
            stacklevel=2
        )
        return groups_filepath

    groups: Dict[str, str] = {}
    for book in readest_library:
        group_id = book.get('groupId')
        group_name = book.get('groupName')
        if group_id and group_name:
            groups[group_id] = group_name

    if groups:
        os.makedirs(os.path.dirname(groups_filepath), exist_ok=True)
        try:
            with open(groups_filepath, 'w', encoding='utf-8') as f:
                json.dump(
                    [{'groupId': gid, 'groupName': groups[gid]} for gid in sorted(groups)],
                    f,
                    ensure_ascii=False,
                    indent=4,
                )
        except OSError as exc:
            warnings.warn(
                f"Unable to write Readest groups file to '{groups_filepath}': {exc}.",
                UserWarning,
                stacklevel=2
            )

    return groups_filepath


def normalize_title(title: str) -> str:
    """
    Normalizes a title for comparison, tolerant of spelling variations:
    converts to lowercase, removes accents and extra whitespace.

    Example: "Crepúsculo dos Ídolos" → "crepusculo dos idolos"
    """
    if not title:
        return ''

    # remove diacritics
    nfd = unicodedata.normalize('NFD', title)
    sem_acentos = ''.join(c for c in nfd if not unicodedata.combining(c))
    s = sem_acentos.lower()

    # remove common noisy prefixes
    prefixes = [r'^microsoft word -', r'^ebook\s*-?', r'^pdfcoffee\.com_', r'^pdfcoffee\.com', r'^dokumen\.pub_', r'^ebook\s']
    for p in prefixes:
        s = re.sub(p, ' ', s)

    # normalize separators
    s = s.replace('_', ' ').replace('-', ' ')

    # strip common file extensions
    s = re.sub(r"\.(rtf|docx?|pdf|epub)$", '', s)

    # remove non-alphanumeric (keep spaces)
    s = re.sub(r"[^0-9a-z ]+", ' ', s)

    # collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    return s


def generate_group_id(existing_ids: Set[str], length: int = 7) -> str:
    """
    Generates a random hexadecimal ID of `length` characters (lowercase),
    guaranteed not to collide with any existing ID.

    The format follows the pattern observed in Readest groups: e.g. "f882f2c".
    """
    while True:
        new_id = uuid.uuid4().hex[:length]
        if new_id not in existing_ids:
            return new_id


def extract_progress(doc_position_str: str) -> Optional[List[int]]:
    """
    Extracts the reading progress from the ReadEra `doc_position` field
    (a JSON string with `page` and `pagesCount` fields).

    Returns:
        [current_page, total_pages]  — if the book has been started (page > 0)
        None                         — if the book has not been started (page == 0)
    """
    try:
        pos = json.loads(doc_position_str)
        page = pos.get('page', 0)
        pages_count = pos.get('pagesCount', 0)
        return [page, pages_count] if page > 0 else None
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Booknotes: ReadEra citations → Readest annotations
# ──────────────────────────────────────────────────────────────────────────────
#
# A ReadEra citation stores its anchor as a crengine (CoolReader) xpointer, e.g.
#     /body/DocFragment[21]/body/html/body/p[9]/text().337
# which we translate into an EPUB Canonical Fragment Identifier (CFI), e.g.
#     epubcfi(/6/42!/4/20,/1:0,/1:337)
#
# Mapping rules (validated against real ReadEra/Readest data):
#   • DocFragment[N]            → spine step  /6/{2N}
#   • an element that is the k-th element child of its parent → CFI step  2k
#     (so a crengine typed index like p[9] must first be resolved to the real
#      node, then re-numbered against *all* element siblings)
#   • text().OFFSET            → /{2*(elements before)+1}:OFFSET
# The actual content document (from the EPUB) is required to resolve element
# indices correctly, because crengine's DOM differs from the raw XHTML.

_BASE36 = string.digits + string.ascii_lowercase


def generate_note_id(existing_ids: Set[str], length: int = 7) -> str:
    """Random base36 id (e.g. "kl0a8t1"), unique against `existing_ids`."""
    while True:
        new_id = ''.join(secrets.choice(_BASE36) for _ in range(length))
        if new_id not in existing_ids:
            return new_id


def _local_name(node) -> str:
    name = node.localName or node.nodeName
    return name.split(':')[-1] if name else ''


class EpubContent:
    """Minimal EPUB reader: resolves the spine and parses content documents.

    Only the parts required for xPath→CFI conversion and page estimation are
    implemented; everything is stdlib (`zipfile` + `xml.dom.minidom`).
    """

    def __init__(self, epub_path: str):
        self._zip = zipfile.ZipFile(epub_path)
        opf_path = self._find_opf()
        self._opf_dir = posixpath.dirname(opf_path)
        self.spine: List[Optional[str]] = self._parse_spine(opf_path)
        self._dom_cache: Dict[str, minidom.Document] = {}
        self._len_cache: Dict[int, int] = {}

    def close(self) -> None:
        self._zip.close()

    def _find_opf(self) -> str:
        root = ET.fromstring(self._zip.read('META-INF/container.xml'))
        for el in root.iter():
            if _local_tag(el.tag) == 'rootfile' and el.get('full-path'):
                return el.get('full-path')
        raise ValueError('EPUB container.xml has no rootfile')

    def _resolve(self, href: str) -> str:
        joined = posixpath.join(self._opf_dir, href) if self._opf_dir else href
        return posixpath.normpath(joined)

    def _parse_spine(self, opf_path: str) -> List[Optional[str]]:
        root = ET.fromstring(self._zip.read(opf_path))
        manifest: Dict[str, str] = {}
        order: List[str] = []
        for el in root.iter():
            tag = _local_tag(el.tag)
            if tag == 'item' and el.get('id'):
                manifest[el.get('id')] = el.get('href')
            elif tag == 'itemref' and el.get('idref'):
                order.append(el.get('idref'))
        return [self._resolve(manifest[i]) if manifest.get(i) else None for i in order]

    def document(self, spine_index: int) -> minidom.Document:
        """Parsed content document for the 1-based spine index."""
        path = self.spine[spine_index - 1]
        if path is None:
            raise ValueError(f'spine item {spine_index} has no content document')
        if path not in self._dom_cache:
            self._dom_cache[path] = minidom.parseString(self._zip.read(path))
        return self._dom_cache[path]

    def text_length(self, spine_index: int) -> int:
        """Number of characters of text content in the 1-based spine item."""
        if spine_index in self._len_cache:
            return self._len_cache[spine_index]
        path = self.spine[spine_index - 1]
        length = 0 if path is None else len(_text_content(self.document(spine_index)))
        self._len_cache[spine_index] = length
        return length

    def total_text_length(self) -> int:
        return sum(self.text_length(i) for i in range(1, len(self.spine) + 1))


def _local_tag(tag: str) -> str:
    return tag.split('}')[-1]


def _text_content(node) -> str:
    parts: List[str] = []
    stack = list(node.childNodes)[::-1]
    while stack:
        cur = stack.pop()
        if cur.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
            parts.append(cur.data)
        elif cur.nodeType == Node.ELEMENT_NODE:
            stack.extend(list(cur.childNodes)[::-1])
    return ''.join(parts)


def _parse_crengine_xpath(xpath: str) -> Tuple[int, List[str], Optional[int]]:
    """Split a crengine xpointer into (docfragment_index, steps, offset).

    e.g. "/body/DocFragment[21]/body/html/body/p[9]/text().337"
         → (21, ['html', 'body', 'p[9]', 'text()'], 337)
    The leading crengine wrappers (everything up to and including `html`) are
    dropped so the remaining steps map onto the real content document.
    """
    match = re.match(r'^/body/DocFragment\[(\d+)\](/.*)$', xpath)
    if not match:
        raise ValueError(f'unrecognized crengine xpath: {xpath!r}')
    fragment_index = int(match.group(1))
    rest = match.group(2)

    offset: Optional[int] = None
    offset_match = re.search(r'\.(\d+)$', rest)
    if offset_match:
        offset = int(offset_match.group(1))
        rest = rest[:offset_match.start()]

    steps = [s for s in rest.split('/') if s]
    if 'html' in steps:
        steps = steps[steps.index('html') + 1:]
    return fragment_index, steps, offset


def _parse_step(step: str) -> Tuple[str, int]:
    """`p[9]` → ('p', 9); `body` → ('body', 1)."""
    match = re.match(r'^([^\[\]]+)(?:\[(\d+)\])?$', step)
    if not match:
        raise ValueError(f'unrecognized xpath step: {step!r}')
    return match.group(1), int(match.group(2)) if match.group(2) else 1


def _resolve_anchor(document: minidom.Document, steps: List[str]):
    """Resolve crengine steps to (element_cfi_steps, text_step_or_None, target_node).

    `element_cfi_steps` are the even CFI numbers for each element on the path;
    `text_step` is the odd CFI number of the trailing text node (if any).
    """
    node = document.documentElement  # <html>
    element_steps: List[int] = []
    text_step: Optional[int] = None
    target_node = node

    for step in steps:
        if step.startswith('text()'):
            index_match = re.match(r'^text\(\)(?:\[(\d+)\])?$', step)
            text_index = int(index_match.group(1)) if index_match and index_match.group(1) else 1
            text_nodes = [c for c in node.childNodes
                          if c.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE)]
            target_node = text_nodes[text_index - 1]
            elements_before = 0
            for child in node.childNodes:
                if child is target_node:
                    break
                if child.nodeType == Node.ELEMENT_NODE:
                    elements_before += 1
            text_step = 2 * elements_before + 1
            break

        name, typed_index = _parse_step(step)
        element_children = [c for c in node.childNodes if c.nodeType == Node.ELEMENT_NODE]
        matches = [c for c in element_children if _local_name(c) == name]
        node = matches[typed_index - 1]
        element_steps.append(2 * (element_children.index(node) + 1))
        target_node = node

    return element_steps, text_step, target_node


def _anchor_to_cfi(epub: EpubContent, xpath: str):
    """Return (spine_step, element_steps, text_step, offset, target_node)."""
    fragment_index, steps, offset = _parse_crengine_xpath(xpath)
    document = epub.document(fragment_index)
    element_steps, text_step, target_node = _resolve_anchor(document, steps)
    return 2 * fragment_index, element_steps, text_step, offset, target_node


def _leaf(text_step: Optional[int], offset: Optional[int]) -> str:
    if text_step is None:
        return ''
    return f'/{text_step}:{offset}' if offset is not None else f'/{text_step}'


def xpaths_to_cfi(epub: EpubContent, xpath_begin: str, xpath_end: str) -> str:
    """Build a Readest/foliate range CFI from a citation's begin/end xpointers."""
    spine_b, elems_b, text_b, off_b, _ = _anchor_to_cfi(epub, xpath_begin)
    spine_e, elems_e, text_e, off_e, _ = _anchor_to_cfi(epub, xpath_end)

    if spine_b == spine_e:
        common_len = 0
        while (common_len < len(elems_b) and common_len < len(elems_e)
               and elems_b[common_len] == elems_e[common_len]):
            common_len += 1
        parent = f'/6/{spine_b}!' + ''.join(f'/{s}' for s in elems_b[:common_len])
        start = ''.join(f'/{s}' for s in elems_b[common_len:]) + _leaf(text_b, off_b)
        end = ''.join(f'/{s}' for s in elems_e[common_len:]) + _leaf(text_e, off_e)
        return f'epubcfi({parent},{start},{end})'

    # begin/end in different spine items: factor the range at the spine level
    start = f'/{spine_b}!' + ''.join(f'/{s}' for s in elems_b) + _leaf(text_b, off_b)
    end = f'/{spine_e}!' + ''.join(f'/{s}' for s in elems_e) + _leaf(text_e, off_e)
    return f'epubcfi(/6,{start},{end})'


def _chars_before(document: minidom.Document, target_node) -> int:
    """Characters of text content appearing before `target_node` in document order."""
    count = 0
    found = False

    def walk(node) -> None:
        nonlocal count, found
        for child in node.childNodes:
            if found:
                return
            if child is target_node:
                found = True
                return
            if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                count += len(child.data)
            elif child.nodeType == Node.ELEMENT_NODE:
                walk(child)

    walk(document.documentElement)
    return count


def estimate_page(epub: EpubContent, xpath_begin: str, total_locations: int) -> Optional[int]:
    """Estimate the Readest page of a highlight from its character fraction.

    Readest derives a booknote's page from foliate's "locations" (the book split
    into ~equal character blocks). We reproduce it as
        round(fraction * total_locations) + 1
    where `fraction` is (characters before the anchor) / (total characters).
    """
    if not total_locations:
        return None
    fragment_index, steps, offset = _parse_crengine_xpath(xpath_begin)
    document = epub.document(fragment_index)
    _, _, target_node = _resolve_anchor(document, steps)

    chars_before = sum(epub.text_length(i) for i in range(1, fragment_index))
    chars_before += _chars_before(document, target_node)
    chars_before += offset or 0

    total = epub.total_text_length()
    if total <= 0:
        return None
    fraction = chars_before / total
    return round(fraction * total_locations) + 1


def read_nav_total(nav_path: str) -> int:
    """Total number of foliate locations from a Readest `nav.json` file."""
    try:
        with open(nav_path, encoding='utf-8') as f:
            nav = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    for entry in nav.get('toc', []):
        total = (entry.get('location') or {}).get('total')
        if total:
            return int(total)
    return 0


def find_book_epub(book_dir: str) -> Optional[str]:
    matches = sorted(glob.glob(os.path.join(book_dir, '*.epub')))
    return matches[0] if matches else None


def build_booknotes(
    citations: List[dict],
    book: dict,
    book_dir: str,
) -> Tuple[List[dict], List[str]]:
    """Convert a ReadEra doc's citations into Readest `annotation` booknotes.

    Returns (booknotes, failures). Citations that cannot be converted are
    skipped and reported in `failures` rather than aborting the book.
    """
    booknotes: List[dict] = []
    failures: List[str] = []
    if not citations:
        return booknotes, failures

    epub_path = find_book_epub(book_dir)
    if not epub_path:
        failures.append(f"{book.get('title', '')}: no .epub found in '{book_dir}'")
        return booknotes, failures

    try:
        epub = EpubContent(epub_path)
    except (OSError, zipfile.BadZipFile, ValueError, ET.ParseError) as exc:
        failures.append(f"{book.get('title', '')}: cannot read EPUB ({exc})")
        return booknotes, failures

    total_locations = read_nav_total(os.path.join(book_dir, 'nav.json'))
    used_ids: Set[str] = set()

    try:
        for citation in citations:
            note_data_raw = citation.get('note_data') or '{}'
            try:
                note_data = json.loads(note_data_raw)
            except (json.JSONDecodeError, TypeError):
                note_data = {}

            xpath_begin = note_data.get('xPath')
            xpath_end = note_data.get('xPathEnd') or xpath_begin
            text = citation.get('note_body') or ''
            if not xpath_begin or not text:
                continue

            try:
                cfi = xpaths_to_cfi(epub, xpath_begin, xpath_end)
                page = estimate_page(epub, xpath_begin, total_locations)
            except (ValueError, IndexError, AttributeError) as exc:
                failures.append(f"{book.get('title', '')}: {xpath_begin} ({exc})")
                continue

            created = citation.get('note_insert_time') or int(
                datetime.now(timezone.utc).timestamp() * 1000
            )
            modified = citation.get('note_modified_time') or created
            note_id = generate_note_id(used_ids)
            used_ids.add(note_id)

            booknotes.append({
                'bookHash': book.get('hash'),
                'metaHash': book.get('metaHash'),
                'id': note_id,
                'type': 'annotation',
                'cfi': cfi,
                'xpointer0': None,
                'xpointer1': None,
                'page': page,
                'text': text,
                'style': 'underline',
                'color': 'green',
                'note': '',
                'createdAt': created,
                'updatedAt': modified,
                'deletedAt': None,
            })
    finally:
        epub.close()

    return booknotes, failures


# ──────────────────────────────────────────────────────────────────────────────
# Booknotes: ReadEra PDF citations → Readest annotations
# ──────────────────────────────────────────────────────────────────────────────
#
# PDFs have no shared text document: ReadEra anchors a citation with its own
# extractor (e.g. "/page[19]/block[13]/line[0]/char[37]@x:y") while Readest
# renders each page with pdf.js into an HTML text layer and stores highlights as
# EPUB-style CFIs resolved against THAT DOM. The two index spaces don't map, so
# we reproduce Readest's exact pipeline via a bundled Node helper (pdf.js +
# foliate's own CFI generator) and locate the highlight by its text. We keep only
# the page index from ReadEra's xpointer; the in-page CFI comes from pdf.js.

PDF_CFI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools', 'pdf_cfi')
PDF_CFI_SCRIPT = os.path.join(PDF_CFI_DIR, 'pdf_to_cfi.mjs')


def parse_pdf_page0(xpath: str) -> Optional[int]:
    """0-based page index N from a crengine PDF xpointer '/page[N]/...'."""
    match = re.match(r'/page\[(\d+)\]', xpath or '')
    return int(match.group(1)) if match else None


def run_pdf_cfi_helper(pdf_path: str, items: List[dict]) -> List[dict]:
    """Call the Node pdf.js helper to convert (page0, text) items into CFIs.

    Returns a list aligned with `items`; each element is {'cfi': ...} on success
    or {'error': ...} on a per-item failure. Raises RuntimeError if the helper
    cannot be run at all (missing Node, missing deps, crash).
    """
    node = shutil.which('node')
    if not node:
        raise RuntimeError("Node.js ('node') not found on PATH; required for PDF highlights")
    if not os.path.isdir(os.path.join(PDF_CFI_DIR, 'node_modules')):
        raise RuntimeError(f"PDF helper dependencies missing; run `npm install` in {PDF_CFI_DIR}")

    payload = json.dumps({'pdf': pdf_path, 'items': items})
    proc = subprocess.run(
        [node, PDF_CFI_SCRIPT],
        input=payload, capture_output=True, text=True, cwd=PDF_CFI_DIR,
    )
    if not proc.stdout:
        raise RuntimeError(f"PDF helper produced no output: {proc.stderr.strip()[:500]}")
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"PDF helper returned invalid JSON: {proc.stdout[:300]}")
    if isinstance(out, dict) and out.get('error'):
        raise RuntimeError(f"PDF helper error: {out['error']}")
    return out.get('results', [])


def find_book_pdf(book_dir: str) -> Optional[str]:
    matches = sorted(glob.glob(os.path.join(book_dir, '*.pdf')))
    return matches[0] if matches else None


def build_pdf_booknotes(
    citations: List[dict],
    book: dict,
    book_dir: str,
) -> Tuple[List[dict], List[str]]:
    """Convert a ReadEra PDF doc's citations into Readest `annotation` booknotes.

    Returns (booknotes, failures). Citations that cannot be converted are skipped
    and reported in `failures` rather than aborting the book.
    """
    booknotes: List[dict] = []
    failures: List[str] = []
    title = book.get('title', '')
    if not citations:
        return booknotes, failures

    pdf_path = find_book_pdf(book_dir)
    if not pdf_path:
        failures.append(f"{title}: no .pdf found in '{book_dir}'")
        return booknotes, failures

    prepared: List[Tuple[dict, int, str]] = []
    for citation in citations:
        try:
            note_data = json.loads(citation.get('note_data') or '{}')
        except (json.JSONDecodeError, TypeError):
            note_data = {}
        page0 = parse_pdf_page0(note_data.get('xPath') or '')
        text = citation.get('note_body') or ''
        if page0 is None or not text:
            failures.append(f"{title}: citation missing page/text")
            continue
        prepared.append((citation, page0, text))

    if not prepared:
        return booknotes, failures

    items = [{'page0': page0, 'text': text} for (_, page0, text) in prepared]
    try:
        results = run_pdf_cfi_helper(pdf_path, items)
    except RuntimeError as exc:
        failures.append(f"{title}: {exc}")
        return booknotes, failures

    book_hash = book.get('hash')
    meta_hash = book.get('metaHash')
    used_ids: Set[str] = set()

    for (citation, page0, text), result in zip(prepared, results):
        cfi = (result or {}).get('cfi')
        if not cfi:
            failures.append(f"{title}: page {page0} ({(result or {}).get('error', 'no cfi')})")
            continue

        created = citation.get('note_insert_time') or int(
            datetime.now(timezone.utc).timestamp() * 1000
        )
        modified = citation.get('note_modified_time') or created
        note_id = generate_note_id(used_ids)
        used_ids.add(note_id)

        booknotes.append({
            'bookHash': book_hash,
            'metaHash': meta_hash,
            'id': note_id,
            'type': 'annotation',
            'cfi': cfi,
            'xpointer0': None,
            'xpointer1': None,
            'page': page0 + 1,
            'text': text,
            'style': 'underline',
            'color': 'green',
            'note': '',
            'createdAt': created,
            'updatedAt': modified,
            'deletedAt': None,
        })

    return booknotes, failures


# ──────────────────────────────────────────────────────────────────────────────
# ReadEra library indexing
# ──────────────────────────────────────────────────────────────────────────────

def build_readera_index(
    readera: dict,
) -> Tuple[Dict[str, Tuple[str, dict]], Dict[str, str], Dict[str, Tuple[str, dict]], Dict[str, Tuple[str, dict]]]:
    """
    Builds two indexes from the ReadEra library:

    doc_map : normalized_title → (uri, doc_data)
        Allows looking up a document by title, tolerating accent and
        capitalization variations.
        Priority: `doc_title` field (book metadata) > `doc_file_name_title`
        (filename), as the filename may contain noise such as
        "dokumen.pub_o-estrangeiro-9788501117434".

    doc_to_coll : uri → collection_name
        Maps each document to the collection (group) it belongs to in ReadEra.
    """
    docs = readera.get('docs', [])
    colls = readera.get('colls', [])

    # URI → document data
    uri_to_data: Dict[str, dict] = {doc['uri']: doc['data'] for doc in docs}

    # URI → collection name
    doc_to_coll: Dict[str, str] = {}
    for coll in colls:
        coll_name = coll['data']['coll_title']
        for doc_uri in coll.get('docs', []):
            doc_to_coll[doc_uri] = coll_name

    # Normalized title → (uri, doc_data)
    doc_map: Dict[str, Tuple[str, dict]] = {}
    # identifier maps for direct matching
    md5_map: Dict[str, Tuple[str, dict]] = {}
    sha1_map: Dict[str, Tuple[str, dict]] = {}

    for uri, data in uri_to_data.items():
        # populate id maps
        md5 = data.get('doc_md5')
        sha1 = data.get('doc_sha1')
        if md5:
            md5_map[md5] = (uri, data)
        if sha1:
            sha1_map[sha1] = (uri, data)

        # collect candidate title sources
        candidates = []
        if data.get('doc_title'):
            candidates.append(data.get('doc_title'))
        if data.get('doc_file_name_title'):
            candidates.append(data.get('doc_file_name_title'))

        # some ReadEra entries may include nested metadata
        meta = data.get('meta', {}) or {}
        if isinstance(meta, dict):
            mt = meta.get('title') or meta.get('identifier')
            if mt:
                candidates.append(mt)

        for t in candidates:
            nt = normalize_title(t)
            if nt and nt not in doc_map:
                doc_map[nt] = (uri, data)

        # fallback: ensure filename is indexed
        fname = data.get('doc_file_name_title') or ''
        nf = normalize_title(fname)
        if nf and nf not in doc_map:
            doc_map[nf] = (uri, data)

    return doc_map, doc_to_coll, md5_map, sha1_map


def find_best_match(readest_title: str, norm_title: str, doc_map: Dict[str, Tuple[str, dict]], 
                    hard_mismatches: Dict, topn: int = 3,
                    ratio_thresh: float = 0.58, overlap_thresh: float = 0.5):
    """
    Return best match (uri,data) if it passes thresholds, else (None, candidates).
    First checks hard_mismatches for manual overrides, then does fuzzy matching.
    Candidates is a list of tuples (candidate_title, ratio, overlap, uri).
    """
    if not norm_title:
        return None, []

    # 1) Check hard_mismatches first
    if readest_title in hard_mismatches:
        readera_candidates = hard_mismatches[readest_title]
        for readera_title in readera_candidates:
            readera_norm = normalize_title(readera_title)
            if readera_norm in doc_map:
                uri, data = doc_map[readera_norm]
                return (uri, data), [(readera_norm, 1.0, 1.0, uri)]

    # 2) Fuzzy matching
    tokens_a = set(norm_title.split())
    scores = []
    for cand, (uri, data) in doc_map.items():
        ratio = difflib.SequenceMatcher(None, norm_title, cand).ratio()
        tokens_b = set(cand.split())
        min_len = max(1, min(len(tokens_a), len(tokens_b)))
        overlap = len(tokens_a & tokens_b) / min_len
        scores.append((ratio, overlap, cand, uri))

    # sort by ratio then overlap
    scores.sort(key=lambda x: (x[0], x[1]), reverse=True)

    candidates = [(c[2], round(c[0], 3), round(c[1], 3), c[3]) for c in scores[:topn]]
    if scores:
        best = scores[0]
        if best[0] >= ratio_thresh and best[1] >= overlap_thresh:
            return (best[3], doc_map[best[2]][1]), candidates

    return None, candidates


# ──────────────────────────────────────────────────────────────────────────────
# Main migration
# ──────────────────────────────────────────────────────────────────────────────

def migrate(
    readera_path: str,
    readest_library_path: str,
    readest_groups_path: str,
    out_library_path: str,
    out_groups_path: str,
) -> None:
    """
    Runs the full migration and saves the output files.

    Parameters
    ----------
    readera_path          : path to the ReadEra library file
    readest_library_path  : path to the Readest library file
    readest_groups_path   : path to the Readest groups file
    out_library_path      : destination for the migrated Readest library
    out_groups_path       : destination for the updated groups file
    """

    # ── 1. Load input files ──────────────────────────────────────────────────
    with open(readera_path, encoding='utf-8') as f:
        readera = json.load(f)
    with open(readest_library_path, encoding='utf-8') as f:
        readest_library: List[dict] = json.load(f)
    with open(readest_groups_path, encoding='utf-8') as f:
        readest_groups: List[dict] = json.load(f)

    # ── 2. Build ReadEra indexes ─────────────────────────────────────────────
    doc_map, doc_to_coll, md5_map, sha1_map = build_readera_index(readera)
    # uri → citations (highlights), kept separate from the per-doc `data` index
    uri_to_citations: Dict[str, List[dict]] = {
        doc['uri']: doc.get('citations', []) for doc in readera.get('docs', [])
    }

    # ── 2b. Load hard_mismatches for manual overrides ─────────────────────────
    hard_mismatches: Dict = {}
    try:
        with open('knowledge/hard_mismatches.json', encoding='utf-8') as f:
            hard_mismatches = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # no hard_mismatches file; use empty dict
        pass

    # ── 3. Readest groups index ──────────────────────────────────────────────
    # name → id  (for fast lookup and new group detection)
    groups_by_name: Dict[str, str] = {g['groupName']: g['groupId'] for g in readest_groups}
    existing_ids: Set[str] = {g['groupId'] for g in readest_groups}
    updated_groups = deepcopy(readest_groups)  # modified in-place if new groups are created

    # ── 4. Process each book in the Readest library ──────────────────────────
    matched: List[str] = []
    not_matched: List[str] = []
    new_groups_created: List[str] = []

    output_library = []
    match_candidates: Dict[str, List] = {}
    booknotes_migrated: Dict[str, int] = {}
    booknote_failures: List[str] = []

    for book in deepcopy(readest_library):
        title = book.get('title', '').strip()
        key = normalize_title(title)

        match = None

        # 1) try direct identifier match (hash / metaHash)
        book_hash = book.get('hash')
        meta_hash = book.get('metaHash')
        if book_hash:
            match = md5_map.get(book_hash) or sha1_map.get(book_hash)
        if not match and meta_hash:
            match = md5_map.get(meta_hash) or sha1_map.get(meta_hash)

        # 2) exact normalized title
        if not match:
            match = doc_map.get(key)

        # 3) fuzzy matching
        if not match:
            best, candidates = find_best_match(title, key, doc_map, hard_mismatches)
            if best:
                match = best
            else:
                not_matched.append(title)
                match_candidates[title] = candidates
                output_library.append(book)
                continue

        # found a match
        if title not in matched:
            matched.append(title)

        uri, data = match

        # get book's config.json (Readest stores one folder per book: <root>/<hash>/)
        readest_root = os.path.dirname(readest_library_path)
        book_dir = os.path.join(readest_root, book_hash) if book_hash else readest_root
        book_config_path = os.path.join(book_dir, 'config.json')
        if os.path.isfile(book_config_path):
            with open(book_config_path, encoding='utf-8') as f:
                book_config: dict = json.load(f)
        else:
            book_config = {
                "searchConfig": {},
                "schemaVersion": 1,
            }

        # ── 4a. Progress ─────────────────────────────────────────────────
        progress = extract_progress(data.get('doc_position', '{}'))
        book['progress'] = progress
        book_config['progress'] = progress

        # ── 4b. Group ────────────────────────────────────────────────────
        coll_name = doc_to_coll.get(uri)
        if coll_name:
            # Create the group in Readest if it doesn't exist yet
            if coll_name not in groups_by_name:
                new_id = generate_group_id(existing_ids)
                existing_ids.add(new_id)
                groups_by_name[coll_name] = new_id
                updated_groups.append({'groupId': new_id, 'groupName': coll_name})
                new_groups_created.append(f"'{coll_name}' → id={new_id}")

            book['groupId'] = groups_by_name[coll_name]
            book['groupName'] = coll_name

        output_library.append(book)

        # ── 4c. Booknotes ────────────────────────────────────────────────
        # Migrate ReadEra citations (highlights) → Readest `annotation` booknotes.
        # EPUBs convert the xpointer directly; PDFs go through the pdf.js helper.
        book_format = (book.get('format') or data.get('doc_format') or '').upper()
        citations = uri_to_citations.get(uri, [])
        new_booknotes: List[dict] = []
        failures: List[str] = []
        if citations and book_format == 'EPUB':
            new_booknotes, failures = build_booknotes(citations, book, book_dir)
        elif citations and book_format == 'PDF':
            new_booknotes, failures = build_pdf_booknotes(citations, book, book_dir)
        booknote_failures.extend(failures)
        if new_booknotes:
            existing_booknotes = book_config.get('booknotes') or []
            book_config['booknotes'] = existing_booknotes + new_booknotes
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            book_config['lastSyncedAtNotes'] = now_ms
            book_config['lastPushedAtNotes'] = now_ms
            booknotes_migrated[title] = len(new_booknotes)

        # ── 4d. Updated At ───────────────────────────────────────────────
        book['updatedAt'] = int(datetime.now(timezone.utc).timestamp() * 1000)
        book_config['updatedAt'] = int(datetime.now(timezone.utc).timestamp() * 1000)

        # ── 4e. Last Pushed At Config ────────────────────────────────────
        book_config['lastPushedAtConfig'] = int(datetime.now(timezone.utc).timestamp() * 1000)

        # ── 4f. Save book's config file ──────────────────────────────────
        os.makedirs(os.path.dirname(book_config_path), exist_ok=True)
        with open(book_config_path, 'w', encoding='utf-8') as f:
            json.dump(book_config, f, ensure_ascii=False, separators=(',', ':'))

    # ── 5. Save output files ─────────────────────────────────────────────────
    with open(out_library_path, 'w', encoding='utf-8') as f:
        json.dump(output_library, f, ensure_ascii=False, separators=(',', ':'))

    with open(out_groups_path, 'w', encoding='utf-8') as f:
        json.dump(updated_groups, f, ensure_ascii=False, separators=(',', ':'))

    # write match candidates for manual review (if any)
    if match_candidates:
        with open('readest_migrated/match_candidates.json', 'w', encoding='utf-8') as f:
            json.dump(match_candidates, f, ensure_ascii=False, indent=2)

    # write booknote conversion failures for manual review (if any)
    if booknote_failures:
        os.makedirs('readest_migrated', exist_ok=True)
        with open('readest_migrated/booknote_failures.json', 'w', encoding='utf-8') as f:
            json.dump(booknote_failures, f, ensure_ascii=False, indent=2)

    # ── 6. Report ────────────────────────────────────────────────────────────
    sep = '─' * 62
    print(sep)
    print('  ReadEra → Readest migration complete')
    print(sep)
    print(f'  Books migrated        : {len(matched)}')
    for t in matched:
        print(f'    ✓ {t}')
    print(f'  No match found        : {len(not_matched)}')
    for t in not_matched:
        print(f'    ✗ {t}')
    print(f'  New groups created    : {len(new_groups_created)}')
    for g in new_groups_created:
        print(f'    + {g}')
    total_booknotes = sum(booknotes_migrated.values())
    print(f'  Booknotes migrated    : {total_booknotes}')
    for t, n in booknotes_migrated.items():
        print(f'    ✎ {t} ({n})')
    if booknote_failures:
        print(f'  Booknotes skipped     : {len(booknote_failures)}')
    print()
    print(f'  → Library : {out_library_path}')
    print(f'  → Groups  : {out_groups_path}')
    print(sep)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Migrates reading progress and groups from ReadEra to Readest.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--readera',
        default=os.path.join(get_library_directory('readera'), 'library.json'),
        help='Library file exported from ReadEra',
    )
    parser.add_argument(
        '--readest',
        default=os.path.join(get_library_directory('readest'), 'library.json'),
        help='Current Readest library file',
    )
    parser.add_argument(
        '--groups',
        default=get_readest_groups_filepath(),
        help='Readest groups file (id + name)',
    )
    parser.add_argument(
        '--out-library',
        default='readest_migrated/library.json',
        help='Output: Readest library with migrated progress and groups',
    )
    parser.add_argument(
        '--out-groups',
        default='readest_migrated/groups.json',
        help='Output: Readest groups file (including any newly created groups)',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    migrate(
        readera_path=args.readera,
        readest_library_path=args.readest,
        readest_groups_path=args.groups,
        out_library_path=args.out_library,
        out_groups_path=args.out_groups,
    )
