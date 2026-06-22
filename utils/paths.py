"""
TODO: write docstring
"""

import os
import sys
import json
import platform
import warnings
from typing import Dict

def get_library_directory(program_name: str, fallback: str | None = None) -> str:
    """
    TODO: write docstring
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
        if fallback:
            return fallback
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
            if fallback:
                return fallback
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
