---
type: Báo cáo HI Predict
date: 2026-08-07
depth: nhanh
verdict: THẬN TRỌNG
---

# Báo cáo HI Predict: Khôi phục lỗi phân tích cú pháp Tree-sitter

## Bối cảnh

Lần chạy trình phân tích C/C++ quan sát được đã ghi nhận:

- 20.186 tệp được quét;
- 6.673 tệp có cờ lỗi gốc (root error) của Tree-sitter hoặc nút `ERROR` tường minh (33,06%);
- 2.650 nút `ERROR` tường minh;
- các mẫu CP932 cũ chứa nút `MISSING` mà không có nút `ERROR` tường minh;
- khoảng 3.281 mục lệnh biên dịch (16,25% số tệp được quét);
- năm header mà quá trình thử lại ngữ pháp thay thế C/C++ hiện có đã chọn trình phân tích khác.

Hai con số nổi bật không đo lường cùng một thứ. Vì 2.650 nút `ERROR`
tường minh có thể ảnh hưởng đến tối đa 2.650 tệp, nên ít nhất 4.023 trong số
6.673 tệp bị đánh dấu (ít nhất 60,29%) không có nút `ERROR` tường minh. Các tệp
này có khả năng chiếm ưu thế bởi các điều kiện khôi phục nút `MISSING` hoặc các
điều kiện `has_error` gốc khác. Việc duyệt AST vẫn tiếp tục, vì vậy đây là tín
hiệu về chất lượng chứ không phải là lỗi trình phân tích ở mức chạy.

Ghi chú truy xuất ngữ cảnh: `mind_mcp` không chứa đoạn văn dự án nào khớp và
`graph_mcp` không khả dụng. Do đó, phân tích sử dụng kiểm tra mã nguồn hỗ trợ
Serena, bằng chứng từ lần chạy chính xác của kho lưu trữ và một lần chạy thử
tập trung. Độ tin cậy suy ra từ mã là cao đối với luồng điều khiển hiện tại và
trung bình đối với phân bố nguyên nhân gốc cho đến khi báo cáo chi tiết được bật
cho một lần chạy.

## Tóm tắt điều hành

Tiếp tục đưa dữ liệu vào, nhưng tách phần phân tích được khôi phục nhanh khỏi
một đường ống sửa chữa có giới hạn. Không hạ ngưỡng libclang trên toàn cục hoặc
thử lại toàn bộ 6.673 tệp. Trước tiên, làm cho chất lượng phân tích theo từng
tệp có thể quan sát được, sau đó ưu tiên việc thử lại dựa trên mức độ tổn hại
cấu trúc và sản lượng ngữ nghĩa, chạy các phương án dự phòng tốn kém trong các
worker cô lập theo ngân sách, và gắn nhãn hoặc cách ly đầu ra đồ thị có độ tin
cậy thấp.

Kết luận là **THẬN TRỌNG** vì mọi rủi ro đã xác định đều có biện pháp giảm
thiểu cụ thể, nhưng việc mở rộng phương án dự phòng trước khi bổ sung cô lập tài
nguyên, làm sạch cờ biên dịch, dấu vân tay ngữ cảnh bộ nhớ đệm và xuất bản có
truy xuất nguồn gốc có thể làm tăng độ trễ và gây ô nhiễm đồ thị.

## Bằng chứng từ triển khai hiện tại

| Bằng chứng | Hành vi hiện tại | Hệ quả |
| --- | --- | --- |
| `cplus_analyzer._tree_error_stats` | Đếm riêng `has_error` gốc, nút `ERROR` tường minh và nút `MISSING` | Số tệp được in ra không thể so sánh trực tiếp với số lượng `ERROR` tường minh |
| `parse_c_family_file` | Tiếp tục trích xuất từ AST Tree-sitter đã khôi phục | Kết quả một phần vẫn có sẵn, nhưng độ tin cậy của chúng không được ràng buộc ở hạ nguồn |
| `--parse-errors-path` | Có thể ghi siêu dữ liệu ngôn ngữ phân tích, mã hóa, thử lại, `ERROR` và `MISSING` theo từng tệp | Khả năng này tồn tại nhưng chưa được nối qua trình khởi chạy phân tích `dev.py` gốc |
| Thử lại header | Thử ngữ pháp C và C++ cho các header mơ hồ | Hữu ích, nhưng chỉ bao phủ một nhóm lỗi |
| Dự phòng libclang | Kích hoạt ở 50, 100 hoặc 200 lỗi tường minh tùy theo kích thước tệp | Không thể chạm tới hầu hết các tệp ít lỗi hoặc chỉ có `MISSING` |
| Chọn phương án dự phòng | So sánh số lỗi Tree-sitter với số chẩn đoán clang | Hai con số có ngữ nghĩa khác nhau và không phải là phép so sánh chất lượng đáng tin cậy |
| Bộ nhớ đệm phân tích | Tránh công việc lặp lại | Không lấy dấu vân tay đầy đủ ngữ cảnh biên dịch, phiên bản trình phân tích hoặc chính sách khôi phục |
| Kiểm thử hiện có | `python -m pytest -q tests/test_cplus_graph_runtime.py` đã qua 4 bài kiểm thử | Hành vi siêu dữ liệu hiện tại đã được xác minh; hành vi khắc phục vẫn cần các bài kiểm thử chuyên dụng |

