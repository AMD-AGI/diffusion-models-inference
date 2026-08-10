# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import argparse
import logging
import sys
from pathlib import Path
from typing import Set

from miopen_convolution import MIOpenConvolution

logger = logging.getLogger(__name__)


def load_database(db_path: Path) -> Set[MIOpenConvolution]:
    """Load all entries from *.ufdb.txt
    
    Args:
        db_path: Path to *.ufdb.txt
        
    Returns:
        Set of MIOpenConvolution instances
    """
    tuned_commands: Set[MIOpenConvolution] = set()
    
    if not db_path.exists():
        logger.info(f"DB file not found: {db_path}")
        return tuned_commands
    
    logger.info(f"Loading DB from {db_path}")
    with open(db_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            try:
                problem = MIOpenConvolution.from_db_key(line)
                tuned_commands.add(problem)
            except Exception as e:
                logger.warning(f"Failed to parse database entry at line {line_num}: {e}")
                continue
    
    logger.info(f"Loaded {len(tuned_commands)} unique tuned problems from database")
    return tuned_commands


def filter_commands(commands: list[str], tuned_commands: Set[MIOpenConvolution]) -> tuple[list[str], list[str]]:
    """Filter commands to remove those already tuned.
    
    Args:
        commands: List of MIOpenDriver command strings
        tuned_commands: Set of already-tuned MIOpenConvolution instances
        
    Returns:
        Tuple of (filtered_commands, skipped_commands)
    """
    filtered = []
    skipped = []
    
    for cmd in commands:
        cmd = cmd.strip()
        if not cmd or cmd.startswith('#'):
            continue
        
        try:
            problem = MIOpenConvolution.from_miopendriver_command(cmd)
            
            if problem in tuned_commands:
                skipped.append(cmd)
                logger.debug(f"Skipping already-tuned command: {cmd[:80]}...")
            else:
                filtered.append(cmd)
        except Exception as e:
            logger.warning(f"Failed to parse command, including it anyway: {cmd[:80]}... ({e})")
            filtered.append(cmd)
    
    return filtered, skipped


def get_database_filename(db_prefix: str, db_dir: Path) -> Path:
    """Get the database filename for the given prefix.
    
    Args:
        db_prefix: MIOpen DB filename prefix (e.g., 'gfx942130', 'gfx950100')
        db_dir: Directory containing database files
        
    Returns:
        Path to the .ufdb.txt database file
        
    Raises:
        FileNotFoundError: If no database file is found for the prefix
    """
    if not db_prefix:
        raise FileNotFoundError("No DB prefix provided, cannot determine database file")
    
    matching_files = sorted(db_dir.glob(f'{db_prefix}*.ufdb.txt'))
    
    if matching_files:
        logger.info(f"Found database file: {matching_files[0]}")
        if len(matching_files) > 1:
            logger.info(f"Multiple matches found, using: {matching_files[0].name}")
        return matching_files[0]
    else:
        raise FileNotFoundError(f"No database file found (prefix: {db_prefix}) in {db_dir}")


def main():
    parser = argparse.ArgumentParser(
        description='Filter MIOpenDriver commands to remove already-tuned workloads'
    )
    parser.add_argument(
        'commands_file',
        type=str,
        help='Input file containing MIOpenDriver commands (one per line)'
    )
    parser.add_argument(
        'output_file',
        type=str,
        help='Output file for filtered commands'
    )
    parser.add_argument(
        '--db-path',
        type=str,
        required=True,
        help='Path to MIOpen database directory (MIOPEN_USER_DB_PATH)'
    )
    parser.add_argument(
        '--db-prefix',
        type=str,
        default='',
        help='MIOpen DB filename prefix (e.g., gfx942130). If empty, filtering is skipped.'
    )
    
    args = parser.parse_args()
    
    # Load commands
    commands_path = Path(args.commands_file)
    if not commands_path.exists():
        logger.error(f"Commands file not found: {commands_path}")
        sys.exit(1)
    
    with open(commands_path, 'r') as f:
        commands = f.readlines()
    
    logger.info(f"Read {len(commands)} total commands")
    
    db_dir = Path(args.db_path)
    try:
        db_file = get_database_filename(args.db_prefix, db_dir)
        tuned_commands = load_database(db_file)
    except FileNotFoundError as e:
        logger.warning(f"{e} — skipping filtering")
        tuned_commands = set()
    
    untuned_commands, tuned_commands = filter_commands(commands, tuned_commands)
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        for cmd in untuned_commands:
            f.write(cmd.strip() + '\n')

    logger.info(f"Total commands: {len(commands)}")
    logger.info(f"Untuned: {len(untuned_commands)}")
    logger.info(f"Tuned: {len(tuned_commands)}")


if __name__ == '__main__':
    sys.exit(main())