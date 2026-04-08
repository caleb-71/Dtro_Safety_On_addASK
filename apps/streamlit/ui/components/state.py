# ui/state.py
import streamlit as st
import datetime as dt

def get_state(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]

def set_state(key: str, value):
    st.session_state[key] = value

def clear_state(prefix: str):
    for k in list(st.session_state.keys()):
        if k.startswith(prefix):
            del st.session_state[k]

# [NEW] 대시보드 상태 기본값 초기화 로직
def init_dashboard_filters():
    today = dt.date.today()
    if "dash01_common_start" not in st.session_state:
        st.session_state["dash01_common_start"] = today - dt.timedelta(days=29)
    if "dash01_common_end" not in st.session_state:
        st.session_state["dash01_common_end"] = today
    if "dash01_trend_filter" not in st.session_state:
        st.session_state["dash01_trend_filter"] = "전체"
    if "dash01_smap_filter" not in st.session_state:
        st.session_state["dash01_smap_filter"] = "전체"