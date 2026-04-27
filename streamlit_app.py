import streamlit as st
import pandas as pd
from main_engine import (
    rodar_sistema,
    montar_multipla,
    get_arbitrage,
    processar_arbitrage,
    CACHE_TTL_HISTORICO,
    CACHE_TTL_VALUE_BETS,
    LIGAS_PERMITIDAS,
    LIGA_ESPORTE,
)
from stats_historicas import buscar_stats_ligas

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_HISTORICO, show_spinner=False)
def _cached_stats_ligas() -> dict:
    return buscar_stats_ligas()

@st.cache_data(ttl=CACHE_TTL_VALUE_BETS, show_spinner=False)
def _cached_rodar(modo: str, odd_min: float, odd_max: float,
                  mercados: frozenset, stats_key: int) -> list[dict]:
    """Cache curto para os resultados — evita rebuscar ao trocar aba."""
    stats = _cached_stats_ligas()
    return rodar_sistema(odd_min, odd_max, set(mercados) or None,
                         stats_ligas=stats, modo=modo)


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Trader PRO", page_icon="🚀", layout="wide")
st.title("🚀 Trader PRO — Sistema Completo")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

DEFAULTS = {
    "res_fut": None, "res_nba": None,
    "arbs_fut": None, "arbs_nba": None,
    "banca": 1_000.0, "qtd": 10,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

MERCADOS_FUTEBOL = ["ML","Totals","Over/Under","Asian Handicap","Corner","BTTS","Double Chance"]
MERCADOS_NBA     = [
    "ML","Spread","Totals","ML HT","Spread HT","Totals HT",
    "Totals 1Q","Spread Q1","ML Q1",
    "Team Total Home","Team Total Away",
    "Alternative Totals","Alternative Spread",
    "Points O/U","Rebounds O/U","Assists O/U",
    "Steals O/U","Blocks O/U","Field Goals Made O/U","Threes Made O/U",
    "Points & Rebounds O/U","Points & Assists O/U",
    "Assists & Rebounds O/U","Points, Assists & Rebounds O/U","Steals & Blocks O/U",
    "Double Double","Triple Double",
    "Player Points Milestones","Player Rebounds Milestones",
    "Player Assists Milestones","Player Threes Milestones",
    "Player First Basket","Player First Assist","Player First Rebound","Player Props",
]

with st.sidebar:
    st.header("⚙️ Parâmetros")

    odd_min = st.number_input("Odd mínima", 1.0, 10.0, 1.10, 0.05)
    odd_max = st.number_input("Odd máxima", 1.0, 10.0, 2.00, 0.05)
    qtd     = st.slider("Picks exibidos", 1, 50, 10)
    banca   = st.number_input("Banca (R$)", 100.0, 100_000.0, 1_000.0, 100.0,
                               help="Usada para Kelly e calculadora de arb.")
    st.divider()

    st.caption("⚽ Mercados Futebol")
    sel_fut = st.multiselect("Futebol", MERCADOS_FUTEBOL,
                              default=["ML","Totals","Over/Under"],
                              label_visibility="collapsed")

    st.caption("🏀 Mercados NBA")
    sel_nba = st.multiselect("NBA", MERCADOS_NBA,
                              default=["ML","Spread","Totals",
                                       "Points O/U","Rebounds O/U","Assists O/U"],
                              label_visibility="collapsed")
    st.divider()
    st.caption("🗄️ Cache: Eventos 5min · Value Bets 30s · Stats 24h · Arb ao vivo")

    col_b1, col_b2 = st.columns(2)
    buscar_fut = col_b1.button("⚽ Buscar Futebol", use_container_width=True)
    buscar_nba = col_b2.button("🏀 Buscar NBA",     use_container_width=True)

# ---------------------------------------------------------------------------
# Execução ao clicar
# ---------------------------------------------------------------------------

if buscar_fut:
    with st.spinner("Buscando futebol..."):
        stats = _cached_stats_ligas()
        st.session_state["res_fut"]  = rodar_sistema(
            odd_min, odd_max, set(sel_fut) or None, stats_ligas=stats, modo="futebol"
        )
    with st.spinner("Buscando arbitragens de futebol..."):
        st.session_state["arbs_fut"] = processar_arbitrage(
            get_arbitrage(limit=50), esporte="futebol"
        )
    st.session_state["banca"] = banca
    st.session_state["qtd"]   = qtd

if buscar_nba:
    with st.spinner("Buscando NBA..."):
        stats = _cached_stats_ligas()
        st.session_state["res_nba"]  = rodar_sistema(
            odd_min, odd_max, set(sel_nba) or None, stats_ligas=stats, modo="nba"
        )
    with st.spinner("Buscando arbitragens NBA..."):
        st.session_state["arbs_nba"] = processar_arbitrage(
            get_arbitrage(limit=50), esporte="nba"
        )
    st.session_state["banca"] = banca
    st.session_state["qtd"]   = qtd

banca_ref = st.session_state["banca"]
qtd_ref   = st.session_state["qtd"]

# ---------------------------------------------------------------------------
# Helpers visuais
# ---------------------------------------------------------------------------

def _cor_ev(v):
    if v > 0: return "color:#2ecc71;font-weight:500"
    if v < 0: return "color:#e74c3c"
    return ""

def _cor_profit(v):
    return "color:#2ecc71;font-weight:500" if v > 0 else ""

def _calcular_stakes_por_meta(legs, banca_total, lucro_desejado):
    retorno_alvo    = banca_total + lucro_desejado
    resultado       = []
    total_investido = 0.0
    for leg in legs:
        try:    odd = float(leg.get("odds", 0))
        except: return None
        if odd <= 1.0: return None
        stake = retorno_alvo / odd
        total_investido += stake
        resultado.append({
            "bookmaker": leg.get("bookmaker",""),
            "side":      leg.get("side",""),
            "odd":       odd,
            "stake":     round(stake, 2),
            "retorno":   round(stake * odd, 2),
            "link":      leg.get("directLink") or leg.get("href",""),
        })
    if total_investido > banca_total:
        return None
    lucro_real = round(retorno_alvo - total_investido, 2)
    for r in resultado:
        r["total_investido"] = round(total_investido, 2)
        r["lucro_garantido"] = lucro_real
    return resultado


# ---------------------------------------------------------------------------
# Componentes reutilizáveis
# ---------------------------------------------------------------------------

def _tabela_ev_futebol(df: pd.DataFrame, key: str) -> None:
    RENAME = {
        "jogo":"Jogo","liga":"Liga","horario":"Horário","tipo":"Tipo",
        "mercado":"Mercado","linha":"Linha","casa":"Casa","odd":"Odd",
        "prob_modelo":"Prob.(%)","ev":"EV","score":"Score","fonte":"Fonte",
    }
    cols = [c for c in RENAME if c in df.columns]
    styled = (
        df[cols].rename(columns=RENAME).style
        .map(_cor_ev, subset=["EV"])
        .format({"Odd":"{:.2f}","Prob.(%)":"{:.1f}%","EV":"{:+.3f}","Score":"{:.2f}"})
    )
    st.dataframe(styled, width="stretch", hide_index=True)


def _tabela_ev_nba(df: pd.DataFrame, key: str) -> None:
    """Tabela NBA com colunas específicas: jogador visível quando for props."""
    RENAME = {
        "jogo":"Jogo","horario":"Horário","mercado":"Mercado",
        "tipo":"Descrição","linha":"Linha","casa":"Casa",
        "odd":"Odd","prob_modelo":"Prob.(%)","ev":"EV","fonte":"Fonte",
    }
    cols = [c for c in RENAME if c in df.columns]
    styled = (
        df[cols].rename(columns=RENAME).style
        .map(_cor_ev, subset=["EV"])
        .format({"Odd":"{:.2f}","Prob.(%)":"{:.1f}%","EV":"{:+.3f}"})
    )
    st.dataframe(styled, width="stretch", hide_index=True)


def _exibir_multipla(resultados: list[dict], banca: float, key_prefix: str) -> None:
    multi = montar_multipla(resultados, banca)
    st.subheader("🎯 Múltipla Inteligente")
    if not multi["picks"]:
        st.warning("Sem picks suficientes para montar a múltipla.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Odd total",       f"{multi['odd_total']:.2f}")
    c2.metric("Prob. combinada", f"{multi['prob_total']:.1f}%")
    c3.metric("EV",              f"{multi['ev']:+.3f}")
    c4.metric("Stake sugerida",  f"R$ {multi['stake']:.2f}")
    st.markdown("**Picks (jogos distintos):**")
    for i, p in enumerate(multi["picks"], 1):
        st.markdown(f"{i}. **{p['jogo']}** — {p['tipo']} @ {p['odd']:.2f} _(EV: {p['ev']:+.3f})_")


def _exibir_arbitrage(arbs: list[dict], banca_sidebar: float, key_prefix: str) -> None:
    if not arbs:
        st.info("Nenhuma oportunidade de arbitragem encontrada no momento.")
        return

    rows = []
    for arb in arbs:
        legs      = arb.get("legs", [])
        stakes    = arb.get("optimal_stakes", [])
        legs_desc = " / ".join(
            f"{l.get('bookmaker')} {l.get('side','').upper()} @ {l.get('odds','?')}"
            for l in legs
        )
        rows.append({
            "Jogo":             arb["jogo"],
            "Liga":             arb.get("liga",""),
            "Horário":          arb["horario"],
            "Mercado":          arb["mercado"],
            "Lucro (%)":        arb["profit_pct"],
            "Legs":             legs_desc,
            "Retorno p/R$100":  round(stakes[0].get("potentialReturn",0),2) if stakes else 0,
        })
    st.dataframe(
        pd.DataFrame(rows).style.map(_cor_profit, subset=["Lucro (%)"]),
        width="stretch", hide_index=True,
    )
    st.info("ℹ️ Filtro de odds **não afeta** arbitragem — avaliada pelo lucro total garantido.")
    st.markdown("### 📌 Como executar")

    for arb in arbs[:10]:
        legs    = arb.get("legs", [])
        stakes  = arb.get("optimal_stakes", [])
        leg_map = {(l.get("bookmaker"), l.get("side")): l for l in legs}
        arb_id  = str(arb.get("id", arb["jogo"]))

        with st.expander(f"📌 {arb['jogo']}  |  {arb['mercado']}  |  Lucro: **{arb['profit_pct']:.2f}%**"):

            # Execução padrão
            st.markdown("#### Execução padrão")
            hdr = st.columns([3,1,1,1])
            for h, t in zip(hdr, ["**Casa/Lado**","**Odd**","**Stake**","**Retorno**"]):
                h.markdown(t)
            for s in stakes:
                bk, side = s.get("bookmaker",""), s.get("side","")
                leg  = leg_map.get((bk, side), {})
                link = leg.get("directLink") or leg.get("href","")
                row  = st.columns([3,1,1,1])
                row[0].markdown(f"**{bk}** — {side.upper()}" + (f"  [↗]({link})" if link else ""))
                row[1].markdown(f"`{leg.get('odds','?')}`")
                row[2].markdown(f"R$ {s.get('stake',0):.2f}")
                row[3].markdown(f"R$ {s.get('potentialReturn',0):.2f}")
            if stakes:
                ret   = stakes[0].get("potentialReturn", 0)
                total = sum(s.get("stake",0) for s in stakes)
                st.success(f"✅ Invest: R$ {total:.2f} → Retorno: R$ {ret:.2f} → Lucro: R$ {ret-total:.2f}")

            st.divider()

            # Calculadora
            st.markdown("#### 🎯 Calculadora — sua meta")
            cc1, cc2 = st.columns(2)
            banca_calc = cc1.number_input(
                "Banca (R$)", 10.0, 1_000_000.0, float(banca_sidebar), 10.0,
                key=f"{key_prefix}_bc_{arb_id}",
            )
            lucro_meta = cc2.number_input(
                "Lucro desejado (R$)", 1.0, 1_000_000.0,
                max(1.0, round(banca_calc * arb["profit_pct"] / 100, 2)), 1.0,
                key=f"{key_prefix}_lm_{arb_id}",
            )
            sc = _calcular_stakes_por_meta(legs, banca_calc, lucro_meta)
            if sc is None:
                lucro_max = round(banca_calc * arb["profit_pct"] / 100, 2)
                st.warning(f"⚠️ Banca insuficiente. Máximo possível: **R$ {lucro_max:.2f}** ({arb['profit_pct']:.2f}%)")
            else:
                hdr2 = st.columns([3,1,1,1])
                for h, t in zip(hdr2, ["**Casa/Lado**","**Odd**","**Stake**","**Retorno**"]):
                    h.markdown(t)
                for r in sc:
                    row = st.columns([3,1,1,1])
                    link = r.get("link","")
                    row[0].markdown(f"**{r['bookmaker']}** — {r['side'].upper()}" + (f"  [↗]({link})" if link else ""))
                    row[1].markdown(f"`{r['odd']:.2f}`")
                    row[2].markdown(f"R$ {r['stake']:.2f}")
                    row[3].markdown(f"R$ {r['retorno']:.2f}")
                t_inv = sc[0]["total_investido"]
                l_grt = sc[0]["lucro_garantido"]
                st.success(f"✅ Invest: R$ {t_inv:.2f} → Lucro garantido: R$ {l_grt:.2f} (sobra R$ {banca_calc-t_inv:.2f})")
            st.caption(f"Atualizado: {arb.get('updated_at','—')}")


# ---------------------------------------------------------------------------
# MODO FUTEBOL
# ---------------------------------------------------------------------------

def _render_futebol():
    res  = st.session_state["res_fut"]
    arbs = st.session_state["arbs_fut"]

    if res is None:
        st.info("Clique em **⚽ Buscar Futebol** na sidebar para iniciar.")
        return

    aba_ev, aba_arb = st.tabs(["📊 Oportunidades EV", "⚖️ Arbitragem"])

    with aba_ev:
        if not res:
            st.warning("Nenhuma oportunidade encontrada.")
        else:
            df = (
                pd.DataFrame(res)
                .sort_values("ev", ascending=False)
                .head(qtd_ref).reset_index(drop=True)
            )
            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Picks",      len(df))
            m2.metric("EV médio",   f"{df['ev'].mean():+.3f}")
            m3.metric("Odd média",  f"{df['odd'].mean():.2f}")
            m4.metric("EV > 0",     int((df["ev"]>0).sum()))
            _tabela_ev_futebol(df, "fut_ev")
            st.divider()
            _exibir_multipla(res, banca_ref, "fut")

    with aba_arb:
        if arbs:
            a1,a2,a3 = st.columns(3)
            a1.metric("Oportunidades", len(arbs))
            a2.metric("Maior lucro",   f"{arbs[0]['profit_pct']:.2f}%")
            a3.metric("Lucro médio",   f"{sum(a['profit_pct'] for a in arbs)/len(arbs):.2f}%")
        _exibir_arbitrage(arbs or [], banca_ref, "fut")


# ---------------------------------------------------------------------------
# MODO NBA
# ---------------------------------------------------------------------------

def _render_nba():
    res  = st.session_state["res_nba"]
    arbs = st.session_state["arbs_nba"]

    if res is None:
        st.info("Clique em **🏀 Buscar NBA** na sidebar para iniciar.")
        return

    aba_ev, aba_arb = st.tabs(["📊 Oportunidades EV", "⚖️ Arbitragem"])

    with aba_ev:
        if not res:
            st.warning("Nenhuma oportunidade NBA encontrada.")
        else:
            df = (
                pd.DataFrame(res)
                .sort_values("ev", ascending=False)
                .head(qtd_ref).reset_index(drop=True)
            )
            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Picks",     len(df))
            m2.metric("EV médio",  f"{df['ev'].mean():+.3f}")
            m3.metric("Odd média", f"{df['odd'].mean():.2f}")
            m4.metric("EV > 0",    int((df["ev"]>0).sum()))

            # Filtro rápido por tipo de mercado NBA
            tipos = sorted(df["mercado"].dropna().unique().tolist())
            sel_tipo = st.multiselect("Filtrar mercado", tipos, default=tipos, key="nba_tipo_filter")
            df_view = df[df["mercado"].isin(sel_tipo)] if sel_tipo else df

            _tabela_ev_nba(df_view, "nba_ev")
            st.divider()
            _exibir_multipla(res, banca_ref, "nba")

    with aba_arb:
        if arbs:
            a1,a2,a3 = st.columns(3)
            a1.metric("Oportunidades NBA", len(arbs))
            a2.metric("Maior lucro",       f"{arbs[0]['profit_pct']:.2f}%")
            a3.metric("Lucro médio",       f"{sum(a['profit_pct'] for a in arbs)/len(arbs):.2f}%")
        _exibir_arbitrage(arbs or [], banca_ref, "nba")


# ---------------------------------------------------------------------------
# Layout principal — toggle ⚽ / 🏀
# ---------------------------------------------------------------------------

modo = st.radio(
    "Modo",
    ["⚽ Futebol", "🏀 NBA"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

if modo == "⚽ Futebol":
    _render_futebol()
else:
    _render_nba()
