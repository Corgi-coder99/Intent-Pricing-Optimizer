"""Chính sách giới hạn tài nguyên và lưu giữ dữ liệu (chống quá tải đĩa/RAM, chống dữ liệu phình vô hạn)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    # --- lưu giữ
    bronze_retention_days: int = 90            # file bronze đã xử lý xong và quá hạn thì bị xoá (silver + history vẫn còn)
    bronze_max_bytes: int = 2 * 1024 ** 3      # vượt ngân sách thì xoá file đã xử lý cũ nhất trước
    quarantine_retention_days: int = 30
    history_retention_days: int = 365
    # --- chống quá tải
    max_file_bytes: int = 500 * 1024 ** 2      # file lớn hơn bị từ chối, không sao chép
    max_files_per_run: int = 200               # phần còn lại để lượt sau (backpressure)
    max_quarantine_rows_per_batch: int = 100_000   # file rác không được lấp đầy đĩa
    min_free_bytes: int = 1024 ** 3            # đĩa trống dưới ngưỡng này thì từ chối chạy
    chunk_rows: int = 200_000                  # đọc file theo khối để RAM không phình
    landing_settle_seconds: float = 0.5        # bỏ qua file đang được ghi dở


DEFAULT_POLICY = Policy()
