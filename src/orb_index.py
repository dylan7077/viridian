"""Global ORB matcher — identifies a card against EVERY indexed card at once.

All cards' ORB descriptors are pooled into a single FLANN-LSH index. A scan's
descriptors are matched against the whole pool; each good match votes for its
owning card, and the card with the most votes wins. No pHash shortlist gate, so
the right card is found no matter how a perceptual hash would have ranked it —
which is what makes identification reliable for arbitrary phone photos.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

import config

_DB_PATH = config.DATA_DIR / "orb_db.npz"
_WIDTH = 480

# Confidence gating — only present a match when we're genuinely sure; otherwise
# escalate the feature count and, failing that, return "not confident" (no guess).
_FEATURE_STEPS = (600,)        # single solid pass; gate rejects if not confident
_MIN_VOTES = 18                # absolute vote floor for the winner
_MIN_LEAD = 10                 # winner must beat runner-up by this many votes
_MIN_RATIO = 2.0               # winner votes >= this * runner-up votes
# Vote count at which the absolute-evidence factor saturates. Set to the vote floor
# so that, past _MIN_VOTES, confidence reflects how decisively the winner beats the
# runner-up (the dominance) rather than raw count. Old high-detail cards score
# hundreds of votes; modern simple-art cards win clearly with ~18-25 — both must
# read as confident. _MIN_VOTES still guards against too little evidence.
_SCORE_FULL = 18
_MIN_CONF = 0.82               # overall confidence gate (0..1) — now ≈ dominance margin


class OrbIndex:
    def __init__(self):
        self.ok = False
        self.cards = []
        self._mtime = None
        # 500 query features keeps matching decisive while staying fast against
        # the large (~20k-card) descriptor pool.
        self.orb = cv2.ORB_create(nfeatures=500)
        self._load()

    def _load(self) -> None:
        """(Re)load the descriptor DB. We keep the descriptors grouped by card and
        build a *small* FLANN index per query over a pHash shortlist, instead of one
        giant index over all 20k cards — the giant scan is memory-bandwidth-bound and
        runs ~50s on a cloud VM. The shortlist scan is ~150 cards → sub-second, and on
        the same machine. (No eager global index: saves ~280MB RAM + the build time.)"""
        if not _DB_PATH.exists():
            self.ok = False
            return
        try:
            d = np.load(_DB_PATH, allow_pickle=True)
            self.desc = d["desc"]
            self.owner = d["owner"]
            self.cards = list(d["cards"])
            self._id_to_idx = {c.get("id"): i for i, c in enumerate(self.cards)}
            # rows[card_index] = the descriptor row indices belonging to that card.
            order = np.argsort(self.owner, kind="stable")
            sowner = self.owner[order]
            bnd = np.searchsorted(sowner, np.arange(len(self.cards) + 1))
            self._rows = {i: order[bnd[i]:bnd[i + 1]]
                          for i in range(len(self.cards)) if bnd[i + 1] > bnd[i]}
            self._gflann = None          # lazily built only if the global query() is used
            self._mtime = _DB_PATH.stat().st_mtime
            self.ok = True
        except Exception:
            self.ok = False

    def maybe_reload(self) -> None:
        """Hot-reload if the DB file changed on disk (rebuilt in the background)."""
        try:
            mtime = _DB_PATH.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self._load()

    def __len__(self):
        return len(self.cards)

    def _features(self, card_bgr: np.ndarray, nf: int):
        g = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2GRAY)
        s = _WIDTH / g.shape[1]
        if s < 1:
            g = cv2.resize(g, None, fx=s, fy=s)
        return cv2.ORB_create(nfeatures=nf).detectAndCompute(g, None)[1]

    @staticmethod
    def _new_flann() -> "cv2.FlannBasedMatcher":
        return cv2.FlannBasedMatcher(
            dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1),
            dict(checks=50))

    def _vote(self, qd, flann, owner, ratio: float = 0.8) -> dict:
        """Each good query feature votes for the card it best matches. `flann` is the
        (sub)index and `owner` maps each trained descriptor row to a card index."""
        if qd is None or len(qd) < 2:
            return {}
        qd = np.ascontiguousarray(qd.astype(np.uint8))
        try:
            matches = flann.knnMatch(qd, k=2)
        except Exception:
            return {}
        votes: dict[int, int] = {}
        for pr in matches:
            if len(pr) == 2 and pr[0].distance < ratio * pr[1].distance:
                ci = int(owner[pr[0].trainIdx])
                votes[ci] = votes.get(ci, 0) + 1
        return votes

    def _result(self, votes: dict, method: str) -> Optional[dict]:
        """Rank votes and apply the confidence gate (shared by both query paths)."""
        if not votes:
            return None
        ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        bi, score = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        lead = score - runner
        margin = score / (score + runner) if (score + runner) else 0.0
        confidence = round(min(0.99, margin * min(1.0, score / _SCORE_FULL)), 2)
        confident = (score >= _MIN_VOTES and lead >= _MIN_LEAD
                     and score >= runner * _MIN_RATIO and confidence >= _MIN_CONF)
        return {"card": self.cards[bi], "orb_score": int(score),
                "runner_up": int(runner), "confidence": confidence,
                "method": method, "confident": bool(confident)}

    def query_shortlist(self, card_bgr: np.ndarray, card_ids) -> Optional[dict]:
        """Fast identify: ORB-match the photo against ONLY the given candidate cards
        (a pHash shortlist). Builds a tiny FLANN over their descriptors — sub-second
        even on a cloud VM — and uses the same voting + confidence gate as the global
        path, so accuracy is preserved (and votes are usually cleaner)."""
        self.maybe_reload()
        if not self.ok or not card_ids:
            return None
        idxs = [self._id_to_idx[c] for c in card_ids
                if c in self._id_to_idx and self._id_to_idx[c] in self._rows]
        if not idxs:
            return None
        sub = np.vstack([self.desc[self._rows[i]] for i in idxs]).astype(np.uint8)
        sub_owner = np.concatenate([np.full(len(self._rows[i]), i) for i in idxs])
        flann = self._new_flann()
        flann.add([sub])
        flann.train()
        qd = self._features(card_bgr, _FEATURE_STEPS[0])
        if qd is None or len(qd) < 8:
            return None
        return self._result(self._vote(qd, flann, sub_owner), "orb")

    def query(self, card_bgr: np.ndarray) -> Optional[dict]:
        """Global identify against EVERY card (slow on cloud VMs — kept as a fallback).
        Builds the big FLANN lazily on first use."""
        self.maybe_reload()
        if not self.ok:
            return None
        if self._gflann is None:
            self._gflann = self._new_flann()
            self._gflann.add([self.desc])
            self._gflann.train()
        best = None
        for nf in _FEATURE_STEPS:
            qd = self._features(card_bgr, nf)
            if qd is None or len(qd) < 8:
                continue
            best = self._result(self._vote(qd, self._gflann, self.owner), "orb_global")
            if best and best["confident"]:
                break
        return best
