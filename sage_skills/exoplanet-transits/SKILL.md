---
name: exoplanet-transits
description: "Exoplanet transit data from NASA. Use when the user wants to: fetch transiting exoplanet metadata from the NASA Exoplanet Archive; download TESS or Kepler photometry from NASA MAST; phase-fold a light curve to reveal a transit; analyze transit depth, duration, or radius ratio; produce a transit summary plot. Pure data skill — no UI. If the user wants an interactive picker, the agent should chain this with the sage-dropdown skill."
---

# exoplanet-transits — Data Skill

This skill provides **data only** — fetching from NASA APIs, processing photometry,
and computing transit parameters. It contains no dropdowns, widgets, or display
logic. When the user asks for a picker, chain with **sage-dropdown** (a separate
generic UI skill) and let the agent decide kernel-variable names.

## Required libraries

`lightkurve` and `astropy` are **not** pre-installed in the Sage image. Install
once at the top of the first script in a session (no-op if already installed):

```python
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "lightkurve", "astropy"])
```

---

## Execution rules — read before writing any code

- Save every script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc (`python << 'EOF'`). Never chain commands with `&&`.
- Never call `plt.show()` in scripts. Save to a PNG with `plt.savefig(...)` and print the path. Do NOT call `display(Image(path))` — Sage renders images once when your chat reply contains `![](path)`.
- Read existing kernel variables via `globals().get("VAR_NAME")`. The system prompt's `EXISTING KERNEL VARIABLES` block tells you what's already set by previous cells.

---

## Step 1 — Fetch the transiting-exoplanet catalog

Pure data step. Produces one kernel variable:

| Variable  | Type            | Contents |
|-----------|-----------------|----------|
| `planets` | list of dicts   | Each dict has the NASA Exoplanet Archive fields below |

Each item has these keys: `pl_name`, `hostname`, `pl_orbper` (orbital period, days),
`pl_trandep` (transit depth, fraction), `pl_rade` (planet radius, R⊕),
`pl_bmasse` (planet mass, M⊕), `sy_vmag` (host star V magnitude),
`st_teff` (host star Teff, K), `st_rad` (host star radius, R☉).

Items are sorted alphabetically by `pl_name`.

```python
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "lightkurve", "astropy"])

import requests

# Fallback list of well-known bright transiting exoplanets, used if the NASA
# Exoplanet Archive API is slow / unreachable. Each dict has the same keys as
# the live API.
_FALLBACK_PLANETS = [
    {"pl_name": "HD 209458 b",  "hostname": "HD 209458",  "pl_orbper": 3.52474,  "pl_trandep": 0.01478, "pl_rade": 15.59, "pl_bmasse": 219.5, "sy_vmag": 7.65,  "st_teff": 6117.0, "st_rad": 1.20},
    {"pl_name": "HAT-P-11 b",   "hostname": "HAT-P-11",   "pl_orbper": 4.88781,  "pl_trandep": 0.00330, "pl_rade":  4.36, "pl_bmasse":  26.0, "sy_vmag": 9.47,  "st_teff": 4780.0, "st_rad": 0.75},
    {"pl_name": "WASP-18 b",    "hostname": "WASP-18",    "pl_orbper": 0.94145,  "pl_trandep": 0.00960, "pl_rade": 13.40, "pl_bmasse": 3260.0,"sy_vmag": 9.29,  "st_teff": 6400.0, "st_rad": 1.24},
    {"pl_name": "WASP-76 b",    "hostname": "WASP-76",    "pl_orbper": 1.80988,  "pl_trandep": 0.01200, "pl_rade": 21.40, "pl_bmasse": 617.0, "sy_vmag": 9.52,  "st_teff": 6250.0, "st_rad": 1.76},
    {"pl_name": "WASP-121 b",   "hostname": "WASP-121",   "pl_orbper": 1.27492,  "pl_trandep": 0.01580, "pl_rade": 19.54, "pl_bmasse": 557.0, "sy_vmag": 10.44, "st_teff": 6460.0, "st_rad": 1.46},
    {"pl_name": "HAT-P-7 b",    "hostname": "HAT-P-7",    "pl_orbper": 2.20474,  "pl_trandep": 0.00640, "pl_rade": 15.97, "pl_bmasse": 510.0, "sy_vmag": 10.46, "st_teff": 6350.0, "st_rad": 1.84},
    {"pl_name": "GJ 436 b",     "hostname": "GJ 436",     "pl_orbper": 2.64390,  "pl_trandep": 0.00690, "pl_rade":  4.22, "pl_bmasse":  22.4, "sy_vmag": 10.61, "st_teff": 3416.0, "st_rad": 0.46},
    {"pl_name": "WASP-12 b",    "hostname": "WASP-12",    "pl_orbper": 1.09142,  "pl_trandep": 0.01450, "pl_rade": 19.40, "pl_bmasse": 447.0, "sy_vmag": 11.69, "st_teff": 6300.0, "st_rad": 1.60},
    {"pl_name": "WASP-39 b",    "hostname": "WASP-39",    "pl_orbper": 4.05528,  "pl_trandep": 0.02170, "pl_rade": 14.17, "pl_bmasse":  90.0, "sy_vmag": 12.09, "st_teff": 5400.0, "st_rad": 0.90},
    {"pl_name": "WASP-6 b",     "hostname": "WASP-6",     "pl_orbper": 3.36101,  "pl_trandep": 0.01650, "pl_rade": 13.39, "pl_bmasse": 171.0, "sy_vmag": 11.90, "st_teff": 5450.0, "st_rad": 0.87},
    {"pl_name": "WASP-17 b",    "hostname": "WASP-17",    "pl_orbper": 3.73548,  "pl_trandep": 0.01850, "pl_rade": 21.44, "pl_bmasse": 159.0, "sy_vmag": 11.60, "st_teff": 6650.0, "st_rad": 1.58},
    {"pl_name": "GJ 3470 b",    "hostname": "GJ 3470",    "pl_orbper": 3.33665,  "pl_trandep": 0.00580, "pl_rade":  4.75, "pl_bmasse":  14.0, "sy_vmag": 12.27, "st_teff": 3600.0, "st_rad": 0.48},
]

planets = []
print("Fetching transiting exoplanets from NASA Exoplanet Archive...")
try:
    url = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
    query = (
        "SELECT pl_name, hostname, pl_orbper, pl_trandep, pl_rade, pl_bmasse, "
        "sy_vmag, st_teff, st_rad, ra, dec "
        "FROM pscomppars "
        "WHERE tran_flag=1 AND sy_vmag < 12.5 AND pl_orbper < 15 "
        "AND pl_orbper IS NOT NULL AND pl_rade IS NOT NULL AND sy_vmag IS NOT NULL "
        "ORDER BY pl_name ASC"
    )
    resp = requests.get(url, params={"query": query, "format": "json"}, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    planets = [p for p in rows if p.get("pl_orbper") and p.get("sy_vmag")]
    print(f"Got {len(planets)} planets from NASA Exoplanet Archive.")
except Exception as e:
    print(f"NASA Exoplanet Archive unavailable ({e}). Using built-in fallback list.")
    planets = list(_FALLBACK_PLANETS)

planets = sorted(planets, key=lambda p: p["pl_name"])
print(f"Catalog ready: {len(planets)} entries (sorted by planet name).")
```

