import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st

st.set_page_config(
    page_title="AI Trading Lab",
    page_icon="🤖",
    layout="wide"
)

pages = {
    "🗂️ Dashboard":    "dashboard",
    "📊 Analyze":      "analyze",
    "🤖 Portfolio Sim":"portfolio_sim",
    "🛑 Trailing Stops":"trailing_stops",
}

with st.sidebar:
    st.title("🤖 AI Trading Lab")
    st.caption("Multi-agent trading system")
    st.divider()
    selected = st.radio("Navegación", list(pages.keys()))

if selected == "🗂️ Dashboard":
    from app.views.dashboard import render
    render()
elif selected == "📊 Analyze":
    from app.views.analyze import render
    render()
elif selected == "🤖 Portfolio Sim":
    from app.views.portfolio_sim import render
    render()
elif selected == "🛑 Trailing Stops":
    from app.views.trailing_stops import render
    render()