## Các thỏa thuận đồng thuận

1. Tiếp tục phân tích và xuất bản bằng chứng một phần hữu ích; riêng cảnh báo
   Tree-sitter không được làm cho lần chạy toàn bộ thất bại.
2. Tách đưa dữ liệu vào thông thường khỏi sửa chữa. Việc thử lại tốn kém không
   được chặn khả năng sẵn sàng của đồ thị cho toàn bộ kho dữ liệu.
3. Bật chẩn đoán chi tiết trước khi thay đổi chính sách dự phòng. Nguyên nhân
   gốc phải được nhóm theo phần mở rộng, mã hóa, ngôn ngữ phân tích, ngữ cảnh
   biên dịch và chữ ký lỗi.
4. Dùng thang khôi phục có giới hạn và ưu tiên thay vì áp dụng libclang cho mọi
   tệp bị đánh dấu.
5. Truyền lan backend phân tích, tầng chất lượng và nguồn gốc để đầu ra đã khôi
   phục không bị âm thầm coi là có thẩm quyền.
6. Đưa dấu vân tay của trình phân tích, ngữ pháp, lệnh biên dịch, mã hóa và
   chính sách khôi phục vào định danh bộ nhớ đệm.

## Xung đột và cách giải quyết

| Chủ đề | Kiến trúc sư | Bảo mật | Hiệu suất | UX | Người phản biện | Giải quyết |
| --- | --- | --- | --- | --- | --- | --- |
| Khi nào sửa chữa | Đánh giá chất lượng trước khi trích xuất; hàng đợi theo giai đoạn | Chỉ dự phòng cô lập | Lần chạy thứ hai không đồng bộ, có ngân sách | Trạng thái tức thì và tiến trình khả thi | Bắt đầu với thay đổi chẩn đoán nhỏ nhất | Hoàn tất lần phân tích đã khôi phục trước, phát siêu dữ liệu chất lượng ngay lập tức và xử lý việc sửa chữa ưu tiên trong lần chạy thứ hai có thể tiếp tục lại |
| Ngưỡng dự phòng | Thay số lượng thô bằng điểm tổng hợp | Yêu cầu cô lập và giới hạn tài nguyên | Không bao giờ hạ trên toàn cục | Hiển thị lý do và kết quả xác định | Thí điểm trước khi xây dựng một cỗ máy chính sách rộng | Giữ ngưỡng hiện tại cho đến khi thí điểm 100 tệp; sau đó dùng chấm điểm tổn hại cấu trúc/sản lượng ngữ nghĩa theo ngân sách chạy |
| Chi tiết báo cáo | Thêm vị trí và đoạn mã giới hạn | Tránh tiết lộ mã nguồn/đường dẫn | Tổng hợp để phân loại rẻ | Cung cấp nguyên nhân xếp hạng và hành động tiếp theo | Không xây dựng bảng điều khiển trước | Mặc định là đường dẫn tương đối theo kho lưu trữ, vị trí, chữ ký chuẩn hóa và tổng hợp; làm cho đoạn mã đã làm sạch là tùy chọn |
| Xuất bản đồ thị | Giữ bằng chứng độ tin cậy thấp có thể tìm kiếm nhưng không có thẩm quyền | Cách ly các đóng góp không an toàn/độ tin cậy thấp | Xuất bản đường cơ sở kịp thời | Không bao giờ che giấu trạng thái chất lượng một phần | Xuất bản không lỗi là không cần thiết | Xuất bản bằng chứng tệp/ký hiệu có dấu chất lượng; chỉ loại bỏ các cạnh gọi/kế thừa mạnh cho các tệp bị tổn hại nghiêm trọng |

## Tóm tắt rủi ro

