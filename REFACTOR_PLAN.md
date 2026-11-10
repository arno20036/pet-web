# app.py 重構計畫（給 Claude Code 執行）

> 來源：六次三方會議（高級程式工程師、商業主管、系統架構分析師）的結論整理。
> 專案性質：**學生專題**。付款流程是模擬的，README 公開測試帳密是刻意的，**這些不要改，也不要當成問題處理**。

## 0. 目標與原則

**目標：** `app.py`（目前 4,075 行、91 個函式、78 個路由）拆成清楚的分層結構，**不改變網站的任何外部行為**。

必須遵守的原則：

1. **行為不變。** 網址、表單欄位、模板顯示的內容、資料庫結構和資料都維持原樣。唯一的例外是第 3 步列出的兩個邏輯錯誤。
2. **不換技術。** 繼續用 Flask 3 + 原生 sqlite3 + Jinja2，**不要**引入 SQLAlchemy 或其他 ORM，也不要改前端框架。
3. **小步前進。** 依照下面的步驟 0 到 4 進行；每一步完成後都要能啟動網站、跑過測試，再 `git commit`。不要一次大搬家。
4. **保護資料。** 測試一律用 `database.db` 的副本（例如複製到暫存目錄），不要改到原本的 `database.db`。
5. **行號會變動。** 下面的行號是以重構前的 `app.py` 為準，請用函式名稱搜尋。
6. 每完成一步，向使用者回報：做了什麼、`app.py` 剩幾行、測試結果。

## 1. 目標目錄結構

```
pet-web-master/
├── app.py                  # 只剩 create_app() 與啟動，約 30 行
├── config.py               # DATABASE 路徑、UPLOAD_FOLDER、SECRET_KEY（從環境變數讀，沒有就用固定的開發用值）
├── pet_platform/
│   ├── __init__.py         # create_app()：註冊 blueprints、filters、context_processor、errorhandler
│   ├── db.py               # get_db()（存在 g）、teardown 關閉連線、query_one_or_404()、init-db 指令
│   ├── decorators.py       # login_required、role_required(*roles)
│   ├── constants.py        # OrderStatus、PaymentStatus、Role、ReviewType、TrainerLevel
│   ├── utils/
│   │   ├── time.py         # now_utc8()、utc8 filter、日期解析
│   │   └── uploads.py      # 儲存上傳檔案（集中原本 4 處重複的邏輯）
│   ├── services/
│   │   ├── order_service.py    # 狀態轉換表、預約驗證、取消、改期、list_orders()
│   │   ├── payment_service.py  # 模擬付款、補款、退款任務
│   │   ├── ledger_service.py   # 撥款給訓練師、抽成、等級、提領、平台帳
│   │   └── stats_service.py    # 統計儀表板指標、側欄徽章計數
│   └── blueprints/
│       ├── auth.py         # /register /login /logout（3 個）
│       ├── main.py         # / /listing /booking /uploads /trainer/<id>/listings（5 個）
│       ├── member.py       # /member/*（10 個）
│       ├── trainer.py      # /trainer/*（16 個）
│       ├── admin.py        # /admin/*（25 個）
│       └── api/            # /api/*（19 個），再細分 chat.py、orders.py、reviews.py、accounts.py
├── templates/              # 不搬動；只更新 url_for 的 endpoint 名稱
├── schema.sql              # 補齊成實際的 24 張表
└── tests/
    └── test_smoke.py
```

依賴方向：`blueprints → services → db / decorators / constants / utils`。service 不可以 import blueprint，也不可以呼叫 `flash`、`redirect`、`render_template`；遇到業務錯誤時丟出自訂例外（例如 `ServiceError(msg)`），由路由層轉成 flash 或 JSON。

## 2. 執行步驟

### 步驟 0：安全網（先做，不改任何邏輯）

