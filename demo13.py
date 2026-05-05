import streamlit as st
import fitz  # PyMuPDF
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold, GenerationConfig
from PIL import Image
import io
import time
import datetime
import markdown
import re
import os
import pickle

# --- 1. การตั้งค่าเริ่มต้น และ Session State ---
keys_to_init = {
    'processed_data': {}, 
    'page_idx': 0,
    'is_running': False,
    'stop_clicked': False,
    'full_summaries': "",
    'exhausted_models': {},
    'current_active_model': None,
    'flash_models_list': [], 
    'pdf_bytes': None,
    'pdf_name': "",
    'selected_pages': [],
    'show_reset_confirm': False,
    'settings_changed_alert': False,
    'last_settings': {},
    'show_start_popup': False, 
    'estimated_tokens_used': 0,
    'status_mode': 'blue',
    'user_api_key': "",
    
    # --- ตัวแปรใหม่สำหรับระบบ Global Context & Minimap ---
    'phase': 'idle', # idle, global_scan, detail_scan
    'global_data': {}, # เก็บ {'topic': '', 'summary': ''} ของแต่ละหน้า
    'global_context_text': "", # ข้อความภาพรวมทั้งหมดที่รวมร่างแล้ว
    'global_map_list': [] # สารบัญรวมทั้งหมดเพื่อทำ Minimap
}

for k, v in keys_to_init.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.set_page_config(page_title="PDF Note Space 🩺", layout="wide")

# --- 2. Custom CSS & Minimalist Design ---
st.markdown("""
<style>
    .stApp { background-color: #F4F7F6; }
    .main-header { font-size: 2.2rem; font-weight: 800; color: #1A365D; margin-bottom: 1rem; }
    .stButton>button { border-radius: 8px; transition: all 0.3s; font-weight: 600; }
    .stButton>button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    .edit-box { border: 1px solid #E2E8F0; border-radius: 10px; padding: 15px; background: #FFFFFF; font-size: 14px; line-height: 1.6;}
    
    /* Animation & Status Colors */
    @keyframes pulse-blue { 0% { box-shadow: 0 0 0 0 rgba(49, 130, 206, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(49, 130, 206, 0); } 100% { box-shadow: 0 0 0 0 rgba(49, 130, 206, 0); } }
    @keyframes pulse-yellow { 0% { box-shadow: 0 0 0 0 rgba(214, 158, 46, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(214, 158, 46, 0); } 100% { box-shadow: 0 0 0 0 rgba(214, 158, 46, 0); } }
    @keyframes pulse-red { 0% { box-shadow: 0 0 0 0 rgba(229, 62, 62, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(229, 62, 62, 0); } 100% { box-shadow: 0 0 0 0 rgba(229, 62, 62, 0); } }
    @keyframes pulse-purple { 0% { box-shadow: 0 0 0 0 rgba(128, 90, 213, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(128, 90, 213, 0); } 100% { box-shadow: 0 0 0 0 rgba(128, 90, 213, 0); } }
    
    .status-blue { background: #EBF8FF; border-left: 5px solid #3182CE; color: #2B6CB0; animation: pulse-blue 2s infinite; padding: 15px; border-radius: 10px; margin-bottom: 15px;}
    .status-yellow { background: #FFFFF0; border-left: 5px solid #D69E2E; color: #B7791F; animation: pulse-yellow 2s infinite; padding: 15px; border-radius: 10px; margin-bottom: 15px;}
    .status-red { background: #FFF5F5; border-left: 5px solid #E53E3E; color: #C53030; animation: pulse-red 2s infinite; padding: 15px; border-radius: 10px; margin-bottom: 15px;}
    .status-purple { background: #FAF5FF; border-left: 5px solid #805AD5; color: #553C9A; animation: pulse-purple 2s infinite; padding: 15px; border-radius: 10px; margin-bottom: 15px;}
</style>
""", unsafe_allow_html=True)

# --- 3. ฟังก์ชันอัจฉริยะ (Helper Functions) และระบบ Auto-save ---

# ฟังก์ชันเซฟข้อมูลลงไฟล์
def save_workspace():
    data_to_save = {
        'pdf_bytes': st.session_state.pdf_bytes,
        'pdf_name': st.session_state.pdf_name,
        'processed_data': st.session_state.processed_data,
        'global_data': st.session_state.global_data,
        'global_context_text': st.session_state.global_context_text,
        'global_map_list': st.session_state.global_map_list,
        'selected_pages': st.session_state.selected_pages,
        'full_summaries': st.session_state.full_summaries,
        'user_api_key': st.session_state.user_api_key
    }
    try:
        with open("autosave_workspace.pkl", "wb") as f:
            pickle.dump(data_to_save, f)
    except Exception:
        pass

# ฟังก์ชันโหลดข้อมูลกลับมาเมื่อเน็ตหลุด/รีเฟรช
def load_workspace():
    if os.path.exists("autosave_workspace.pkl"):
        try:
            with open("autosave_workspace.pkl", "rb") as f:
                data = pickle.load(f)
                for k, v in data.items():
                    st.session_state[k] = v
            return True
        except Exception:
            return False
    return False

# ฟังก์ชันลบไฟล์เซฟ (เมื่อกด Reset เปลี่ยนเอกสาร)
def clear_workspace():
    if os.path.exists("autosave_workspace.pkl"):
        try:
            os.remove("autosave_workspace.pkl")
        except Exception:
            pass

