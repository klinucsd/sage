#!/usr/bin/env python3
"""CKAN dataset fetcher for ckan-skill-builder.

Downloads tabular resources from a CKAN dataset into a local directory
that tabular-skill-builder's inventory.py can then treat as if it were
a cloned git repo.

Usage:
    python fetch.py <ckan-dataset-url> <out-dir>

<ckan-dataset-url> is either:
  - `.../api/3/action/package_show?id=<slug>` (API URL), or
  - `.../dataset/<slug>` (browse URL — the API URL is inferred).

Writes to <out-dir>/:
  - Downloaded tabular resources (CSV, TSV, XLSX, Parquet, GPKG,
    GeoJSON, JSON, RData/rda/rds, Shapefile ZIPs unpacked).
  - `_ckan_metadata.json` — dataset title, description, tags, license,
    organization, source URL, and per-downloaded-resource metadata.
  - `_skipped_resources.json` — resources not downloaded, with the
    reason (unsupported format, download error). Recorded so the agent
    can surface them to the user instead of silently dropping.

Uses only the Python standard library — no pip install required.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse, urlencode, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

# Case-insensitive allowlist. CKAN's `format` field varies wildly across
# portals ("CSV", "csv", ".csv", "Comma Separated Values"), so we
# normalize aggressively before matching.
TABULAR_FORMATS = {
    "csv", "tsv", "txt",              # delimited text
    "xlsx", "xls",                    # Excel
    "parquet",                        # columnar
    "gpkg", "geopackage",             # spatial single-file
    "geojson",                        # spatial vector
    "json",                           # sometimes tabular (rejected downstream if not)
    "shp", "shapefile",               # typically ships inside a ZIP
    "rdata", "rda", "rds",            # R-serialized
    "zip",                            # unpacked, contents re-classified
}

_UNSAFE_FS_CHARS = set('/\\:*?"<>|')


class _StripDefaultPortRedirect(HTTPRedirectHandler):
    """Rewrite redirect Locations that carry a redundant default port.

    CKAN portals (data.ca.gov / data.cnra.ca.gov / others) 302-redirect
    resource-download URLs to AWS S3 presigned URLs, and the Location
    header often includes an explicit `:443` on the HTTPS URL. AWS SigV4
    signs `Host: s3.amazonaws.com` WITHOUT the port for standard HTTPS,
    but urllib preserves the port from the URL — it then sends
    `Host: s3.amazonaws.com:443` and S3 rejects the request with
    `SignatureDoesNotMatch` (HTTP 403). Curl doesn't hit this because it
    normalises the URL and drops default ports; urllib does not.

    Stripping `:443` from HTTPS redirect targets makes urllib send the
    canonical Host header the signature was computed against.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl.startswith("https://") and ":443/" in newurl:
            newurl = newurl.replace(":443/", "/", 1)
        elif newurl.startswith("http://") and ":80/" in newurl:
            newurl = newurl.replace(":80/", "/", 1)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_StripDefaultPortRedirect())


def _normalize_format(fmt: str | None) -> str:
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
    """Prefer CKAN's own `name` field; fall back to a caller-provided slug."""
    return pkg.get("name") or fallback