- 建立分支 `refactor/split-app`。
- 新增 `.gitignore`：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.env`（**保留** `database.db` 和 `uploads/`，展示時要用）。
- 新增 `tests/test_smoke.py`（pytest）：
  - fixture：把 `database.db` 複製到 `tmp_path`，讓 app 指向這個副本。
  - 分別用 `c001@gmail.com`、`t001@gmail.com`、`a001@gmail.com`（密碼 `123123`）登入。
  - 對每個角色可以進入的 GET 頁面發請求，斷言狀態碼是 200 或 302、沒有 500。需要 id 的路由，就從資料庫撈一筆現有的 id 來用。
  - 另外寫一個測試，把每個模板裡用到的 `url_for` 都 render 一次（例如逐一打開所有頁面），用來抓漏改的 endpoint。
- 在 `requirements.txt` 加入 `pytest`。
- **完成標準：** `pytest` 全部通過，接著 commit。

### 步驟 1：抽出共用工具（行為不變）

| 項目 | 現況 | 修改方式 |
| --- | --- | --- |
| 權限檢查 | 46 處 `if session.get('user_role') != 'xxx': return redirect(...)`、28 處 `if 'user_id' not in session` | 改用 `@login_required`、`@role_required('admin')`。**保留原本的轉址目標**：沒登入轉 login；角色不符轉 index。原本回傳 JSON 401 的 API，改用 `@login_required(json=True)` 或另寫一個 `api_login_required` |
| 資料庫連線 | 92 次 `get_db_connection()`、151 次 `conn.close()` | 改用 `db.get_db()`，連線存在 `g`，由 `app.teardown_appcontext` 統一關閉。刪掉所有手動 `close()`；`commit()` 保留在原本的位置 |
| 查無資料 | 大量「查不到 → close → flash → redirect」 | 能統一的地方改用 helper，但**flash 文字和轉址目標要跟原本一樣** |
| 函式內 import | 15 處（`import datetime`、`import time`、`import re`、`import uuid`） | 全部移到檔案開頭。注意 `booking_create`、`member_orders`、`process_payment` 裡的 `import datetime` 會遮蔽外層的 `from datetime import datetime`，改成 `from datetime import date` 等明確寫法 |
| 時間 | 41 處 `datetime('now', 'localtime')` 寫死在 SQL 裡 | 抽成常數或 helper（例如 `NOW_SQL = "datetime('now', 'localtime')"`）。**不要**改存入的時區，以免舊資料的時間錯位 |
| 狀態字串 | `'pending'`、`'confirmed'`、`'in_progress'`、`'completed'`、`'cancelled'`、`'rejected'`、`'inquiry'`、`'modification_pending'` 等散落各處 | 集中到 `constants.py`（用 `class OrderStatus(str, Enum)` 或簡單的類別常數），資料庫裡的值不變 |
| 註解與殘留 | 21 處對話式註解（"But wait"、"User asked"、"Actually"、"Let's"、"REMOVED"、"DEBUG"）、被註解掉的程式、`print` 除錯（16 處） | 刪除；`print` 改用 `app.logger`。只保留說明「為什麼這樣做」的註解 |
| 重複程式行 | L871–872 重複的 `@app.route('/member/orders')`、L601–602 重複的 `start_time = ...`、`member_orders` 裡重複計算的 `today` | 刪除重複 |
| 上傳 | 4 處各自組檔名、存檔（大頭貼、證照、刊登新增、刊登編輯） | 集中到 `utils/uploads.save_upload(file, prefix)`。檔名改用 `werkzeug.utils.secure_filename` 再加上 uuid 前綴，避免中文或特殊檔名出問題；**已經存在的舊檔名不要動** |

**完成標準：** 測試通過；`app.py` 大約縮到 3,000 行；commit。

### 步驟 2：拆成 Blueprint（只搬移，不改邏輯）

- 建立 `pet_platform/__init__.py` 的 `create_app()`；`app.py` 只保留：
  ```python
  from pet_platform import create_app
  app = create_app()
  if __name__ == '__main__':
      app.run(debug=True, port=8000)
  ```
  （這是學生專題的本機開發，`debug=True` 可以保留。）
- 依網址前綴把路由搬進 6 個 Blueprint。**網址完全不變**；Blueprint 不另外設 `url_prefix`，或設定的前綴要剛好等於原本的網址。
- 全域搜尋 `templates/` 和 Python 裡的所有 `url_for('...')` 與 `redirect(url_for(...))`，改成帶 Blueprint 前綴的名稱，例如 `url_for('admin.admin_order_tracking')`。模板裡用 JS 寫死的網址（例如 `fetch('/api/...')`）不用改。
- `@app.template_filter('utc8')`、`@app.context_processor inject_sidebar_counts` 改成在 `create_app()` 裡註冊。
- `secret_key`：改從環境變數 `SECRET_KEY` 讀；沒有就用固定的開發用字串，這樣重啟後才不會把所有人登出。
- **完成標準：** 測試通過（特別是 `url_for` 的測試）；`app.py` 小於 40 行；commit。

### 步驟 3：業務邏輯搬到 service，並修正兩個邏輯錯誤

路由函式只負責：讀取 request、呼叫 service、決定 flash 或 render 或 JSON。**目標：沒有任何超過 60 行的路由函式。**

優先處理的大函式（重構前的行數）：

| 函式 | 行數 | 搬到 |
| --- | --- | --- |
| `admin_statistics_dashboard` | 195 | `stats_service`：每個指標寫成一個函式 |
| `member_orders` | 184 | `order_service.list_orders(user_id, role, scope, start_date, end_date)` |
| `process_payment` | 134 | `payment_service.pay_order()`（模擬邏輯保留原樣，移除 `time.sleep(1)` 可以，但不是必要） |
| `admin_refunds` | 127 | `payment_service` |
| `trainer_order_action` | 118 | `order_service.transition()` |
| `api_cancel_order` | 113 | `order_service.cancel()` |
| `inject_sidebar_counts` | 102 | `stats_service.sidebar_counts(user_id, role)` |
| `trainer_listing_edit`、`trainer_account`、`trainer_orders`、`booking_create` | 約 96–101 | 對應的 service |

**要修正的邏輯錯誤（只修這兩個，修完各加一個測試）：**

1. **訂單狀態機沒有檢查前一個狀態。** 在 `trainer_order_action` 裡，`complete` 不檢查目前狀態，所以未付款的訂單也能直接完成並撥款；`start` 只擋「已付款但未確認」，完全沒付款的反而放行。改成用轉換表，並用條件式更新：
   ```python
   TRANSITIONS = {
       ('trainer', 'accept'):   ({PENDING},            CONFIRMED),
       ('trainer', 'reject'):   ({PENDING},            REJECTED),
       ('trainer', 'start'):    ({CONFIRMED},          IN_PROGRESS),  # 另外要求 payment_status='paid' 且 payment_confirmed=1
       ('trainer', 'complete'): ({IN_PROGRESS},        COMPLETED),
       ('member',  'cancel'):   ({PENDING, CONFIRMED}, CANCELLED),
   }
   # UPDATE orders SET status=? WHERE id=? AND status IN (...)；rowcount 為 0 就代表狀態不對
   ```
   接單時的時段重疊檢查（含 `force` 參數）要保留。
   > 注意：假資料裡有些已完成的訂單是直接標成完成的，這個修正**不要**回頭改舊資料。
2. **呼叫了不存在的函式。** 約 L2428 呼叫 `get_platform_commission_rate()`，但這個函式不存在，錯誤會被 `except` 吞掉，所以「款項已發放」的訊息不會出現。改成 `get_trainer_commission_rate(order['seller_id'])`，`except` 改成記錄日誌並 flash 錯誤。

另外：`process_order_payment_to_trainer` 和 `update_platform_balance` 目前各開一個連線、分開 commit。搬到 `ledger_service` 時，改成共用 `g.db`、在同一個 commit 裡完成。金額型別不用改。

**完成標準：** 測試通過，兩個新測試也通過；沒有超過 60 行的路由函式；commit。

### 步驟 4：合併重複

- `member_orders` 和 `trainer_orders` 裡的即將到來、歷史、待確認三段查詢，SELECT 和 JOIN 幾乎相同 → 合成一個 `list_orders()`，用參數決定 WHERE。同時移除 `member_orders` 開頭查完全部訂單又重開連線的多餘查詢（先確認那個結果有沒有被用到）。
- 銀行帳戶目前有 6 個路由、分散在 `user_bank_accounts` 和 `trainer_bank_accounts` 兩張表：`/api/bank_accounts/<id>` DELETE、`/member/bank_account` POST、`/member/bank_account/<id>/delete`、`/api/user/bank-accounts` GET/POST、`/api/user/bank-accounts/<id>` DELETE。程式碼合併成一組共用函式；**舊網址先保留**，改成呼叫同一份實作，避免前端壞掉。資料表先不要合併。
- `inject_sidebar_counts` 的十幾個 COUNT 盡量合成一個查詢（用 `SUM(CASE WHEN ...)`）。
- `ensure_platform_tables()` 和 `ensure_reschedule_tables()` 的 CREATE TABLE 搬進 `schema.sql`，讓 `schema.sql` 跟實際的 24 張表一致（可以用 `sqlite3 database.db .schema` 對照）。新增 `flask init-db` 指令；原本執行時呼叫 ensure 的地方，等確認表都存在之後再移除。
- **完成標準：** 測試通過；後端總行數比重構前少大約 25%；commit。

## 3. 不要做的事

- 不要改模擬付款的流程，也不要接真實金流。
- 不要改 README 的測試帳密、`database.db` 的資料或 `uploads/` 裡已有的檔案。
- 不要改網址、模板版面、CSS。
- 不要引入 ORM、前端框架、Redis、WebSocket。
- 不要改資料庫欄位型別（例如 REAL 改 INTEGER），以免影響現有資料。

## 4. 可選（使用者確認後才做）

以下是第一輪會議提到、跟模擬無關的小問題，修改量都不大。**先問使用者要不要做：**

- 聊天室（`templates/chat_snippet.html`，約 L420–444、L500–536）和評價列表（`trainer_orders.html`，約 L515–532）用 `innerHTML` 插入使用者輸入的文字 → 改用 `textContent`，或先做 HTML escape。
- `api_cancel_order` 用 `bank_account_id` 查 `user_bank_accounts` 時沒有加上 `AND user_id = ?`。
- 取消訂單時執行 `UPDATE orders SET paid_amount = paid_amount + total_price`，邏輯可疑，要先確認原意。
- 刪除會員（`admin_member_delete`）只刪了 users 和 trainer_applications，會留下孤兒訂單。
