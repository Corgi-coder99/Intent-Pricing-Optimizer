# Nguồn dữ liệu và mức độ tin cậy

| Dữ liệu | Nguồn | Trạng thái |
|---|---|---|
| Giá bán lẻ theo cửa hàng, quốc gia (8 nước EU/UK) | Open Prices (Open Food Facts), giấy phép ODbL, tải qua API | Thật, do cộng đồng đóng góp, chưa đảm bảo độ đúng. Tải dở (hết trần yêu cầu), Bỉ chưa đủ |
| Tỷ giá EUR/GBP... | ECB (data-api.ecb.europa.eu), đọc trực tiếp 2026-11-17 | Thật. 1 GBP = 1,2004 EUR |
| Cơ cấu chi phí của một sàn TMĐT | Zalando SE, báo cáo kết quả hợp nhất năm 2024 (https://corporate.zalando.com/en/node/10738), đọc trực tiếp 2026-11-17 | Thật, đã kiểm chứng ở nguồn gốc |
| Biên gộp ngành bán lẻ theo nước (7 nước EU + Anh) | Eurostat, bộ `sbs_na_dt_r2`, chỉ số V12110 và V12130, lấy qua API chính thức ngày 2026-11-17 (`src/connectors/eurostat_retail.py`) | Thật, số của CẢ NGÀNH theo nước, năm 2019 (Anh: 2018). Biên gộp % do mã tính |
| VAT theo nước | `src/pipeline/reference/vat_rates.csv` | **Chưa kiểm chứng** (ghi từ trí nhớ, cần đối chiếu nguồn chính thức) |
| Giá vốn từng chi nhánh | Chưa có (dữ liệu nội bộ công ty, không công khai) | Thiếu |

## Biên gộp ngành bán lẻ theo nước (Eurostat), nhóm siêu thị/thực phẩm G47.11, doanh thu chưa gồm VAT
Pháp 21,02% · Đức 23,41% · Tây Ban Nha 23,94% · Ý 22,67% · Hà Lan 27,33% · Bỉ 37,74% · Ireland 34,68% · Anh 31,81% (năm 2018).
- Là biên gộp của cả ngành ở mỗi nước (doanh thu trừ chi phí mua hàng bán lại), **không phải** giá vốn của công ty hay sản phẩm nào.
- Năm 2020 bị loại (đại dịch làm lệch); Anh chỉ có đến 2018 vì Eurostat ngừng thu thập sau Brexit, nên Anh khác năm với các nước còn lại.
- Số liệu của một số nước dao động mạnh giữa các năm (ví dụ Ireland nhóm bán lẻ chung: 20,3% năm 2018, 37,0% năm 2019), nên đừng
  đọc chênh lệch nhỏ giữa các nước như khác biệt thật.
- Dùng làm điểm neo của kịch bản (cột `scenario_anchor`) và làm mốc so sánh (cột `sector_gross_margin_pct`).

## Số của Zalando dùng thế nào, và không dùng được vào đâu
Năm 2024 (triệu EUR): doanh thu 10.572,5; giá vốn 6.270,5; lợi nhuận gộp 4.302,0; chi phí fulfilment 2.418,4; chi phí marketing 979,2.
Tỷ lệ tính từ các số đó: biên gộp 40,69%; fulfilment 22,87%; marketing 9,26%; còn lại sau hai khoản chi phí khoảng 8,55% doanh thu.

- Đây là số tổng hợp của MỘT công ty thời trang. Nó **không** cho biết giá vốn từng sản phẩm, từng nước, và không đại diện cho
  thực phẩm (nhóm hàng chính của dữ liệu giá thị trường).
- Số Zalando giờ chỉ là phương án dự phòng cho điểm neo khi thiếu số Eurostat. Cách dùng trong dự án là **kịch bản giả định** (`scenario_margin_pct` trong bảng `market_margin`): giả sử mọi chi nhánh
  mua cùng một giá vốn và giá vốn đó cho biên 40,69% khi bán ở giá thị trường Pháp, thì biên sẽ là bao nhiêu nếu bán ở giá thị
  trường nước khác. Kết quả là biên **giả định**, không phải lợi nhuận thật của công ty nào.
- Khi có giá vốn thật (`branch_cost`), cột `margin_at_market_pct` và `own_margin_pct` dùng số thật và thay thế kịch bản.

## Đã tìm nhưng KHÔNG dùng
- **Amazon, phân khúc International (kết quả 2025):** các con số chỉ lấy được từ trang tổng hợp bên thứ ba; trang gốc thông cáo
  trả lỗi 403 khi đọc nên chưa kiểm chứng. Không đưa vào dữ liệu.
- **Phí bán hàng của Amazon ở châu Âu:** không lấy được bảng phí chính thức (chỉ có bài đăng diễn đàn người bán và blog bên thứ ba,
  chủ yếu cho thị trường Mỹ). Không đưa vào dữ liệu. Muốn dùng, hãy lấy bảng phí từ Seller Central của từng nước.
- Số liệu của một công ty bán trên sàn: chưa tìm thấy nguồn công khai đáng tin có giá vốn theo sản phẩm hoặc theo nước.

## Ghi nguồn khi công bố
"Contains information from Open Food Facts contributors, available under the ODbL." Số Zalando: Zalando SE, báo cáo 2024. Số ngành: Eurostat (sbs_na_dt_r2).