def get_best_available_model(models_list):
    current_time = time.time()
    st.session_state.exhausted_models = {k: v for k, v in st.session_state.exhausted_models.items() if v > current_time}
    preferred = ["gemini-2.5-flash-lite"]
    sorted_models = sorted(models_list, key=lambda x: next((i for i, p in enumerate(preferred) if p in x), 99))
    for m in sorted_models:
        if m not in st.session_state.exhausted_models: return m
    return None

def calc_dynamic_fontsize(text, rect_width, rect_height):
    if not text or rect_width <= 0 or rect_height <= 0: return 12
    area = rect_width * rect_height
    char_count = len(text)
    if char_count == 0: return 14
    estimated_size = (area / (char_count * 0.55)) ** 0.5
    return max(8, min(36, int(estimated_size))) 

def split_content_hq(text):
    c_txt, hy_txt, q_txt = "", "", ""
    temp = text
    if "High-Yield:" in temp:
        parts = temp.split("High-Yield:")
        c_txt = parts[0].strip()
        temp = parts[1]
        if "Quiz:" in temp:
            hq_parts = temp.split("Quiz:")
            hy_txt = "High-Yield:\n" + hq_parts[0].strip()
            q_txt = "Quiz:\n" + hq_parts[1].strip()
        else:
            hy_txt = "High-Yield:\n" + temp.strip()
    elif "Quiz:" in temp:
        parts = temp.split("Quiz:")
        c_txt = parts[0].strip()
        q_txt = "Quiz:\n" + parts[1].strip()
    else:
        c_txt = temp.strip()
    return c_txt, hy_txt, q_txt

def get_layout_preview(c_pos, hy_pos, q_pos):
    layout_map = {"ด้านขวา": [], "ด้านล่าง": [], "ด้านซ้าย": [], "ด้านบน": []}
    layout_map[c_pos].append("<span style='color: #2D3748;'><b>เนื้อหา</b></span>")
    layout_map[hy_pos].append("<span style='color: #E53E3E;'><b>High-Yield</b></span>")
    layout_map[q_pos].append("<span style='color: #3182CE;'><b>Quiz</b></span>")
    
    right_box = f"<div style='flex: 0.35; background: #F7FAFC; padding: 5px; font-size: 8px; border-left: 1px solid #E2E8F0;'>{'<br><br>'.join(layout_map['ด้านขวา'])}</div>" if layout_map['ด้านขวา'] else ""
    left_box = f"<div style='flex: 0.35; background: #F7FAFC; padding: 5px; font-size: 8px; border-right: 1px solid #E2E8F0;'>{'<br><br>'.join(layout_map['ด้านซ้าย'])}</div>" if layout_map['ด้านซ้าย'] else ""
    bottom_box = f"<div style='height: 40px; background: #F7FAFC; padding: 5px; font-size: 8px; border-top: 1px solid #E2E8F0; text-align: center;'>{' | '.join(layout_map['ด้านล่าง'])}</div>" if layout_map['ด้านล่าง'] else ""
    top_box = f"<div style='height: 40px; background: #F7FAFC; padding: 5px; font-size: 8px; border-bottom: 1px solid #E2E8F0; text-align: center;'>{' | '.join(layout_map['ด้านบน'])}</div>" if layout_map['ด้านบน'] else ""
    
    html_str = f"""<div style="width: 100%; height: 180px; border-radius: 10px; background: white; border: 2px solid #CBD5E0; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">{top_box}<div style="flex: 1; display: flex; flex-direction: row;">{left_box}<div style="flex: 1; background: #EDF2F7; border: 2px dashed #A0AEC0; margin: 8px; display: flex; align-items: center; justify-content: center; font-size: 12px; color: #718096; font-weight: bold; border-radius: 5px;">Slide</div>{right_box}</div>{bottom_box}</div>"""
    st.markdown(html_str, unsafe_allow_html=True)

