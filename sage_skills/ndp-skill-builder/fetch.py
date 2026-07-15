#!/usr/bin/env python3
"""NDP catalog fetcher — thin URL rewriter that delegates to ckan-skill-builder.

The National Data Platform (nationaldataplatform.org) is a standard CKAN
instance, but its CKAN API is mounted at `/catalog/` instead of the site
root and its canonical user-facing browse URLs are on a Next.js frontend
at `/dataset/<slug>` (not `/catalog/dataset/<slug>`). This wrapper
translates any NDP URL form into the `/catalog/api/3/action/package_show`
URL that ckan-skill-builder can consume unchanged, then delegates the
download to ckan-skill-builder in-process.

Keeping the delegation code-level (an import + call, not a subprocess and
not an agent-visible second hop) means the agent sees a normal two-hop
chain — ndp-skill-builder → tabular-skill-builder — and ckan-skill-builder
stays untouched.

Usage:
    python fetch.py <ndp-url> <out-dir>

<ndp-url> may be any of:
  - https://nationaldataplatform.org/dataset/<slug>              (canonical user-facing)
  - https://nationaldataplatform.org/catalog/dataset/<slug>      (CKAN native browse)
  - https://nationaldataplatform.org/catalog/api/3/action/package_show?id=<slug>
    (already the CKAN API URL — passed through unchanged)

Writes to <out-dir>/: exactly what ckan-skill-builder writes —
downloaded tabular resources, `_ckan_metadata.json`, `_skipped_resources.json`.
This wrapper adds no sidecars of its own.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse


def _rewrite_ndp_url(url: str) -> str:
    """Turn any NDP catalog URL into the CKAN /catalog/api/3/action/... URL."""
    parsed = urlparse(url)
    host = parsed.netloc or ""
    if "nationaldataplatform.org" not in host:
        raise ValueError(
            f"Not an NDP catalog URL: {url!r}. Expected a "
            "nationaldataplatform.org URL."
        )
    # Already API-form (either /catalog/api/... or /api/...); pass through.
    if "/api/3/action/package_show" in parsed.path:
        return url
    # Browse URL forms: /dataset/<slug> or /catalog/dataset/<slug>.
    parts = [p for p in parsed.path.split("/") if p]
    if "dataset" not in parts:
        raise ValueError(
            f"Cannot extract dataset slug from {url!r}. Expected "
            "https://nationaldataplatform.org/dataset/<slug> or "
            "https://nationaldataplatform.org/catalog/dataset/<slug>."
        )
    i = parts.index("dataset")
    if i + 1 >= len(parts):
        raise ValueError(f"Missing slug after /dataset/ in {url!r}")
    slug = parts[i + 1]
    return (
        f"https://nationaldataplatform.org/catalog/api/3/action/"
        f"package_show?id={slug}"
    )


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        print(__doc__)
        return

    ndp_url = argv[1]
    out_dir = argv[2]

    api_url = _rewrite_ndp_url(ndp_url)
    print(f"NDP URL       : {ndp_url}")
    print(f"CKAN API URL  : {api_url}")
    print("(delegating to ckan-skill-builder for the download)")
    print()

    # Import ckan-skill-builder's fetch.py at runtime and call its main().
    # Both skills live under the same skills root — this dance is the ONLY
    # place the two-skill dependency is expressed.
    _here = Path(__file__).resolve().parent
    ckan_dir = _here.parent / "ckan-skill-builder"
    ckan_fetch_path = ckan_dir / "fetch.py"
    if not ckan_fetch_path.exists():
        raise RuntimeError(
            f"ckan-skill-builder/fetch.py not found next to ndp-skill-builder "
            f"(looked at {ckan_fetch_path}). Ensure both skills are installed "
            "under the same skills directory."
        )
    if str(ckan_dir) not in sys.path:
        sys.path.insert(0, str(ckan_dir))
    import fetch as _ckan_fetch  # type: ignore  # noqa: E402
    _ckan_fetch.main(["fetch.py", api_url, out_dir])


if __name__ == "__main__":
    main(sys.argv)
