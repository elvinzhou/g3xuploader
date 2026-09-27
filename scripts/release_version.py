#!/usr/bin/env python3
"""
Decide the next release from the commits since the last version tag.

Commit subjects follow Conventional Commits (https://www.conventionalcommits.org):

    feat: ...                  -> minor  (1.8.5 -> 1.9.0)
    fix: / perf: / refactor:   -> patch  (1.8.5 -> 1.8.6)
    revert: / build: / deps:   -> patch
    feat!: / "BREAKING CHANGE:" in the body -> major (1.8.5 -> 2.0.0)
    docs: / chore: / ci: / test: / style:  -> no release

Subjects that don't use a known type (e.g. the older "navdata: fix ...")
count as a patch, so a code change is never silently left unreleased.
Merge commits and "Bump version:" commits are ignored.

Used by .github/workflows/release.yml:

    python3 scripts/release_version.py --notes-file notes.md >> "$GITHUB_OUTPUT"

prints "part=<major|minor|patch>" (or "part=" when nothing needs releasing)
and writes grouped release notes to --notes-file.
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

MINOR_TYPES = {"feat"}
PATCH_TYPES = {"fix", "perf", "refactor", "revert", "build", "deps"}
NO_RELEASE_TYPES = {"docs", "chore", "ci", "test", "tests", "style"}

PARTS = ("patch", "minor", "major")  # ascending

_SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z][\w-]*)(?:\([^)]*\))?(?P<bang>!)?:\s*(?P<desc>.+)$")
_BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)


@dataclass
class Commit:
    sha: str
    subject: str
    body: str = ""


def classify(commit: Commit) -> Optional[str]:
    """Return the bump this commit needs: 'major', 'minor', 'patch', or None."""
    subject = commit.subject.strip()
    if subject.startswith("Bump version:") or subject.startswith("Merge "):
        return None

    if _BREAKING_RE.search(commit.body or ""):
        return "major"

    m = _SUBJECT_RE.match(subject)
    if not m:
        return "patch"
    if m.group("bang"):
        return "major"

    ctype = m.group("type").lower()
    if ctype in MINOR_TYPES:
        return "minor"
    if ctype in NO_RELEASE_TYPES:
        return None
    return "patch"  # PATCH_TYPES and unknown/legacy scopes alike


def highest(parts: List[Optional[str]]) -> Optional[str]:
    """The largest bump among parts (None when nothing needs releasing)."""
    ranked = [PARTS.index(p) for p in parts if p]
    return PARTS[max(ranked)] if ranked else None


def release_notes(commits: List[Commit]) -> str:
    """Markdown release notes grouped by kind; no-release commits are omitted."""
    sections = {"major": [], "minor": [], "patch": []}
    for c in commits:
        part = classify(c)
        if part:
            m = _SUBJECT_RE.match(c.subject.strip())
            desc = m.group("desc") if m and m.group("type").lower() in MINOR_TYPES | PATCH_TYPES else c.subject.strip()
            sections[part].append(f"- {desc} ({c.sha[:7]})")

    out = []
    for part, heading in (("major", "Breaking changes"), ("minor", "Features"), ("patch", "Fixes and other changes")):
        if sections[part]:
            out.append(f"## {heading}\n")
            out.extend(sections[part])
            out.append("")
    return "\n".join(out)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def last_tag() -> Optional[str]:
    try:
        return _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*").strip() or None
    except subprocess.CalledProcessError:
        return None


def commits_since(tag: Optional[str]) -> List[Commit]:
    rev_range = f"{tag}..HEAD" if tag else "HEAD"
    raw = _git("log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", rev_range)
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        commits.append(Commit(sha=sha, subject=subject, body=body))
    return commits


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", help="Tag to compare against (default: latest v* tag)")
    parser.add_argument("--override", default="auto", choices=("auto",) + PARTS,
                        help="Force a bump level instead of deriving it from commits")
    parser.add_argument("--notes-file", help="Write markdown release notes here")
    args = parser.parse_args(argv)

    tag = args.since or last_tag()
    commits = commits_since(tag)
    part = highest([classify(c) for c in commits])
    if args.override != "auto":
        part = args.override

    if args.notes_file:
        notes = release_notes(commits) or "Maintenance release.\n"
        with open(args.notes_file, "w") as f:
            f.write(notes)

    print(f"Since {tag or 'the first commit'}: {len(commits)} commit(s), bump: {part or 'none'}",
          file=sys.stderr)
    print(f"part={part or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
