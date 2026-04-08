# ui/state.py
import streamlit as st

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
