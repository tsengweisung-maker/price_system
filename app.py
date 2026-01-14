import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import re
import bcrypt
import smtplib
from email.mime.text import MIMEText
import random
import string
import time
from datetime import datetime, timezone, timedelta

# === 1. 頁面設定 (手機優化基礎) ===
st.set_page_config(
    page_title="士電牌價查詢系統", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# === CSS: 賈伯斯風格介面優化 ===
st.markdown("""
<style>
/* 隱藏不必要的元素 */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: visible !important;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stElementToolbar"] { display: none; }
.stAppDeployButton {display: none;}
[data-testid="stManageAppButton"] {display: none;}

/* 全域字體優化 (更適合閱讀) */
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

/* 📱 手機版卡片設計 (Card UI) */
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    padding: 15px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    background-color: white;
    margin-bottom: 10px;
    transition: transform 0.2s;
}

/* 規格標題 (大字體) */
.card-spec {
    font-size: 1.3rem;
    font-weight: 700;
    color: #1f1f1f;
    margin-bottom: 5px;
}

/* 價格標籤 (強調) */
.card-price {
    font-size: 1.1rem;
    font-weight: 600;
    color: #0068c9; /* 士電藍 */
}

/* 說明文字 (次要資訊) */
.card-desc {
    font-size: 0.9rem;
    color: #666;
    margin-top: 5px;
    line-height: 1.4;
}

/* 側邊欄計算機美化 */
.calc-box {
    background-color: #f8f9fa;
    padding: 16px;
    border-radius: 12px;
    border: 1px solid #dee2e6;
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
#  🔐 雲端資安設定 & 全域變數
# ==========================================
if "email" in st.secrets:
    SMTP_EMAIL = st.secrets["email"]["smtp_email"]
    SMTP_PASSWORD = st.secrets["email"]["smtp_password"]
else:
    SMTP_EMAIL = ""
    SMTP_PASSWORD = ""

GOOGLE_SHEET_NAME = '經銷牌價表_資料庫'
SEARCH_COLS = ['NO.', '規格', '說明']
# 這裡不需要 DISPLAY_COLS 了，因為我們用卡片顯示

# === Session State 初始化 ===
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_email' not in st.session_state: st.session_state.user_email = ""
if 'real_name' not in st.session_state: st.session_state.real_name = ""
if 'login_attempts' not in st.session_state: st.session_state.login_attempts = 0

if 'selected_product' not in st.session_state: st.session_state.selected_product = None 
if 'input_discount' not in st.session_state: st.session_state.input_discount = 0.0
if 'input_price' not in st.session_state: st.session_state.input_price = 0

# === 連線與工具函式 ===
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    elif os.path.exists('service_account.json'):
        creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        return gspread.authorize(creds)
    else: return None

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_log(action, user_email, note=""):
    client = get_client()
    if not client: return
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        try: ws = sh.worksheet("Logs")
        except: return 
        ws.append_row([get_tw_time(), user_email, action, note])
    except: pass

def get_greeting():
    tw_tz = timezone(timedelta(hours=8))
    current_hour = datetime.now(tw_tz).hour
    if 5 <= current_hour < 11: return "早安 ☀️"
    elif 11 <= current_hour < 18: return "你好 👋"
    elif 18 <= current_hour < 23: return "晚安 🌙"
    else: return "夜深了，不要太累了 ☕"

def check_password(plain_text, hashed_text):
    try: return bcrypt.checkpw(plain_text.encode('utf-8'), hashed_text.encode('utf-8'))
    except: return False

def hash_password(plain_text):
    return bcrypt.hashpw(plain_text.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def generate_random_password(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))

def send_reset_email(to_email, new_password):
    if not SMTP_EMAIL or not SMTP_PASSWORD: return False, "系統未設定寄信信箱。"
    subject = "【士林電機FA】密碼重置通知"
    body = f"您好：\n您的系統密碼已重置。\n新密碼為：{new_password}\n請使用此密碼登入後，盡快修改為您習慣的密碼。"
    msg = MIMEText(body); msg['Subject'] = subject; msg['From'] = SMTP_EMAIL; msg['To'] = to_email
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo(); smtp.starttls(); smtp.login(SMTP_EMAIL, SMTP_PASSWORD); smtp.send_message(msg)
        return True, "信件發送成功"
    except Exception as e: return False, "寄信失敗，請稍後再試。"

@st.cache_data(ttl=600)
def get_update_date():
    client = get_client()
    if not client: return ""
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        ws = sh.worksheet("Users")
        date_val = ws.cell(1, 4).value
        return date_val if date_val else "未知"
    except: return "未知"

def login(email, password):
    client = get_client()
    if not client: return False, "連線失敗"
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        ws = sh.worksheet("Users")
        users = ws.get_all_records()
        for user in users:
            if str(user.get('email')).strip() == email.strip():
                if check_password(password, str(user.get('password'))):
                    found_name = str(user.get('name')) if user.get('name') else email
                    write_log("登入成功", email)
                    return True, found_name
                else:
                    write_log("登入失敗", email, "密碼錯誤")
                    return False, "密碼錯誤"
        write_log("登入失敗", email, "帳號不存在")
        return False, "此 Email 尚未註冊"
    except Exception as e: return False, "登入過程錯誤"

def change_password(email, new_password):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        ws = sh.worksheet("Users")
        cell = ws.find(email)
        if cell:
            ws.update_cell(cell.row, 2, hash_password(new_password))
            write_log("修改密碼", email, "使用者自行修改")
            return True
        return False
    except: return False

def reset_password_flow(target_email):
    client = get_client()
    if not client: return False, "連線失敗"
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        ws = sh.worksheet("Users")
        try: cell = ws.find(target_email.strip())
        except gspread.exceptions.CellNotFound: return False, "此 Email 尚未註冊"
        new_pw = generate_random_password()
        sent, msg = send_reset_email(target_email, new_pw)
        if not sent: return False, msg
        ws.update_cell(cell.row, 2, hash_password(new_pw))
        write_log("重置密碼", target_email, "忘記密碼重置")
        return True, "重置成功！新密碼已寄送到您的信箱。"
    except Exception as e: return False, "重置失敗"

@st.cache_data(ttl=600)
def load_data():
    client = get_client()
    if not client: return pd.DataFrame()
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        data = sh.sheet1.get_all_records()
        return pd.DataFrame(data).astype(str)
    except: return pd.DataFrame()

def clean_currency(val):
    if not val or pd.isna(val): return None
    val_str = str(val)
    clean_str = re.sub(r'[^\d.]', '', val_str)
    try: return float(clean_str)
    except ValueError: return None

# ==========================================
#  🧮 雙向計算邏輯 (修正：整數與小數位)
# ==========================================
def update_price_from_discount():
    if st.session_state.selected_product and st.session_state.selected_product['price']:
        base_price = st.session_state.selected_product['price']
        discount = st.session_state.input_discount
        new_price = base_price * (discount / 100)
        # [修改] 價格強制轉為整數 (int)
        st.session_state.input_price = int(round(new_price))

def update_discount_from_price():
    if st.session_state.selected_product and st.session_state.selected_product['price']:
        base_price = st.session_state.selected_product['price']
        price = st.session_state.input_price
        if base_price > 0:
            new_discount = (price / base_price) * 100
            # [修改] 折數保留兩位小數
            st.session_state.input_discount = round(new_discount, 2)
        else:
            st.session_state.input_discount = 0.0

# [關鍵] 按鈕回調函式
def select_product_callback(spec, desc, price):
    st.session_state.selected_product = {
        'spec': spec,
        'desc': desc,
        'price': price
    }
    st.session_state.input_discount = 100.00
    st.session_state.input_price = int(price) # 初始值也是整數

# ==========================================
#               主程式
# ==========================================
def main_app():
    # --- 1. 登入畫面 ---
    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.header("🔒 士林電機FA 2026年經銷牌價查詢系統")
            
            if st.session_state.login_attempts >= 3:
                st.error("⚠️ 登入失敗次數過多，請重新整理網頁後再試。")
                return

            tab1, tab2 = st.tabs(["會員登入", "忘記密碼"])
            default_email = st.query_params.get("email", "")

            with tab1:
                with st.form("login_form"):
                    input_email = st.text_input("Email", value=default_email)
                    input_pass = st.text_input("密碼", type="password")
                    submitted = st.form_submit_button("登入", use_container_width=True)
                    if submitted:
                        with st.spinner("正在驗證身分..."):
                            success, result = login(input_email, input_pass)
                            if success:
                                st.session_state.logged_in = True
                                st.session_state.user_email = input_email
                                st.session_state.real_name = result
                                st.session_state.login_attempts = 0
                                st.rerun()
                            else:
                                st.session_state.login_attempts += 1
                                st.error(f"{result} (剩餘: {3 - st.session_state.login_attempts})")
            with tab2:
                st.caption("系統將發送新密碼至您的 Email")
                with st.form("reset_form"):
                    reset_email = st.text_input("請輸入註冊 Email", value=default_email)
                    reset_submit = st.form_submit_button("發送重置信", use_container_width=True)
                    if reset_submit:
                        if reset_email:
                            with st.spinner("系統處理中..."):
                                success, msg = reset_password_flow(reset_email)
                                if success: st.success(msg)
                                else: st.error(msg)
                        else: st.warning("請輸入 Email")
        return

    # --- 2. 側邊欄 ---
    with st.sidebar:
        greeting = get_greeting()
        st.write(f"👤 **{st.session_state.real_name}**，{greeting}")
        
        st.markdown("---")
        st.subheader("🧮 業務試算")

        if st.session_state.selected_product:
            p = st.session_state.selected_product
            if not p['price']:
                st.warning("⚠️ 此商品無經銷價")
            else:
                st.markdown(f"""
                <div class="calc-box">
                    <div class="product-title">{p['spec']}</div>
                    <div class="card-desc">{p['desc']}</div>
                    <div class="card-price" style="margin-top:10px;">經銷價: ${p['price']:,.0f}</div>
                </div>
                """, unsafe_allow_html=True)
                
                # [修改] 格式化輸入框
                st.number_input(
                    "販售折數 (%)", 
                    min_value=0.0, max_value=200.0, step=0.5, 
                    format="%.2f", # <-- 強制兩位小數
                    key="input_discount", 
                    on_change=update_price_from_discount
                )
                
                st.number_input(
                    "販售價格 ($)", 
                    min_value=0, step=100, 
                    format="%d", # <-- 強制整數 (無小數點)
                    key="input_price", 
                    on_change=update_discount_from_price
                )
                st.caption("💡 雙向自動換算 (價格已取整)")
        else:
            st.info("👈 請在右側點擊產品「試算」按鈕")

        st.markdown("---")
        with st.expander("🔑 修改密碼"):
            new_pwd = st.text_input("新密碼", type="password")
            if st.button("確認修改"):
                if new_pwd:
                    if change_password(st.session_state.user_email, new_pwd): st.success("已更新！")
                    else: st.error("失敗")
        
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.selected_product = None
            st.rerun()

    # --- 3. 主查詢介面 (手機版大改版) ---
    st.title("🔍 士林電機FA 2026年經銷牌價")
    update_date = get_update_date()
    if update_date: st.caption(f"📅 資料庫最後更新：{update_date}")
    st.markdown("---")

    df = load_data()

    if not df.empty:
        search_term = st.text_input("輸入關鍵字搜尋", "", placeholder="例如: FX5U / SDC / 馬達")
        
        display_df = df.copy()
        if search_term:
            valid_search = [c for c in ['NO.', '規格', '說明'] if c in display_df.columns]
            mask = display_df[valid_search].apply(lambda x: x.str.contains(search_term, case=False, na=False)).any(axis=1)
            display_df = display_df[mask]

        # 這裡我們只抓需要的欄位
        final_cols = ['規格', '牌價', '經銷價', '說明', '訂購品(V)']
        final_cols = [c for c in final_cols if c in display_df.columns]
        
        if not display_df.empty and final_cols:
            final_df = display_df[final_cols].copy()
            for col in ['牌價', '經銷價']:
                if col in final_df.columns:
                    final_df[col] = final_df[col].apply(clean_currency)

            result_count = len(final_df)
            
            # === 📱 賈伯斯模式：自動卡片排列 ===
            if result_count > 50:
                st.info(f"搜尋結果：共 {result_count} 筆 (請縮小範圍至 50 筆以內以顯示卡片)")
                # 超過 50 筆還是用表格，避免手機滑到死
                st.dataframe(final_df, use_container_width=True, hide_index=True)
            else:
                st.success(f"搜尋結果：共 {result_count} 筆")
                
                # 遍歷每一筆資料，畫出卡片
                for index, row in final_df.iterrows():
                    spec = str(row['規格']) if pd.notna(row['規格']) else ""
                    dist_price_val = row['經銷價']
                    
                    if pd.isna(dist_price_val) or dist_price_val == "":
                        dist_price_val = None
                        price_display = "請洽詢"
                    else:
                        price_display = f"${dist_price_val:,.0f}"

                    desc = str(row['說明']) if pd.notna(row['說明']) else ""
                    order_mark = "📦 訂購品" if str(row.get('訂購品(V)', '')).strip() == 'V' else ""

                    # === 這是「卡片」容器 ===
                    with st.container():
                        # 使用 columns 做佈局：左邊是資訊，右邊是按鈕
                        c_info, c_btn = st.columns([3, 1])
                        
                        with c_info:
                            # 產品規格 (標題)
                            st.markdown(f'<div class="card-spec">{spec}</div>', unsafe_allow_html=True)
                            # 價格與標記
                            st.markdown(f'<div class="card-price">{price_display} <span style="font-size:0.8rem;color:#999;font-weight:normal;">(經銷價)</span> {order_mark}</div>', unsafe_allow_html=True)
                            # 說明
                            if desc:
                                st.markdown(f'<div class="card-desc">{desc}</div>', unsafe_allow_html=True)
                        
                        with c_btn:
                            # 垂直置中按鈕有點難，我們用空行來推
                            st.write("") 
                            if dist_price_val is not None:
                                c_btn.button(
                                    "試算", 
                                    key=f"btn_{index}",
                                    use_container_width=True, # 按鈕填滿寬度，手指好按
                                    on_click=select_product_callback,
                                    args=(spec, desc, float(dist_price_val))
                                )
                            else:
                                c_btn.button("試算", key=f"btn_{index}", disabled=True, use_container_width=True)
                        
                        st.markdown("---") # 卡片分隔線

        else:
            if search_term: st.warning("查無資料")
    else:
        st.error("資料庫連線異常，請稍後再試。")

if __name__ == "__main__":
    main_app()