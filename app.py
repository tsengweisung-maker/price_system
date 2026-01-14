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

# === 1. 頁面設定 (新增：強制預設展開側邊欄) ===
st.set_page_config(
    page_title="士電牌價查詢系統", 
    layout="wide",
    initial_sidebar_state="expanded"  # <-- 強制展開，避免預設縮起
)

# === CSS: 介面優化 (修正：找回側邊欄開關按鈕) ===
st.markdown("""
<style>
/* 1. 隱藏右上角漢堡選單 (三個點點) */
#MainMenu {visibility: hidden;}

/* 2. 隱藏頁尾 "Made with Streamlit" */
footer {visibility: hidden;}

/* 3. [修正] 必須顯示 header，否則左上角的「展開側邊欄」箭頭會消失 */
header {visibility: visible !important;}

/* 4. 但我們可以把 header 裡面的彩虹裝飾條隱藏，保持簡潔 */
[data-testid="stDecoration"] {display: none;}

/* 5. 隱藏表格的工具列 */
[data-testid="stElementToolbar"] { display: none; }

/* 6. 隱藏右下角開發者按鈕 */
.stAppDeployButton {display: none;}
[data-testid="stManageAppButton"] {display: none;}

/* 強制表格標頭 (Header) 置中 */
th { text-align: center !important; }

/* 調整搜尋框的大小與字體 */
input[type="text"] { font-size: 1.2rem; }

/* 側邊欄計算機樣式 */
.calc-box {
    background-color: #f0f2f6;
    padding: 15px;
    border-radius: 10px;
    margin-bottom: 20px;
    border: 1px solid #d1d5db;
}
.product-title {
    font-weight: bold;
    color: #1f77b4;
    font-size: 1.1rem;
    margin-bottom: 5px;
}
.price-tag {
    font-size: 1rem;
    color: #333;
    margin-bottom: 15px;
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
DISPLAY_COLS = ['規格', '牌價', '經銷價', '說明', '訂購品(V)']

# === Session State 初始化 ===
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user_email' not in st.session_state:
    st.session_state.user_email = ""
if 'real_name' not in st.session_state:
    st.session_state.real_name = ""
if 'login_attempts' not in st.session_state:
    st.session_state.login_attempts = 0

# === 計算機相關 State ===
if 'selected_product' not in st.session_state:
    st.session_state.selected_product = None 
if 'input_discount' not in st.session_state:
    st.session_state.input_discount = 0.0
if 'input_price' not in st.session_state:
    st.session_state.input_price = 0.0

# === 連線函式 ===
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    elif os.path.exists('service_account.json'):
        creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        return gspread.authorize(creds)
    else:
        return None

# === 工具函式 ===
def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_log(action, user_email, note=""):
    client = get_client()
    if not client: return
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        try:
            ws = sh.worksheet("Logs")
        except:
            return 
        ws.append_row([get_tw_time(), user_email, action, note])
    except:
        pass

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
#  🧮 雙向計算邏輯
# ==========================================
def update_price_from_discount():
    base_price = st.session_state.selected_product['price']
    discount = st.session_state.input_discount
    new_price = base_price * (discount / 100)
    st.session_state.input_price = round(new_price)

def update_discount_from_price():
    base_price = st.session_state.selected_product['price']
    price = st.session_state.input_price
    if base_price > 0:
        new_discount = (price / base_price) * 100
        st.session_state.input_discount = round(new_discount, 2)
    else:
        st.session_state.input_discount = 0.0

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
                        with st.spinner("正在驗證身分，請稍候..."):
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

    # --- 2. 側邊欄 (含計算機) ---
    with st.sidebar:
        greeting = get_greeting()
        st.write(f"👤 **{st.session_state.real_name}**，{greeting}")
        
        # === 🧮 業務計算機 ===
        st.markdown("---")
        st.subheader("🧮 業務試算")

        if st.session_state.selected_product:
            p = st.session_state.selected_product
            st.markdown(f"""
            <div class="calc-box">
                <div class="product-title">{p['spec']}</div>
                <div class="price-tag">說明: {p['desc']}</div>
                <div class="price-tag"><b>經銷價: ${p['price']:,.0f}</b></div>
            </div>
            """, unsafe_allow_html=True)
            
            st.number_input(
                "販售折數 (%)", 
                min_value=0.0, max_value=200.0, step=1.0, 
                key="input_discount", on_change=update_price_from_discount
            )
            st.number_input(
                "販售價格 ($)", 
                min_value=0.0, step=100.0, 
                key="input_price", on_change=update_discount_from_price
            )
            st.caption("💡 提示：輸入任一欄位，系統會自動換算另一欄。")
            
        else:
            st.info("👈 請在右側搜尋產品後，點擊「試算」按鈕，即可在此計算報價。")

        # 原本的功能區
        st.markdown("---")
        with st.expander("🔑 修改密碼"):
            new_pwd = st.text_input("新密碼", type="password")
            if st.button("確認修改"):
                if new_pwd:
                    if change_password(st.session_state.user_email, new_pwd):
                        st.success("密碼已更新！")
                    else: st.error("修改失敗")
                else: st.warning("密碼不能為空")
        
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.selected_product = None
            st.rerun()

    # --- 3. 主查詢介面 ---
    st.title("🔍 士林電機FA 2026年經銷牌價查詢系統")
    update_date = get_update_date()
    if update_date: st.caption(f"📅 資料庫最後更新：{update_date}")
    st.markdown("---")

    df = load_data()

    if not df.empty:
        search_term = st.text_input("輸入關鍵字搜尋", "", placeholder="例如: FX5U / SDC / 馬達")
        
        display_df = df.copy()
        if search_term:
            valid_search = [c for c in SEARCH_COLS if c in display_df.columns]
            mask = display_df[valid_search].apply(lambda x: x.str.contains(search_term, case=False, na=False)).any(axis=1)
            display_df = display_df[mask]

        final_cols = [c for c in DISPLAY_COLS if c in display_df.columns]
        
        if not display_df.empty and final_cols:
            final_df = display_df[final_cols].copy()
            for col in ['牌價', '經銷價']:
                if col in final_df.columns:
                    final_df[col] = final_df[col].apply(clean_currency)

            st.info(f"搜尋結果：共 {len(final_df)} 筆 (點擊左側「試算」按鈕可進行報價)")
            
            # === 自定義表格顯示 (加入按鈕) ===
            cols = st.columns([1, 2, 1.5, 1.5, 2, 1])
            fields = ["操作", "規格", "牌價", "經銷價", "說明", "訂購"]
            for col, field in zip(cols, fields):
                col.markdown(f"**{field}**")
            st.markdown("---")

            for index, row in final_df.iterrows():
                spec = str(row['規格']) if pd.notna(row['規格']) else ""
                list_price = f"{row['牌價']:,.0f}" if pd.notna(row['牌價']) else ""
                dist_price_val = row['經銷價'] if pd.notna(row['經銷價']) else 0
                dist_price_str = f"{dist_price_val:,.0f}" if dist_price_val else ""
                desc = str(row['說明']) if pd.notna(row['說明']) else ""
                order_mark = str(row['訂購品(V)']) if pd.notna(row['訂購品(V)']) else ""

                c1, c2, c3, c4, c5, c6 = st.columns([1, 2, 1.5, 1.5, 2, 1])
                
                if c1.button("試算", key=f"btn_{index}"):
                    st.session_state.selected_product = {
                        'spec': spec,
                        'desc': desc,
                        'price': dist_price_val
                    }
                    st.session_state.input_discount = 100.0
                    st.session_state.input_price = float(dist_price_val)
                    st.rerun()

                c2.write(spec)
                c3.write(list_price)
                c4.write(dist_price_str)
                c5.write(desc)
                c6.write(order_mark)
                st.markdown("<div style='margin: -15px 0px;'></div><hr style='margin: 5px 0px;'>", unsafe_allow_html=True)

        else:
            if search_term: st.warning("查無資料")
    else:
        st.error("資料庫連線異常，請稍後再試。")

if __name__ == "__main__":
    try:
        main_app()
    except Exception as e:
        st.error("系統暫時忙碌中，請重新整理或聯繫管理員。")