# Red Team — vb6-worker SLL pre-scan plan

Adversarial pass trên plan (inline, 2026-10-02). Mỗi giả định tấn công kèm
đáp trả + biện pháp trong plan.

## H1 — "SLL count làm has_error false-positive"

Tấn công: SLL có thể báo lỗi ở chỗ LL chấp nhận (ANTLR docs ghi nhận edge
case) → file sạch bị has_error=true, bị loại khỏi retry-set, message sai.

Đáp trả: chấp nhận có chủ đích. Consumer là chuỗi thông báo + retry-set
(chỉ chạy trong path batch-failure hiếm) + 2 meta flags không có test/consumer
đọc giá trị đếm (research §2). Primary batch vẫn parse file → không mất
payload. VERIFY đếm bail rate trên corpus legacy để lượng hóa (phase 02.4).

## H2 — "Bail-on-full-context đổi ngữ nghĩa two-stage khi SLL=1"

Tấn công: helper dùng chung thêm full-context bail → khi operator bật
VB6_WORKER_SLL=1, file từng full-context-simulate trong SLL stage giờ bail →
reparse LL → khác hành vi cũ?

Đáp trả: kết quả payload VẪN vendor-exact (LL stage là vendor tail nguyên
bản); parity test byte-equal là gate (TwoStageParityTest). Hành vi đổi đúng
hướng: hai-giai-doạn không còn thể tự rơi vách đá trong stage SLL. Default
OFF nên ảnh hưởng production ngay lập tức = 0.

## H3 — "Sửa pre-pass xong, user vẫn chậm vì parse loop LL default"

Tấn công: vách đá chỉ dời từ pre-pass sang parse loop (WorkerRunner LL khi
SLL=0) → pain còn nguyên, chỉ visible hơn.

Đáp trả: đúng — và đó là R4 có chủ đích: không relitigate go/no-go cũ đo
trên corpus sai. Plan này交付: (a) pre-pass bounded (đóng nửa công việc 2×),
(b) visibility xuyên suốt, (c) số đo corpus legacy cho follow-up có bằng chứng.
Nếu VERIFY thấy loop chậm trên cùng file → follow-up riêng (per-file guard
hoặc reconsider SLL default với data corpus legacy) — scope creep chặn ở đây.

## H4 — "error_nodes 0/1 phá silent downstream"

Tấn công: đồ thị/graph ingest đọc error_nodes?

Đáp trả: grep lần 1 (research §2) không có consumer; checklist phase 02.4
re-grep lần cuối trước commit. Nếu phase 02 thấy consumer → quay lại
AD-05, chọn phương án đếm exact CHỈ cho file bail-và-nhỏ (< ngưỡng dòng,
LL deadline) — phương án dự phòng đã nghĩ sẵn, không chất vào plan chính.

## H5 — "N của scanning lệch khi loadError"

Tấn công: entries.size() làm mẫu số nhưng i chỉ tăng entry đọc được →
dòng cuối `scanning (N-k)/N` trông như treo.

Đáp trả: cosmetic, hiếm (file không đọc được); đã ghi Open question.
Không tối ưu thêm.

## H6 — "SLL DFA/memory blowup trên file lớn nhất (~20k dòng)"

Tấn công: SLL vẫn có thể phình DFA cache.

Đáp trả: DFA static shared per-grammar, bounded bởi số decision; SLL đo
thực trên corpus khác ~1s/228 file. VERIFY ghi RSS + wall cho file lớn nhất
(phase 02.1) — nếu nghẽn memory thì giảm scope: helper chỉ dùng cho
pre-scan, parseStageSll giữ nguyên recipe cũ (tách AD-04 thành 2 helper).

## H7 — "Refactor helper đụng parse chính → regression"

Đáp trả: TwoStageParityTest byte-equal + full `-k vb6` là gate M4; refactor
chỉ di chuyển code đã chạy ổn định, thêm listener mới (đơn giản, đo được).

## Kết luận

Không hypothesis nào buộc đổi kiến trúc. H3 là rủi ro lớn nhất nhưng được
quản bằng cách tách thành R4 + follow-up có data. Plan APPROVED để implement.