def _pick_filename(resource: dict, idx: int) -> str:
    """Choose a safe local filename for a resource.

    Priority: `resource['name']` if it has a file extension → URL
    basename if it has one → `<resource-id-prefix>.<fmt>`.
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


def _download(url: str, dest: Path, *, timeout: int = 120) -> None:
    req = Request(url, headers={"User-Agent": "ckan-skill-builder/0.1"})
    # Use the port-stripping opener so S3-backed CKAN portals don't 403.
    with _OPENER.open(req, timeout=timeout) as r, dest.open("wb") as f:
        while True:
            chunk = r.read(1 << 15)  # 32 KiB
            if not chunk:
                break
            f.write(chunk)


def _unpack_zip(zpath: Path, out_dir: Path) -> list[Path]:
    """Extract a ZIP into `out_dir`, skipping path-traversal entries."""
    extracted: list[Path] = []
    with zipfile.ZipFile(zpath) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            mp = Path(member.filename)
            if mp.is_absolute() or ".." in mp.parts:
                continue
            dest = out_dir / mp
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, dest.open("wb") as dst:
                dst.write(src.read())
            extracted.append(dest)
    return extracted


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    ckan_url = argv[1]
    out_dir = Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    api_url = _resolve_api_url(ckan_url)
    print(f"CKAN API URL: {api_url}")
    req = Request(api_url, headers={"User-Agent": "ckan-skill-builder/0.1"})
    with urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode("utf-8"))

    if not payload.get("success"):
        print(f"CKAN API returned success=false: {payload!r}")
        return 1
    pkg = payload["result"]
    slug = _dataset_slug(pkg, fallback=out_dir.name)

    metadata: dict = {
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
    skipped: list[dict] = []

    for idx, res in enumerate(pkg.get("resources", [])):
        fmt = _normalize_format(res.get("format"))
        if fmt not in TABULAR_FORMATS:
            skipped.append({
                "id":     res.get("id"),
                "name":   res.get("name"),
                "url":    res.get("url"),
                "format": res.get("format"),
                "reason": f"unsupported format: {res.get('format')!r}",
            })
            continue

        fname = _pick_filename(res, idx)
        dest = out_dir / fname
        try:
            print(f"downloading [{fmt}] {res.get('name') or fname} -> {fname}")
            _download(res["url"], dest)
        except Exception as e:
            skipped.append({
                "id":     res.get("id"),
                "name":   res.get("name"),
                "url":    res.get("url"),
                "format": res.get("format"),
                "reason": f"download error: {type(e).__name__}: {e}",
            })
            continue

        if fmt == "zip":
            unpack_dir = out_dir / f"_unpacked_{dest.stem}"
            unpack_dir.mkdir(parents=True, exist_ok=True)
            try:
                extracted = _unpack_zip(dest, unpack_dir)
                print(f"  unpacked {len(extracted)} file(s) -> {unpack_dir.name}/")
                dest.unlink()
            except zipfile.BadZipFile as e:
                skipped.append({
                    "id":     res.get("id"),
                    "name":   res.get("name"),
                    "url":    res.get("url"),
                    "format": res.get("format"),
                    "reason": f"bad zip: {e}",
                })
                continue

        metadata["resources"].append({
            "id":          res.get("id"),
            "name":        res.get("name"),
            "description": res.get("description"),
            "url":         res.get("url"),
            "format":      res.get("format"),
            "size":        res.get("size"),
            "local_file":  fname,
        })

    (out_dir / "_ckan_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False)
    )
    (out_dir / "_skipped_resources.json").write_text(
        json.dumps({"skipped": skipped}, indent=2, ensure_ascii=False)
    )

    n_downloaded = len(metadata["resources"])
    print()
    print(f"CKAN dataset : {metadata['title']!r}")
    print(f"Slug         : {slug}")
    print(f"Downloaded   : {n_downloaded} tabular resource(s)")
    print(f"Skipped      : {len(skipped)} resource(s)")
    print(f"Out dir      : {out_dir}")
    print(f"Metadata     : {out_dir}/_ckan_metadata.json")
    if skipped:
        print(f"Skipped list : {out_dir}/_skipped_resources.json")
    print()
    print("Next: hand off to tabular-skill-builder starting at its Step 2 —")
    print("      run its inventory.py on the out dir above.")

    if n_downloaded == 0:
        # Signal via stdout, not via a non-zero exit / SystemExit.
        # Some ARGUS backends run Python scripts in-process (KernelShellBackend),
        # where SystemExit propagates as an unhandled exception and derails the
        # agent — matching inventory.py's pattern is safer.
        print()
        print("NO tabular resources were downloaded — the caller should stop "
              "and tell the user (see _skipped_resources.json for reasons).")


if __name__ == "__main__":
    main(sys.argv)
