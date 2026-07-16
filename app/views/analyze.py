import streamlit as st
from graph.trading_graph import build_graph, TradingState


def render():
    st.title("📊 Analyze — Grafo en tiempo real")

    col1, col2 = st.columns([3, 1])
    with col1:
        ticker = st.text_input("Ticker", value="NVDA").upper().strip()
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        run = st.button("🚀 Analizar", use_container_width=True)

    if not run:
        st.info("Introduce un ticker y pulsa Analizar para ejecutar el grafo completo.")
        return

    initial_state: TradingState = {
        "ticker": ticker,
        "df": None,
        "technical_result": None,
        "sentiment_result": None,
        "critic_result": None,
        "decision": None,
        "execution_result": None,
        "portfolio": None,
    }

    NODE_LABELS = {
        "data_node":       "📥 data_node — descargando OHLCV",
        "indicators_node": "📐 indicators_node — calculando RSI, SMA, Momentum",
        "technical_node":  "📈 technical_node — generando señal técnica",
        "sentiment_node":  "📰 sentiment_node — analizando noticias con FinBERT",
        "critic_node":     "🧠 critic_node — DeepSeek-R1 razonando...",
        "decision_node":   "⚖️ decision_node — tomando decisión final",
        "execution_node":  "💼 execution_node — ejecutando orden",
    }

    completed = {}

    with st.status("Ejecutando grafo...", expanded=True) as status:
        app_graph = build_graph()

        for chunk in app_graph.stream(initial_state):
            node_name = list(chunk.keys())[0]
            completed[node_name] = chunk[node_name]
            label = NODE_LABELS.get(node_name, node_name)
            st.write(f"✅ {label}")

        status.update(label="✅ Grafo completado", state="complete", expanded=False)

    # Extraer resultados
    tech      = completed.get("technical_node", {}).get("technical_result") or {}
    sent      = completed.get("sentiment_node", {}).get("sentiment_result") or {}
    critic    = completed.get("critic_node", {}).get("critic_result") or {}
    dec       = completed.get("decision_node", {}).get("decision") or {}
    exec_r    = completed.get("execution_node", {}).get("execution_result") or {}
    portfolio = exec_r.get("portfolio", {})

    st.divider()

    # ── DeepSeek Thinking ────────────────────────────────────────────────────
    thinking = critic.get("thinking", "")
    if thinking:
        with st.expander("🧠 DeepSeek Thinking", expanded=True):
            st.markdown(thinking)

    st.divider()

    # ── Resultados por sección ───────────────────────────────────────────────
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("📈 Technical")
        st.metric("Signal", tech.get("signal", "—"))
        c1, c2 = st.columns(2)
        c1.metric("RSI", f"{tech.get('rsi', 0):.1f}")
        c2.metric("Price", f"${tech.get('price', 0):.2f}")

        st.subheader("📰 Sentiment")
        st.metric("Score", f"{sent.get('sentiment', 0):+.4f}",
                  delta=f"{sent.get('headlines', 0)} headlines")
        for r in (sent.get("raw_results") or [])[:3]:
            emoji = "🟢" if r["score"] > 0 else ("🔴" if r["score"] < 0 else "⚪")
            st.caption(f"{emoji} `{r['score']:+.2f}` {r['title'][:65]}")

    with col_r:
        st.subheader("🧠 Critic")
        verdict = critic.get("verdict", "—")
        verdict_color = "🟢" if verdict == "APPROVED" else "🔴"
        st.metric("Verdict", f"{verdict_color} {verdict}")

        rag = critic.get("rag_precedents") or []
        if rag:
            st.markdown("**Precedentes RAG:**")
            for p in rag:
                m = p["metadata"]
                st.caption(
                    f"`{m['date']}` RSI={m['rsi']:.1f} {m['signal']} "
                    f"→ **{m['outcome']}** (sim: {p['similarity']:.2f})"
                )

        st.subheader("⚖️ Decision")
        action = dec.get("action", "—")
        action_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "")
        st.metric("Action", f"{action_emoji} {action}")
        c1, c2 = st.columns(2)
        c1.metric("Score", f"{dec.get('score', 0):+.3f}")
        c2.metric("Threshold", dec.get("threshold_used", "—"))
        if dec.get("critic_override"):
            st.warning("⚠️ Critic override activo — threshold elevado a 0.85")

    st.divider()

    st.subheader("💼 Portfolio")
    if portfolio:
        c1, c2, c3 = st.columns(3)
        c1.metric("Cash", f"${portfolio.get('cash', 0):,.2f}")
        c2.metric("Total value", f"${portfolio.get('total_value', 0):,.2f}")
        c3.metric("Posiciones", len(portfolio.get("positions", {})))
        trade = exec_r.get("trade")
        if trade and trade.get("status") == "filled":
            st.success(f"Trade ejecutado: {trade['trade']['action']} "
                       f"{trade['trade']['quantity']} shares @ ${trade['trade']['price']:.2f}")
    else:
        st.caption("Sin actividad en portfolio esta ejecución.")

    st.divider()

    st.subheader("🧠 Critic Reasoning")
    st.markdown(critic.get("reasoning", "—"))
