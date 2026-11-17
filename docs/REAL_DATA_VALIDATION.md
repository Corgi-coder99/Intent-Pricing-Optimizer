# Kiểm chứng mô hình bằng dữ liệu thật, và đánh giá nguồn mới

Tạo bằng `python -m src.real_elasticity` (số liệu ngày 2026-11-17). Kết quả đầy đủ: `data/reports/real_elasticity.json` (không đưa vào git).

## 1. Độ nhạy giá: mô phỏng so với giao dịch thật
Nguồn thật: Online Retail II (Chen, D. 2012, UCI, DOI 10.24432/C5CG6D, **CC BY 4.0**), giao dịch ẩn danh của một nhà bán lẻ trực tuyến
ở Anh, 12/2009 đến 12/2011, đọc (chỉ đọc) từ bảng `clean_lines` của dự án online-retail-insights. Mức quan sát: sản phẩm x tuần.
Chỉ giữ đơn bán (loại trả hàng, mã không phải sản phẩm, giá <= 0), sản phẩm có ≥30 tuần, giá biến động (CV ≥ 5%, ≥3 mức giá),
tuần có ≥2 hoá đơn, và loại giá cực đoan so với trung vị.

| Đối tượng | Độ co giãn của cầu theo giá | Số sản phẩm | Dùng được? |
|---|---|---|---|
| Toàn bộ dữ liệu | **−2,27** (95%: −2,34 đến −2,19) | 1.709 | Có |
| Anh | −2,20 (95%: −2,28 đến −2,13) | 1.640 | Có |
| EU ngoài Anh | −8,47 | **46** | **Không**: quá ít sản phẩm, đơn sỉ rời rạc |
| Mô phỏng, trung bình theo doanh thu | **−2,44** | 50.000 phiên | (đối chiếu) |
| Mô phỏng, trung bình / trung vị | −3,02 / −1,71 | 50.000 phiên | (đối chiếu) |

**Kết luận:** mức nhạy giá TỔNG THỂ của mô phỏng (−2,4) nằm sát giá trị quan sát thật (−2,3), nên không chỉnh lại dữ liệu mô phỏng.
Mình kiểm tra bộ ước lượng trước trên dữ liệu tổng hợp có độ co giãn biết trước (tìm lại đúng −1,5 và −0,4; có test).

**Chưa được kiểm chứng, vẫn là giả định:** sự khác biệt theo loại khiếu nại trong mô phỏng (khách chê đắt −6,0; không phàn nàn −1,3;
lỗi chất lượng −3,1; vận chuyển −2,4). Dữ liệu thật ở trên không có review nên không kiểm tra được phần này.

**Giới hạn của kiểm chứng:** (a) đây là độ co giãn QUAN SÁT, không phải nhân quả (khuyến mãi trùng thời điểm nhu cầu cao, giá sỉ theo bậc
số lượng); (b) sản phẩm là quà tặng/đồ gia dụng bán sỉ-lẻ ở Anh, không phải thực phẩm hay điện tử; (c) cầu theo số lượng khác cầu theo xác suất
chuyển đổi (hai đại lượng chỉ so sánh được ở mức độ lớn).

## 2. Đánh giá nguồn mới (quy trình: giấy phép, truy cập, độ phủ, độ tin cậy, rủi ro)
| Nguồn | Quyết định | Lý do |
|---|---|---|
| Online Retail II (UCI) | **Dùng** (kiểm chứng Tầng 2) | CC BY 4.0, ẩn danh, đã có sẵn, có cột quốc gia |
| Open Prices, ECB, Eurostat | **Dùng** (đã nạp trước đó) | Mở, truy cập qua API chính thức |
| Amazon Reviews Multi (có tiếng Pháp/Đức) | **Loại** | Đã ngừng phát hành; giấy phép chỉ cho nghiên cứu phi thương mại và cấm cả khi nhận phí (kể cả thực tập có lương) |
| Các bản sao review gắn Apache/CC0/CC BY | **Chưa dùng** | Nhãn trên bản sao không thay đổi điều khoản của dữ liệu gốc (Amazon); không xác minh được giấy phép gốc |
| Allociné (đánh giá phim, tiếng Pháp) | **Loại** | Không phải thương mại điện tử |
| Webis-CLS-10 (review sách/DVD/nhạc) | **Loại** | Giấy phép không rõ, không phải nhóm hàng của dự án |
| Women's E-Commerce Clothing Reviews (Kaggle, CC0 theo trang Kaggle) | **Ứng viên, chưa dùng** | Cần tài khoản Kaggle để tải; giấy phép chỉ xác nhận qua trang tổng hợp. Nếu bạn tự tải về, có thể thả vào `data/landing/` sau khi đổi tên cột theo hợp đồng `review` |
| Amazon (phí bán, kết quả phân khúc International) | **Chưa dùng** | Không kiểm chứng được ở nguồn gốc (xem `docs/DATA_SOURCES.md`) |

**Tầng 1 (đọc review) vẫn chưa được kiểm chứng trên review thật.** Bộ phân loại hiện chỉ biết khoảng 20 từ khoá tiếng Anh; chỉ nên coi là
bản minh hoạ cho đến khi được đo độ chính xác trên review thật có giấy phép sạch.
Lỗi đã bắt được nhờ kiểm thử trình duyệt: câu "Packaging felt cheap and protective seal was broken" bị gán nhầm `quality_defect`
(trước đây lấy loại khớp cuối cùng); đã sửa thành loại có nhiều từ khoá khớp nhất.