# --- 4. Sidebar: Note Settings ---
with st.sidebar:
    st.markdown("<h2 style='color: #2D3748;'>🩺 Note Settings</h2>", unsafe_allow_html=True)
    is_locked = st.session_state.is_running
    
    api_input = st.text_input("🔑 ใส่ Gemini API Key:", type="password", value=st.session_state.user_api_key, disabled=is_locked)
    if api_input != st.session_state.user_api_key:
        st.session_state.user_api_key = api_input
        save_workspace() # บันทึก API Key ถาวร
        st.rerun()
        
    api_key = st.session_state.user_api_key
    
    if api_key:
        try:
            genai.configure(api_key=api_key)
            all_models = [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            flash_models = [m for m in all_models if "flash" in m.lower() or "pro" in m.lower()] 
            
            flash_models = sorted(flash_models, key=lambda x: 0 if "gemini-2.5-flash-lite" in x else 1)
            
            st.session_state.flash_models_list = flash_models
            best_model = get_best_available_model(flash_models)
            
            if is_locked:
                st.info("🔒 ระบบล็อกการตั้งค่าขณะรัน")
            else:
                display_options = []
                model_map = {}
                for m in flash_models:
                    status = f"⏳ [รอ]" if m in st.session_state.exhausted_models else "✅ [พร้อม]"
                    opt = f"{status} {m}"
                    display_options.append(opt)
                    model_map[opt] = m
                selected_display = st.selectbox("AI Model (สลับอัตโนมัติ):", display_options, index=0)
                st.session_state.current_active_model = model_map[selected_display]
        except: 
            st.error("API Key ไม่ถูกต้อง โปรดตรวจสอบให้แน่ใจว่าไม่มีช่องว่างหรือตัวอักษรแปลกปลอม")

    st.markdown("### 💳 Token Tracker (จำลอง)")
    token_used = st.session_state.estimated_tokens_used
    st.progress(min(token_used / 1000000, 1.0)) 
    st.caption(f"ใช้ไปแล้ว: **{token_used:,} Tokens**")

    st.divider()
    med_year = st.selectbox("ระดับ นสพ.", [2, 3, 4, 5, 6], index=2, disabled=is_locked)
    max_tokens = st.selectbox("กำหนด Max Output Tokens", [600, 1200, 1500], index=1, disabled=is_locked)
    
    st.markdown("### 📐 จัดสรรพื้นที่กระดาษ (Layout)")
    margin_right_pct = st.slider("เพิ่มพื้นที่ด้านขวา (%)", 0, 50, 25, disabled=is_locked)
    margin_bottom_pct = st.slider("เพิ่มพื้นที่ด้านล่าง (%)", 0, 50, 15, disabled=is_locked)
    
    pos_options = ["ด้านขวา", "ด้านล่าง", "ด้านซ้าย", "ด้านบน"]
    content_pos = st.selectbox("วาง [เนื้อหา] ไว้ที่:", pos_options, index=0, disabled=is_locked)
    hy_pos = st.selectbox("วาง [High-Yield] ไว้ที่:", pos_options, index=1, disabled=is_locked) 
    quiz_pos = st.selectbox("วาง [Quiz] ไว้ที่:", pos_options, index=0, disabled=is_locked)
    
    want_minimap = st.checkbox("เปิดใช้งาน Minimap (สารบัญรวม)", value=True, disabled=is_locked)
    
    st.markdown("### 📋 ข้อมูลที่ต้องการ")
    want_content = st.checkbox("คำอธิบายเนื้อหา", value=True, disabled=is_locked)
    want_summary = st.checkbox("สรุป High-Yield", value=True, disabled=is_locked)
    want_quiz = st.checkbox("Quiz", value=True, disabled=is_locked)
    quiz_count = 3
    want_answer = False
    if want_quiz:
        quiz_count = st.slider("จำนวนข้อ Quiz:", 1, 5, 3, disabled=is_locked)
        want_answer = st.checkbox("รวมเฉลย", value=True, disabled=is_locked)

    get_layout_preview(content_pos, hy_pos, quiz_pos)

    current_settings = {
        "year": med_year, "max_tok": max_tokens, 
        "m_right": margin_right_pct, "m_bottom": margin_bottom_pct,
        "c_pos": content_pos, "hy_pos": hy_pos, "q_pos": quiz_pos, "map": want_minimap,
        "content": want_content, "summary": want_summary, "quiz": want_quiz, "count": quiz_count, "ans": want_answer
    }
    if not is_locked and st.session_state.last_settings and current_settings != st.session_state.last_settings:
        st.session_state.settings_changed_alert = True
    st.session_state.last_settings = current_settings

# --- 5. หน้าหลัก: การจัดการไฟล์ ---
st.markdown("<div class='main-header'>📚 PDF Note Space: Smart Reader</div>", unsafe_allow_html=True)

if not st.session_state.pdf_bytes:
    # 🌟 ฟีเจอร์ใหม่: ตรวจจับไฟล์เซฟอัตโนมัติ
    if os.path.exists("autosave_workspace.pkl"):
        st.info("💾 **พบงานที่ทำค้างไว้!** (ระบบ Auto-save ป้องกันเน็ตหลุด/หน้าจอดับ)")
        if st.button("🔄 กู้คืนงานที่ทำค้างไว้", type="primary"):
            with st.spinner("กำลังโหลดข้อมูล..."):
                if load_workspace():
                    st.success("กู้คืนสำเร็จ!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("ไฟล์กู้คืนมีปัญหา หรือหมดอายุแล้ว")
    
    uploaded_file = st.file_uploader("อัปโหลดสไลด์อาจารย์ (PDF) - รองรับสูงสุด 400 หน้า", type="pdf")
    if uploaded_file:
        with st.status(f"กำลังนำเข้าไฟล์: {uploaded_file.name}...", expanded=True) as status:
            st.session_state.pdf_bytes = uploaded_file.getvalue()
            st.session_state.pdf_name = uploaded_file.name
            doc_tmp = fitz.open(stream=st.session_state.pdf_bytes, filetype="pdf")
            st.session_state.selected_pages = list(range(len(doc_tmp)))
            for i in range(len(doc_tmp)):
                st.session_state[f"sel_{i}"] = True
            
            # Initialize Minimap placeholders
            st.session_state.global_map_list = [f"Page {i+1}" for i in range(len(doc_tmp))]
            
            save_workspace() # บันทึกข้อมูลทันทีเมื่ออัปโหลดเสร็จ
            
            status.update(label=f"✅ อัปโหลดสำเร็จ: {uploaded_file.name}", state="complete")
        st.rerun()
else:
    st.info(f"📄 ไฟล์ปัจจุบัน: **{st.session_state.pdf_name}**")
    if st.button("🗑️ เปลี่ยนเอกสาร (Reset)"):
        st.session_state.show_reset_confirm = True

    if st.session_state.show_reset_confirm:
        st.warning("ยืนยันการล้างข้อมูลทั้งหมด?")
        c1, c2 = st.columns(2)
        if c1.button("✅ ยืนยัน", type="primary"):
            clear_workspace() # ลบไฟล์เซฟทิ้งด้วย
            for k in keys_to_init: del st.session_state[k]
            st.rerun()
        if c2.button("❌ ยกเลิก"):
            st.session_state.show_reset_confirm = False
            st.rerun()

# --- 6. ระบบเลือกหน้า (Page Customizer) ---
if st.session_state.pdf_bytes and not is_locked:
    doc_in = fitz.open(stream=st.session_state.pdf_bytes, filetype="pdf")
    total_pages = len(doc_in)
    
    if "expander_open" not in st.session_state: st.session_state.expander_open = True
    if st.session_state.expander_open:
        with st.expander("🖼️ เลือกว่าจะให้ AI ประมวลผลหน้าไหนบ้าง (Customize Pages)", expanded=True):
            st.info("💡 ทุกหน้าถูกเลือกไว้เป็นค่าเริ่มต้น")
            c_btn1, c_btn2, c_btn3 = st.columns([1,1,2])
            
            if c_btn1.button("✅ เลือกทั้งหมด"):
                st.session_state.selected_pages = list(range(total_pages))
                st.rerun()
            if c_btn2.button("❌ ไม่เลือกเลย"):
                st.session_state.selected_pages = []
                st.rerun()
            if c_btn3.button("💾 ยืนยันการเลือกหน้า", type="primary"):
                st.session_state.expander_open = False
                save_workspace() # บันทึกหลังเลือกหน้าเสร็จ
                st.rerun()
            
            st.write("---")
            cols_per_row = 5
            for row_idx in range(0, total_pages, cols_per_row):
                cols = st.columns(cols_per_row)
                for col_idx in range(cols_per_row):
                    page_num = row_idx + col_idx
                    if page_num < total_pages:
                        with cols[col_idx]:
                            page = doc_in[page_num]
                            pix = page.get_pixmap(dpi=40)
                            st.image(pix.tobytes("png"), use_container_width=True) 
                            
                            is_checked = st.checkbox(f"หน้า {page_num+1}", value=(page_num in st.session_state.selected_pages), key=f"sel_{page_num}")
                            if is_checked and page_num not in st.session_state.selected_pages:
                                st.session_state.selected_pages.append(page_num)
                            elif not is_checked and page_num in st.session_state.selected_pages:
                                st.session_state.selected_pages.remove(page_num)
    else:
        if st.button("⚙️ เปิดหน้าต่างเลือกหน้าอีกครั้ง"):
            st.session_state.expander_open = True
            st.rerun()

# --- 7. แถบสถานะส่วนบน (Animated Status) และ Pop-up คอนเฟิร์มก่อนรัน ---
if st.session_state.pdf_bytes:
    doc_in = fitz.open(stream=st.session_state.pdf_bytes, filetype="pdf")
    total_pages = len(doc_in)
    
    if st.session_state.settings_changed_alert and not st.session_state.is_running:
        st.warning("🔔 ตรวจพบการเปลี่ยนการตั้งค่า: หน้าถัดไปจะใช้รูปแบบใหม่ทันที")
        if st.button("รับทราบ"): st.session_state.settings_changed_alert = False

    if st.session_state.show_start_popup:
        with st.container():
            st.markdown("### 📊 สรุปข้อมูลก่อนเริ่มสร้างเอกสาร")
            pages_to_do = len([i for i in st.session_state.selected_pages if i not in st.session_state.processed_data])
            est_tokens = pages_to_do * max_tokens
            st.info(f"""
            - หน้าที่ต้องประมวลผลเพิ่ม: **{pages_to_do} หน้า**
            - คาดการณ์ Token ที่ต้องใช้ (Output): **~{est_tokens:,} Tokens**
            """)
            c_start1, c_start2 = st.columns(2)
            if c_start1.button("✅ ยืนยันรันงาน", type="primary"):
                if st.session_state.current_active_model in st.session_state.exhausted_models:
                    st.session_state.current_active_model = get_best_available_model(st.session_state.flash_models_list)
                
                # กำหนด Phase เริ่มต้น
                if st.session_state.phase == 'idle':
                    st.session_state.phase = 'global_scan'
                
                st.session_state.is_running = True
                st.session_state.stop_clicked = False
                st.session_state.show_start_popup = False
                st.rerun()
            if c_start2.button("❌ ยกเลิก"):
                st.session_state.show_start_popup = False
                st.rerun()
    
    action_placeholder = st.empty() 

    col_run1, col_ctrl2, col_ctrl3 = st.columns([1,1,1])
    if col_run1.button("🚀 Start / Continue", type="primary", disabled=is_locked):
        st.session_state.show_start_popup = True
        st.rerun()
    if col_ctrl2.button("🛑 Stop / Pause", disabled=not st.session_state.is_running):
        st.session_state.is_running = False
        st.session_state.stop_clicked = True
        st.rerun()

    # --- 8. E-BOOK READER & EDITING ---
    st.write("---")
    st.subheader(f"📖 Clinical E-Book: หน้า {st.session_state.page_idx + 1}")
    
    c_nav1, c_nav2, c_nav3 = st.columns([1, 2, 1])
    with c_nav1:
        if st.button("⬅️ หน้าก่อนหน้า") and st.session_state.page_idx > 0:
            st.session_state.page_idx -= 1
            st.rerun()
    with c_nav2:
        new_page = st.slider("กระโดดไปหน้า:", 1, total_pages, st.session_state.page_idx + 1, label_visibility="collapsed")
        if new_page - 1 != st.session_state.page_idx:
            st.session_state.page_idx = new_page - 1
            st.rerun()
    with c_nav3:
        if st.button("หน้าถัดไป ➡️") and st.session_state.page_idx < total_pages - 1:
            st.session_state.page_idx += 1
            st.rerun()

    curr = st.session_state.page_idx
    if curr in st.session_state.processed_data:
        data = st.session_state.processed_data[curr]
        col_v1, col_v2 = st.columns([1.2, 1])
        with col_v1:
            st.image(data["img"], use_container_width=True, caption="E-book preview")
        with col_v2:
            st.markdown("### 📝 บันทึกการเรียน")
            if f"editing_{curr}" not in st.session_state: st.session_state[f"editing_{curr}"] = False
            display_text = data["user_text"] if data["user_text"] else data["ai_text"]
            
            if st.session_state[f"editing_{curr}"]:
                edited = st.text_area("แก้ไขเนื้อหา:", value=display_text, height=400)
                ce1, ce2 = st.columns(2)
                if ce1.button("💾 ยืนยันการแก้ไข", type="primary"):
                    st.session_state.processed_data[curr]["user_text"] = edited
                    st.session_state[f"editing_{curr}"] = False
                    save_workspace() # Auto-save เมื่อแก้ไขเสร็จ
                    st.rerun()
                if ce2.button("🔄 คืนค่าต้นฉบับ AI"):
                    st.session_state.processed_data[curr]["user_text"] = ""
                    st.session_state[f"editing_{curr}"] = False
                    save_workspace() # Auto-save เมื่อรีเซ็ตข้อความ
                    st.rerun()
            else:
                formatted_display = display_text.replace("High-Yield:", "<br><b style='color: #C53030; font-size: 1.3em;'>🚨 High-Yield:</b>").replace("Quiz:", "<br><b style='color: #2B6CB0;'>📝 Quiz:</b>").replace("เฉลย:", "<br><b>💡 เฉลย:</b>")
                st.markdown(f"<div class='edit-box'>{formatted_display}</div>", unsafe_allow_html=True)
                if st.button("✏️ พิมพ์แก้ไขเนื้อหานี้"):
                    st.session_state[f"editing_{curr}"] = True
                    if st.session_state.is_running:
                        st.session_state.is_running = False 
                        st.warning("⚠️ ระบบหยุดรันชั่วคราวเพื่อให้แก้ไขได้สะดวก กด Continue เพื่อรันต่อ")
                    st.rerun()
    else:
        st.info(f"⏳ หน้าที่ {curr+1} ยังไม่ได้ประมวลผล (อยู่ในคิว)...")

    # --- 9. ปุ่มดาวน์โหลด PDF ---
    st.write("---")
    if len(st.session_state.processed_data) > 0:
        if st.button("📦 รวบรวมและดาวน์โหลด PDF (ฉบับอัปเดตแก้ไขล่าสุด)"):
            with st.spinner("กำลังประกอบร่างไฟล์ PDF ฉบับสมบูรณ์ (รอสักครู่นะครับ)..."):
                doc_out = fitz.open()
                arch = fitz.Archive(".")
                
                for i in range(total_pages):
                    p_in = doc_in[i]; w, h = p_in.rect.width, p_in.rect.height
                    new_w = w * (1 + margin_right_pct/100)
                    new_h = h * (1 + margin_bottom_pct/100)
                    p_out = doc_out.new_page(width=new_w, height=new_h)
                    p_out.show_pdf_page(fitz.Rect(0, 0, w, h), doc_in, i)
                    
                    bg_color = (0.97, 0.98, 0.99)
                    if margin_right_pct > 0: p_out.draw_rect(fitz.Rect(w, 0, new_w, new_h), color=bg_color, fill=bg_color, width=0)
                    if margin_bottom_pct > 0: p_out.draw_rect(fitz.Rect(0, h, w, new_h), color=bg_color, fill=bg_color, width=0)
                    
                    rects = {
                        "ด้านขวา": fitz.Rect(w + 10, 10, new_w - 10, h - 10),
                        "ด้านล่าง": fitz.Rect(10, h + 10, w - 10, new_h - 10),
                        "ด้านซ้าย": fitz.Rect(10, 10, (w * margin_right_pct/100) - 10, h - 10), 
                        "ด้านบน": fitz.Rect(10, 10, w - 10, (h * margin_bottom_pct/100) - 10) 
                    }
                    
                    minimap_rect = fitz.Rect(w + 10, new_h - (new_h/3), new_w - 10, new_h - 10)
                    if want_minimap and margin_right_pct > 0:
                        rects["ด้านขวา"] = fitz.Rect(w + 10, 10, new_w - 10, new_h - (new_h/3) - 10)
                        minimap_bg = (0.93, 0.95, 0.97)
                        p_out.draw_rect(minimap_rect, color=minimap_bg, fill=minimap_bg, width=0)
                    
                    if i in st.session_state.processed_data:
                        raw_txt = st.session_state.processed_data[i]["user_text"] or st.session_state.processed_data[i]["ai_text"]
                        
                        if raw_txt and "⚠️" not in raw_txt:
                            raw_txt = re.sub(r'^[•\-\*]\s*$', '', raw_txt, flags=re.MULTILINE)
                            c_txt, hq_txt, q_txt = split_content_hq(raw_txt)
                            
                            box_contents = {"ด้านขวา": "", "ด้านล่าง": "", "ด้านซ้าย": "", "ด้านบน": ""}
                            if c_txt: box_contents[content_pos] += c_txt + "\n\n"
                            if hq_txt: box_contents[hy_pos] += hq_txt + "\n\n"
                            if q_txt: box_contents[quiz_pos] += q_txt + "\n\n"
                            
                            for pos, text_chunk in box_contents.items():
                                if not text_chunk.strip(): continue
                                box = rects[pos]
                                is_hy_alone = (text_chunk.strip().startswith("High-Yield:") and "Quiz:" not in text_chunk)
                                
                                if is_hy_alone:
                                    text_chunk = text_chunk.replace("High-Yield:", "<b>🚨 High-Yield:</b><br>")
                                else:
                                    text_chunk = text_chunk.replace("High-Yield:", "<b>🚨 High-Yield:</b><br>").replace("Quiz:", "<b>📝 Quiz:</b><br>").replace("เฉลย:", "<b>💡 เฉลย:</b><br>")

                                f_size = calc_dynamic_fontsize(text_chunk, box.width, box.height)
                                html = markdown.markdown(text_chunk, extensions=['tables'])
                                
                                color_css = "#9B2C2C" if is_hy_alone else "#2D3748"
                                css = f"""
                                @font-face {{ font-family: 'T'; src: url('THSarabunNew.ttf'); }}
                                @font-face {{ font-family: 'T'; font-weight: bold; src: url('THSarabunNew Bold.ttf'); }}
                                body {{ font-family: 'T'; font-size: {f_size}px; line-height: 1.3; color: {color_css}; }}
                                b {{ font-size: 1.1em; }}
                                table {{ border-collapse: collapse; width: 100%; }} th, td {{ border: 0.5px solid #CBD5E0; padding: 2px; }}
                                """
                                try: p_out.insert_htmlbox(box, f"<style>{css}</style><body>{html}</body>", archive=arch)
                                except: p_out.insert_textbox(box, text_chunk, fontsize=f_size)
                            
                            if want_minimap and len(st.session_state.global_map_list) > 0 and margin_right_pct > 0:
                                map_lines = []
                                for m_idx, m_str in enumerate(st.session_state.global_map_list):
                                    if m_idx not in st.session_state.selected_pages:
                                        continue 
                                        
                                    if m_idx == i: 
                                        map_lines.append(f"<b style='color: #E53E3E;'>➡️ หน้า {m_idx+1}: {m_str}</b>")
                                    else:
                                        map_lines.append(f"<span style='color: #718096;'>• หน้า {m_idx+1}: {m_str}</span>")
                                
                                map_text = "<b>📍 Minimap (สารบัญรวม):</b><br>" + "<br>".join(map_lines)
                                m_size = max(8, calc_dynamic_fontsize(map_text, minimap_rect.width, minimap_rect.height) - 4)
                                css_m = f"body {{ font-family: 'T'; font-size: {m_size}px; line-height: 1.2; }}"
                                try: p_out.insert_htmlbox(minimap_rect, f"<style>{css_m}</style><body>{map_text}</body>", archive=arch)
                                except: pass

                if st.session_state.full_summaries:
                    model_os = genai.GenerativeModel(get_best_available_model(st.session_state.flash_models_list) or "gemini-1.5-flash")
                    os_prompt = f"สรุป High-yield สำหรับ นสพ.ปี {med_year} จัดรูปแบบมินิมอล **มีข้อมูลเปรียบเทียบให้ทำเป็น Markdown Table ทันที**:\n{st.session_state.full_summaries[:30000]}"
                    try:
                        safety = { HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE, HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE, HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE, HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE }
                        os_res = model_os.generate_content(os_prompt, safety_settings=safety)
                        os_html = markdown.markdown(os_res.text, extensions=['tables'])
                    except:
                        os_html = "One-sheet Summary ไม่พร้อมใช้งาน"
                    
                    p_os = doc_out.new_page(width=595, height=842)
                    f_size_os = calc_dynamic_fontsize(os_html, 515, 762)
                    css_os = f"@font-face {{ font-family: 'T'; src: url('THSarabunNew.ttf'); }} @font-face {{ font-family: 'T'; font-weight: bold; src: url('THSarabunNew Bold.ttf'); }} body {{ font-family: 'T'; font-size: {f_size_os}px; }} h2 {{ text-align: center; border-bottom: 2px solid #0369A1; color: #0369A1;}} table {{ width: 100%; border-collapse: collapse; margin: 10px 0;}} th, td {{ border: 1px solid #9CA3AF; padding: 6px; }} th {{ background-color: #E0F2FE; font-weight: bold; text-align: center; }}"
                    p_os.insert_htmlbox(fitz.Rect(40,40,555,802), f"<style>{css_os}</style><body><h2>⭐ CLINICAL ONE-SHEET ⭐</h2>{os_html}</body>", archive=arch)

                pdf_res = doc_out.tobytes()
                st.download_button("💾 ดาวน์โหลดไฟล์สมบูรณ์", data=pdf_res, file_name=f"Note_{st.session_state.pdf_name}", mime="application/pdf")

    # --- 10. ระบบประมวลผลหลังบ้าน (2-Phase Background Processor) ---
    if st.session_state.is_running and not st.session_state.stop_clicked:
        active_m = st.session_state.current_active_model
        model = genai.GenerativeModel(active_m)
        config = GenerationConfig(max_output_tokens=max_tokens)
        safety = { HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE, HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE, HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE, HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE }

        # ==========================================
        # PHASE 1: GLOBAL SCAN (สแกนภาพรวมและสารบัญ)
        # ==========================================
        if st.session_state.phase == 'global_scan':
            target_global = next((i for i in st.session_state.selected_pages if i not in st.session_state.global_data), None)
            
            if target_global is None:
                # สแกนภาพรวมเสร็จแล้ว รวบรวมข้อมูล
                st.session_state.phase = 'detail_scan'
                
                context_lines = []
                for idx in st.session_state.selected_pages:
                    g_info = st.session_state.global_data.get(idx, {})
                    topic = g_info.get('topic', f"หน้า {idx+1}")
                    summary = g_info.get('summary', "")
                    st.session_state.global_map_list[idx] = topic # อัปเดต Map
                    context_lines.append(f"- หน้า {idx+1} ({topic}): {summary}")
                
                # บันทึกความจำภาพรวม
                st.session_state.global_context_text = "\n".join(context_lines)
                save_workspace() # Auto-save
                st.rerun()

            else:
                # กำลังสแกน Global
                st.session_state.status_mode = 'purple'
                action_html = f"<div class='status-purple'><b>🔍 [Phase 1/2] กำลังสแกนภาพรวม (หน้า {target_global+1} / {total_pages})</b><br>🤖 เพื่อสร้างระบบความจำ Minimap และ Context</div>"
                action_placeholder.markdown(action_html, unsafe_allow_html=True)
                
                p_img = doc_in[target_global].get_pixmap(dpi=50) # ใช้ DPI ต่ำเพื่อความรวดเร็ว
                img = Image.open(io.BytesIO(p_img.tobytes("png")))
                
                prompt_global = """
                วิเคราะห์สไลด์หน้านี้อย่างรวดเร็ว ตอบกลับตามรูปแบบนี้เท่านั้น (ห้ามมีคำอื่น):
                TOPIC: (ชื่อหัวข้อเรื่องของหน้านี้ สั้นๆ 1-3 คำ เพื่อทำสารบัญ)
                SUMMARY: (สรุปใจความสำคัญของหน้านี้สั้นๆ 1 ประโยค เพื่อให้ AI ตัวอื่นรู้ว่าหน้านี้สอนเรื่องอะไร)
                """
                
                try:
                    # ใช้ Token น้อยมาก เพื่อความเร็ว
                    fast_config = GenerationConfig(max_output_tokens=100)
                    resp = model.generate_content([prompt_global, img], safety_settings=safety, generation_config=fast_config)
                    text = resp.text.strip()
                    st.session_state.estimated_tokens_used += len(text)
                    
                    topic = text.split("TOPIC:")[1].split("SUMMARY:")[0].strip() if "TOPIC:" in text and "SUMMARY:" in text else f"หัวข้อหน้า {target_global+1}"
                    summary = text.split("SUMMARY:")[1].strip() if "SUMMARY:" in text else "ข้อมูลสไลด์"
                    
                    st.session_state.global_data[target_global] = {'topic': topic, 'summary': summary}
                    st.session_state.global_map_list[target_global] = topic
                    save_workspace() # Auto-save หลังจากผ่านไปแต่ละหน้า
                except Exception as e:
                    # ถ้า Error ให้ข้ามไปก่อน (ใส่ค่า default)
                    st.session_state.global_data[target_global] = {'topic': f"หน้า {target_global+1}", 'summary': ""}
                
                time.sleep(0.1) # 🚀 ปลดล็อกความเร็ว: ลดการหน่วงเวลาลงเหลือ 0.1 วินาที (เดิม 1.5 วินาที)
                st.rerun()

        # ==========================================
        # PHASE 2: DETAIL SCAN (ลงรายละเอียดทีละหน้า)
        # ==========================================
        elif st.session_state.phase == 'detail_scan':
            target = None
            is_recheck = False
            
            for i in range(total_pages):
                if i in st.session_state.processed_data and "⚠️" in st.session_state.processed_data[i].get("ai_text", ""):
                    target = i
                    is_recheck = True
                    break
                    
            if target is None:
                target = next((i for i in range(total_pages) if i not in st.session_state.processed_data), None)

            if target is not None:
                is_selected = target in st.session_state.selected_pages
                if not is_selected:
                    st.session_state.processed_data[target] = {"ai_text": "", "user_text": "", "img": doc_in[target].get_pixmap(dpi=50).tobytes("png")}
                    st.rerun()

                if is_recheck:
                    st.session_state.status_mode = 'yellow'
                    icon, msg = "🔄", f"[Phase 2/2] กำลังย้อนกลับไปซ่อมหน้าที่ Error (หน้า {target+1})"
                else:
                    st.session_state.status_mode = 'blue'
                    icon, msg = "⚡", f"[Phase 2/2] กำลังลงรายละเอียดหน้าที่ {target+1} / {total_pages}"
                    
                action_html = f"<div class='status-{st.session_state.status_mode}'><b>{icon} {msg}</b><br>🤖 อิงบริบทจากภาพรวม | สั่งงาน: <code>{active_m}</code></div>"
                action_placeholder.markdown(action_html, unsafe_allow_html=True)
                
                p_img = doc_in[target].get_pixmap(dpi=75)
                img = Image.open(io.BytesIO(p_img.tobytes("png")))

                pattern_parts = []
                if want_content: pattern_parts.append(f"(อธิบายเนื้อหาโดยตรง เริ่มเนื้อหาเลย)")
                if want_summary: pattern_parts.append("High-Yield:\n- (สรุปจุดตายที่ออกสอบ กระชับสุดๆ)")
                if want_quiz: 
                    q_sec = f"Quiz:\n1. (คำถาม)\n2. (คำถาม)" if quiz_count >= 2 else f"Quiz:\n1. (คำถาม)"
                    if want_answer: q_sec += f"\n\nเฉลย:\n1. (เฉลย)\n2. (เฉลย)" if quiz_count >= 2 else f"\n\nเฉลย:\n1. (เฉลย)"
                    pattern_parts.append(q_sec)
                strict_pattern = "\n\n".join(pattern_parts)

                prompt = f"""
                คุณคืออาจารย์แพทย์สอน นสพ. ปี {med_year} ตอบเป็นภาษาไทย
                
                **นี่คือบริบทภาพรวมของเอกสารนี้ (Global Context):**
                เพื่อให้คุณเข้าใจว่าหน้านี้เชื่อมโยงกับหน้าอื่นๆ อย่างไร:
                {st.session_state.global_context_text}
                
                **คำเตือนและข้อบังคับสำคัญ:**
                1. อธิบายเนื้อหาของสไลด์ในรูปภาพนี้ โดยอิงความสอดคล้องจาก "บริบทภาพรวม" ด้านบน เพื่อไม่ให้เนื้อหาหลุดกรอบ
                2. **ต้องเน้นตัวหนาที่คำสำคัญ** เพื่อเพิ่มสมาธิให้คนอ่าน ไม่ให้ข้ามคำสำคัญเด็ดขาด
                3. **เน้นที่การให้เหตุผลกับเหตุการณ์สำคัญ (เช่น อาการ/กลไก) ....เพราะ....** เพื่อให้ผู้อ่านเข้าใจและจดจำได้นาน
                4. ตอบให้ครบตาม Pattern ด้านล่างเป๊ะๆ ห้ามข้าม ห้ามมีคำทักทาย หากเป็นหน้าว่างให้ตอบ 'NON_CONTENT'

                {strict_pattern}
                """
                
                is_success = False
                retry_count = 0

                while retry_count < 2 and not is_success:
                    try:
                        resp = model.generate_content([prompt, img], safety_settings=safety, generation_config=config)
                        final_text = resp.text.strip()
                        
                        st.session_state.estimated_tokens_used += len(final_text) 
                        
                        if "NON_CONTENT" in final_text:
                            final_text = ""
                        else:
                            if want_summary and "High-Yield:" in final_text:
                                st.session_state.full_summaries += f"\n[Page {target+1}] " + final_text.split("High-Yield:")[-1].split("Quiz:")[0]
                        
                        is_success = True
                        st.session_state.processed_data[target] = {"ai_text": final_text, "user_text": "", "img": p_img.tobytes("png")}
                        
                        save_workspace() # Auto-save เมื่อประมวลผลหน้านี้สำเร็จ
                        
                    except Exception as e:
                        error_msg = str(e)
                        if "429" in error_msg or "Quota" in error_msg:
                            st.session_state.exhausted_models[active_m] = time.time() + 60
                            st.session_state.status_mode = 'yellow'
                            action_placeholder.markdown(f"<div class='status-yellow'><b>⚠️ `{active_m}` คิวเต็ม!</b><br>🔍 กำลังหาโมเดลสำรอง...</div>", unsafe_allow_html=True)
                            time.sleep(2)
                            
                            new_model = get_best_available_model(st.session_state.flash_models_list)
                            if new_model:
                                active_m = new_model
                                st.session_state.current_active_model = new_model
                                model = genai.GenerativeModel(new_model)
                                retry_count += 1
                            else:
                                st.session_state.status_mode = 'red'
                                action_placeholder.markdown(f"<div class='status-red'><b>🚨 โควต้าเต็มทุกโมเดล (ไม่มีตัวว่าง)</b><br>กด Pause รอ 1 นาทีแล้วกด Continue ครับ</div>", unsafe_allow_html=True)
                                st.session_state.processed_data[target] = {"ai_text": "⚠️ โควต้าเต็มทุกโมเดล (ไม่มีตัวว่าง) กด Pause รอ 1 นาทีแล้วกด Continue ครับ", "user_text": "", "img": p_img.tobytes("png")}
                                is_success = True
                        elif "400" in error_msg:
                            st.session_state.exhausted_models[active_m] = time.time() + 86400 
                            st.session_state.processed_data[target] = {"ai_text": f"⚠️ โมเดล {active_m} ถูกแบนชั่วคราวเนื่องจากไม่อ่านรูป โปรดรอระบบสลับโมเดล", "user_text": "", "img": p_img.tobytes("png")}
                            is_success = True
                        else:
                            st.session_state.processed_data[target] = {"ai_text": f"⚠️ Error: {error_msg}", "user_text": "", "img": p_img.tobytes("png")}
                            is_success = True

                time.sleep(0.1) # 🚀 ปลดล็อกความเร็ว: ลดการหน่วงเวลาลงเหลือ 0.1 วินาที (เดิม 3 วินาที)
                st.rerun()
            else:
                st.session_state.is_running = False
                st.session_state.phase = 'idle'
                action_placeholder.markdown(f"<div class='status-blue' style='border-left-color: #38A169; color: #2F855A; background: #F0FFF4;'><b>✅ ประมวลผลครบทุกหน้าแล้ว!</b><br>สามารถพิมพ์แก้ไขเนื้อหา หรือกดดาวน์โหลดไฟล์สมบูรณ์ได้เลยครับ</div>", unsafe_allow_html=True)
