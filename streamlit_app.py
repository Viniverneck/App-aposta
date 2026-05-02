import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone

from main_engine import (
    buscar_comparacao_odds,
    buscar_live_arb,
    rodar_sistema,
)

st.set_page_config(layout="centered")

st.title("📊 Sistema de Arbitragem")

st.write("APP INICIOU")  # teste de render no iPad

# ==============================
# BOTÕES DE EXECUÇÃO
# ==============================

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("Rodar Sistema"):
        with st.spinner("Buscando oportunidades..."):
            dados = rodar_sistema()
            df = pd.DataFrame(dados)
            st.dataframe(df, use_container_width=True)

with col2:
    if st.button("Comparar Odds"):
        with st.spinner("Comparando odds..."):
            dados = buscar_comparacao_odds()
            df = pd.DataFrame(dados)
            st.dataframe(df, use_container_width=True)

with col3:
    if st.button("Live Arbitragem"):
        with st.spinner("Buscando live arb..."):
            dados = buscar_live_arb()
            df = pd.DataFrame(dados)
            st.dataframe(df, use_container_width=True)

# ==============================
# INFO RODAPÉ
# ==============================

st.markdown("---")
st.write("Última atualização:", datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC"))
