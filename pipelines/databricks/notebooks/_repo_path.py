# Databricks notebook source
# Shared by the other notebooks (%run ./_repo_path): put the repo's Python packages on the path.
import os
import sys


def repo_root() -> str:
    """The Git folder root: the first parent of this notebook that holds pipelines/src."""
    path = os.getcwd()
    while path != "/":
        if os.path.isdir(os.path.join(path, "pipelines", "src")):
            return path
        path = os.path.dirname(path)
    raise RuntimeError("Run this notebook from the txlens Git folder (see docs/DATABRICKS.md).")


ROOT = repo_root()
for package in ("pipelines/src", "evals/src"):
    if os.path.join(ROOT, package) not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, package))
print(f"txlens code from {ROOT}")
