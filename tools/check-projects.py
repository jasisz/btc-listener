#!/usr/bin/env python3
"""Check every Aver project with its own explicit module root.

The historical concurrency probe has its own aver.toml and short imports.
Passing one module root to 'aver check .' cannot describe both projects.
A temporary symlink view excludes nested project roots from the parent's scan;
every excluded root is then checked separately. Source and manifests stay put.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def projects(root, aver):
    nested = []
    with tempfile.TemporaryDirectory(prefix="btc-check-") as directory:
        view = Path(directory)

        def link_tree(source, destination):
            destination.mkdir(exist_ok=True)
            for child in sorted(source.iterdir()):
                if child.name in (".git", ".claude", "__pycache__", "node_modules", "target"):
                    continue
                # A symlinked directory supplies imports owned by another project.
                # Check it at that owner, not as unrelated entrypoints here.
                if child.is_symlink() and child.is_dir():
                    continue
                target = destination / child.name
                if child.is_dir() and not child.is_symlink():
                    if (child / "aver.toml").is_file():
                        nested.append(child)
                    else:
                        link_tree(child, target)
                else:
                    target.symlink_to(child, target_is_directory=child.is_dir())

        link_tree(root, view)
        subprocess.run([aver, "check", str(view), "--module-root", str(root)], cwd=root, check=True)
    for project in nested:
        projects(project, aver)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aver", default="aver")
    args = parser.parse_args()
    projects(Path(__file__).resolve().parents[1], args.aver)
