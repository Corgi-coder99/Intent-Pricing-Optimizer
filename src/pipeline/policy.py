"""Resource-limit and retention policy (protects against disk/RAM overload and unbounded data growth)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    # --- retention
    bronze_retention_days: int = 90            # processed bronze files past this age are deleted (silver + history remain)
    bronze_max_bytes: int = 2 * 1024 ** 3      # over budget: delete the oldest processed files first
    quarantine_retention_days: int = 30
    history_retention_days: int = 365
    # --- overload protection
    max_file_bytes: int = 500 * 1024 ** 2      # larger files are rejected, not copied
    max_files_per_run: int = 200               # the rest wait for the next run (backpressure)
    max_quarantine_rows_per_batch: int = 100_000   # a junk file must not fill the disk
    min_free_bytes: int = 1024 ** 3            # refuse to run when free disk is below this
    chunk_rows: int = 200_000                  # read files in chunks so RAM does not grow
    landing_settle_seconds: float = 0.5        # skip files that are still being written


DEFAULT_POLICY = Policy()
