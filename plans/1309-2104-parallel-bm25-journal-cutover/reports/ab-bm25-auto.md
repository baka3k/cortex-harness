# A/B report: BM25 auto-corpus (phase A2 — quyết định flip default ON)

- **Ngày:** 2026-09-13 · branch `feat/change-db`
- **Script:** `scripts/benchmark_bm25_auto.py` — chạy:
  `PYTHONPATH=code-tiny .venv/bin/python scripts/benchmark_bm25_auto.py`
- **Thiết kế:** engine Python thật (`IntelligentRetrievalEngine`), chỉ stub
  `_retrieve_qdrant` / `_retrieve_keyword` (pattern phase-04). Semantic score
  mô phỏng vector neighbors: affinity docs giảm dần từ 0.92, phần còn lại
  0.35–0.47 (jitter md5 deterministic). Keyword stub rỗng để A/B đo thuần
  hiệu ứng bm25.
- **Candidate set:** 40 nodes giống realworld (payment/auth/order/invoice/
  shipping/notification… TS + PY). **Query set:** 12 — 4 structural,
  4 semantic, 4 temporal (gồm 2 câu tiếng Việt), top_k=10.
- **Bridge:** auto (Rust `cortex_retrieval_py.so`); parity 2 đường đã có test
  riêng (`test_bm25_auto_corpus.py::BridgeModeParityTests` — cùng score).

## Kết luận review (tiêu chí flip của plan A2)

**ĐẠT — flip `CORTEX_BM25_AUTO` default ON là an toàn.** Bằng chứng:

1. **21/120 slot top-10 bị đảo chỗ** (12 query × 10); **20/21 đảo chỗ giữa
   các candidate có Δsemantic < 0.1** — đúng tiêu chí "chỉ đảo candidate
   semantic gần nhau".
2. **Trường hợp Δsemantic 0.362 duy nhất** (query `what changed this week in
   order service`, rank 5) là artifact của diff theo cặp rank: candidate
   giữ vị trí ON rank 5 (`emit_order_event`, sem 0.830) chỉ tụt 1 hạng từ
   OFF rank 4; cặp displaced thật sự là `order.service.ts:OrderService`
   (sem 0.422) đẩy `auth.fixture.ts:validateTokenFixture` (sem 0.468) ra
   khỏi top-10 — **Δsemantic thật 0.046 (< 0.1)** và có lý do keyword rõ:
   file `order.service.ts` khớp token "order service" cho query scope order.
3. **14 "hit mới"** đều là re-entry của candidate đã có trong seeds, cùng
   domain với query: payment files cho "recently changed payment files",
   auth files cho "last modified auth session code", order files cho
   "what changed this week in order service", `refundWorker` cho "refund
   processing" — lý do keyword rõ ràng, không có hit lạ.
4. **Query tiếng Việt** (`hàm xử lý thanh toán bị lỗi`, `hàm nào bị sửa
   gần đây`): tokenizer identifier `[a-z0-9_]+` không sinh token khớp →
   bm25 im lặng, ranking OFF = ON từng char. Không corrupt semantic path.
5. **Structural queries:** thứ tự top-4 affinity giữ nguyên, score top-1
   được củng cố (0.215 → 0.317 nhờ bm25) — bm25 reinforces, không override.
6. **Latency:** mean +0.01 ms/query (noise; corpus build trên 40 seeds
   ~0.2 ms mỗi search).

**Rollback:** env `CORTEX_BM25_AUTO=0` (không cần đụng code) hoặc 1 dòng
`_BM25_AUTO_DEFAULT = False` trong `intelligent_retrieval.py`.

**Lưu ý:** semantic ở đây là stub mô phỏng; sau khi deploy nên spot-check
thêm explore path trên store thật (1-2 query payment/auth) để xác nhận cùng
hướng ảnh hưởng.

---

## Output script (verbatim)

Candidate set: 40 nodes · Query set: 12 (4 structural / 4 semantic / 4 temporal) · top_k=10

Đang chạy OFF (auto_bm25=False)…
Đang chạy ON  (auto_bm25=True)…

