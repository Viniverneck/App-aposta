import streamlit as st
import pandas as pd
from main_engine import (
    rodar_sistema,
    montar_multipla,
    get_arbitrage,
    processar_arbitrage,
    get_dropping_odds,
    get_events,
    get_value_bets,
    CACHE_TTL_EVENTOS,
    CACHE_TTL_VALUE_BETS,
    CACHE_TTL_DROPPING,
    CACHE_TTL_HISTORICO,
)
from stats_historicas import buscar_stats_ligas

# ---------------------------------------------------------------------------
# Funções cacheadas
# ---------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_HISTORICO, show_spinner=False)
def _cached_stats_ligas() -> dict:
    return buscar_stats_ligas()

@st.cache_data(ttl=CACHE_TTL_EVENTOS, show_spinner=False)
def _cached_events() -> list[dict]:
    return get_events()

@st.cache_data(ttl=CACHE_TTL_VALUE_BETS, show_spinner=False)
def _cached_value_bets(bookmaker: str) -> list[dict]:
    return get_value_bets(bookmaker)

@st.cache_data(ttl=CACHE_TTL_DROPPING, show_spinner=False)
def _cached_dropping(min_drop: float) -> dict:
    return get_dropping_odds(min_drop_pct=min_drop)


# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Trader PRO", page_icon="🚀", layout="wide")
st.title("🚀 Trader PRO — Sistema Completo")

# ---------------------------------------------------------------------------
# Session state — persiste resultados entre re-runs
# ---------------------------------------------------------------------------
# O Streamlit reroda o script inteiro a cada interação (toggle, number_input,
# etc). Sem session_state os resultados somem. Aqui guardamos tudo que foi
# buscado para que a calculadora e o toggle funcionem sem rebuscar a API.

if "resultados"  not in st.session_state: st.session_state["resultados"]  = None
if "arbs"        not in st.session_state: st.session_state["arbs"]        = None
if "drop_index"  not in st.session_state: st.session_state["drop_index"]  = None
if "banca_busca" not in st.session_state: st.session_state["banca_busca"] = 1_000.0
if "qtd_busca"   not in st.session_state: st.session_state["qtd_busca"]   = 10


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Parâmetros")

    odd_min = st.number_input("Odd mínima", min_value=1.0, max_value=10.0, value=1.10, step=0.05)
    odd_max = st.number_input("Odd máxima", min_value=1.0, max_value=10.0, value=2.00, step=0.05)
    qtd     = st.slider("Picks exibidos", min_value=1, max_value=50, value=10)
    banca   = st.number_input(
        "Banca (R$)", min_value=100.0, max_value=100_000.0,
        value=1_000.0, step=100.0,
        help="Usada para Kelly (EV) e calculadora de arb.",
    )

    st.divider()
    st.markdown("**Mercados** _(só para aba EV)_")
    MERCADOS_DISPONIVEIS = ["ML", "Totals", "Over/Under", "Asian Handicap",
                            "Corner", "BTTS", "Double Chance"]
    mercados_sel = st.multiselect(
        "Filtrar por mercado",
        options=MERCADOS_DISPONIVEIS,
        default=["ML", "Totals", "Over/Under"],
        help="Filtro de mercado não afeta a busca de arbitragem.",
    )
    mercados_filtro: set[str] | None = set(mercados_sel) if mercados_sel else None

    st.divider()
    st.markdown("**Dropping Odds**")
    min_drop = st.slider("Queda mínima (%)", min_value=1, max_value=30, value=5,
                         help="Sinaliza sharp money. Queda >= X% desde a abertura.")

    st.divider()
    st.caption(
        "🗄️ Cache ativo:\n"
        "• Eventos: 5 min\n"
        "• Value Bets: 30s\n"
        "• Dropping Odds: 1 min\n"
        "• Stats históricas: 24h\n"
        "• Arbitragem: sempre ao vivo"
    )
    buscar = st.button("🔎 Buscar oportunidades", use_container_width=True)


# ---------------------------------------------------------------------------
# Ao clicar em Buscar — executa a API e salva no session_state
# ---------------------------------------------------------------------------

if buscar:
    with st.spinner("Buscando oportunidades de EV..."):
        stats = _cached_stats_ligas()
        st.session_state["resultados"] = rodar_sistema(
            odd_min, odd_max, mercados_filtro, stats_ligas=stats
        )

    with st.spinner("Buscando arbitragens ao vivo..."):
        arbs_raw = get_arbitrage(limit=50)
        st.session_state["arbs"] = processar_arbitrage(arbs_raw)

    with st.spinner("Buscando dropping odds..."):
        st.session_state["drop_index"] = _cached_dropping(float(min_drop))

    # Guardar banca e qtd usados na busca para referência na calculadora
    st.session_state["banca_busca"] = banca
    st.session_state["qtd_busca"]   = qtd