| Rủi ro | Mức độ nghiêm trọng | Nhân khẩu | Giảm thiểu |
| --- | --- | --- | --- |
| AST đã khôi phục âm thầm tạo ký hiệu hoặc quan hệ sai | Cao | Kiến trúc sư, Bảo mật | Thêm trường chất lượng/nguồn gốc; cách ly các cạnh mạnh bị tổn hại nghiêm trọng; xác thực sản lượng ngữ nghĩa |
| Dự phòng không giới hạn thêm nhiều giờ hoặc cạn kiệt bộ nhớ | Cao | Hiệu suất, Bảo mật | Tiến trình worker cô lập, giới hạn thời gian chờ/RSS theo tệp, độ đồng thời có giới hạn, ngân sách tệp/thời gian cho toàn lần chạy |
| Lệnh biên dịch không đáng tin cậy phơi bày cờ clang nguy hiểm hoặc đường dẫn ngoài | Cao; có điều kiện là nghiêm trọng đối với kho lưu trữ thù địch | Bảo mật | Không bao giờ thực thi chuỗi lệnh; danh sách trắng cờ nghiêm ngặt; cô lập đường dẫn chuẩn hóa; hạn chế hệ thống tệp/mạng |
| Ngưỡng hiện tại bỏ sót hầu hết các tệp bị đánh dấu | Cao | Kiến trúc sư, Hiệu suất | Phân loại nhóm `MISSING`, phạm vi lỗi, mã hóa, ngữ pháp và sản lượng trích xuất; nhắm mục tiêu thử lại theo mức tổn hại/giá trị |
| Bộ nhớ đệm cũ che giấu các cải tiến phân tích/ngữ cảnh | Trung bình–Cao | Kiến trúc sư, Hiệu suất | Lấy dấu vân tay ngữ pháp, phiên bản phân tích/backend, cờ biên dịch, mã hóa và phiên bản chính sách khôi phục |
| Chẩn đoán chi tiết rò rỉ đường dẫn máy chủ hoặc mã nguồn | Trung bình | Bảo mật, UX | Đường dẫn chuẩn hóa tương đối theo kho lưu trữ, quyền/lưu giữ hạn chế, đoạn mã đã làm sạch tùy chọn |
| Người vận hành hiểu nhầm 6.673 tệp là 6.673 lỗi nghiêm trọng | Trung bình | UX, Người phản biện | Tách các chỉ số sạch, `ERROR` tường minh, chỉ `MISSING`, giải mã mất dữ liệu, thử lại thành công và cách ly |

## Chi tiết nhân khẩu

### Kiến trúc sư

**Mối quan tâm:** Chỉ số hiện tại không phải là thước đo chất lượng; dữ liệu đồ
thị đã khôi phục thiếu ranh giới độ tin cậy được ràng buộc; dự phòng xảy ra sau
khi trích xuất Tree-sitter; định danh bộ nhớ đệm bỏ sót các phần của ngữ cảnh
phân tích; độ bao phủ lệnh biên dịch phải được đánh giá cho các đơn vị dịch
(translation unit) thay vì mọi header.

**Khuyến nghị:** Giới thiệu hợp đồng `ParseQuality` ổn định giữa phân tích và
trích xuất; định tuyến thử lại theo nhóm nguồn; chọn một kết quả thắng ở cấp
toàn tệp dùng tổn hại cấu trúc cộng với sản lượng ngữ nghĩa; truyền lan nguồn
gốc backend/chất lượng; lấy dấu vân tay ngữ cảnh phân tích thực tế.
**Độ tin cậy:** cao đối với kiến trúc, trung bình đối với phân bố nguyên nhân.

### Bảo mật

**Mối đe dọa:** Mã nguồn sai định dạng có thể gây từ chối dịch vụ cho trình
phân tích; lỗi libclang trong tiến trình có thể làm kẹt hoặc làm sập quá trình
đưa dữ liệu vào; đối số biên dịch và đường dẫn không an toàn có thể mở rộng phạm
vi phơi bày hệ thống tệp/mã gốc; AST một phần bị nhiễm độc có thể làm hỏng tính
toàn vẹn đồ thị; báo cáo/bộ nhớ đệm có thể tiết lộ đường dẫn hoặc mã nguồn độc
quyền.

**Giảm thiểu:** Chạy dự phòng trong các tiến trình con dùng một lần, giới hạn
tài nguyên; dùng danh sách trắng cờ biên dịch nghiêm ngặt mà không thực thi lệnh;
chuẩn hóa và cô lập mọi đường dẫn; giới hạn tệp, duyệt AST, chẩn đoán và báo
cáo; dùng đường dẫn tương đối và lưu giữ hạn chế; giữ khôi phục không phá hủy.
**Mức độ nghiêm trọng:** cao, có điều kiện là nghiêm trọng đối với các kho lưu
trữ do kẻ tấn công kiểm soát.

