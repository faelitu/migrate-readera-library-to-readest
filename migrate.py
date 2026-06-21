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
import difflib
import argparse
import platform
import warnings
import unicodedata
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

        if os.path.isfile(readest_lib_dir):
            return readest_lib_dir
        else:
            warnings.warn(
                f"Readest's library not found in current environment. "
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
    library_filepath = os.path.join(get_library_directory('readest'), 'library.json')
    # TODO: montar dict de grupos e sobrescrever o groups.json
    return os.path.join(
        os.path.dirname(os.path.abspath(sys.argv[0])), 
        'former_libraries', 'readest', 'groups.json'
    )

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

        # get book's config.json
        book_config_path = os.path.join(os.path.dirname(readest_library_path), 'config.json')
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
        # TODO

        # ── 4d. Updated At ───────────────────────────────────────────────
        book['updatedAt'] = int(datetime.now(timezone.utc).timestamp() * 1000)
        book_config['updatedAt'] = int(datetime.now(timezone.utc).timestamp() * 1000)

        # ── 4e. Last Pushed At Config ────────────────────────────────────
        book_config['lastPushedAtConfig'] = int(datetime.now(timezone.utc).timestamp() * 1000)

        # ── 4f. Save book's config file ──────────────────────────────────
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