# ---------------------------------------------------------------------------
# Exibição — roda sempre que há dados, independente do botão
# ---------------------------------------------------------------------------

resultados = st.session_state["resultados"]
arbs       = st.session_state["arbs"]
drop_index = st.session_state["drop_index"]
banca_ref  = st.session_state["banca_busca"]
qtd_ref    = st.session_state["qtd_busca"]

if resultados is None:
    st.info("Configure os parâmetros na sidebar e clique em **🔎 Buscar oportunidades**.")
    st.stop()


# ---------------------------------------------------------------------------
# Helpers de cor
# ---------------------------------------------------------------------------

def _cor_ev(val: float) -> str:
    if val > 0: return "color: #2ecc71; font-weight: 500"
    if val < 0: return "color: #e74c3c"
    return ""

def _cor_profit(val: float) -> str:
    return "color: #2ecc71; font-weight: 500" if val > 0 else ""


# ---------------------------------------------------------------------------
# Aba EV
# ---------------------------------------------------------------------------

def _tabela_ev(df: pd.DataFrame, key: str) -> None:
    so_sharp = st.toggle("🔥 Mostrar apenas picks com Sharp Signal", value=False, key=key)
    df_view  = df[df["drop_sinal"] == True] if so_sharp else df  # noqa: E712

    if df_view.empty:
        st.info("Nenhum pick com sharp signal. Desative o filtro para ver todos.")
        return

    RENAME = {
        "jogo": "Jogo", "liga": "Liga", "horario": "Horário", "tipo": "Tipo",
        "mercado": "Mercado", "linha": "Linha", "casa": "Casa", "odd": "Odd",
        "prob_modelo": "Prob. (%)", "ev": "EV", "score": "Score",
        "fonte": "Fonte", "drop_sinal": "Sharp 🔥",
    }
    styled = (
        df_view.rename(columns=RENAME).style
        .map(_cor_ev, subset=["EV"])
        .format({"Odd": "{:.2f}", "Prob. (%)": "{:.1f}%",
                 "EV": "{:+.3f}", "Score": "{:.2f}"})
    )
    st.dataframe(styled, width="stretch", hide_index=True)


def _exibir_multipla(multi: dict) -> None:
    st.subheader("🎯 Múltipla Inteligente")
    if not multi["picks"]:
        st.warning("Sem picks suficientes para montar a múltipla.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Odd total",       f"{multi['odd_total']:.2f}")
    c2.metric("Prob. combinada", f"{multi['prob_total']:.1f}%")
    c3.metric("EV",              f"{multi['ev']:+.3f}")
    c4.metric("Stake sugerida",  f"R$ {multi['stake']:.2f}")

    st.markdown("**Picks selecionados (jogos distintos):**")
    for i, p in enumerate(multi["picks"], start=1):
        sharp = " 🔥" if p.get("drop_sinal") else ""
        fonte = f" _[{p.get('fonte', '')}]_" if p.get("fonte") else ""
        st.markdown(
            f"{i}. **{p['jogo']}** — {p['tipo']} @ {p['odd']:.2f} "
            f"_(EV: {p['ev']:+.3f})_{fonte}{sharp}"
        )


# ---------------------------------------------------------------------------
# Aba Arbitragem
# ---------------------------------------------------------------------------

def _calcular_stakes_por_meta(
    legs: list[dict],
    banca_total: float,
    lucro_desejado: float,
) -> list[dict] | None:
    """
    Recalcula stakes para atingir lucro_desejado com banca_total.
    Retorna None se banca insuficiente.
    """
    retorno_alvo   = banca_total + lucro_desejado
    resultado      = []
    total_investido = 0.0

    for leg in legs:
        try:
            odd = float(leg.get("odds", 0))
        except (TypeError, ValueError):
            return None
        if odd <= 1.0:
            return None
        stake = retorno_alvo / odd
        total_investido += stake
        resultado.append({
            "bookmaker": leg.get("bookmaker", ""),
            "side":      leg.get("side", ""),
            "odd":       odd,
            "stake":     round(stake, 2),
            "retorno":   round(stake * odd, 2),
            "link":      leg.get("directLink") or leg.get("href", ""),
        })

    if total_investido > banca_total:
        return None

    lucro_real = round(retorno_alvo - total_investido, 2)
    for r in resultado:
        r["total_investido"] = round(total_investido, 2)
        r["lucro_garantido"] = lucro_real

    return resultado


