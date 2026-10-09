import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_feed  # noqa: E402


@pytest.fixture(autouse=True)
def _dummy_spec_material(request, monkeypatch):
    """The real VENDOR_SPEC_CONFIG ships with empty material (must be filled by the owner; empty fails loudly).
    Existing pipeline tests only care about row counts, so give them dummy materials -- unless the test is
    marked real_spec_config."""
    if request.node.get_closest_marker("real_spec_config"):
        return
    cfg = deepcopy(build_feed.VENDOR_SPEC_CONFIG)
    for vendor_cfg in cfg.values():
        if vendor_cfg.get("material_pending"):
            continue
        vendor_cfg["material"] = {loc: f"TEST-MATERIAL-{loc}" for loc in ("en", "de", "fr", "nl")}
    monkeypatch.setattr(build_feed, "VENDOR_SPEC_CONFIG", cfg)
