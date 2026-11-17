"""RIS trends: roughness / rutting averages, PCI and condition categories -> ris_trends_dw (in memory) + DB ids -> ris_trends_data (new surveys appended)."""
from ris_kpi.ris_trends.pipeline import run_ris_trends

__all__ = ["run_ris_trends"]
