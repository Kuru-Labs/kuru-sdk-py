#!/usr/bin/env python3
"""
Bump version in pyproject.toml
Usage: python bump_version.py [patch|minor|major]
"""
import sys
import os
import tomllib
from pathlib import Path
from typing import Tuple

def parse_version(version_str: str) -> Tuple[int, int, int]:
    """Parse semantic version string."""
    parts = version_str.split('.')
    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {version_str}")
    return tuple(int(p) for p in parts)

def bump_version(version_str: str, bump_type: str) -> str:
    """Bump semantic version."""
    major, minor, patch = parse_version(version_str)

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    else:
        raise ValueError(f"Invalid bump type: {bump_type}")

    return f"{major}.{minor}.{patch}"

def main():
    bump_type = sys.argv[1] if len(sys.argv) > 1 else "patch"

    if bump_type not in ("patch", "minor", "major"):
        print(f"Error: Invalid bump type '{bump_type}'")
        sys.exit(1)

    pyproject_path = Path("pyproject.toml")

    # Read current version
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    old_version = data["project"]["version"]
    new_version = bump_version(old_version, bump_type)

    # Read the file as text to preserve formatting
    with open(pyproject_path, "r") as f:
        content = f.read()

    # Replace the version line
    updated_content = content.replace(
        f'version = "{old_version}"',
        f'version = "{new_version}"'
    )

    # Write back
    with open(pyproject_path, "w") as f:
        f.write(updated_content)

    # Output for GitHub Actions
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"old_version={old_version}\n")
            f.write(f"new_version={new_version}\n")

    print(f"Version bumped: {old_version} -> {new_version}")

if __name__ == "__main__":
    main()
