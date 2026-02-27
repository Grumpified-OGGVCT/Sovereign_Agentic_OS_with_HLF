#!/usr/bin/env python3
"""
Generate TextMate grammar (syntaxes/hlf.tmLanguage.json) from hls.yaml tokens.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent.parent
_HLS_PATH = _REPO_ROOT / "governance" / "hls.yaml"
_OUTPUT_PATH = _REPO_ROOT / "syntaxes" / "hlf.tmLanguage.json"


def generate() -> dict:
    with _HLS_PATH.open() as f:
        hls = yaml.safe_load(f)

    grammar = {
        "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
        "name": "HLF",
        "scopeName": "source.hlf",
        "fileTypes": ["hlf"],
        "patterns": [
            {"include": "#version_header"},
            {"include": "#tag"},
            {"include": "#terminator"},
            {"include": "#string"},
            {"include": "#number"},
            {"include": "#keyword"},
            {"include": "#comment"},
        ],
        "repository": {
            "version_header": {
                "match": r"\[HLF-v\d+\]",
                "name": "keyword.control.hlf",
            },
            "tag": {
                "match": r"\[[A-Z_]+\]",
                "name": "entity.name.tag.hlf",
            },
            "terminator": {
                "match": r"Ω",
                "name": "keyword.operator.terminator.hlf",
            },
            "string": {
                "match": r'"([^"\\]|\\.)*"',
                "name": "string.quoted.double.hlf",
            },
            "number": {
                "match": r"-?\d+(\.\d+)?",
                "name": "constant.numeric.hlf",
            },
            "keyword": {
                "match": r"\b(true|false)\b",
                "name": "constant.language.hlf",
            },
            "comment": {
                "match": r"#.*$",
                "name": "comment.line.number-sign.hlf",
            },
        },
    }
    return grammar


def main() -> None:
    grammar = generate()
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(json.dumps(grammar, indent=2))
    print(f"Generated {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