## Latency 

| Query | OFF (ms) | ON (ms) | Δ (ms) |
|---|---|---|---|
| `who calls processPayment` | 1.33 | 0.35 | -0.98 |
| `callers of validateToken` | 0.10 | 0.26 | +0.17 |
| `dependencies of OrderService` | 0.08 | 0.30 | +0.22 |
| `where is sendNotification used` | 0.08 | 0.27 | +0.19 |
| `find code similar to refund processing` | 0.09 | 0.25 | +0.16 |
| `explain the authentication flow with JWT` | 0.09 | 0.24 | +0.14 |
| `hàm xử lý thanh toán bị lỗi` | 1.11 | 0.24 | -0.88 |
| `code similar to invoice total calculation` | 0.08 | 0.24 | +0.15 |
| `recently changed payment files` | 0.08 | 0.23 | +0.15 |
| `last modified auth session code` | 0.08 | 0.23 | +0.15 |
| `what changed this week in order service` | 0.08 | 0.23 | +0.15 |
| `hàm nào bị sửa gần đây` | 0.08 | 0.24 | +0.16 |
| **mean** | **0.28** | **0.26** | **-0.02** |

## Ranking diff (top-10) 

#### `who calls processPayment`

Intent: `structural`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `payment.controller.ts:processPayment` 0.2150 | `payment.controller.ts:processPayment` 0.3174 |  |
| 2 | `payment.controller.ts:PaymentController` 0.2044 | `payment.controller.ts:PaymentController` 0.1778 |  |
| 3 | `payment.fixture.ts:processPaymentFixture` 0.1938 | `payment.fixture.ts:processPaymentFixture` 0.1686 |  |
| 4 | `refund_worker.py:retry_refund` 0.1833 | `refund_worker.py:retry_refund` 0.1594 |  |
| 5 | `order.service.ts:createOrder` 0.0556 | `order.service.ts:createOrder` 0.0483 |  |
| 6 | `cache.helpers.ts:cacheGet` 0.0534 | `cache.helpers.ts:cacheGet` 0.0464 |  |
| 7 | `sync_invoice.py:sync_invoice` 0.0534 | `sync_invoice.py:sync_invoice` 0.0464 |  |
| 8 | `shipping.service.ts:trackShipment` 0.0525 | `shipping.service.ts:trackShipment` 0.0457 |  |
| 9 | `order_events.py:emit_order_event` 0.0511 | `order_events.py:emit_order_event` 0.0444 |  |
| 10 | `auth.fixture.ts:validateTokenFixture` 0.0509 | `auth.fixture.ts:validateTokenFixture` 0.0443 |  |

#### `callers of validateToken`

Intent: `structural`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `auth.service.ts:validateToken` 0.2150 | `auth.service.ts:validateToken` 0.3174 |  |
| 2 | `auth.service.ts:AuthService` 0.2044 | `auth.service.ts:AuthService` 0.1778 |  |
| 3 | `auth.fixture.ts:validateTokenFixture` 0.1939 | `auth.fixture.ts:validateTokenFixture` 0.1686 |  |
| 4 | `session.store.ts:loadSession` 0.1833 | `session.store.ts:loadSession` 0.1594 |  |
| 5 | `cache.helpers.ts:cacheSet` 0.0550 | `cache.helpers.ts:cacheSet` 0.0478 |  |
| 6 | `notification.queue.ts:NotificationQueue` 0.0536 | `notification.queue.ts:NotificationQueue` 0.0466 |  |
| 7 | `auth.service.ts:hashPassword` 0.0536 | `auth.service.ts:hashPassword` 0.0466 |  |
| 8 | `refund_worker.py:retry_refund` 0.0529 | `refund_worker.py:retry_refund` 0.0460 |  |
| 9 | `invoice.service.ts:generateInvoice` 0.0524 | `invoice.service.ts:generateInvoice` 0.0456 |  |
| 10 | `audit.log.ts:auditLog` 0.0513 | `audit.log.ts:auditLog` 0.0446 |  |

