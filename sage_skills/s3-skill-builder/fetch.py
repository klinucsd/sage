#!/usr/bin/env python3
"""S3 prefix fetcher for s3-skill-builder.

Downloads every tabular object under an anonymous-readable S3 prefix
into a local directory that tabular-skill-builder's inventory.py can
then treat as if it were a cloned git repo.

Usage:
    python fetch.py <s3-url> <out-dir>

<s3-url> is either:
  - `s3://<bucket>/<prefix>`, or
  - `https://<bucket>.s3.amazonaws.com/<prefix>` (path style with
    embedded region also accepted).

Writes to <out-dir>/:
  - Downloaded tabular objects, laid out under the prefix's parent
    directory so the natural key hierarchy is preserved
    (e.g. `s3://bucket/foo/year=2020/data.csv` under prefix
    `foo/` lands at `<out-dir>/year=2020/data.csv`).
  - `_s3_metadata.json` — bucket, prefix, source URL, region,
    object count / total bytes, and a per-downloaded-object list
    (key, size, last-modified).
  - `_skipped_objects.json` — objects not downloaded, with reason
    (unsupported format, download error).

Uses only the Python standard library — no boto3 or pip install
required. Anonymous public buckets only; if you need to reach an
authenticated bucket, upgrade this fetcher later to shell out to
`aws s3` (which handles SigV4).
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree as ET

# Case-insensitive tabular format allowlist — mirrors ckan-skill-builder.
TABULAR_FORMATS = {
    "csv", "tsv", "txt",
    "xlsx", "xls",
    "parquet",
    "gpkg", "geopackage",
    "geojson",
    "json",
    "shp", "shapefile",
    "rdata", "rda", "rds",
    "zip",
    "gz",  # single-file gzip (e.g. foo.csv.gz) — gunzipped after download
}

# Extensions we recognise as tabular when nested inside a .gz.
_GZ_INNER_TABULAR = {
    "csv", "tsv", "txt",
    "json", "geojson",
    "parquet",
}

S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


class _StripDefaultPortRedirect(HTTPRedirectHandler):
    """Same defensive redirect handler as ckan-skill-builder's fetch.py.

    Public S3 GETs don't hit the SigV4 :443 problem (they're not
    presigned), but S3 does 301/307 for cross-region requests, and
    we don't want a redirect Location to preserve any junk port
    that would break the request.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl.startswith("https://") and ":443/" in newurl:
            newurl = newurl.replace(":443/", "/", 1)
        elif newurl.startswith("http://") and ":80/" in newurl:
            newurl = newurl.replace(":80/", "/", 1)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_StripDefaultPortRedirect())
_USER_AGENT = "s3-skill-builder/0.1"


def _normalize_ext(name: str) -> str:
    """Return the last extension of `name` (without the dot), lower-cased."""
    ext = Path(name).suffix.lower().lstrip(".")
    return ext


def _parse_s3_url(url: str) -> tuple[str, str]:
    """Turn a user-supplied URL into (bucket, prefix).

    Prefix is returned WITHOUT a leading slash and MAY be empty.
    A trailing slash is preserved when the user included one — that
    signals "this is a directory prefix" and disambiguates from
    single-object keys.
    """
    if url.startswith("s3://"):
        rest = url[5:]
        parts = rest.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        return bucket, prefix

    parsed = urlparse(url)
    host = parsed.hostname or ""
    m = re.match(r"^(?P<bucket>[a-z0-9.\-]+)\.s3([.\-][a-z0-9\-]+)?\.amazonaws\.com$", host)
    if m:
        bucket = m.group("bucket")
        prefix = parsed.path.lstrip("/")
        return bucket, prefix

    m = re.match(r"^s3([.\-][a-z0-9\-]+)?\.amazonaws\.com$", host)
    if m:
        path = parsed.path.lstrip("/")
        parts = path.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        return bucket, prefix

    raise ValueError(
        f"Cannot parse S3 URL {url!r}. Expected s3://<bucket>/<prefix> or "
        "https://<bucket>.s3.amazonaws.com/<prefix>."
    )