def _exibir_arbitrage(arbs: list[dict], banca_sidebar: float) -> None:
    if not arbs:
        st.info("Nenhuma oportunidade de arbitragem encontrada no momento.")
        return

    # Tabela resumo
    resumo_rows = []
    for arb in arbs:
        legs   = arb.get("legs", [])
        stakes = arb.get("optimal_stakes", [])
        legs_desc = " / ".join(
            f"{l.get('bookmaker')} {l.get('side','').upper()} @ {l.get('odds','?')}"
            for l in legs
        )
        retorno_base = stakes[0].get("potentialReturn", 0) if stakes else 0
        resumo_rows.append({
            "Jogo":             arb["jogo"],
            "Liga":             arb["liga"],
            "Horário":          arb["horario"],
            "Mercado":          arb["mercado"],
            "Lucro (%)":        arb["profit_pct"],
            "Legs":             legs_desc,
            "Retorno p/ R$100": round(retorno_base, 2),
        })

    df_arb = pd.DataFrame(resumo_rows)
    st.dataframe(
        df_arb.style.map(_cor_profit, subset=["Lucro (%)"]),
        width="stretch", hide_index=True,
    )

    st.info(
        "ℹ️ O filtro de odds da sidebar **não afeta** esta aba. "
        "Arbs são avaliadas pelo lucro total garantido, não pelas odds individuais."
    )

    st.markdown("### 📌 Como executar cada oportunidade")

    for arb in arbs[:10]:
        legs    = arb.get("legs", [])
        stakes  = arb.get("optimal_stakes", [])
        leg_map = {(l.get("bookmaker"), l.get("side")): l for l in legs}
        arb_id  = arb.get("id", arb["jogo"])  # fallback se não tiver id

        label = (
            f"📌 {arb['jogo']}  |  {arb['mercado']}  |  "
            f"Lucro garantido: **{arb['profit_pct']:.2f}%**"
        )
        with st.expander(label):

            # ── Execução padrão ───────────────────────────────────────────
            st.markdown("#### Execução padrão")
            st.caption("Stakes calculadas pela API para maximizar o lucro proporcional.")

            cols_h = st.columns([3, 1, 1, 1])
            cols_h[0].markdown("**Casa / Lado**")
            cols_h[1].markdown("**Odd**")
            cols_h[2].markdown("**Stake**")
            cols_h[3].markdown("**Retorno**")

            for s in stakes:
                bk    = s.get("bookmaker", "")
                side  = s.get("side", "")
                leg   = leg_map.get((bk, side), {})
                link  = leg.get("directLink") or leg.get("href", "")
                row   = st.columns([3, 1, 1, 1])
                row[0].markdown(
                    f"**{bk}** — {side.upper()}"
                    + (f"  [↗ abrir]({link})" if link else "")
                )
                row[1].markdown(f"`{leg.get('odds', '?')}`")
                row[2].markdown(f"R$ {s.get('stake', 0):.2f}")
                row[3].markdown(f"R$ {s.get('potentialReturn', 0):.2f}")

            if stakes:
                ret_pad   = stakes[0].get("potentialReturn", 0)
                total_pad = sum(s.get("stake", 0) for s in stakes)
                st.success(
                    f"✅ Investimento: R$ {total_pad:.2f}  →  "
                    f"Retorno: R$ {ret_pad:.2f}  →  "
                    f"Lucro: R$ {ret_pad - total_pad:.2f} (qualquer resultado)"
                )

            st.divider()

            # ── Calculadora de meta ───────────────────────────────────────
            st.markdown("#### 🎯 Calculadora — defina sua meta")
            st.caption(
                "Informe quanto você tem disponível e quanto quer lucrar. "
                "O sistema recalcula as stakes sem buscar a API novamente."
            )

            cc1, cc2 = st.columns(2)
            banca_calc = cc1.number_input(
                "Banca disponível (R$)",
                min_value=10.0, max_value=1_000_000.0,
                value=float(banca_sidebar), step=10.0,
                key=f"banca_calc_{arb_id}",
            )
            lucro_meta = cc2.number_input(
                "Lucro desejado (R$)",
                min_value=1.0, max_value=1_000_000.0,
                value=max(1.0, round(banca_calc * arb["profit_pct"] / 100, 2)),
                step=1.0,
                key=f"lucro_meta_{arb_id}",
                help="O sistema verifica se sua banca cobre essa meta.",
            )

            # Cálculo reativo — roda a cada mudança nos number_inputs acima
            # sem chamar nenhum endpoint de API
            stakes_custom = _calcular_stakes_por_meta(legs, banca_calc, lucro_meta)

            if stakes_custom is None:
                lucro_max = round(banca_calc * arb["profit_pct"] / 100, 2)
                st.warning(
                    f"⚠️ Banca insuficiente para R$ {lucro_meta:.2f} de lucro. "
                    f"Com R$ {banca_calc:.2f} o máximo possível aqui é "
                    f"**R$ {lucro_max:.2f}** ({arb['profit_pct']:.2f}%)."
                )
            else:
                cols_h2 = st.columns([3, 1, 1, 1])
                cols_h2[0].markdown("**Casa / Lado**")
                cols_h2[1].markdown("**Odd**")
                cols_h2[2].markdown("**Stake**")
                cols_h2[3].markdown("**Retorno**")

                for r in stakes_custom:
                    row = st.columns([3, 1, 1, 1])
                    link = r.get("link", "")
                    row[0].markdown(
                        f"**{r['bookmaker']}** — {r['side'].upper()}"
                        + (f"  [↗ abrir]({link})" if link else "")
                    )
                    row[1].markdown(f"`{r['odd']:.2f}`")
                    row[2].markdown(f"R$ {r['stake']:.2f}")
                    row[3].markdown(f"R$ {r['retorno']:.2f}")

                t_inv = stakes_custom[0]["total_investido"]
                l_grt = stakes_custom[0]["lucro_garantido"]
                st.success(
                    f"✅ Investimento: R$ {t_inv:.2f}  →  "
                    f"Lucro garantido: R$ {l_grt:.2f}  "
                    f"(sobra R$ {banca_calc - t_inv:.2f} de banca)"
                )

            st.caption(f"Atualizado: {arb.get('updated_at', '—')}")