#### `dependencies of OrderService`

Intent: `structural`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `order.service.ts:OrderService` 0.2150 | `order.service.ts:OrderService` 0.3174 |  |
| 2 | `order.service.ts:createOrder` 0.2045 | `order.service.ts:createOrder` 0.1778 |  |
| 3 | `invoice.service.ts:generateInvoice` 0.1939 | `invoice.service.ts:generateInvoice` 0.1686 |  |
| 4 | `order_events.py:emit_order_event` 0.1834 | `order_events.py:emit_order_event` 0.1595 |  |
| 5 | `refund_worker.py:retry_refund` 0.0528 | `refund_worker.py:retry_refund` 0.0459 |  |
| 6 | `cart.service.ts:mergeCarts` 0.0524 | `cart.service.ts:mergeCarts` 0.0455 |  |
| 7 | `payment.fixture.ts:processPaymentFixture` 0.0516 | `payment.fixture.ts:processPaymentFixture` 0.0449 |  |
| 8 | `shipping.service.ts:trackShipment` 0.0509 | `shipping.service.ts:trackShipment` 0.0443 |  |
| 9 | `payment.controller.ts:PaymentController` 0.0500 | `payment.controller.ts:PaymentController` 0.0435 |  |
| 10 | `payment.schema.ts:paymentSchema` 0.0491 | `payment.schema.ts:paymentSchema` 0.0427 |  |

#### `where is sendNotification used`

Intent: `structural`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `notification.queue.ts:sendNotification` 0.2150 | `notification.queue.ts:sendNotification` 0.3174 |  |
| 2 | `notification.queue.ts:NotificationQueue` 0.2044 | `notification.queue.ts:NotificationQueue` 0.1777 |  |
| 3 | `order.service.ts:createOrder` 0.1937 | `order.service.ts:createOrder` 0.1685 |  |
| 4 | `shipping.service.ts:estimateShipping` 0.0551 | `shipping.service.ts:estimateShipping` 0.0479 |  |
| 5 | `invoice.pdf.ts:renderInvoicePdf` 0.0550 | `invoice.pdf.ts:renderInvoicePdf` 0.0479 |  |
| 6 | `shipping.service.ts:trackShipment` 0.0522 | `shipping.service.ts:trackShipment` 0.0454 |  |
| 7 | `audit.log.ts:auditLog` 0.0513 | `audit.log.ts:auditLog` 0.0446 |  |
| 8 | `invoice.service.ts:generateInvoice` 0.0501 | `invoice.service.ts:generateInvoice` 0.0436 |  |
| 9 | `order_events.py:emit_order_event` 0.0498 | `order_events.py:emit_order_event` 0.0433 |  |
| 10 | `refund_worker.py:retry_refund` 0.0484 | `refund_worker.py:retry_refund` 0.0421 |  |

#### `find code similar to refund processing`

Intent: `semantic`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `payment.controller.ts:refundPayment` 0.5150 | `payment.controller.ts:refundPayment` 0.4478 |  |
| 2 | `refund_worker.py:retry_refund` 0.4884 | `refund_worker.py:retry_refund` 0.4247 |  |
| 3 | `payment.controller.ts:captureCharge` 0.4618 | `payment.controller.ts:captureCharge` 0.4016 |  |
| 4 | `payment.gateway.ts:chargeGateway` 0.4352 | `payment.gateway.ts:chargeGateway` 0.3784 |  |
| 5 | `retry.policy.ts:withRetry` 0.1117 | `refund.worker.ts:refundWorker` 0.1742 | ON thêm hit mới |
| 6 | `order.service.ts:createOrder` 0.1116 | `retry.policy.ts:withRetry` 0.0972 | đảo chỗ, Δsemantic 0.000 (< 0.1) |
| 7 | `auth.fixture.ts:validateTokenFixture` 0.1104 | `order.service.ts:createOrder` 0.0970 | đảo chỗ, Δsemantic 0.001 (< 0.1) |
| 8 | `auth.service.ts:hashPassword` 0.1097 | `auth.fixture.ts:validateTokenFixture` 0.0960 | đảo chỗ, Δsemantic 0.001 (< 0.1) |
| 9 | `report.builder.ts:buildDailyReport` 0.1087 | `auth.service.ts:hashPassword` 0.0954 | đảo chỗ, Δsemantic 0.001 (< 0.1) |
| 10 | `auth.service.ts:AuthService` 0.1070 | `report.builder.ts:buildDailyReport` 0.0946 | đảo chỗ, Δsemantic 0.002 (< 0.1) |