def _rel_root_of(prefix: str) -> str:
    """The 'logical root' inside the prefix — everything up to and
    including the last `/`. Object keys are stripped by this to
    produce local relative paths.

    - prefix `csv/by_year/17` → root `csv/by_year/` → key
      `csv/by_year/1750.csv` becomes `1750.csv` locally.
    - prefix `records/csv.gz/` → root `records/csv.gz/` → key
      `records/csv.gz/locationid=1/year=2006/f.csv.gz` becomes
      `locationid=1/year=2006/f.csv.gz` locally.
    - prefix `` (whole bucket) → root `` → whole key preserved.
    """
    if "/" in prefix:
        return prefix.rsplit("/", 1)[0] + "/"
    return ""


def _list_objects(bucket: str, prefix: str) -> list[dict]:
    """Anonymous ListObjectsV2 with pagination.

    Returns a list of dicts: {'key': str, 'size': int, 'last_modified': str}.
    Raises on any HTTP error other than 200.
    """
    endpoint = f"https://{bucket}.s3.amazonaws.com/"
    token: str | None = None
    out: list[dict] = []
    while True:
        params = ["list-type=2", f"prefix={quote(prefix, safe='/=')}", "max-keys=1000"]
        if token:
            params.append(f"continuation-token={quote(token, safe='')}")
        url = endpoint + "?" + "&".join(params)
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with _OPENER.open(req, timeout=60) as r:
            body = r.read()
        root = ET.fromstring(body)
        for c in root.findall("s3:Contents", S3_NS):
            key = c.findtext("s3:Key", default="", namespaces=S3_NS)
            size = int(c.findtext("s3:Size", default="0", namespaces=S3_NS))
            lm = c.findtext("s3:LastModified", default="", namespaces=S3_NS)
            out.append({"key": key, "size": size, "last_modified": lm})
        truncated = (root.findtext("s3:IsTruncated", default="false", namespaces=S3_NS) == "true")
        if not truncated:
            break
        token = root.findtext("s3:NextContinuationToken", default=None, namespaces=S3_NS)
        if not token:
            break
    return out


def _detect_region_via_head(bucket: str) -> str | None:
    """Best-effort region detection from the bucket's response headers."""
    endpoint = f"https://{bucket}.s3.amazonaws.com/"
    req = Request(endpoint, headers={"User-Agent": _USER_AGENT})
    try:
        with _OPENER.open(req, timeout=15) as r:
            return r.headers.get("x-amz-bucket-region")
    except Exception:
        return None


