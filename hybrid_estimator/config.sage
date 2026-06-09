import sys
import os
from sage.all import *

# ============================================================
# External dependencies and local project files
# ============================================================

LATTICE_ESTIMATOR_PATH = "/home/inca/lattice-estimator"

if LATTICE_ESTIMATOR_PATH not in sys.path:
    sys.path.append(LATTICE_ESTIMATOR_PATH)

from estimator.reduction import RC
import estimator.simulator as Simulator

from batchCVP import batchCVPP_cost

# ------------------------------------------------------------
# Load local helper files
# ------------------------------------------------------------
# 你的当前目录下有 proba_utils.sage 和 utils.sage，
# 所以这里直接加载当前目录下的文件。
#
# 如果后面你又恢复了 framework/instance_gen.sage 这种结构，
# 再把这里改成 load("framework/xxx.sage") 即可。
# ------------------------------------------------------------

if os.path.exists("proba_utils.sage"):
    load("proba_utils.sage")
else:
    raise FileNotFoundError("Cannot find proba_utils.sage in the project root.")

if os.path.exists("utils.sage"):
    load("utils.sage")

sys.stdout.flush()