# Bảo mật, nhiều người dùng và nguồn dữ liệu ngoài

## Nguồn dữ liệu: Open Prices (Open Food Facts)
- Giấy phép ODbL. Khi công bố phải ghi: "Contains information from Open Food Facts contributors, available under the ODbL".
- Chỉ dùng API công khai, không cào trang web. Không dùng Amazon/Shopee/Lazada.
- API trả cả `owner` (tên người đóng góp), `owner_comment`, `proof` (ảnh biên lai). Connector KHÔNG lưu các trường này.
- **Vì sao trước đây thiếu dữ liệu một số nước (Ireland, Đức):** API bỏ qua bộ lọc quốc gia (cả ở `/prices` lẫn `/locations`),
  chỉ lọc được theo tiền tệ hoặc `location_id`. Quét theo tiền tệ EUR chỉ thấy các bản ghi mới nhất, do Pháp/Đức áp đảo.
  Cách xử lý: quét danh sách địa điểm (~8.000), lọc quốc gia ở phía client, rồi tải giá theo từng `location_id` với hạn mức
  mỗi quốc gia và mỗi địa điểm (`--mode by-location`, mặc định). UK chỉ ~2.000 bản ghi trong toàn bộ nguồn.
- Quốc gia được phép: EU27 + Vương quốc Anh. Tiền tệ: EUR, GBP, SEK, DKK, PLN, CZK, HUF, RON, BGN.

## Connector (src/connectors/openprices.py)
HTTPS tới một host duy nhất, chỉ 2 đường dẫn (`/prices`, `/locations`) · kiểm tra TLS · không theo chuyển hướng · không tải URL
trong dữ liệu (chống SSRF) · giới hạn 5 MB/phản hồi, timeout 20 s, 1 request/giây, thử lại tối đa 3 lần · chỉ JSON ·
danh sách trường cho phép · kiểm tra từng giá trị · làm sạch văn bản (ký tự điều khiển, công thức CSV, `|`) ·
User-Agent chung, không cookie/token/thông tin cá nhân · ghi file bằng tmp + rename.

## Mô hình (đã bỏ pickle)
Mô hình lưu ở `data/processed/lgbm_pricing.txt` bằng định dạng văn bản gốc của LightGBM. Nạp file này không thể chạy mã tuỳ ý
(khác `joblib/pickle`). Mỗi mô hình có `model_version` (SHA-256 của file) trả về ở `/health` và mọi phản hồi dự đoán.
File `lgbm_pricing.pkl` cũ không còn được nạp và có thể xoá.

## Nhiều người dùng, nhiều máy
- **Mỗi người một khoá riêng:** `python -m src.auth create --name alice --role user` (khoá hiện một lần; chỉ lưu băm SHA-256).
  Vai trò `user` gọi dự đoán; `admin` thêm `/v1/admin/reload`. Vô hiệu hoá: `python -m src.auth disable --id <id>`.
  Khoá `API_KEY` trong môi trường là khoá quản trị chung (nên chỉ dùng cho vận hành).
- **Kho khoá đã từng có khoá thì không bao giờ quay về chế độ mở**, kể cả khi mọi khoá bị vô hiệu hoá.
- **Không va chạm:** các lệnh dự đoán không ghi trạng thái dùng chung (chỉ đọc mô hình), nên nhiều người gọi cùng lúc không đè nhau.
  Giới hạn tốc độ tính RIÊNG cho từng khoá (120/phút) nên một người không làm nghẽn người khác; giới hạn theo IP đặt cao (600/phút)
  vì cả văn phòng thường chung một IP.
- **Cùng kết quả:** cùng `model_version` + cùng đầu vào cho đúng cùng kết quả trên mọi máy. Kết quả chỉ khác khi các máy dùng
  mô hình khác nhau (mỗi máy tự huấn luyện trên dữ liệu khác nhau). Hãy dùng MỘT máy chủ API chung hoặc phân phối cùng một file mô hình.
- **Nhật ký kiểm toán:** `data/state/audit.log` (xoay vòng 5 MB x 3) ghi thời điểm, request_id, IP, khoá nào, đường dẫn, mã trạng thái;
  KHÔNG ghi nội dung yêu cầu. Mỗi phản hồi có header `X-Request-ID` để truy vết.
- **Reverse proxy:** chỉ khi đặt `TRUST_PROXY=1` mới tin `X-Forwarded-For` (lấy phần tử cuối do proxy thêm, phải là IP hợp lệ);
  mặc định header này bị bỏ qua nên giả mạo không né được giới hạn.
- **Chạy pipeline từ nhiều máy trên cùng thư mục chia sẻ mạng KHÔNG được hỗ trợ:** khoá chạy dựa trên tạo file nguyên tử của hệ
  điều hành, không đáng tin trên một số ổ chia sẻ. Mỗi máy nên có `data/` riêng, hoặc chỉ một máy chạy pipeline.

## API
So sánh khoá hằng thời gian · `REQUIRE_API_KEY=1` (đặt sẵn trong Docker) → 503 nếu chưa cấu hình khoá · chặn dò khoá (10 lần sai/phút/IP) ·
body tối đa 64 KB · header `nosniff`, `no-store`, `X-Frame-Options` · Pydantic kiểm tra đầu vào · Docker chạy user không phải root.

## Dữ liệu cộng đồng bị phá hoại hoặc sai
Bảng `competitor_prices` dùng thống kê bền vững: điểm lệch quá 5 lần MAD bị loại khỏi min/median/max và được đếm ở `n_outliers`;
mỗi nhóm có `confidence` (`ok` khi có ≥2 cửa hàng và ≥3 quan sát, ngược lại `low`). Phương pháp này chặn các giá trị cực đoan rõ ràng,
không chống được kẻ phá hoại tinh vi đặt giá gần thực tế.

## Đã quét
Không có khoá/mật khẩu trong repo · `.env`, `data/` ngoài git · không eval/exec/pickle/shell · `pip-audit` sạch tại thời điểm quét
và chạy lại hằng tuần qua `.github/workflows/security.yml` (chỉ chạy khi repo được đẩy lên GitHub).

## Rủi ro còn lại (không sửa được bằng mã)
- HTTPS, WAF và giới hạn tốc độ cho nhiều bản sao dịch vụ phải làm ở hạ tầng (reverse proxy như nginx/Caddy, API gateway).
  Bộ đếm giới hạn tốc độ nằm trong bộ nhớ từng tiến trình, không chia sẻ giữa các bản sao.
- Khoá API chỉ an toàn khi truyền qua HTTPS; qua HTTP thường chúng bị lộ trên đường truyền.
- Ai đọc được `data/state/api_keys.json` chỉ thấy băm, nhưng ai ghi được vào đó thì tự tạo được khoá: giữ quyền ghi hạn chế.
- Dữ liệu Open Prices do cộng đồng đóng góp, không có bảo đảm về độ đúng.
