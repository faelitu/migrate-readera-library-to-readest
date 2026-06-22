import os
import json
import argparse
from typing import List

from .paths import get_library_directory
from .norms import normalize_title

def deduplicate_booknotes(new_booknotes: List[dict], existing_booknotes: List[dict]):
    existing_norm_texts = [normalize_title(bn['text']) for bn in existing_booknotes]
    deduped = [bn for bn in new_booknotes if normalize_title(bn['text']) not in existing_norm_texts]
    return deduped

def fix_duplicated_bookmarks(readest_dir: str):
    books_dirs = [
        os.path.join(readest_dir, d)
        for d in os.listdir(readest_dir) 
        if os.path.isdir(os.path.join(readest_dir, d))
    ]
    
    library_filepath = os.path.join(readest_dir, 'library.json')
    with open(library_filepath, encoding='utf-8') as f:
        library: dict = json.load(f)

    book_count = 0
    for book_dir in books_dirs:
        config_filepath = os.path.join(book_dir, 'config.json')

        with open(config_filepath, encoding='utf-8') as f:
            book_config: dict = json.load(f)
        
        booknotes = book_config.get('booknotes') or []
        
        dedup = []
        text_found = []
        dup_found = 0
        for booknote in booknotes:
            norm_text = normalize_title(booknote['text'])
            bn_type = booknote['type']
            bn_page = booknote['page']
            if (bn_type, bn_page, norm_text) not in text_found:
                text_found.append((bn_type, bn_page, norm_text))
                booknote['text'] = booknote['text'].replace('\n', '')
                dedup.append(booknote)
            else:
                dup_found += 1
        
        if dup_found:
            book_count += 1
            book_config['booknotes'] = sorted(dedup, key=lambda x: x['page'])
            with open(config_filepath, 'w', encoding='utf-8') as f:
                json.dump(book_config, f, ensure_ascii=False, separators=(',', ':'))
            
            book_hash = os.path.basename(book_dir)
            book = [b for b in library if b['hash'] == book_hash][0]
            book_title = book['title']
            print(f"{book_count}: {book_title}")
            print(f"  - {dup_found}/{len(booknotes)} duplicated bookmarks found in {book_dir} and deleted.")
            print(f"  - {len(dedup)} bookmarks remaining.")
    
    if book_count:
        print("All bookmarks deduplicated.")
    else:
        print("No duplicated bookmarks found.")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deduplicates Readest's bookmarks.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--readest_dir',
        default=get_library_directory('readest'),
        help="Readest's library directory location",
    )
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()
    fix_duplicated_bookmarks(
        readest_dir=args.readest_dir
    )
