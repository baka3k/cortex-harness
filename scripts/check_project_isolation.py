#!/usr/bin/env python3
"""Fail when project-specific keywords leak into Git-tracked artifacts.

The denylist deliberately lives outside this repository.  This keeps the
scanner reusable and prevents customer or project identifiers from becoming
part of Cortex Harness itself.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DENYLIST_ENV = "CORTEX_PROJECT_KEYWORD_DENYLISTS"
_TEXT_ENCODINGS = ("utf-8-sig", "cp932", "shift_jis", "utf-16")


@dataclass(frozen=True, order=True)
class Finding:
    origin: str
    path: str
    line: int
    keyword_digest: str


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def load_keywords(paths: Iterable[Path]) -> tuple[str, ...]:
    keywords: dict[str, str] = {}
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ValueError(f"cannot read denylist {path}: {exc}") from exc
        for raw_line in content.splitlines():
            candidate = raw_line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            normalized = _normalize(candidate)
            if normalized:
                keywords.setdefault(normalized, candidate)
    if not keywords:
        raise ValueError("project keyword denylist is empty")
    return tuple(keywords[key] for key in sorted(keywords))


def _git(root: Path, *args: str, check: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or f"git {' '.join(args)} failed")
    return completed.stdout


def _tracked_paths(root: Path) -> tuple[str, ...]:
    payload = _git(root, "ls-files", "--cached", "-z")
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in payload.split(b"\0")
        if item
    )


def _changed_paths(root: Path, *, cached: bool) -> set[str]:
    args = ["diff"]
    if cached:
        args.append("--cached")
    args.extend(["--name-only", "--diff-filter=ACMR", "-z"])
    payload = _git(root, *args)
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in payload.split(b"\0")
        if item
    }


def _decode_candidates(payload: bytes) -> Iterable[str]:
    seen: set[str] = set()
    for encoding in _TEXT_ENCODINGS:
        if encoding == "utf-16" and not payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            text = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text not in seen:
            seen.add(text)
            yield text


def _digest(keyword: str) -> str:
    return hashlib.sha256(keyword.encode("utf-8")).hexdigest()[:12]


def _scan_text(origin: str, path: str, text: str, keywords: Sequence[str]) -> set[Finding]:
    findings: set[Finding] = set()
    for line_number, line in enumerate(text.splitlines() or [text], 1):
        normalized_line = _normalize(line)
        for keyword in keywords:
            if _normalize(keyword) in normalized_line:
                findings.add(Finding(origin, path, line_number, _digest(keyword)))
    return findings


def _scan_payload(origin: str, path: str, payload: bytes, keywords: Sequence[str]) -> set[Finding]:
    findings: set[Finding] = set()
    for text in _decode_candidates(payload):
        findings.update(_scan_text(origin, path, text, keywords))
    return findings


def scan_repository(
    root: Path,
    keywords: Sequence[str],
    *,
    source: str = "both",
) -> tuple[Finding, ...]:
    if source not in {"working-tree", "index", "both"}:
        raise ValueError(f"unsupported scan source: {source}")
    root = root.resolve()
    tracked = _tracked_paths(root)
    divergent = _changed_paths(root, cached=True) | _changed_paths(root, cached=False)
    findings: set[Finding] = set()
    for relative_path in tracked:
        absolute = root / relative_path
        if source in {"working-tree", "both"} and absolute.exists():
            findings.update(
                _scan_text("working-tree-path", relative_path, relative_path, keywords)
            )
        if source == "index" or (
            source == "both" and (relative_path in divergent or not absolute.exists())
        ):
            findings.update(
                _scan_text("index-path", relative_path, relative_path, keywords)
            )
        if source in {"working-tree", "both"}:
            try:
                if absolute.is_symlink():
                    payload = os.readlink(absolute).encode("utf-8", errors="surrogateescape")
                else:
                    payload = absolute.read_bytes()
            except OSError:
                payload = b""
            if payload:
                findings.update(_scan_payload("working-tree", relative_path, payload, keywords))
        if source == "index" or (
            source == "both" and (relative_path in divergent or not absolute.exists())
        ):
            index_payload = _git(root, "show", f":{relative_path}", check=False)
            if index_payload:
                findings.update(_scan_payload("index", relative_path, index_payload, keywords))
    return tuple(sorted(findings))


def _denylist_paths(cli_paths: Sequence[str]) -> tuple[Path, ...]:
    values = list(cli_paths)
    configured = os.environ.get(DENYLIST_ENV, "")
    if configured:
        values.extend(item for item in configured.split(os.pathsep) if item)
    return tuple(Path(item).expanduser().resolve() for item in values)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", default=".", help="Git worktree to scan")
    parser.add_argument(
        "--denylist-file",
        action="append",
        default=[],
        help=f"External UTF-8 keyword file; may also be supplied via {DENYLIST_ENV}",
    )
    parser.add_argument(
        "--source",
        choices=("working-tree", "index", "both"),
        default="both",
        help="Git view to scan; both also checks divergent index blobs",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = _denylist_paths(args.denylist_file)
    if not paths:
        print("[project-isolation] NOT_RUN: no external denylist configured", file=sys.stderr)
        return 2
    try:
        keywords = load_keywords(paths)
        findings = scan_repository(Path(args.root), keywords, source=args.source)
    except ValueError as exc:
        print(f"[project-isolation] NOT_RUN: {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(
                f"{finding.origin}:{finding.path}:{finding.line}:"
                f"keyword_sha256={finding.keyword_digest}"
            )
        print(f"[project-isolation] FAILED: findings={len(findings)}", file=sys.stderr)
        return 1
    print(
        f"[project-isolation] VERIFIED: tracked_files={len(_tracked_paths(Path(args.root)))} "
        f"keywords={len(keywords)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
