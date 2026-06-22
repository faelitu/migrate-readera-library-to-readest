import os
import json
import argparse

from .times import now_ms
from .paths import get_library_directory


def delete_bookmarks(readest_dir: str):
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
        
        deleted = 0
        deleted_booknotes = []
        for booknote in booknotes:
            was_deleted = booknote['deletedAt']
            if not was_deleted:
                booknote['deletedAt'] = now_ms()
                deleted_booknotes.append(booknote)
                deleted += 1

        if deleted:
            book_count += 1
            book_config['booknotes'] = sorted(deleted_booknotes, key=lambda x: x['page'])
            with open(config_filepath, 'w', encoding='utf-8') as f:
                json.dump(book_config, f, ensure_ascii=False, separators=(',', ':'))
            
            book_hash = os.path.basename(book_dir)
            book = [b for b in library if b['hash'] == book_hash][0]
            book_title = book['title']
            print(f"{book_count}: {book_title}")
            print(f"  - {len(deleted_booknotes)} active bookmarks were found in {book_dir} and deleted.")
            print(f"  - {len([bn for bn in book_config['booknotes'] if not bn['deletedAt']])} active bookmarks remaining.")
    
    if book_count:
        print("All bookmarks deleted.")
    else:
        print("No bookmarks found.")

def really_delete_bookmarks(readest_dir: str):
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
        
        if booknotes:
            book_count += 1
            book_config['booknotes'] = []
            with open(config_filepath, 'w', encoding='utf-8') as f:
                json.dump(book_config, f, ensure_ascii=False, separators=(',', ':'))
            
            book_hash = os.path.basename(book_dir)
            book = [b for b in library if b['hash'] == book_hash][0]
            book_title = book['title']
            print(f"{book_count}: {book_title}")
            print(f"  - {len(booknotes)} bookmarks were found in {book_dir} and deleted for real.")
            print(f"  - {len(book_config['booknotes'])} bookmarks remaining.")
    
    if book_count:
        print("All bookmarks deleted.")
    else:
        print("No bookmarks found.")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deletes Readest's bookmarks.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--readest_dir',
        default=get_library_directory('readest'),
        help="Readest's library directory location",
    )
    parser.add_argument(
        '--force', '-f',
        default=False,
        help="Deletes bookmarks for real, not just setting deletedAt",
    )
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args() 
    delete_bookmarks(
        readest_dir=args.readest_dir
    )
    if args.force:
        print("⚠ Force Deleting: Deleting all Readest's bookmarks from files...")
        really_delete_bookmarks(
            readest_dir=args.readest_dir
        )