After this script runs, `planets` is in the kernel namespace. The agent does NOT
need to display anything; if the user wants a picker, see "Composing with
sage-dropdown" below.

---

## Composing with sage-dropdown

When the user asks for an interactive selector ("let me pick", "show me a
dropdown", "select one"), in the SAME script that produces `planets`, append a
call to `sage-dropdown`. Read `sage-dropdown`'s SKILL.md for full API details.

**The agent decides the kernel variable names.** Recommended names that the
downstream steps below expect:

| Variable              | Source field   | Description |
|-----------------------|----------------|-------------|
| `TARGET_PLANET`       | `pl_name`      | User-selected planet name |
| `TARGET_STAR`         | `hostname`     | User-selected host star name |
| `ORBITAL_PERIOD_DAYS` | `pl_orbper`    | Orbital period in days |
| `PLANET_DATA`         | `@self`        | Full record dict for the selected planet |

If you pick different names, Steps 2–4 below need to be updated to read those
names instead.

---

## Step 2 — Download a TESS / Kepler light curve

Reads from kernel namespace: `TARGET_STAR`, `TARGET_PLANET` (for plot title),
`ORBITAL_PERIOD_DAYS` (only the existence of `TARGET_STAR` is required to run).

Writes to kernel namespace: `lc` (lightkurve LightCurve), `lc_mission` (str).

```python
import lightkurve as lk
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import warnings, os
warnings.filterwarnings("ignore")

star   = globals().get("TARGET_STAR")
planet = globals().get("TARGET_PLANET", star)

if not star:
    raise RuntimeError("TARGET_STAR not set — run Step 1 / select a planet first")

outdir = globals().get("SAGE_OUTPUT_DIR", "/tmp")
os.makedirs(outdir, exist_ok=True)

print(f"Searching NASA MAST for {star} light curves...")
result  = lk.search_lightcurve(star, mission="TESS", exptime="short")
mission = "TESS"
if len(result) == 0:
    result, mission = lk.search_lightcurve(star, mission="Kepler", exptime="short"), "Kepler"
if len(result) == 0:
    result, mission = lk.search_lightcurve(star), "any"

print(f"Found {len(result)} sector(s) — mission: {mission}")
if len(result) == 0:
    raise RuntimeError(f"No light curves found for {star} on NASA MAST")

n_dl = min(3, len(result))
lc_coll = result[:n_dl].download_all()

from lightkurve import LightCurveCollection
lc = lc_coll.stitch() if isinstance(lc_coll, LightCurveCollection) else lc_coll
lc = lc.normalize().remove_outliers(sigma=5)
print(f"Light curve: {len(lc):,} points spanning "
      f"{lc.time.value[-1] - lc.time.value[0]:.1f} days")

csv_path = os.path.join(outdir, "lc_raw.csv")
lc.to_pandas().to_csv(csv_path, index=False)
print(f"Saved raw light curve → {csv_path}")

globals()["lc"]         = lc
globals()["lc_mission"] = mission

fig, ax = plt.subplots(figsize=(12, 3))
ax.plot(lc.time.value, lc.flux.value, "k.", ms=0.8, alpha=0.35, rasterized=True)
ax.set_xlabel("Time (BTJD days)")
ax.set_ylabel("Normalized Flux")
ax.set_title(f"{planet} — Raw {mission} Light Curve ({len(lc):,} points)")
ax.set_ylim(
    float(lc.flux.value.mean()) - 5 * float(lc.flux.value.std()),
    float(lc.flux.value.mean()) + 5 * float(lc.flux.value.std()),
)
plt.tight_layout()
plot_path = os.path.join(outdir, "lc_raw.png")
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"Saved plot → {plot_path}")
```

---

## Step 3 — Phase-fold the light curve

Reads: `lc`, `ORBITAL_PERIOD_DAYS`. Writes: `TRANSIT_DEPTH`, `TRANSIT_DURATION_DAYS`, `RP_RS`.

**Critical rules:**
- Read `lc` from `globals()` — never re-read `lc_raw.csv`.
- Read `ORBITAL_PERIOD_DAYS` from `globals()` — never hardcode the period.
- Use `lc.fold(period=period)` from lightkurve — do not re-implement phase folding.

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import os, warnings
warnings.filterwarnings("ignore")

lc     = globals().get("lc")
period = globals().get("ORBITAL_PERIOD_DAYS")
planet = globals().get("TARGET_PLANET", "unknown planet")

if lc is None:
    raise RuntimeError("lc not found — run Step 2 first")
if period is None:
    raise RuntimeError("ORBITAL_PERIOD_DAYS not set — select a planet first")

outdir = globals().get("SAGE_OUTPUT_DIR", "/tmp")
os.makedirs(outdir, exist_ok=True)

print(f"Phase-folding {planet} at P = {period:.4f} days...")

folded = lc.fold(period=period)
binned = folded.bin(time_bin_size=0.004)
flux_arr = np.array(binned.flux.value, dtype=float)
time_arr = np.array(binned.time.value, dtype=float)

depth    = float(1.0 - np.nanmin(flux_arr))
floor    = 1.0 - 0.5 * depth
in_tr    = flux_arr < floor
duration = float(np.sum(in_tr) * 0.004 * period) if np.any(in_tr) else float("nan")
rp_rs    = float(np.sqrt(max(depth, 0.0)))

print(f"Transit depth:     {depth*100:.4f}%")
print(f"Transit duration:  {duration*24:.2f} h")
print(f"Rp/Rs estimate:    {rp_rs:.4f}")

globals()["TRANSIT_DEPTH"]         = depth
globals()["TRANSIT_DURATION_DAYS"] = duration
globals()["RP_RS"]                 = rp_rs

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 4))
ax1.plot(np.array(folded.time.value), np.array(folded.flux.value),
         ".", ms=1.2, alpha=0.25, color="steelblue")
