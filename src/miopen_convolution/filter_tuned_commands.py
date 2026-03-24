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


def get_database_filename(arch: str, db_dir: Path) -> Path:
    """Get the database filename for the given architecture.
    
    Args:
        arch: Architecture name (e.g., 'mi300', 'mi308')
        db_dir: Directory containing database files
        
    Returns:
        Path to the .ufdb.txt database file
    """
    
    # TODO: add entries here as needed
    arch_to_gfx = {
        'mi300': 'gfx942130',
        'mi355': 'gfx950100',
    }
    
    if arch in arch_to_gfx:
        gfx_prefix = arch_to_gfx[arch]
        matching_files = sorted(db_dir.glob(f'{gfx_prefix}*.ufdb.txt'))
        
        if matching_files:
            logger.info(f"Found database file for {arch}: {matching_files[0]}")
            if len(matching_files) > 1:
                logger.info(f"Multiple matches found, using: {matching_files[0].name}")
            return matching_files[0]
        else:
            logger.warning(f"No database file found for {arch} (prefix: {gfx_prefix})")
    else:
        logger.warning(f"Unknown architecture '{arch}', trying auto-detection")
    
    ufdb_files = sorted(db_dir.glob('*.ufdb.txt'))
    
    if not ufdb_files:
        logger.warning("No .ufdb.txt files found")
        return db_dir / "placeholder.ufdb.txt"
    
    if len(ufdb_files) == 1:
        logger.info(f"Auto-detected database file: {ufdb_files[0]}")
        return ufdb_files[0]
    
    # Multiple files found, use first in a-z order
    logger.warning(f"Multiple .ufdb.txt files found, using {ufdb_files[0]}")
    return ufdb_files[0]


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
        '--arch',
        type=str,
        default='unknown',
        help='Architecture name (e.g., mi300, mi308) for database selection'
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
    db_file = get_database_filename(args.arch, db_dir)
    tuned_commands = load_database(db_file)
    
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