def _download_object(bucket: str, key: str, dest: Path, *, timeout: int = 300) -> None:
    """Stream a single object to `dest`. Follows region redirects."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://{bucket}.s3.amazonaws.com/{quote(key, safe='/')}"
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with _OPENER.open(req, timeout=timeout) as r, dest.open("wb") as f:
        while True:
            chunk = r.read(1 << 15)  # 32 KiB
            if not chunk:
                break
            f.write(chunk)


def _gunzip_in_place(path: Path) -> Path:
    """Gunzip `path` (foo.csv.gz) to sibling `foo.csv`. Deletes the .gz."""
    if not path.name.lower().endswith(".gz"):
        return path
    out = path.with_suffix("")  # strip trailing .gz
    with gzip.open(path, "rb") as src, out.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return out


def _unpack_zip(zpath: Path, out_dir: Path) -> list[Path]:
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


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        print(__doc__)
        return

    s3_url = argv[1]
    out_dir = Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    bucket, prefix = _parse_s3_url(s3_url)
    rel_root = _rel_root_of(prefix)

    if not prefix:
        print(f"REFUSING to fetch an entire bucket without a prefix: {s3_url!r}")
        print("Supply s3://<bucket>/<some/prefix/> to scope the download.")
        return

    print(f"S3 bucket   : {bucket}")
    print(f"Prefix      : {prefix}")
    region = _detect_region_via_head(bucket)
    if region:
        print(f"Region      : {region}")

    objects = _list_objects(bucket, prefix)
    if not objects:
        print(f"No objects matched prefix {prefix!r}.")

    metadata: dict = {
        "bucket":      bucket,
        "prefix":      prefix,
        "source_url":  s3_url,
        "region":      region,
        "object_count_matched": len(objects),
        "objects":     [],  # populated below with downloaded objects
    }
    skipped: list[dict] = []
    n_downloaded = 0
    total_bytes = 0

    for obj in objects:
        key = obj["key"]
        # Ignore objects that ARE the prefix directory itself (0-byte
        # marker entries some tools create).
        if key.endswith("/"):
            continue

        base = Path(key).name
        # Classify by extension. For `.gz`, inspect the inner extension
        # to decide whether it's tabular after gunzip.
        ext = _normalize_ext(base)
        if ext == "gz":
            inner_ext = _normalize_ext(base[:-3])
            if inner_ext not in _GZ_INNER_TABULAR:
                skipped.append({**obj, "reason": f"unsupported gz inner format: {inner_ext!r}"})
                continue
        elif ext not in TABULAR_FORMATS:
            skipped.append({**obj, "reason": f"unsupported format: {ext!r}"})
            continue

        # Local path: strip the "logical root" of the prefix so the
        # remaining key structure is preserved but not the entire
        # bucket-root chain.
        if rel_root and key.startswith(rel_root):
            rel = key[len(rel_root):]
        else:
            rel = key
        # Defensive: refuse absolute / path-traversal-y keys.
        if rel.startswith("/") or ".." in Path(rel).parts:
            skipped.append({**obj, "reason": "unsafe key path"})
            continue

        dest = out_dir / rel
        try:
            print(f"downloading [{ext}] s3://{bucket}/{key} -> {rel}")
            _download_object(bucket, key, dest)
        except Exception as e:
            skipped.append({**obj, "reason": f"download error: {type(e).__name__}: {e}"})
            continue

        # Post-download unpacking / decompression.
        if ext == "zip":
            unpack_dir = out_dir / f"_unpacked_{dest.stem}"
            unpack_dir.mkdir(parents=True, exist_ok=True)
            try:
                extracted = _unpack_zip(dest, unpack_dir)
                print(f"  unpacked {len(extracted)} file(s) -> {unpack_dir.name}/")
                dest.unlink()
            except zipfile.BadZipFile as e:
                skipped.append({**obj, "reason": f"bad zip: {e}"})
                continue
        elif ext == "gz":
            try:
                out = _gunzip_in_place(dest)
                print(f"  gunzipped -> {out.relative_to(out_dir)}")
                dest = out
            except Exception as e:
                skipped.append({**obj, "reason": f"gunzip error: {type(e).__name__}: {e}"})
                continue

        metadata["objects"].append({
            "key":           key,
            "size":          obj["size"],
            "last_modified": obj["last_modified"],
            "local_file":    str(dest.relative_to(out_dir)),
        })
        n_downloaded += 1
        total_bytes += obj["size"]

    metadata["object_count_downloaded"] = n_downloaded
    metadata["total_bytes_downloaded"] = total_bytes

    (out_dir / "_s3_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str)
    )
    (out_dir / "_skipped_objects.json").write_text(
        json.dumps({"skipped": skipped}, indent=2, ensure_ascii=False, default=str)
    )

    print()
    print(f"S3 bucket    : {bucket}")
    print(f"Prefix       : {prefix}")
    if region:
        print(f"Region       : {region}")
    print(f"Matched      : {len(objects)} object(s)")
    print(f"Downloaded   : {n_downloaded} tabular object(s) ({total_bytes/1_048_576:.1f} MiB)")
    print(f"Skipped      : {len(skipped)} object(s)")
    print(f"Out dir      : {out_dir}")
    print(f"Metadata     : {out_dir}/_s3_metadata.json")
    if skipped:
        print(f"Skipped list : {out_dir}/_skipped_objects.json")

    if n_downloaded == 0:
        print()
        print("NO tabular objects were downloaded — the caller should stop "
              "and tell the user (see _skipped_objects.json for reasons).")
        return

    print()
    print("Next: hand off to tabular-skill-builder starting at its Step 2 —")
    print("      run its inventory.py on the out dir above.")


if __name__ == "__main__":
    main(sys.argv)
