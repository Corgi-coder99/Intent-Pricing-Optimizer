# Bảo mật và nguồn dữ liệu ngoài

## Nguồn dữ liệu: Open Prices (Open Food Facts)
- Giấy phép ODbL. Khi công bố phải ghi: "Contains information from Open Food Facts contributors, available under the ODbL".
- Chỉ dùng API công khai, không cào trang web. Không dùng Amazon/Shopee/Lazada.
- Phát hiện khi thăm dò: API trả cả `owner` (tên người đóng góp), `owner_comment`, `proof` (ảnh biên lai). Connector KHÔNG lưu các trường này.
- API không lọc được theo quốc gia (tham số bị bỏ qua); chỉ lọc theo tiền tệ. Connector lọc quốc gia ở phía client.
  UK chỉ có ~2.000 bản ghi (GBP). Ireland không xuất hiện trong các trang đã quét.

## Connector (src/connectors/openprices.py)
HTTPS tới một host duy nhất · kiểm tra TLS · không theo chuyển hướng · không tải URL trong dữ liệu (chống SSRF) ·
giới hạn dung lượng phản hồi 5 MB, timeout 20 s, 60 trang/tiền tệ, 5.000 bản ghi, 1 request/giây, thử lại tối đa 3 lần ·
chỉ nhận JSON · danh sách trường cho phép · kiểm tra từng giá trị · làm sạch văn bản (ký tự điều khiển, công thức CSV, `|`) ·
User-Agent chung, không cookie/token/thông tin cá nhân · ghi file bằng tmp + rename.

## API (src/api.py)
`X-API-Key` so sánh hằng thời gian · `REQUIRE_API_KEY=1` (đặt sẵn trong Docker) làm API trả 503 nếu quên cấu hình khoá,
không bao giờ mở công khai · giới hạn 120 yêu cầu/phút/client · chặn dò khoá (10 lần sai/phút) · body tối đa 64 KB ·
header `nosniff`, `no-store`, `X-Frame-Options` · kiểm tra đầu vào bằng Pydantic · Docker chạy bằng user không phải root.

## Đã quét
Không có khoá/mật khẩu trong repo · `.env`, `data/`, `*.pkl` nằm ngoài git · không dùng eval/exec/pickle trực tiếp/shell ·
`pip-audit` trên requirements.txt: không có lỗ hổng đã biết (tại thời điểm quét).

## Rủi ro còn lại (cần biết)
- `joblib.load` dựa trên pickle: chỉ nạp file mô hình do chính pipeline tạo; ai ghi được vào `data/processed/` thì chạy được mã tuỳ ý.
  Giữ thư mục đó chỉ ghi được bởi tài khoản chạy dịch vụ.
- Giới hạn tốc độ nằm trong bộ nhớ của một tiến trình; sau reverse proxy `client.host` là IP của proxy. Cần HTTPS, WAF và
  giới hạn tốc độ ở proxy khi triển khai công khai.
- Dữ liệu cộng đồng (Open Prices) có thể sai hoặc bị phá hoại; kiểm tra hợp lệ chỉ bắt được giá trị bất thường rõ ràng.
- `pip-audit` chỉ biết lỗ hổng đã công bố; nên chạy lại định kỳ.
