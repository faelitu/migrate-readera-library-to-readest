#!/usr/bin/env python3
"""
readera_to_readest.py
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
    python readera_to_readest.py [options]

    python readera_to_readest.py \\
        --readera     readera-library.json \\
        --readest     readest-library.json \\
        --groups      readest_groups.json  \\
        --out-library readest-library-migrated.json \\
        --out-groups  readest_groups-migrated.json
"""

import json
import random
import argparse
import unicodedata
from copy import deepcopy
from typing import Optional, Tuple, Dict, Set, List


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """
    Normalizes a title for comparison, tolerant of spelling variations:
    converts to lowercase, removes accents and extra whitespace.

    Example: "Crepúsculo dos Ídolos" → "crepusculo dos idolos"
    """
    nfd = unicodedata.normalize('NFD', title)
    sem_acentos = ''.join(c for c in nfd if not unicodedata.combining(c))
    return sem_acentos.lower().strip()


def generate_group_id(existing_ids: Set[str], length: int = 7) -> str:
    """
    Generates a random hexadecimal ID of `length` characters (lowercase),
    guaranteed not to collide with any existing ID.

    The format follows the pattern observed in Readest groups: e.g. "f882f2c".
    """
    while True:
        new_id = ''.join(random.choices('0123456789abcdef', k=length))
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
) -> Tuple[Dict[str, Tuple[str, dict]], Dict[str, str]]:
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
    for uri, data in uri_to_data.items():
        title = data.get('doc_title') or data.get('doc_file_name_title', '')
        if title:
            doc_map[normalize_title(title)] = (uri, data)

    return doc_map, doc_to_coll


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
    doc_map, doc_to_coll = build_readera_index(readera)

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

    for book in deepcopy(readest_library):
        title = book.get('title', '').strip()
        key = normalize_title(title)
        match = doc_map.get(key)

        if match:
            uri, data = match
            matched.append(title)

            # ── 4a. Progress ─────────────────────────────────────────────────
            # Extract from doc_position and overwrite the Readest progress field
            book['progress'] = extract_progress(data.get('doc_position', '{}'))

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
        else:
            not_matched.append(title)

        output_library.append(book)

    # ── 5. Save output files ─────────────────────────────────────────────────
    with open(out_library_path, 'w', encoding='utf-8') as f:
        json.dump(output_library, f, ensure_ascii=False, indent=4)

    with open(out_groups_path, 'w', encoding='utf-8') as f:
        json.dump(updated_groups, f, ensure_ascii=False, indent=4)

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
        default='former_libraries/readera/library.json',
        help='Library file exported from ReadEra',
    )
    parser.add_argument(
        '--readest',
        default='former_libraries/readest/library.json',
        help='Current Readest library file',
    )
    parser.add_argument(
        '--groups',
        default='readest_groups.json',
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