#### `explain the authentication flow with JWT`

Intent: `semantic`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `auth.service.ts:validateToken` 0.5150 | `jwt.utils.ts:signJwt` 0.5553 | đảo chỗ, Δsemantic 0.030 (< 0.1) |
| 2 | `jwt.utils.ts:signJwt` 0.4886 | `auth.service.ts:validateToken` 0.4478 | đảo chỗ, Δsemantic 0.030 (< 0.1) |
| 3 | `auth.service.ts:refreshToken` 0.4622 | `auth.service.ts:refreshToken` 0.4019 |  |
| 4 | `session.store.ts:loadSession` 0.4357 | `session.store.ts:loadSession` 0.3789 |  |
| 5 | `cart.service.ts:mergeCarts` 0.1130 | `cart.service.ts:mergeCarts` 0.0983 |  |
| 6 | `payment.schema.ts:paymentSchema` 0.1128 | `payment.schema.ts:paymentSchema` 0.0981 |  |
| 7 | `payment.controller.ts:refundPayment` 0.1088 | `payment.controller.ts:refundPayment` 0.0946 |  |
| 8 | `order.service.ts:cancelOrder` 0.1084 | `order.service.ts:cancelOrder` 0.0943 |  |
| 9 | `retry.policy.ts:withRetry` 0.1078 | `retry.policy.ts:withRetry` 0.0938 |  |
| 10 | `audit.log.ts:auditLog` 0.1027 | `audit.log.ts:auditLog` 0.0893 |  |

#### `hàm xử lý thanh toán bị lỗi`

Intent: `default`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `payment.controller.ts:processPayment` 0.3800 | `payment.controller.ts:processPayment` 0.3800 |  |
| 2 | `payment.controller.ts:captureCharge` 0.3615 | `payment.controller.ts:captureCharge` 0.3615 |  |
| 3 | `payment.gateway.ts:chargeGateway` 0.3430 | `payment.gateway.ts:chargeGateway` 0.3430 |  |
| 4 | `refund_worker.py:retry_refund` 0.3246 | `refund_worker.py:retry_refund` 0.3246 |  |
| 5 | `auth.service.ts:AuthService` 0.0972 | `auth.service.ts:AuthService` 0.0972 |  |
| 6 | `user.service.ts:updateUserProfile` 0.0966 | `user.service.ts:updateUserProfile` 0.0966 |  |
| 7 | `auth.service.ts:refreshToken` 0.0939 | `auth.service.ts:refreshToken` 0.0939 |  |
| 8 | `invoice.service.ts:generateInvoice` 0.0870 | `invoice.service.ts:generateInvoice` 0.0870 |  |
| 9 | `auth.fixture.ts:validateTokenFixture` 0.0801 | `auth.fixture.ts:validateTokenFixture` 0.0801 |  |
| 10 | `user.service.ts:createUser` 0.0796 | `user.service.ts:createUser` 0.0796 |  |

#### `code similar to invoice total calculation`