### Hiệu suất

**Điểm nghẽn:** Với chỉ 2.650 lỗi tường minh và ngưỡng tối thiểu là 50, tối đa
53 tệp hiện có thể đủ điều kiện dự phòng (và có thể ít hơn). Việc thử lại đồng
bộ 6.673 tệp sẽ thêm `6.673 × độ trễ dự phòng`; với minh họa 0,5–2 giây mỗi tệp,
khoảng 56 phút đến 3,7 giờ.

**Phương án thay thế:** Giữ Tree-sitter đã khôi phục làm đường cơ sở nhanh; lưu
hàng đợi sửa chữa có thể tiếp tục lại; ưu tiên các tệp bị tổn hại/giá trị cao;
mượn cờ biên dịch từ các đơn vị dịch đại diện cho header; lưu bộ nhớ đệm cả kết
quả thành công lẫn không cải thiện cuối. Bắt đầu với tối đa 500 tệp hoặc 15 phút
mỗi lần chạy, một lần phân tích Tree-sitter thay thế và một lần thử libclang mỗi
tệp, sau đó tinh chỉnh từ các phép đo. **Độ tin cậy:** cao đối với các giới hạn
khả năng đạt/chi phí, trung bình đối với độ trễ tuyệt đối.

### UX

**Vấn đề:** Dòng tóm tắt duy nhất trộn ngữ nghĩa cấp tệp và cấp nút; chỉ mười
đường dẫn mẫu được hiển thị; CLI gốc không phơi bày tạo phẩm chi tiết; người
dùng hạ nguồn không thể biết kết quả là sạch, đã khôi phục, đã sửa chữa hay đã
cách ly.

**Trường hợp biên:** Các tệp chỉ có `MISSING`, giải mã CP932/mất dữ liệu, mã
được sinh/vendor, header mơ hồ, Pro*C, tài nguyên Windows, thiếu ngữ cảnh biên
dịch và kết quả cũ trong bộ nhớ đệm cần trạng thái riêng biệt. Đầu ra CLI phải
hữu ích ở cả văn bản thuần và dạng máy đọc được mà không phụ thuộc vào màu sắc.
**Mối quan tâm về khả năng truy cập:** nhật ký đường dẫn thô dài khó điều hướng;
các tóm tắt ổn định và đường dẫn báo cáo được ưu tiên hơn hàng nghìn dòng được
phát ra.

### Người phản biện

**Các giả định bị thách thức:** Một tệp bị đánh dấu không nhất thiết không dùng
được; không có lỗi Tree-sitter không phải là mục tiêu sản phẩm; số chẩn đoán
clang thấp hơn không phải là bằng chứng AST tốt hơn; mọi header không cần mục
lệnh biên dịch riêng.

**Phương án thay thế đơn giản hơn:** Trước tiên nối báo cáo JSON hiện có vào
các lần chạy bình thường, sửa nhãn chỉ số và xây dựng một kho vàng (gold corpus)
phân tầng 100 tệp. Không xây dựng cỗ máy hợp nhất đa trình phân tích phổ quát
trước khi bằng chứng cho thấy nhóm nào làm hỏng kết quả đồ thị. **Tình huống xấu
nhất:** thử lại mọi tệp bị đánh dấu làm tăng thời gian chạy nhiều giờ, tiêu tốn
bộ nhớ, duy trì các mục bộ nhớ đệm cũ và thay thế các AST đã khôi phục hữu ích
bằng đầu ra dự phòng nghèo ngữ cảnh.

## Khuyến nghị

1. **Phơi bày tạo phẩm chất lượng phân tích theo phạm vi lần chạy từ `dev.py`.**
   Tái sử dụng `--parse-errors-path` cho C/C++ và định nghĩa một lược đồ chẩn
   đoán chung nhỏ cho các trình phân tích khác. Đây là thay đổi tối thiểu làm
   cho vấn đề có thể đo lường được.
2. **Sửa ngữ nghĩa tóm tắt.** Báo cáo riêng `has_error` cấp tệp, nút/tệp `ERROR`
   tường minh, nút/tệp `MISSING`, giải mã mất dữ liệu, số lần thử lại, cải thiện
   sau thử lại và các tầng chất lượng.
3. **Phân cụm trước khi thử lại.** Nhóm theo loại nguồn, phần mở rộng, mã hóa,
   lựa chọn ngữ pháp, khả năng sẵn có ngữ cảnh biên dịch, trạng thái được
   sinh/vendor, vị trí/chữ ký lỗi và sản lượng trích xuất.
