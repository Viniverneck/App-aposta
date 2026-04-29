import streamlit as st
import pandas as pd
from main_engine import (
    buscar_comparacao_odds,
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
    "res_fut": None, "res_nba": None, "res_tenis": None,
    "arbs_fut": None, "arbs_nba": None, "arbs_tenis": None,
    "comparacao": None,
    "banca": 1_000.0, "qtd": 10,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

MERCADOS_FUTEBOL = ["ML","Totals","Over/Under","Asian Handicap","Corner","BTTS","Double Chance"]
MERCADOS_TENIS = ["ML", "Spread (Games)", "Totals (Games)"]

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

    st.caption("🎾 Mercados Tênis")
    sel_tenis = st.multiselect("Tênis", MERCADOS_TENIS,
                                default=["ML","Spread (Games)","Totals (Games)"],
                                label_visibility="collapsed")
    st.divider()
    st.caption("🗄️ Cache: Eventos 5min · Value Bets 30s · Stats 24h · Arb ao vivo")

    col_b1, col_b2, col_b3 = st.columns(3)
    buscar_fut   = col_b1.button("⚽ Futebol", use_container_width=True)
    buscar_nba   = col_b2.button("🏀 NBA",     use_container_width=True)
    buscar_tenis = col_b3.button("🎾 Tênis",   use_container_width=True)

    st.divider()
    st.markdown("**🔍 Comparação de Odds**")
    margem_max = st.slider(
        "Margem máxima (%)", min_value=0.5, max_value=50.0, value=3.0, step=0.5,
        help="Mostra linhas onde a soma das probs implícitas < 100+X%. Quanto menor, mais próximo de arb real."
    )
    esp_comp = st.multiselect(
        "Esportes",
        ["Football", "Basketball", "Tennis"],
        default=["Football", "Basketball", "Tennis"],
        label_visibility="collapsed",
    )
    buscar_comp = st.button("🔍 Comparar Odds", use_container_width=True)

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

if buscar_tenis:
    with st.spinner("Buscando tênis..."):
        st.session_state["res_tenis"]  = rodar_sistema(
            odd_min, odd_max, set(sel_tenis) or None, stats_ligas={}, modo="tenis"
        )
    with st.spinner("Buscando arbitragens de tênis..."):
        st.session_state["arbs_tenis"] = processar_arbitrage(
            get_arbitrage(limit=50), esporte="tenis"
        )
    st.session_state["banca"] = banca
    st.session_state["qtd"]   = qtd

if buscar_comp:
    with st.spinner("Comparando odds Bet365 x Betano..."):
        st.session_state["comparacao"] = buscar_comparacao_odds(
            esportes=esp_comp if esp_comp else ["Football","Basketball","Tennis"],
            margem_max=margem_max,
        )

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

def _exibir_links_picks(df: pd.DataFrame, key: str) -> None:
    """
    Exibe links clicáveis abaixo da tabela de picks.
    Picks com link direto (Poisson) abrem o evento exato na casa.
    Picks de value_bet_api abrem a home page da casa.
    """
    df_com_link = df[df["link"].notna() & (df["link"] != "")] if "link" in df.columns else pd.DataFrame()
    if df_com_link.empty:
        return

    with st.expander(f"🔗 Links de aposta ({len(df_com_link)} picks)", expanded=False):
        for _, row in df_com_link.iterrows():
            link  = row.get("link", "")
            jogo  = row.get("jogo", "")
            tipo  = row.get("tipo", "")
            odd   = row.get("odd", "")
            casa  = row.get("casa", "").split(" ")[0]  # remove sufixos como "(no latency)"
            fonte = row.get("fonte", "")
            icone = "🎯" if fonte == "value_bet_api" else "📌"
            label = f"{icone} **{jogo}** — {tipo} @ {odd:.2f} _{casa}_"
            st.markdown(f"{label} → [Abrir na {casa}]({link})")


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
    # Links abaixo da tabela
    _exibir_links_picks(df, key)


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
    # Links abaixo da tabela
    _exibir_links_picks(df, key)


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
        link  = p.get("link", "")
        casa  = p.get("casa", "").split(" ")[0]
        link_md = f" → [Apostar na {casa}]({link})" if link else ""
        st.markdown(f"{i}. **{p['jogo']}** — {p['tipo']} @ {p['odd']:.2f} _(EV: {p['ev']:+.3f})_{link_md}")


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
# MODO TÊNIS
# ---------------------------------------------------------------------------

def _render_tenis():
    res  = st.session_state["res_tenis"]
    arbs = st.session_state["arbs_tenis"]

    if res is None:
        st.info("Clique em **🎾 Tênis** na sidebar para iniciar.")
        return

    aba_ev, aba_arb = st.tabs(["📊 Oportunidades EV", "⚖️ Arbitragem"])

    with aba_ev:
        if not res:
            st.warning("Nenhuma oportunidade de tênis encontrada.")
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

            # Tabela específica de tênis: jogadores, torneio, mercado, linha
            RENAME_TEN = {
                "jogo":"Partida","liga":"Torneio","horario":"Horário",
                "mercado":"Mercado","tipo":"Descrição","linha":"Linha",
                "casa":"Casa","odd":"Odd","prob_modelo":"Prob.(%)","ev":"EV","fonte":"Fonte",
            }
            cols_ten = [c for c in RENAME_TEN if c in df.columns]
            styled = (
                df[cols_ten].rename(columns=RENAME_TEN).style
                .map(_cor_ev, subset=["EV"])
                .format({"Odd":"{:.2f}","Prob.(%)":"{:.1f}%","EV":"{:+.3f}"})
            )
            st.dataframe(styled, width="stretch", hide_index=True)
            _exibir_links_picks(df, "tenis_ev")
            st.divider()
            _exibir_multipla(res, banca_ref, "tenis")

    with aba_arb:
        if arbs:
            a1,a2,a3 = st.columns(3)
            a1.metric("Oportunidades", len(arbs))
            a2.metric("Maior lucro",   f"{arbs[0]['profit_pct']:.2f}%")
            a3.metric("Lucro médio",   f"{sum(a['profit_pct'] for a in arbs)/len(arbs):.2f}%")
        _exibir_arbitrage(arbs or [], banca_ref, "tenis")


# ---------------------------------------------------------------------------
# Layout principal — toggle ⚽ / 🏀 / 🎾
# ---------------------------------------------------------------------------

def _render_comparacao():
    dados = st.session_state["comparacao"]

    if dados is None:
        st.info("Configure os parâmetros em **🔍 Comparação de Odds** na sidebar e clique em **Comparar Odds**.")
        return

    if not dados:
        st.warning("Nenhuma linha encontrada com os filtros configurados. Tente aumentar a margem máxima.")
        return

    arbs_reais = [d for d in dados if d["eh_arb"]]

    # Métricas
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Linhas encontradas",   len(dados))
    m2.metric("Arbs reais (< 100%)",  len(arbs_reais), delta="🔥" if arbs_reais else None)
    m3.metric("Menor margem",         f"{dados[0]['margem_pct']:.2f}%")
    m4.metric("Com Betano",           sum(1 for d in dados if d["odd_betano_vb"] or d["odd_betano_op"]))

    if arbs_reais:
        st.error(f"🚨 {len(arbs_reais)} ARB(S) REAL(IS) DETECTADA(S) — Execute imediatamente!", icon="🚨")

    st.markdown("---")

    # Tabela principal
    rows = []
    for d in dados:
        # Ícone de status
        if d["eh_arb"]:
            status = "🚨 ARB"
        elif d["margem_pct"] < 101:
            status = "🔥 Quente"
        elif d["margem_pct"] < 102:
            status = "⚡ Próximo"
        else:
            status = "👀 Monitorar"

        rows.append({
            "Status":        status,
            "Jogo":          d["jogo"],
            "Esporte":       d["esporte"],
            "Horário":       d["horario"],
            "Mercado":       d["mercado"],
            "Tipo":          d["tipo"],
            "Bet365 VB":     d["odd_b365_vb"],
            "Bet365 Op":     d["odd_b365_op"],
            "Betano VB":     d["odd_betano_vb"] or "—",
            "Betano Op":     d["odd_betano_op"] or "—",
            "Melhor VB":     d["melhor_vb"],
            "Melhor Op":     d["melhor_op"],
            "Margem (%)":    d["margem_pct"],
        })

    df = pd.DataFrame(rows).sort_values(["Horário", "Margem (%)"])

    def _cor_margem(val):
        if isinstance(val, float):
            if val < 100:   return "background-color:#1a4a1a;color:#2ecc71;font-weight:bold"
            if val < 101:   return "color:#e74c3c;font-weight:500"
            if val < 102:   return "color:#e67e22;font-weight:500"
        return ""

    styled = (
        df.style
        .map(_cor_margem, subset=["Margem (%)"])
        .format({"Margem (%)": "{:.2f}%", "Bet365 VB": "{}", "Bet365 Op": "{}",
                 "Melhor VB": "{:.3f}", "Melhor Op": "{:.3f}"})
    )
    st.dataframe(styled, width="stretch", hide_index=True)

    # Detalhes com links para execução
    st.markdown("### 📌 Como executar")
    for d in dados[:15]:
        if d["eh_arb"]:
            icone = "🚨"
        elif d["margem_pct"] < 101:
            icone = "🔥"
        else:
            icone = "👀"

        label = f"{icone} {d['jogo']} | {d['mercado']} | Margem: {d['margem_pct']:.2f}%"
        with st.expander(label, expanded=d["eh_arb"]):
            st.markdown(f"**{d['jogo']}** — {d['liga']} — {d['horario']}")
            st.markdown(f"Mercado: `{d['mercado']}` | Tipo: `{d['tipo']}`")

            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Bet365**")
                link_b = d.get("link_b365", "")
                label_b = f"[↗ Abrir Bet365]({link_b})" if link_b else "Sem link"
                st.markdown(label_b)
                st.metric(f"Lado {d['lado_vb'].upper()}", f"{d['odd_b365_vb']:.3f}")
                st.metric(f"Lado {d['lado_op'].upper()}", f"{d['odd_b365_op']:.3f}")

            with col2:
                st.markdown("**Betano**")
                link_n = d.get("link_betano", "")
                label_n = f"[↗ Abrir Betano]({link_n})" if link_n else "Sem link direto"
                st.markdown(label_n)
                st.metric(f"Lado {d['lado_vb'].upper()}", f"{d['odd_betano_vb']:.3f}" if d['odd_betano_vb'] else "—")
                st.metric(f"Lado {d['lado_op'].upper()}", f"{d['odd_betano_op']:.3f}" if d['odd_betano_op'] else "—")

            st.divider()
            if d["eh_arb"]:
                lucro_por_100 = round(100 - (100/d["melhor_vb"] + 100/d["melhor_op"]), 2)
                st.success(
                    f"✅ ARB REAL — Aposte nos dois lados com as melhores odds. "
                    f"Lucro estimado: R$ {lucro_por_100:.2f} por R$ 100 investidos."
                )
                # Calcular stakes ótimas para R$100
                retorno = 100 + lucro_por_100
                stake_vb = round(retorno / d["melhor_vb"], 2)
                stake_op = round(retorno / d["melhor_op"], 2)
                total    = round(stake_vb + stake_op, 2)
                linhas_stakes = (
                    f"**Stakes para R$ {total:.2f} investidos:**\n\n"
                    f"- Lado `{d['lado_vb'].upper()}` @ {d['melhor_vb']:.3f} → **R$ {stake_vb:.2f}**\n"
                    f"- Lado `{d['lado_op'].upper()}` @ {d['melhor_op']:.3f} → **R$ {stake_op:.2f}**"
                )
                st.markdown(linhas_stakes)
            else:
                st.info(
                    f"Margem atual: {d['margem_pct']:.2f}% — "
                    f"Falta {d['margem_pct']-100:.2f}% para virar arb real. "
                    "Monitore — odds mudam rapidamente."
                )


# ---------------------------------------------------------------------------
# Layout principal
# ---------------------------------------------------------------------------

modo = st.radio(
    "Modo",
    ["⚽ Futebol", "🏀 NBA", "🎾 Tênis", "🔍 Comparação"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

if modo == "⚽ Futebol":
    _render_futebol()
elif modo == "🏀 NBA":
    _render_nba()
elif modo == "🎾 Tênis":
    _render_tenis()
else:
    _render_comparacao()