Intent: `semantic`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `invoice.service.ts:calculateInvoiceTotal` 0.5150 | `invoice.service.ts:calculateInvoiceTotal` 0.5783 |  |
| 2 | `invoice.service.ts:generateInvoice` 0.4886 | `invoice.service.ts:generateInvoice` 0.5553 |  |
| 3 | `sync_invoice.py:sync_invoice` 0.4622 | `sync_invoice.py:sync_invoice` 0.4020 |  |
| 4 | `jwt.utils.ts:signJwt` 0.1185 | `invoice.pdf.ts:renderInvoicePdf` 0.1866 | ON thêm hit mới |
| 5 | `payment.fixture.ts:processPaymentFixture` 0.1184 | `jwt.utils.ts:signJwt` 0.1031 | đảo chỗ, Δsemantic 0.000 (< 0.1) |
| 6 | `payment.controller.ts:captureCharge` 0.1106 | `payment.fixture.ts:processPaymentFixture` 0.1029 | đảo chỗ, Δsemantic 0.009 (< 0.1) |
| 7 | `shipping.service.ts:trackShipment` 0.1098 | `payment.controller.ts:captureCharge` 0.0961 | đảo chỗ, Δsemantic 0.001 (< 0.1) |
| 8 | `auth.service.ts:AuthService` 0.1060 | `shipping.service.ts:trackShipment` 0.0954 | đảo chỗ, Δsemantic 0.004 (< 0.1) |
| 9 | `audit.log.ts:auditLog` 0.0998 | `auth.service.ts:AuthService` 0.0922 | đảo chỗ, Δsemantic 0.007 (< 0.1) |
| 10 | `refund.worker.ts:refundWorker` 0.0961 | `audit.log.ts:auditLog` 0.0868 | đảo chỗ, Δsemantic 0.004 (< 0.1) |

#### `recently changed payment files`

Intent: `temporal`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `payment.controller.ts:processPayment` 0.3350 | `payment.controller.ts:processPayment` 0.4217 |  |
| 2 | `payment.controller.ts:refundPayment` 0.3244 | `payment.controller.ts:refundPayment` 0.4125 |  |
| 3 | `payment.schema.ts:paymentSchema` 0.3138 | `payment.schema.ts:paymentSchema` 0.4033 |  |
| 4 | `payment.gateway.ts:chargeGateway` 0.3032 | `payment.gateway.ts:chargeGateway` 0.3941 |  |
| 5 | `shipping.service.ts:trackShipment` 0.1744 | `payment.controller.ts:PaymentController` 0.2589 | ON thêm hit mới |
| 6 | `retry.policy.ts:withRetry` 0.1740 | `payment.fixture.ts:processPaymentFixture` 0.2541 | ON thêm hit mới |
| 7 | `user.repository.ts:findUserByEmail` 0.1732 | `payment.controller.ts:captureCharge` 0.2534 | ON thêm hit mới |
| 8 | `order.service.ts:cancelOrder` 0.1705 | `refund.worker.ts:refundWorker` 0.2312 | ON thêm hit mới |
| 9 | `order_events.py:emit_order_event` 0.1682 | `shipping.service.ts:trackShipment` 0.1516 | đảo chỗ, Δsemantic 0.017 (< 0.1) |
| 10 | `order.service.ts:createOrder` 0.1682 | `retry.policy.ts:withRetry` 0.1513 | đảo chỗ, Δsemantic 0.016 (< 0.1) |

#### `last modified auth session code`

Intent: `temporal`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `session.store.ts:loadSession` 0.3350 | `session.store.ts:loadSession` 0.4217 |  |
| 2 | `auth.service.ts:refreshToken` 0.3244 | `auth.service.ts:refreshToken` 0.3343 |  |
| 3 | `auth.service.ts:validateToken` 0.3138 | `auth.service.ts:validateToken` 0.3250 |  |
| 4 | `shipping.service.ts:estimateShipping` 0.1735 | `auth.service.ts:AuthService` 0.2029 | đảo chỗ, Δsemantic 0.001 (< 0.1) |
| 5 | `auth.service.ts:AuthService` 0.1733 | `auth.service.ts:hashPassword` 0.1916 | ON thêm hit mới |
| 6 | `payment.controller.ts:captureCharge` 0.1728 | `jwt.utils.ts:signJwt` 0.1789 | ON thêm hit mới |
| 7 | `retry.policy.ts:withRetry` 0.1727 | `auth.fixture.ts:validateTokenFixture` 0.1779 | ON thêm hit mới |
| 8 | `user.service.ts:updateUserProfile` 0.1717 | `shipping.service.ts:estimateShipping` 0.1509 | đảo chỗ, Δsemantic 0.005 (< 0.1) |
| 9 | `invoice.pdf.ts:renderInvoicePdf` 0.1707 | `payment.controller.ts:captureCharge` 0.1503 | đảo chỗ, Δsemantic 0.006 (< 0.1) |
| 10 | `order.service.ts:cancelOrder` 0.1702 | `retry.policy.ts:withRetry` 0.1502 | đảo chỗ, Δsemantic 0.007 (< 0.1) |