ax1.plot(time_arr, flux_arr, "r-", lw=1.8, label="Binned (0.4% phase)")
ax1.set_xlabel("Phase (days from transit center)")
ax1.set_ylabel("Normalized Flux")
ax1.set_title(f"{planet} — Phase-Folded Light Curve")
ax1.legend(fontsize=8)

zoom_half = max(3 * duration, 0.05 * period) if not np.isnan(duration) else 0.1 * period
mask = np.abs(time_arr) < zoom_half
if np.sum(mask) > 4:
    ax2.plot(time_arr[mask] * 24, flux_arr[mask], "r.-", lw=1.5, ms=4)
    ax2.set_xlabel("Hours from transit center")
    ax2.set_ylabel("Normalized Flux")
    ax2.set_title(f"Transit Zoom — depth={depth*100:.3f}%, ~{duration*24:.1f} h")
    ax2.axhline(1.0,         color="gray",   ls="--", lw=0.8)
    ax2.axhline(1.0 - depth, color="orange", ls="--", lw=1.0,
                label=f"Depth = {depth*100:.3f}%")
    ax2.legend(fontsize=8)
else:
    ax2.text(0.5, 0.5, "Insufficient data\nfor zoom",
             ha="center", va="center", transform=ax2.transAxes)

plt.tight_layout()
plot_path = os.path.join(outdir, "lc_folded.png")
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"Saved plot → {plot_path}")
```

---

## Step 4 — Transit summary card

Reads: `lc`, `ORBITAL_PERIOD_DAYS`, `TRANSIT_DEPTH`, `TRANSIT_DURATION_DAYS`, `RP_RS`, `PLANET_DATA`.

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import os, warnings
warnings.filterwarnings("ignore")

planet   = globals().get("TARGET_PLANET",       "unknown")
star     = globals().get("TARGET_STAR",         "unknown")
period   = globals().get("ORBITAL_PERIOD_DAYS", float("nan"))
depth    = globals().get("TRANSIT_DEPTH",       0.0)
duration = globals().get("TRANSIT_DURATION_DAYS", float("nan"))
rp_rs    = globals().get("RP_RS",               float("nan"))
pd_data  = globals().get("PLANET_DATA",         {})
lc       = globals().get("lc")

if lc is None:
    raise RuntimeError("lc not found — run Steps 2 and 3 first")

outdir = globals().get("SAGE_OUTPUT_DIR", "/tmp")
os.makedirs(outdir, exist_ok=True)

folded = lc.fold(period=period)
binned = folded.bin(time_bin_size=0.003)
b_time = np.array(binned.time.value, dtype=float)
b_flux = np.array(binned.flux.value, dtype=float)

fig = plt.figure(figsize=(14, 5))
gs  = gridspec.GridSpec(1, 2, width_ratios=[1.8, 1.0], wspace=0.38)

ax = fig.add_subplot(gs[0])
ax.plot(np.array(folded.time.value) * 24, np.array(folded.flux.value),
        ".", ms=1.2, alpha=0.2, color="steelblue")
ax.plot(b_time * 24, b_flux, "r-", lw=2.0, zorder=5, label="Binned LC")
xlim_h = (4 * duration * 24) if not np.isnan(duration) else (0.08 * period * 24)
ax.set_xlim(-xlim_h, xlim_h)
ax.set_xlabel("Hours from Transit Center", fontsize=12)
ax.set_ylabel("Normalized Flux", fontsize=12)
ax.set_title(f"{planet} — Transit Light Curve", fontsize=13, fontweight="bold")
ax.axhline(1.0, color="gray", ls="--", lw=0.8)
if depth > 0:
    ax.axhline(1.0 - depth, color="orange", ls="--", lw=1.2,
               label=f"Depth = {depth*100:.3f}%")
ax.legend(fontsize=9)

ax2 = fig.add_subplot(gs[1])
ax2.axis("off")
vmag_str  = f"{pd_data['sy_vmag']:.1f}"   if pd_data.get("sy_vmag") else "?"
teff_str  = f"{pd_data['st_teff']:.0f} K" if pd_data.get("st_teff") else "?"
rad_str   = f"{pd_data['pl_rade']:.2f} R⊕" if pd_data.get("pl_rade") else "?"
dur_str   = f"{duration*24:.2f} h" if not np.isnan(duration) else "?"
rp_str    = f"{rp_rs:.4f}"          if not np.isnan(rp_rs)    else "?"
rows = [
    ("Planet",        planet),
    ("Host Star",     f"{star}  (V={vmag_str})"),
    ("Star T_eff",    teff_str),
    ("Orbital Period",f"{period:.4f} days"),
    ("Transit Depth", f"{depth*100:.3f}%"),
    ("Duration (est.)", dur_str),
    ("Rp/Rs (est.)",  rp_str),
    ("Rp (archive)",  rad_str),
    ("Data source",   globals().get("lc_mission", "TESS/Kepler")),
]
ax2.set_title("Transit Parameters", fontsize=12, fontweight="bold", pad=10)
for i, (k, v) in enumerate(rows):
    y = 0.92 - i * 0.10
    ax2.text(0.03, y, f"{k}:", fontsize=10, fontweight="bold",
             transform=ax2.transAxes, va="top")
    ax2.text(0.44, y, str(v), fontsize=10, transform=ax2.transAxes, va="top")

plt.suptitle("Exoplanet Transit Explorer — Sage", fontsize=9, color="#888", y=0.01)
plot_path = os.path.join(outdir, "transit_summary.png")
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"Summary saved → {plot_path}")
```

---

## Notes

- First-run install of `lightkurve` and `astropy` is ~60s; cached afterwards.
- TESS short-cadence downloads can be 50–200 MB per sector. The script caps at 3 sectors.
- The NASA Exoplanet Archive TAP API requires no API key.
- For data sources with no live API, this skill ships a built-in fallback list of 12 well-known transiting exoplanets so the catalog is always available.