4. **Thêm chất lượng và nguồn gốc theo từng tệp.** Tối thiểu dùng `clean`
   (sạch), `recovered-low-damage` (đã khôi phục, tổn hại thấp),
   `retry-required` (cần thử lại) và `quarantined` (đã cách ly); đính kèm
   backend, ngôn ngữ, dấu vân tay ngữ cảnh biên dịch và phiên bản chính sách.
5. **Dùng thang khôi phục có giới hạn.** Thử xác thực mã hóa, thử lại ngữ pháp
   header C/C++, che/mã hóa trước theo biến thể ngôn ngữ, libclang nhận biết
   ngữ cảnh, sau đó là dự phòng/cách ly tối thiểu. Cho phép tối đa một lần thử
   mỗi giai đoạn.
6. **Làm cho dự phòng tốn kém không đồng bộ và cô lập.** Áp dụng ngân sách
   tệp/thời gian/bộ nhớ, một nhóm worker nhỏ, bộ ngắt mạch theo nhóm và kết quả
   có thể tiếp tục lại.
7. **Chọn kết quả cải thiện theo sản lượng ngữ nghĩa, không chỉ số lượng chẩn
   đoán.** Dùng độ bao phủ phạm vi lỗi cấu trúc, mức khôi phục khai báo/hàm/kiểu
   cấp cao nhất, tính nhất quán phạm vi và số lượng ký hiệu/gọi ổn định.
8. **Sửa định danh bộ nhớ đệm trước khi mở rộng sửa chữa.** Đưa vào băm mã
   nguồn, mã hóa, phiên bản trình phân tích/ngữ pháp, dấu vân tay ngữ cảnh biên
   dịch, backend dự phòng và phiên bản chính sách khôi phục.
9. **Xác thực bằng thí điểm phân tầng 100 tệp.** Bao gồm C/C++ sạch, header mơ
   hồ, tệp nặng macro/được sinh, CP932, Pro*C, tệp tài nguyên, có và không có
   ngữ cảnh biên dịch. Theo dõi độ trễ p50/p95, RSS, tỷ lệ hết thời gian, thay
   đổi tầng chất lượng, sản lượng ngữ nghĩa và lực lượng/độ chính xác đồ thị.

## Các bước tiếp theo

Vì kết luận là **THẬN TRỌNG**, hãy xử lý các biện pháp giảm thiểu này trước khi
triển khai dự phòng trên diện rộng:

1. Nối và làm phong phú tạo phẩm chẩn đoán, đồng thời gắn nhãn lại các chỉ số
   hiện tại.
2. Xây dựng kho thí điểm phân tầng và thiết lập các đường cơ sở sản lượng ngữ
   nghĩa và hiệu suất hiện tại.
3. Định nghĩa hợp đồng `ParseQuality`/nguồn gốc và chính sách cách ly.
4. Thiết kế worker khôi phục cô lập, có ngân sách và bộ lọc đối số biên dịch.
5. Chỉ khi đó mới tinh chỉnh định tuyến khôi phục và ngưỡng dự phòng từ kết quả
   quan sát của từng nhóm.

## Tiêu chí thành công

- Việc đưa dữ liệu vào thông thường hoàn tất ngay cả khi các tệp riêng lẻ chứa
  cú pháp đã khôi phục.
- Mọi thực thể được trích xuất đều có thể truy vết đến backend trình phân tích
  và tầng chất lượng.
- Sức khỏe chất lượng phân tích và sức khỏe ghi đồ thị được báo cáo riêng biệt.
- Việc khôi phục tốn kém nằm trong ngân sách thời gian tường minh, bộ nhớ và
  tệp.
- Các mục bộ nhớ đệm bị vô hiệu hóa khi ngữ cảnh phân tích hoặc chính sách khôi
  phục thay đổi.
- Các fixture vàng cho thấy độ chính xác ký hiệu/gọi được cải thiện mà không có
  hồi quy p95 thời gian chạy hoặc RSS đỉnh không thể chấp nhận.

## Câu hỏi chưa được giải quyết

1. Gốc nguồn và các nhóm tệp chính xác nào đã tạo ra 6.673 cờ?
2. Có bao nhiêu tệp bị đánh dấu chỉ chứa nút `MISSING`, và các nút đó nằm ở đâu
   so với ranh giới khai báo và hàm?
3. Bao nhiêu phần trăm đơn vị dịch—không phải mọi tệp được quét—có lệnh biên
   dịch dùng được, và header nào có thể kế thừa ngữ cảnh của chúng?
4. Các cảnh báo phân tích hiện tại ảnh hưởng bao nhiêu đến việc trích xuất hàm,
   kiểu và gọi dự kiến trên một mẫu vàng đã được rà soát?
