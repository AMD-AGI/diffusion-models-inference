#!/usr/bin/env python3
"""
Ensure the deleted column exists in the database CSV file.
"""

import sys
import os


def ensure_deleted_column(database_path):
    """Ensure the deleted column exists in the database CSV.

    :param database_path: Path to the database CSV file.
    """
    if not os.path.exists(database_path):
        print("Database doesn't exist, skipping")
        return

    if os.path.getsize(database_path) == 0:
        print("Database is empty, skipping")
        return

    # Read the database
    with open(database_path, 'r', newline='') as f:
        lines = f.readlines()

    if not lines:
        print("Database has no lines, skipping")
        return

    # Check if 'deleted' column already exists
    header = lines[0].strip()
    if ',deleted' in header or header.endswith('deleted'):
        print("Deleted column already exists")
        return

    print("Adding deleted column to database")

    # Add 'deleted' to header
    lines[0] = header + ',deleted\n'

    # Add ',0' to all data rows
    for i in range(1, len(lines)):
        # Strip any trailing whitespace/newlines, then add ,0\n
        lines[i] = lines[i].rstrip() + ',0\n'

    # Write back to file
    with open(database_path, 'w', newline='') as f:
        f.writelines(lines)

    print("Successfully added deleted column with default value 0")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: ensure_deleted.py <database_path>")
        sys.exit(1)

    database_path = sys.argv[1]
    ensure_deleted_column(database_path)
