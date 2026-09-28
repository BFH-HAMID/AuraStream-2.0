#!/usr/bin/env python3
"""Sanity-check README.md: local asset paths exist and internal #anchors
resolve against GitHub-style heading slugs (html-pipeline TocFilter rules:
downcase; strip anything that is not a unicode word char, '-' or ' ';
spaces -> '-')."""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def gh_slug(text: str) -> str:
    t = unicodedata.normalize("NFC", text).lower()
    t = re.sub(r"[^\w\- ]", "", t, flags=re.UNICODE)  # \w = unicode word chars
    return t.replace(" ", "-")


def main() -> int:
    src = README.read_text(encoding="utf-8")
    errors: list[str] = []

    # 1) local image/file references
    refs = re.findall(r'(?:src="|!\[[^\]]*\]\()([^")\s]+\.(?:png|gif|jpg|jpeg|webp|svg))', src)
    local = [r for r in refs if not r.startswith("http")]
    for r in local:
        if not (ROOT / r).exists():
            errors.append(f"missing local asset: {r}")
    print(f"local image refs checked: {len(local)} -> {sorted(set(local))}")

    # 2) headings -> slugs
    slugs: dict[str, int] = {}
    in_fence = False
    for line in src.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not m:
            continue
        slug = gh_slug(m.group(2).strip())
        slugs[slug] = slugs.get(slug, 0) + 1
    dupes = {s: c for s, c in slugs.items() if c > 1}
    if dupes:
        errors.append(f"duplicate heading slugs (GitHub suffixes -1..): {dupes}")

    # 3) internal links (markdown + raw HTML anchors)
    links = re.findall(r"\]\(#([^)]+)\)", src)
    links += re.findall(r'href="#([^"]+)"', src)
    for link in links:
        if link not in slugs:
            errors.append(f"broken anchor #{link}; closest: "
                          + ", ".join(sorted(slugs)[:0]) or "")
            # suggest nearest
            near = [s for s in slugs if link.replace("-", "") in s.replace("-", "")]
            if near:
                errors[-1] += f" (did you mean: {near})"

    print(f"heading slugs: {len(slugs)}; internal links: {len(links)}")
    if errors:
        print("\nERRORS:")
        for e in errors:
            print(" -", e)
        return 1
    print("README check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
