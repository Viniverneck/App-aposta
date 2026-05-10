import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone
from main_engine import (
    buscar_comparacao_odds,
    buscar_crossing_odds,
    get_eventos_live,
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
from telegram_alert import alertar_arbs, testar_conexao

# ---------------------------------------------------------------------------
# Mapeamento de nomes de casas — API usa "Betano", exibimos "Betano BR"
# ---------------------------------------------------------------------------
NOMES_CASAS = {
    "Betano":   "Betano BR",
    "Bet365":   "Bet365",
}

def _nome_casa(casa: str) -> str:
    """Converte nome interno da API para nome de exibição."""
    base = casa.split(" ")[0]  # remove sufixos como "(no latency)"
    return NOMES_CASAS.get(base, casa)


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
# CSS Responsivo — adapta layout para iPad e mobile
# ---------------------------------------------------------------------------
st.markdown("""
<style>

/* ── Mobile (até 768px) ── */
@media (max-width: 768px) {

    /* Ocultar sidebar por padrão */
    [data-testid="stSidebar"] {
        transform: translateX(-100%) !important;
    }

    [data-testid="stSidebar"][aria-expanded="true"] {
        transform: translateX(0) !important;
    }

    /* Layout coluna única */
    [data-testid="column"] {
        width: 100% !important;
        flex: 1 1 100% !important;
    }

    /* Radio vertical */
    .stRadio > div {
        flex-direction: column !important;
        gap: 4px !important;
    }

    /* Tabelas */
    [data-testid="stDataFrame"] iframe {
        height: 500px !important;
    }
}

</style>
""", unsafe_allow_html=True)


# Modo compacto — menos dados, mais leve para iPad
COMPACTO = st.sidebar.toggle(
    "📱 Modo compacto",
    value=st.session_state.get("modo_compacto", False),
    help="Reduz dados exibidos. Recomendado para iPad e conexões lentas.",
    key="modo_compacto_toggle",
)
st.session_state["modo_compacto"] = COMPACTO


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

DEFAULTS = {
    "res_fut": None, "res_nba": None, "res_tenis": None,
    "arbs_fut": None, "arbs_nba": None, "arbs_tenis": None,
    "comparacao": None, "comp_ts": 0.0,
    "live_dados": None, "live_ts": 0.0,
    "banca": 1_000.0, "qtd": 10,
    "valor_invest": 100.0, "intervalo_refresh": 2,
    "auto_refresh": False,
    "auto_refresh_live": False, "intervalo_live": 1,
    "esp_comp_cache": ["Football","Basketball","Tennis"],
    "margem_max_cache": 3.0,
    "mkt_live_cache": ["ML","Totals"],
    "threshold_live_cache": 0.15,
    "odd_alerta_live": 2.40,
    "telegram_ativo": False,
    "ids_alertados": set(),
    "atualizar_tudo_ts": 0.0,
    "modo_compacto": False,
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
                              default=[
                                  "Points O/U","Rebounds O/U","Assists O/U",
                                  "Double Double",
                                  "Player Points Milestones","Player Rebounds Milestones",
                                  "Player Assists Milestones","Player Threes Milestones",
                              ],
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

    # Botão geral com cooldown de 15 min
    COOLDOWN_GERAL = 15 * 60  # segundos
    ts_geral   = st.session_state.get("atualizar_tudo_ts", 0.0)
    elapsed_g  = time.time() - ts_geral
    cooldown_ativo = ts_geral > 0 and elapsed_g < COOLDOWN_GERAL
    if cooldown_ativo:
        restante_g = int(COOLDOWN_GERAL - elapsed_g)
        st.button(
            f"🔄 Atualizar Tudo ({restante_g//60}min {restante_g%60:02d}s)",
            use_container_width=True, disabled=True,
        )
        buscar_tudo = False
    else:
        buscar_tudo = st.button(
            "🔄 Atualizar Tudo", use_container_width=True, type="primary",
            help="Atualiza Futebol → NBA → Tênis em sequência. Cooldown: 15 min.",
        )

    st.divider()
    st.markdown("**🔍 Comparação de Odds**")
    margem_max = st.slider(
        "Margem máxima (%)", min_value=0.5, max_value=10.0, value=3.0, step=0.5,
        help="Mostra onde soma das probs < 100+X%. Menor = mais próximo de arb.",
    )
    esp_comp = st.multiselect(
        "Esportes", ["Football", "Basketball", "Tennis"],
        default=["Football", "Basketball", "Tennis"],
        label_visibility="collapsed",
    )
    valor_invest = st.number_input(
        "💰 Valor investido (R$)", min_value=10.0, max_value=100_000.0,
        value=float(st.session_state.get("valor_invest", 100.0)), step=10.0,
        help="Base para calcular stakes. Altere e veja o recálculo automático.",
        key="valor_invest_input",
    )
    # Salvar imediatamente — recálculo de stakes sem precisar rebuscar
    st.session_state["valor_invest"] = valor_invest

    auto_refresh = st.toggle(
        "🔄 Auto-refresh (só aba Comparação)",
        value=st.session_state.get("auto_refresh", False),
        help="Atualiza automaticamente só quando você está na aba 🔍 Comparação.",
        key="auto_refresh_toggle",
    )
    st.session_state["auto_refresh"] = auto_refresh

    if auto_refresh:
        intervalo_refresh = st.select_slider(
            "Intervalo", options=[1, 2, 5, 10],
            value=st.session_state.get("intervalo_refresh", 2),
            format_func=lambda x: f"{x} min",
            key="intervalo_slider",
        )
        st.session_state["intervalo_refresh"] = intervalo_refresh

    st.divider()
    st.markdown("**📱 Alertas Telegram**")
    telegram_ativo = st.toggle(
        "🔔 Alertar arb real no Telegram",
        value=st.session_state.get("telegram_ativo", False),
        help="Envia mensagem no Telegram quando uma arb real for detectada.",
        key="telegram_toggle",
    )
    st.session_state["telegram_ativo"] = telegram_ativo
    if telegram_ativo:
        if st.button("📡 Testar conexão Telegram", use_container_width=True, key="test_tg"):
            if testar_conexao():
                st.success("✅ Bot conectado! Verifique o Telegram.")
            else:
                st.error("❌ Falhou. Verifique TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no .env")

    buscar_comp = st.button("🔍 Comparar Odds", use_container_width=True)

    st.divider()
    st.markdown("**⚡ Live — Crossing Odds**")
    st.caption("Futebol ao vivo · Bet365")
    mkt_live = st.multiselect(
        "Mercados live",
        ["ML", "Totals"],
        default=["ML", "Totals"],
        label_visibility="collapsed",
    )
    threshold_live = st.slider(
        "Threshold de cruzamento",
        min_value=0.05, max_value=0.50, value=0.15, step=0.05,
        help="Diferença máxima entre as odds para considerar que estão cruzando. "
             "Ex: 0.15 → alerta quando Casa e Fora estão a menos de 0.15 uma da outra.",
    )
    auto_refresh_live = st.toggle(
        "🔄 Auto-refresh live",
        value=st.session_state.get("auto_refresh_live", False),
        help="Atualiza automaticamente só na aba ⚡ Live.",
        key="auto_refresh_live_toggle",
    )
    st.session_state["auto_refresh_live"] = auto_refresh_live
    if auto_refresh_live:
        intervalo_live = st.select_slider(
            "Intervalo live", options=[1, 2, 5], value=1,
            format_func=lambda x: f"{x} min",
            key="intervalo_live_slider",
        )
        st.session_state["intervalo_live"] = intervalo_live
    buscar_live = st.button("⚡ Buscar Live", use_container_width=True, type="primary")


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

# ── Botão Atualizar Tudo ────────────────────────────────────────────────
if buscar_tudo:
    stats = _cached_stats_ligas()
    prog  = st.progress(0, text="Iniciando...")

    prog.progress(10, text="⚽ Buscando Futebol...")
    st.session_state["res_fut"] = rodar_sistema(
        odd_min, odd_max, set(sel_fut) or None, stats_ligas=stats, modo="futebol"
    )
    st.session_state["arbs_fut"] = processar_arbitrage(
        get_arbitrage(limit=50), esporte="futebol"
    )

    prog.progress(40, text="🏀 Buscando NBA...")
    st.session_state["res_nba"] = rodar_sistema(
        odd_min, odd_max, set(sel_nba) or None, stats_ligas=stats, modo="nba"
    )
    st.session_state["arbs_nba"] = processar_arbitrage(
        get_arbitrage(limit=50), esporte="nba"
    )

    prog.progress(70, text="🎾 Buscando Tênis...")
    st.session_state["res_tenis"] = rodar_sistema(
        odd_min, odd_max, set(sel_tenis) or None, stats_ligas={}, modo="tenis"
    )
    st.session_state["arbs_tenis"] = processar_arbitrage(
        get_arbitrage(limit=50), esporte="tenis"
    )

    prog.progress(100, text="✅ Concluído!")
    st.session_state["banca"]          = banca
    st.session_state["qtd"]            = qtd
    st.session_state["atualizar_tudo_ts"] = time.time()
    time.sleep(0.5)
    prog.empty()

# Salvar esp_comp e margem_max para o auto-refresh usar
st.session_state["esp_comp_cache"]  = esp_comp if esp_comp else ["Football","Basketball","Tennis"]
st.session_state["margem_max_cache"] = margem_max

if buscar_comp:
    with st.spinner("Comparando odds Bet365 x Betano BR..."): 
        st.session_state["comparacao"] = buscar_comparacao_odds(
            esportes=st.session_state["esp_comp_cache"],
            margem_max=margem_max,
        )
        st.session_state["comp_ts"] = time.time()
        if st.session_state.get("telegram_ativo"):
            st.session_state["ids_alertados"] = alertar_arbs(
                st.session_state["comparacao"],
                valor_invest=st.session_state.get("valor_invest", 100.0),
                ids_ja_enviados=st.session_state.get("ids_alertados", set()),
            )

if buscar_live:
    with st.spinner("Buscando crossing odds ao vivo..."):
        st.session_state["live_dados"] = buscar_crossing_odds(
            threshold=threshold_live,
            mercados=mkt_live or ["ML","Totals"],
        )
        st.session_state["live_ts"]       = time.time()
        st.session_state["mkt_live_cache"]      = mkt_live
        st.session_state["threshold_live_cache"] = threshold_live

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
            casa  = _nome_casa(row.get("casa", "").split(" ")[0])
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
        casa  = _nome_casa(p.get("casa", "").split(" ")[0])
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
                row[0].markdown(f"**{_nome_casa(bk)}** — {side.upper()}" + (f"  [↗]({link})" if link else ""))
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
                .assign(casa=lambda x: x["casa"].apply(_nome_casa))
                .sort_values("ev", ascending=False)
                .head(min(qtd_ref, 5) if COMPACTO else qtd_ref)
                .reset_index(drop=True)
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
                .assign(casa=lambda x: x["casa"].apply(_nome_casa))
                .sort_values("ev", ascending=False)
                .head(min(qtd_ref, 5) if COMPACTO else qtd_ref)
                .reset_index(drop=True)
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
                .assign(casa=lambda x: x["casa"].apply(_nome_casa))
                .sort_values("ev", ascending=False)
                .head(min(qtd_ref, 5) if COMPACTO else qtd_ref)
                .reset_index(drop=True)
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

def _cor_data(horario: str) -> str:
    """Verde se hoje, amarelo se amanhã."""
    try:
        agora_br = datetime.now(timezone.utc)
        dia, mes = int(horario[:2]), int(horario[3:5])
        if agora_br.day == dia and agora_br.month == mes:
            return "background-color:#1a3a1a;color:#2ecc71;font-weight:500"
        if agora_br.day + 1 == dia and agora_br.month == mes:
            return "background-color:#3a2a00;color:#f0c040"
    except Exception:
        pass
    return ""


def _render_comparacao():
    dados     = st.session_state["comparacao"]
    valor_inv = st.session_state.get("valor_invest", 100.0)
    auto_ref  = st.session_state.get("auto_refresh", False)
    intervalo = st.session_state.get("intervalo_refresh", 2)
    comp_ts   = st.session_state.get("comp_ts", 0.0)

    # ── Auto-refresh ──────────────────────────────────────────────────────
    if auto_ref and comp_ts > 0:
        segundos = intervalo * 60
        elapsed  = time.time() - comp_ts
        restante = max(0, int(segundos - elapsed))
        cr1, cr2 = st.columns([3, 1])
        cr1.caption(f"🔄 Próxima atualização em {restante}s (intervalo: {intervalo} min)")
        if cr2.button("Atualizar agora", key="refresh_now"):
            elapsed = segundos
        if elapsed >= segundos:
            with st.spinner("Atualizando..."):
                st.session_state["comparacao"] = buscar_comparacao_odds(
                    esportes=st.session_state.get("esp_comp_cache", ["Football","Basketball","Tennis"]),
                    margem_max=st.session_state.get("margem_max_cache", 3.0),
                )
                st.session_state["comp_ts"] = time.time()
                if st.session_state.get("telegram_ativo"):
                    st.session_state["ids_alertados"] = alertar_arbs(
                        st.session_state["comparacao"],
                        valor_invest=st.session_state.get("valor_invest", 100.0),
                        ids_ja_enviados=st.session_state.get("ids_alertados", set()),
                    )
            st.rerun()
        else:
            time.sleep(1)
            st.rerun()

    if dados is None:
        st.info("Configure os parâmetros em **🔍 Comparação de Odds** na sidebar e clique em **Comparar Odds**.")
        return

    if not dados:
        st.warning("Nenhuma linha encontrada. Tente aumentar a margem máxima.")
        return

    arbs_reais = [d for d in dados if d["eh_arb"]]
    quentes    = [d for d in dados if not d["eh_arb"] and d["margem_pct"] < 101]

    # ── Métricas ──────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Linhas",        len(dados))
    m2.metric("🚨 Arbs reais", len(arbs_reais))
    m3.metric("🔥 Quentes",    len(quentes))
    m4.metric("Menor margem",  f"{dados[0]['margem_pct']:.2f}%")

    if arbs_reais:
        st.error(f"🚨 {len(arbs_reais)} ARB(S) DETECTADA(S) — Execute imediatamente!")

    st.divider()

    # ── Tabela ordenada por margem ────────────────────────────────────────
    rows = []
    for d in dados:
        if d["eh_arb"]:              status = "🚨 ARB"
        elif d["margem_pct"] < 101:  status = "🔥 Quente"
        elif d["margem_pct"] < 102:  status = "⚡ Próximo"
        else:                        status = "👀 Monitorar"
        rows.append({
            "Status":     status,
            "Jogo":       d["jogo"],
            "Esporte":    d["esporte"],
            "Horário":    d["horario"],
            "Mercado":    d["mercado"],
            "Tipo":       d["tipo"],
            "Bet365 VB":  d["odd_b365_vb"],
            "🔗 Bet365":  d.get("link_b365", "") or "",
            "Bet365 Op":  d["odd_b365_op"],
            "Betano BR VB":  d["odd_betano_vb"] or "—",
            "🔗 Betano BR":  d.get("link_betano", "") or "",
            "Betano BR Op":  d["odd_betano_op"] or "—",
            "Melhor VB":  d["melhor_vb"],
            "Melhor Op":  d["melhor_op"],
            "Margem (%)": d["margem_pct"],
        })

    df = pd.DataFrame(rows).sort_values(["Margem (%)", "Horário"])

    def _cor_margem(val):
        if isinstance(val, float):
            if val < 100: return "background-color:#1a4a1a;color:#2ecc71;font-weight:bold"
            if val < 101: return "color:#e74c3c;font-weight:500"
            if val < 102: return "color:#e67e22;font-weight:500"
        return ""

    st.dataframe(
        df,
        column_config={
            "Margem (%)": st.column_config.NumberColumn("Margem (%)", format="%.2f%%"),
            "Melhor VB":  st.column_config.NumberColumn("Melhor VB",  format="%.3f"),
            "Melhor Op":  st.column_config.NumberColumn("Melhor Op",  format="%.3f"),
            "🔗 Bet365":  st.column_config.LinkColumn("🔗 Bet365",  display_text="Abrir"),
            "🔗 Betano BR":  st.column_config.LinkColumn("🔗 Betano BR",  display_text="Abrir"),
        },
        width="stretch", hide_index=True,
    )

# ---------------------------------------------------------------------------
# MODO LIVE — Crossing Odds
# ---------------------------------------------------------------------------

def _render_live():
    dados    = st.session_state.get("live_dados")
    live_ts  = st.session_state.get("live_ts", 0.0)
    auto_ref = st.session_state.get("auto_refresh_live", False)
    intervalo = st.session_state.get("intervalo_live", 1)
    valor_inv = st.session_state.get("valor_invest", 100.0)

    # ── Auto-refresh ──────────────────────────────────────────────────────
    if auto_ref and live_ts > 0:
        segundos = intervalo * 60
        elapsed  = time.time() - live_ts
        restante = max(0, int(segundos - elapsed))
        cr1, cr2 = st.columns([3, 1])
        cr1.caption(f"🔄 Atualização em {restante}s (intervalo: {intervalo} min)")
        if cr2.button("Agora", key="live_refresh_now"):
            elapsed = segundos
        if elapsed >= segundos:
            with st.spinner("Atualizando crossing odds..."):
                st.session_state["live_dados"] = buscar_crossing_odds(
                    threshold=st.session_state.get("threshold_live_cache", 0.15),
                    mercados=st.session_state.get("mkt_live_cache", ["ML","Totals"]),
                )
                st.session_state["live_ts"] = time.time()
            st.rerun()
        else:
            time.sleep(1)
            st.rerun()

    if dados is None:
        st.info("Clique em **⚡ Buscar Live** na sidebar para iniciar.")
        st.caption("🕐 Pico: Futebol 14h–22h | NBA 21h–04h | Tênis varia")
        # Mostrar calculadora mesmo sem dados
    elif not dados:
        st.warning("Nenhum sinal de crossing odds no momento.")
        st.caption("🕐 Horários de pico: 14h–22h (fins de semana têm mais jogos)")
    else:
        cruzados  = [d for d in dados if d["cruzado"]]
        cruzando  = [d for d in dados if d["cruzando"] and not d["cruzado"]]
        monitorar = [d for d in dados if not d["cruzado"] and not d["cruzando"]]

        # ── Métricas ──────────────────────────────────────────────────────
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🔴 Jogos ao vivo",  len({d["jogo"] for d in dados}))
        m2.metric("🎯 Já cruzaram",    len(cruzados))
        m3.metric("⚡ Cruzando agora", len(cruzando))
        m4.metric("📊 Monitorar",      len(monitorar))

        if cruzados:
            st.error(f"🎯 {len(cruzados)} odd(s) já cruzaram!")
        if cruzando:
            st.warning(f"⚡ {len(cruzando)} odd(s) prestes a cruzar!")

        st.divider()

        # ── Tabela ────────────────────────────────────────────────────────
        rows = []
        for d in dados:
            rows.append({
                "Status":      d["status"],
                "Jogo":        d["jogo"],
                "Liga":        d["liga"],
                "Mercado":     d["mercado"],
                "Casa Odd":    d["odd_home"],
                "Fora Odd":    d["odd_away"],
                "Diferença":   d["diff"],
                "Prob Casa":   f"{d['prob_home']}%",
                "Prob Fora":   f"{d['prob_away']}%",
                "Sinal":       d["apostar_em"],
                "🔗 Aposta":   d.get("link", "") or "",
            })

        df = pd.DataFrame(rows)

        def _cor_status_live(val):
            if "Cruzado"  in str(val): return "color:#2ecc71;font-weight:bold"
            if "Cruzando" in str(val): return "color:#e67e22;font-weight:500"
            return ""

        def _cor_diff_live(val):
            if isinstance(val, float):
                if val < 0.05: return "color:#2ecc71;font-weight:bold"
                if val < 0.15: return "color:#e67e22"
            return ""

        st.dataframe(
            df,
            column_config={
                "Casa Odd":  st.column_config.NumberColumn("Casa Odd",  format="%.3f"),
                "Fora Odd":  st.column_config.NumberColumn("Fora Odd",  format="%.3f"),
                "Diferença": st.column_config.NumberColumn("Diferença", format="%.3f"),
                "🔗 Aposta": st.column_config.LinkColumn("🔗 Aposta", display_text="Abrir"),
            },
            width="stretch", hide_index=True,
        )
        st.divider()

    # ── Calculadora Automática de Cruzamento ──────────────────────────────
    st.markdown("### 🧮 Calculadora de Cruzamento")

    cv1, cv2 = st.columns([2, 1])
    odd_min_cruzamento = cv1.slider(
        "🎯 Odd mínima do cruzamento",
        min_value=1.80, max_value=4.00, value=2.40, step=0.05,
        help="Mostra só jogos onde AMBAS as odds estão acima deste valor. "
             "2.40 = lucro ~16% | 2.70 = lucro ~26% | 3.00 = lucro ~33%",
        key="odd_min_cruzamento_slider",
    )
    val_calc = cv2.number_input(
        "💰 Valor investido (R$)",
        min_value=10.0, max_value=100_000.0,
        value=float(st.session_state.get("valor_invest", 100.0)),
        step=10.0, key="calc_valor_auto",
        help="Altere e todas as stakes recalculam automaticamente.",
    )
    st.session_state["valor_invest"] = val_calc

    if not dados:
        st.info("Busque os jogos ao vivo para ver a calculadora em ação.")
        return

    dados_filtrados = [
        d for d in dados
        if d["odd_home"] >= odd_min_cruzamento and d["odd_away"] >= odd_min_cruzamento
    ]

    if not dados_filtrados:
        lucro_est = round((1 - 2/odd_min_cruzamento) * 100, 1)
        st.info(
            f"Nenhum jogo com ambas as odds ≥ {odd_min_cruzamento:.2f}. "
            f"Cruzamento nessa faixa renderia ~{lucro_est}% de lucro. "
            "Reduza o threshold ou aguarde as odds se moverem."
        )
        return

    lucro_est = round((1 - 2/odd_min_cruzamento) * 100, 1)
    st.caption(
        f"{len(dados_filtrados)} jogo(s) com odds ≥ {odd_min_cruzamento:.2f} "
        f"— cruzamento nessa faixa rende ~{lucro_est}% de lucro"
    )

    # Ordenar: cruzados primeiro → odds mais altas primeiro
    ordem = {"🎯 Cruzado": 0, "⚡ Cruzando": 1, "📊 Monitorar": 2}
    dados_ord = sorted(
        dados_filtrados,
        key=lambda d: (ordem.get(d["status"], 3), -min(d["odd_home"], d["odd_away"]))
    )

    for d in dados_ord:
        odd_h = d["odd_home"]
        odd_a = d["odd_away"]
        odd_min_par = min(odd_h, odd_a)

        soma_p    = (1/odd_h) + (1/odd_a)
        margem_c  = round((soma_p - 1) * 100, 2)
        eh_arb_c  = soma_p < 1.0
        diff_c    = round(abs(odd_h - odd_a), 3)
        lucro_pct = round((1 - soma_p) * 100, 1)

        retorno_alvo = val_calc / soma_p
        stake_h      = round(retorno_alvo / odd_h, 2)
        stake_a      = round(retorno_alvo / odd_a, 2)
        total_c      = round(stake_h + stake_a, 2)
        lucro_c      = round(retorno_alvo - total_c, 2)
        odd_alvo_a   = round(1 / (1 - 1/odd_h), 3) if odd_h > 1 else 0

        if eh_arb_c:                            icone = "🚨"
        elif odd_min_par >= odd_min_cruzamento:  icone = "🔥"
        elif odd_min_par >= 2.40:               icone = "⚡"
        else:                                   icone = "📊"

        desc_h = d.get("desc_home", "Casa")
        desc_a = d.get("desc_away", "Fora")

        label_exp = (
            f"{icone} {d['jogo']} | {d['mercado']} | "
            f"{odd_h:.3f} × {odd_a:.3f} | "
            f"Lucro potencial ~{lucro_pct:.1f}%"
        )
        with st.expander(label_exp, expanded=eh_arb_c or icone == "🔥"):
            st.markdown(f"**{d['jogo']}** 🔴 — {d['liga']} — {d['mercado']}")

            cm1, cm2, cm3, cm4 = st.columns(4)
            cm1.metric(desc_h,      f"{odd_h:.3f}", f"{d['prob_home']}%")
            cm2.metric(desc_a,      f"{odd_a:.3f}", f"{d['prob_away']}%")
            cm3.metric("Diferença", f"{diff_c:.3f}")
            cm4.metric("Margem",    f"{margem_c:+.2f}%",
                       delta="✅ ARB!" if eh_arb_c else None)

            df_c = pd.DataFrame([
                {"Lado": desc_h, "Odd": odd_h, "Stake (R$)": stake_h,
                 "Retorno (R$)": round(stake_h*odd_h,2), "Lucro (R$)": round(stake_h*odd_h-total_c,2)},
                {"Lado": desc_a, "Odd": odd_a, "Stake (R$)": stake_a,
                 "Retorno (R$)": round(stake_a*odd_a,2), "Lucro (R$)": round(stake_a*odd_a-total_c,2)},
            ])

            def _cor_lc(val):
                if isinstance(val, float):
                    if val > 0: return "color:#2ecc71;font-weight:500"
                    if val < 0: return "color:#e74c3c"
                return ""

            st.dataframe(
                df_c.style.map(_cor_lc, subset=["Lucro (R$)"])
                .format({"Odd":"{:.3f}","Stake (R$)":"R$ {:.2f}",
                         "Retorno (R$)":"R$ {:.2f}","Lucro (R$)":"R$ {:.2f}"}),
                width="stretch", hide_index=True,
            )

            if eh_arb_c:
                st.success(
                    f"✅ ARB CONFIRMADA — Lucro garantido: R$ {lucro_c:.2f} "
                    f"com R$ {total_c:.2f} investidos."
                )
            elif d["cruzado"] or d["cruzando"]:
                falta = round(odd_alvo_a - odd_a, 3)
                st.warning(
                    f"⚡ Falta {margem_c:.2f}% para arb. "
                    f"Fora precisaria ≥ {odd_alvo_a:.3f} (falta {falta:.3f})."
                )
            else:
                falta = round(odd_alvo_a - odd_a, 3)
                st.info(
                    f"📊 Monitorando — falta {margem_c:.2f}% para arb "
                    f"(Fora precisa de ≥ {odd_alvo_a:.3f}, falta {falta:.3f})."
                )

            if d.get("link"):
                st.markdown(f"🔗 [Abrir na Bet365]({d['link']})")

# ---------------------------------------------------------------------------
# Layout principal
# ---------------------------------------------------------------------------

modo = st.radio(
    "Modo",
    ["⚽ Futebol", "🏀 NBA", "🎾 Tênis", "🔍 Comparação", "⚡ Live"],
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
elif modo == "🔍 Comparação":
    _render_comparacao()
else:
    _render_live()
