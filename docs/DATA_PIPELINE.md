# Data pipeline: bronze -> silver -> gold

```
landing/ (nguồn thả vào, không sửa)
   -> bronze/<loại>/<hash>_<tên>   bản sao bất biến, phân loại theo schema, trùng hash bị bỏ qua
   -> silver/<loại>.parquet        ép kiểu + kiểm tra + khử trùng theo khoá; dòng sai -> quarantine/<loại>/
   -> gold/*.parquet               sessions_features, region_summary, category_summary, competitor_prices
```
Chạy: `python -m src.pipeline run [--simulate N] [--wait SEC]`, xem `python -m src.pipeline status`.

## Loại dữ liệu (src/pipeline/contracts.py)
| Loại | Nhận diện bằng cột bắt buộc | Khoá khử trùng |
|---|---|---|
| session | session_id, product_id, category, region, giá, hành vi, review, converted | session_id |
| price_observation | product_code, price, currency, country, observed_at | product_code+store+country+city+observed_at+price |
| review | review_id, product_id, review_text | review_id |
File không khớp hợp đồng nào -> `bronze/_unclassified`, không vào silver. Thêm nguồn mới = thêm một Contract.

## Chống loạn dữ liệu
- Bronze đặt tên theo SHA-256: cùng nội dung (kể cả đổi tên file) chỉ được nhận một lần.
- Silver upsert theo khoá (giữ bản mới nhất), nên nạp chồng lô không tạo dòng trùng.
- Dòng sai kiểu/ngoài khoảng hợp lệ bị tách vào quarantine kèm `reject_reason`, không bị xoá âm thầm.
- Mọi file ghi bằng tmp + `os.replace` (nguyên tử); đọc song song không bao giờ thấy file dở.

## Chống vòng lặp và xung đột khi chạy song song
- DAG được kiểm tra bằng Kahn trước khi đụng dữ liệu; có vòng hoặc phụ thuộc thiếu -> `CycleError`.
- Mỗi bước chạy tối đa một lần mỗi lượt; bước lỗi không tự thử lại, các bước phụ thuộc bị bỏ qua.
- Các bước độc lập (3 silver, gold) chạy song song bằng thread pool.
- Khoá toàn pipeline (`state/pipeline.lock`, tạo bằng O_EXCL): chạy chồng -> chờ (`--wait`) hoặc thoát mã 2.
  Khoá cũ hơn 2 giờ được thu hồi; chỉ chủ khoá mới nhả được khoá.
- Gold bỏ qua khi fingerprint đầu vào không đổi.

## Timeout từng bước (đã cưỡng chế)
Mỗi bước chạy trong một tiến trình con; quá hạn thì bị kill, các bước phụ thuộc bị bỏ qua, khoá được nhả.
File ghi nguyên tử nên bị kill giữa chừng không làm hỏng dữ liệu; file `.tmp` mồ côi được dọn ở lượt sau.
Khoá có heartbeat 5 giây, TTL 600 giây chỉ để thu hồi khoá của tiến trình đã chết.

Đo thật trên 50.000 dòng: mọi bước < 2 giây. Timeout đề xuất (khoảng 50-100x thời gian đo):
| Bước | Timeout |
|---|---|
| generate_simulated, bronze_ingest, gold_summaries, gold_competitor_prices | 120 s |
| silver_* | 300 s |
| gold_sessions (offline) | 300 s |
| gold_sessions (LLM) | 1800 s |
Dữ liệu lớn gấp 20 lần vẫn nằm trong hạn. Chỉnh trong `build_nodes()` (src/pipeline/orchestrator.py).
Chi phí: mỗi tiến trình con khởi động ~2 giây trên Windows; chạy cả pipeline ~10 giây thay vì ~2,5 giây.
Cần nhanh hơn thì `--no-isolate` (mất cưỡng chế timeout).

## Dữ liệu cũ KHÔNG bị xoá
- Bronze bất biến, chỉ thêm. Silver upsert theo khoá và chỉ xử lý file mới (tăng dần), không làm lại từ đầu.
- Dòng bị cập nhật: bản cũ được lưu ở `silver/history/<loại>.parquet` kèm `superseded_at`, `superseded_by_batch`.
- Review đã phân tích được cache bền (`state/review_signal_cache.json`); đổi từ khoá/mô hình thì cache tự vô hiệu.
- Huấn luyện lại chỉ khi gold hoặc mã mô hình đổi.
- Việc phải làm lại mỗi lần dữ liệu đổi: tính lại bảng gold (giây) và huấn luyện lại (giây). Bronze tăng dần, chưa có chính sách xoá file cũ.

## Xoá dữ liệu (tombstone) và dọn bronze
- Nguồn muốn xoá bản ghi: thả file CSV có cột `record_type` (session | price_observation | review) và `record_key`
  (các cột khoá nối bằng `|`; số dạng `%g`, ngày `YYYY-MM-DD`; ví dụ `1|A|FR|Paris|2026-01-01|1.5`).
  Silver xoá dòng khớp và lưu bản bị xoá vào `silver/history/` với `history_reason=deleted`. Áp dụng theo thứ tự nạp.
- Bronze: file đã xử lý xong sẽ bị xoá sau `bronze_retention_days` (90) hoặc khi vượt `bronze_max_bytes` (2 GB, xoá cũ nhất
  trước). Manifest vẫn giữ hash nên nạp lại cùng nội dung không bị tính hai lần. File CHƯA xử lý không bao giờ bị xoá.
- Quarantine giữ 30 ngày, history giữ 365 ngày (đều chỉnh được ở `src/pipeline/policy.py`).

## Chống quá tải (policy.py)
| Giới hạn | Mặc định | Khi vượt |
|---|---|---|
| Dung lượng 1 file | 500 MB | từ chối, ghi nhận một lần, không sao chép |
| File mới mỗi lượt | 200 | phần còn lại để lượt sau |
| Đọc theo khối | 200.000 dòng | RAM không phình theo file |
| Dòng quarantine mỗi lô | 100.000 | cắt bớt, chỉ số `quarantine_truncated` |
| Đĩa trống tối thiểu | 1 GB | từ chối chạy (`InsufficientResources`) |
| File đang ghi dở | 0,5 giây ổn định | hoãn sang lượt sau |
Mẹo cho nguồn ghi file lớn: ghi vào `*.tmp` rồi đổi tên sang `.csv`.

## Giới hạn còn lại
- Silver và gold dùng pandas trong RAM; trần thực tế vài triệu dòng. Lớn hơn cần chuyển sang DuckDB/Spark.
- Thứ tự áp dụng theo thứ tự nạp; trong cùng một lượt, file được xếp theo tên.
- Chưa có timeout theo từng chunk bên trong một bước (chỉ timeout cả bước).
- Simulation mới (nội dung khác) cộng dồn vào silver theo session_id; muốn làm lại từ đầu hãy xoá `data/`.
