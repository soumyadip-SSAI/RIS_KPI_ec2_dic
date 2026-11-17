"""RIS view: silver status update -> affected-length intermediate -> ris_view_dw (in memory) + DB ids -> ris_view_data."""
from ris_kpi.ris_view.pipeline import STEPS, run_ris_view

__all__ = ["STEPS", "run_ris_view"]