# ---------------------------------------------------------------------------
# Aba Dropping Odds
# ---------------------------------------------------------------------------

def _exibir_dropping(index: dict) -> None:
    if not index:
        st.info("Nenhuma odd com queda significativa encontrada.")
        return

    rows = [
        {
            "Event ID":     eid,
            "Mercado":      d.get("market", ""),
            "Lado":         d.get("bet_side", ""),
            "Odd abertura": d.get("odd_abertura"),
            "Odd atual":    d.get("odd_atual"),
            "Queda (%)":    d.get("drop_pct", 0),
        }
        for eid, d in index.items()
    ]
    df = pd.DataFrame(rows).sort_values("Queda (%)", ascending=False)
    st.dataframe(
        df.style.format({"Queda (%)": "{:.1f}%", "Odd abertura": "{:.2f}", "Odd atual": "{:.2f}"}),
        width="stretch", hide_index=True,
    )


# ---------------------------------------------------------------------------
# Abas — exibem sempre que há dados no session_state
# ---------------------------------------------------------------------------

aba_ev, aba_arb, aba_drop = st.tabs(
    ["📊 Oportunidades EV", "⚖️ Arbitragem", "📉 Dropping Odds"]
)

# ── Aba 1: EV ───────────────────────────────────────────────────────────────
with aba_ev:
    if not resultados:
        st.warning("Nenhuma oportunidade encontrada para os filtros informados.")
    else:
        COLUNAS_EV = [
            "jogo", "liga", "horario", "tipo", "mercado", "linha",
            "casa", "odd", "prob_modelo", "ev", "score", "fonte", "drop_sinal",
        ]
        df = (
            pd.DataFrame(resultados)
            .reindex(columns=COLUNAS_EV)
            .sort_values("ev", ascending=False)
            .head(qtd_ref)
            .reset_index(drop=True)
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total de picks",   len(df))
        m2.metric("EV médio",         f"{df['ev'].mean():+.3f}")
        m3.metric("Odd média",        f"{df['odd'].mean():.2f}")
        m4.metric("Com sharp signal", int(df["drop_sinal"].sum()))

        _tabela_ev(df, key="sharp_toggle_ev")
        st.divider()
        _exibir_multipla(montar_multipla(resultados, banca_ref))

# ── Aba 2: Arbitragem ────────────────────────────────────────────────────────
with aba_arb:
    if arbs:
        a1, a2, a3 = st.columns(3)
        a1.metric("Oportunidades", len(arbs))
        a2.metric("Maior lucro",   f"{arbs[0]['profit_pct']:.2f}%")
        a3.metric("Lucro médio",   f"{sum(a['profit_pct'] for a in arbs) / len(arbs):.2f}%")

    _exibir_arbitrage(arbs or [], banca_sidebar=banca_ref)

# ── Aba 3: Dropping Odds ─────────────────────────────────────────────────────
with aba_drop:
    if drop_index:
        st.markdown(f"Odds com queda **≥ {min_drop}%** desde a abertura — sinal de sharp money.")
        d1, d2 = st.columns(2)
        d1.metric("Eventos com queda", len(drop_index))
        maior = max(drop_index.values(), key=lambda x: x.get("drop_pct", 0))
        d2.metric("Maior queda", f"{maior['drop_pct']:.1f}%")
        _exibir_dropping(drop_index)
    else:
        st.info("Nenhuma odd com queda significativa encontrada.")