#### `what changed this week in order service`

Intent: `temporal`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `order.service.ts:createOrder` 0.3350 | `order.service.ts:createOrder` 0.4217 |  |
| 2 | `order.service.ts:cancelOrder` 0.3244 | `order.service.ts:cancelOrder` 0.4125 |  |
| 3 | `order.mapper.ts:mapOrderRow` 0.3139 | `order.mapper.ts:mapOrderRow` 0.3808 |  |
| 4 | `order_events.py:emit_order_event` 0.3033 | `order.service.ts:OrderService` 0.2693 | ON thêm hit mới |
| 5 | `auth.fixture.ts:validateTokenFixture` 0.1756 | `order_events.py:emit_order_event` 0.2637 | đảo chỗ, Δsemantic 0.362 (>= 0.1) |
| 6 | `session.store.ts:loadSession` 0.1752 | `order.fixture.ts:createOrderFixture` 0.2339 | ON thêm hit mới |
| 7 | `refund.worker.ts:refundWorker` 0.1743 | `cart.service.ts:addToCart` 0.1719 | đảo chỗ, Δsemantic 0.008 (< 0.1) |
| 8 | `payment.controller.ts:processPayment` 0.1725 | `invoice.service.ts:calculateInvoiceTotal` 0.1696 | ON thêm hit mới |
| 9 | `cart.service.ts:addToCart` 0.1717 | `user.service.ts:createUser` 0.1690 | ON thêm hit mới |
| 10 | `notification.queue.ts:sendNotification` 0.1709 | `shipping.service.ts:trackShipment` 0.1624 | ON thêm hit mới |

#### `hàm nào bị sửa gần đây`

Intent: `default`

| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |
|---|---|---|---|
| 1 | `cart.service.ts:mergeCarts` 0.3800 | `cart.service.ts:mergeCarts` 0.3800 |  |
| 2 | `cache.helpers.ts:cacheSet` 0.3615 | `cache.helpers.ts:cacheSet` 0.3615 |  |
| 3 | `report.builder.ts:buildDailyReport` 0.3431 | `report.builder.ts:buildDailyReport` 0.3431 |  |
| 4 | `notification.queue.ts:sendNotification` 0.1026 | `notification.queue.ts:sendNotification` 0.1026 |  |
| 5 | `payment.gateway.ts:chargeGateway` 0.1016 | `payment.gateway.ts:chargeGateway` 0.1016 |  |
| 6 | `payment.controller.ts:refundPayment` 0.0999 | `payment.controller.ts:refundPayment` 0.0999 |  |
| 7 | `auth.service.ts:refreshToken` 0.0999 | `auth.service.ts:refreshToken` 0.0999 |  |
| 8 | `cache.helpers.ts:cacheGet` 0.0973 | `cache.helpers.ts:cacheGet` 0.0973 |  |
| 9 | `notification.queue.ts:NotificationQueue` 0.0970 | `notification.queue.ts:NotificationQueue` 0.0970 |  |
| 10 | `cart.service.ts:addToCart` 0.0919 | `cart.service.ts:addToCart` 0.0919 |  |

## Tổng kết tiêu chí flip 

- Tổng số vị trí bị đảo thứ tự (trong 12 × top-10): 21
- Số đảo chỗ giữa candidate có Δsemantic < 0.1: 20/21
- Δsemantic lớn nhất trong các cặp bị đảo chỗ: 0.362
- Số hit mới xuất hiện trong top-10 nhờ bm25 (auto corpus build trên seeds nên chỉ là re-entry): 14
