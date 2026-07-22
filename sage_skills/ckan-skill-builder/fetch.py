#!/usr/bin/env python3
"""CKAN dataset fetcher for ckan-skill-builder.

Downloads every resource from a CKAN dataset into a local directory, classifies
each one (array / tabular / docs), unpacks archives (.zip / .tar.gz), and prints
a ROUTE line telling the caller which core builder(s) to hand off to. The shared
classification + routing logic lives in `fetch_common` (same directory) so CKAN,
Zenodo, and NDP all agree on which core handles which format.

Usage:
    python fetch.py <ckan-dataset-url> <out-dir>

<ckan-dataset-url> is either:
  - `.../api/3/action/package_show?id=<slug>` (API URL), or
  - `.../dataset/<slug>` (browse URL — the API URL is inferred).

Writes to <out-dir>/:
  - The downloaded resources (tabular + array files at the top level, docs and
    non-tabular text under `_docs/`, archives unpacked in place).
  - `_ckan_metadata.json` — dataset title, notes, tags, license, organization,
    source URL, and per-resource metadata (for the downstream SKILL.md writer).
  - `_classification.json` — per-file class + source URL (for the downstream
    inventory, e.g. array-skill-builder's lazy-download URLs).
  - `_skipped_resources.json` — resources that were neither buildable nor docs.

Uses only the Python standard library — no pip install required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse, urlencode, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import fetch_common as fc

_UNSAFE_FS_CHARS = set('/\\:*?"<>|')


class _StripDefaultPortRedirect(HTTPRedirectHandler):
    """Rewrite redirect Locations that carry a redundant default port.

    Some CKAN portals sit behind S3, which 403s when urllib preserves an
    explicit `:443`/`:80` in the redirected URL. Stripping it keeps the
    signed-URL signature valid.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl.startswith("https://") and ":443/" in newurl:
            newurl = newurl.replace(":443/", "/", 1)
        elif newurl.startswith("http://") and ":80/" in newurl:
            newurl = newurl.replace(":80/", "/", 1)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_StripDefaultPortRedirect())


def _normalize_format(fmt):
    return (fmt or "").strip().lower().lstrip(".")


def _sanitize(name: str) -> str:
    return "".join("_" if c in _UNSAFE_FS_CHARS else c for c in name).strip() or "unnamed"


def _resolve_api_url(url: str) -> str:
    """Turn any CKAN dataset URL into the package_show API URL."""
    parsed = urlparse(url)
    if "/api/3/action/package_show" in parsed.path:
        return url
    parts = [p for p in parsed.path.split("/") if p]
    if "dataset" in parts:
        i = parts.index("dataset")
        if i + 1 < len(parts):
            slug = parts[i + 1]
            return urlunparse((
                parsed.scheme, parsed.netloc,
                "/api/3/action/package_show", "",
                urlencode({"id": slug}), "",
            ))
    raise ValueError(
        f"Cannot resolve CKAN API URL from {url!r}. Expected either "
        "`.../api/3/action/package_show?id=<slug>` or `.../dataset/<slug>`."
    )


def _dataset_slug(pkg: dict, fallback: str) -> str:
    return pkg.get("name") or fallback


def _pick_filename(resource: dict, idx: int) -> str:
    """Choose a safe local filename for a resource.

    Priority: `resource['name']` if it has a file extension -> URL basename if
    it has one -> `<resource-id-prefix>.<fmt>`.
    """
    name = (resource.get("name") or "").strip()
    fmt = _normalize_format(resource.get("format"))
    if name and Path(name).suffix:
        return _sanitize(name)
    url_name = Path(urlparse(resource.get("url", "")).path).name
    if url_name and Path(url_name).suffix:
        return _sanitize(url_name)
    stem = (resource.get("id") or f"resource_{idx}")[:16]
    return f"{_sanitize(stem)}.{fmt or 'bin'}"


def main(argv: list[str]) -> None:
    """Entry point. Never raises SystemExit (KernelShellBackend runs this
    in-process; a SystemExit would derail the agent)."""
    try:
        if len(argv) != 3:
            print("ERROR: usage: python fetch.py <ckan-dataset-url> <out-dir>")
            return

        ckan_url = argv[1]
        out_dir = Path(argv[2])
        out_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = out_dir / "_docs"

        api_url = _resolve_api_url(ckan_url)
        print(f"CKAN API URL : {api_url}")
        req = Request(api_url, headers={"User-Agent": "ckan-skill-builder/0.1"})
        with urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode("utf-8"))

        if not payload.get("success"):
            print(f"ERROR: CKAN API returned success=false: {payload!r}")
            return
        pkg = payload["result"]
        slug = _dataset_slug(pkg, fallback=out_dir.name)

        metadata = {
            "id":            pkg.get("id"),
            "name":          slug,
            "title":         pkg.get("title"),
            "notes":         pkg.get("notes"),
            "tags":          [t.get("name") for t in pkg.get("tags", []) if t.get("name")],
            "license_title": pkg.get("license_title"),
            "license_url":   pkg.get("license_url"),
            "organization":  (pkg.get("organization") or {}).get("title"),
            "source_url":    pkg.get("url") or ckan_url,
            "resources":     [],
        }

        resources = pkg.get("resources", [])
        print(f"CKAN dataset : {pkg.get('title')!r} ({slug})")
        print(f"\nDownloading {len(resources)} resource(s) to {out_dir} ...")

        entries: list[dict] = []
        for idx, res in enumerate(resources):
            url = res.get("url")
            fname = _pick_filename(res, idx)
            if not url:
                entries.append({"filename": fname, "class": "other",
                                "error": "resource has no url"})
                continue
            got = fc.process_resource(url, fname, out_dir, docs_dir,
                                      user_agent="ckan-skill-builder/0.1",
                                      opener=_OPENER)
            entries += got
            # Record CKAN's per-resource metadata (descriptions the downstream
            # SKILL.md writer wants), tagged with the class we assigned.
            top = got[0] if got else {}
            metadata["resources"].append({
                "id":          res.get("id"),
                "name":        res.get("name"),
                "description": res.get("description"),
                "url":         url,
                "format":      res.get("format"),
                "size":        res.get("size"),
                "local_file":  fname,
                "class":       top.get("class"),
            })

        skipped = [e for e in entries
                   if e["class"] == "other" or "error" in e]

        (out_dir / "_ckan_metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False))
        (out_dir / "_classification.json").write_text(
            json.dumps({"files": entries}, indent=2, default=str))
        (out_dir / "_skipped_resources.json").write_text(
            json.dumps({"skipped": skipped}, indent=2, ensure_ascii=False))

        fc.report_classification(entries, out_dir, docs_dir)

        n_docs = sum(1 for e in entries
                     if e["class"] == "docs" and "error" not in e)
        print(f"\nOut dir      : {out_dir}")
        print(f"Metadata     : {out_dir / '_ckan_metadata.json'}")
        print(f"Classification: {out_dir / '_classification.json'}")
        if n_docs:
            print(f"Docs         : {docs_dir}  ({n_docs} file(s))")
        if skipped:
            print(f"Skipped      : {out_dir / '_skipped_resources.json'} "
                  f"({len(skipped)} resource(s))")

    except ValueError as e:          # bad CKAN URL
        print(f"ERROR: {e}")
    except KeyboardInterrupt:
        print("ERROR: interrupted")
    except Exception as e:
        import traceback
        print(f"ERROR: unexpected {type(e).__name__}: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main(sys.argv)
