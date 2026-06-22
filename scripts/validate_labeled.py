#!/usr/bin/env python3
"""Validate the grader against the REAL labeled card data (not synthetic): does it score
known-damaged cards worse than clean ones? Caches per-image measurements to JSON so the
threshold analysis is instant on re-run. This is the ground-truth QA the grader needs to
be sellable — "validated against N real labeled cards", with numbers.

Populations (filenames/folders carry the labels):
  * clean fronts   — ~/Documents/1..30.jpg            (phone photos, mixed-clean)
  * surface-damage — ~/Documents/card data/front/surfacedmg/*  (label: surf)
  * clean backs    — ~/Documents/card data/back/back_0[0-5]* unlabeled  (clean class)
  * damaged backs  — ~/Documents/card data/back/*_{bad,rip,hp,lp,mp,white,faded}*

Run:  python3 scripts/validate_labeled.py            # uses cache if present
      python3 scripts/validate_labeled.py --rebuild  # re-measure all images
"""
import sys, os, glob, json, statistics as st
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import grading, engine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "debug" / "labeled_cache.json"
HOME = os.path.expanduser("~")
CARD = os.path.join(HOME, "Documents", "card data")
DMG_WORDS = ("bad", "rip", "_hp", "_lp", "_mp", "white", "faded", "holy", "bent", "crease")


def populations():
    clean_fronts = [os.path.join(HOME, "Documents", f"{i}.jpg") for i in range(1, 31)]
    surfdmg = sum((glob.glob(os.path.join(CARD, "front", "surfacedmg", e))
                   for e in ("*.jpg", "*.jpeg", "*.webp", "*.png")), [])
    backs = sum((glob.glob(os.path.join(CARD, "back", e))
                 for e in ("*.jpg", "*.jpeg", "*.webp", "*.png")), [])
    dmg_backs = [f for f in backs if any(w in os.path.basename(f).lower() for w in DMG_WORDS)]
    clean_backs = [f for f in backs if f not in dmg_backs]
    return {"clean_fronts": clean_fronts, "surface_damage": surfdmg,
            "clean_backs": clean_backs, "damaged_backs": dmg_backs}


def measure(path):
    if not os.path.exists(path):
        return None
    try:
        img = engine.decode_image(Path(path).read_bytes())
        if img is None:
            return None
        r = grading.grade_card(img)
        if not r.detected:
            return {"detected": False}
        return {"detected": True, "overall": r.overall,
                "surface": r.surface["grade"], "scuff": r.surface["scuff"],
                "corners": r.corners["grade"], "edges": r.edges["grade"],
                "centering": r.centering.get("grade"), "capture_ok": r.capture.get("ok"),
                # full dicts so _combine variants can be simulated offline (no re-measure)
                "d_centering": r.centering, "d_corners": r.corners,
                "d_edges": r.edges, "d_surface": r.surface}
    except Exception as e:
        return {"error": str(e)}


def build_cache(pops):
    data = {}
    for name, paths in pops.items():
        data[name] = {}
        for p in paths:
            m = measure(p)
            if m is not None:
                data[name][os.path.basename(p)] = m
        print(f"  measured {name}: {len(data[name])}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, indent=1))
    return data


def vals(group, key, only_detected=True):
    return [v[key] for v in group.values()
            if v.get("detected") and v.get(key) is not None] if only_detected else []


def best_split(clean, dmg, lo, hi, step):
    best = None
    t = lo
    while t <= hi:
        tnr = sum(1 for c in clean if c <= t) / len(clean)
        tpr = sum(1 for d in dmg if d > t) / len(dmg)
        bal = (tnr + tpr) / 2
        if best is None or bal > best["bal"]:
            best = {"thr": round(t, 3), "bal": round(bal, 3),
                    "clean_ok": round(tnr, 2), "dmg_ok": round(tpr, 2)}
        t += step
    return best


def report(data):
    print("\n=== SURFACE: clean fronts vs real surface-damage ===")
    cs = vals(data["clean_fronts"], "scuff")
    dsf = vals(data["surface_damage"], "scuff")
    cg = vals(data["clean_fronts"], "surface")
    dg = vals(data["surface_damage"], "surface")
    print(f"  clean  n={len(cs)}  scuff mean {st.mean(cs):.3f}  surface-grade mean {st.mean(cg):.1f}")
    print(f"  damage n={len(dsf)}  scuff mean {st.mean(dsf):.3f}  surface-grade mean {st.mean(dg):.1f}")
    print(f"  grade separation: {st.mean(cg) - st.mean(dg):+.1f} (damaged lower = good)")
    print(f"  optimal scuff split: {best_split(cs, dsf, 0.08, 0.40, 0.005)}")

    # OVERALL separation: cached overall (whatever _combine was when cached) vs the overall
    # recomputed from cached sub-grades with the CURRENT _combine — lets a _combine change be
    # A/B'd offline. Higher clean-overall and lower damaged-overall = better separation.
    print("\n=== OVERALL grade: clean fronts vs surface-damage ===")
    def overalls(group, recompute):
        out = []
        for v in group.values():
            if not v.get("detected"):
                continue
            if recompute and v.get("d_centering"):
                o = grading._combine(v["d_centering"], v["d_corners"], v["d_edges"], v["d_surface"])
            else:
                o = v.get("overall")
            if o is not None:
                out.append(o)
        return out
    for label, recompute in [("cached _combine", False), ("current _combine", True)]:
        co = overalls(data["clean_fronts"], recompute)
        do = overalls(data["surface_damage"], recompute)
        if co and do:
            print(f"  [{label:16}] clean mean {st.mean(co):.1f}  damaged mean {st.mean(do):.1f}"
                  f"  separation {st.mean(co)-st.mean(do):+.1f}")

    if data["damaged_backs"]:
        print("\n=== BACKS: clean vs labeled-damaged (corner/edge proxy) ===")
        for key in ("corners", "edges"):
            cb = vals(data["clean_backs"], key)
            db = vals(data["damaged_backs"], key)
            if cb and db:
                print(f"  {key}: clean mean {st.mean(cb):.1f} (n{len(cb)}) vs "
                      f"damaged mean {st.mean(db):.1f} (n{len(db)})  sep {st.mean(cb)-st.mean(db):+.1f}")


def main():
    pops = populations()
    if "--rebuild" in sys.argv or not CACHE.exists():
        print("measuring (slow, one pass)...")
        data = build_cache(pops)
    else:
        data = json.loads(CACHE.read_text())
        print(f"loaded cache: {CACHE}")
    report(data)


if __name__ == "__main__":
    main()
