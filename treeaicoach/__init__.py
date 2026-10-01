"""TreeAI Coach — assistant vocal anti-gank pour League of Legends (lecture d'écran uniquement)."""

import os as _os

# numpy's BLAS (OpenBLAS / MKL) must stay single-threaded: the detection runs many SMALL
# matrix products per frame (ring colours, identifier, patch verifier) and a multi-threaded
# BLAS spins its idle worker threads between them. Measured (tools/det_gym.py, 4 cores):
# 157 ms of CPU per detected frame with the default threads vs 61 ms with one (wall time
# 40 -> 36 ms): ~1 core stolen from the game for nothing. Set before numpy is imported;
# an explicit user setting wins.
for _k in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_k, "1")
del _k

__version__ = "2.1.0"
APP_NAME = "TreeAI Coach"
