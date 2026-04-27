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
# Sidebar — parâmetros de EV (NÃO afetam a aba de Arbitragem)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Parâmetros")

    odd_min = st.number_input("Odd mínima", min_value=1.0, max_value=10.0, value=1.10, step=0.05)
    odd_max = st.number_input("Odd máxima", min_value=1.0, max_value=10.0, value=2.00, step=0.05)
    qtd     = st.slider("Picks exibidos", min_value=1, max_value=50, value=10)
    banca   = st.number_input("Banca (R$)", min_value=100.0, max_value=100_000.0,
                               value=1_000.0, step=100.0,
                               help="Usada para Kelly (EV) e calculadora de arb.")

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
# Helpers de cor
# ---------------------------------------------------------------------------

def _cor_ev(val: float) -> str:
    if val > 0:   return "color: #2ecc71; font-weight: 500"
    if val < 0:   return "color: #e74c3c"
    return ""

def _cor_profit(val: float) -> str:
    return "color: #2ecc71; font-weight: 500" if val > 0 else ""


# ---------------------------------------------------------------------------
# Aba EV — tabela com toggle sharp
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
# Aba Arbitragem — calculadora de stake por meta + detalhes
# ---------------------------------------------------------------------------

def _calcular_stakes_por_meta(
    legs: list[dict],
    banca_total: float,
    lucro_desejado: float,
) -> list[dict] | None:
    """
    Recalcula as stakes de cada leg para atingir um lucro alvo.

    A arb garante que, independente do resultado, o retorno é igual.
    Equação: stake_leg_i = retorno_alvo / odd_leg_i
    Onde: retorno_alvo = banca_total + lucro_desejado

    Retorna None se a banca for insuficiente ou as odds inválidas.
    """
    retorno_alvo = banca_total + lucro_desejado

    resultado = []
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
            "bookmaker":       leg.get("bookmaker", ""),
            "side":            leg.get("side", ""),
            "odd":             odd,
            "stake":           round(stake, 2),
            "retorno":         round(stake * odd, 2),
            "link":            leg.get("directLink") or leg.get("href", ""),
        })

    # Verificar se a banca cobre o investimento total
    if total_investido > banca_total:
        return None  # banca insuficiente para essa meta

    lucro_real = round(retorno_alvo - total_investido, 2)
    for r in resultado:
        r["total_investido"] = round(total_investido, 2)
        r["lucro_garantido"] = lucro_real

    return resultado


def _exibir_arbitrage(arbs: list[dict], banca_sidebar: float) -> None:
    if not arbs:
        st.info("Nenhuma oportunidade de arbitragem encontrada no momento.")
        return

    # ── Tabela resumo ────────────────────────────────────────────────────────
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
            "Jogo":              arb["jogo"],
            "Liga":              arb["liga"],
            "Horário":           arb["horario"],
            "Mercado":           arb["mercado"],
            "Lucro (%)":         arb["profit_pct"],
            "Legs":              legs_desc,
            "Retorno p/ R$100":  round(retorno_base, 2),
        })

    df_arb = pd.DataFrame(resumo_rows)
    styled = df_arb.style.map(_cor_profit, subset=["Lucro (%)"])
    st.dataframe(styled, width="stretch", hide_index=True)

    # ── Aviso sobre filtro de odds ───────────────────────────────────────────
    st.info(
        "ℹ️ O filtro de odds da sidebar **não afeta** esta aba. "
        "Arbs são avaliadas pelo lucro total garantido, não pelas odds individuais.",
        icon=None,
    )

    # ── Detalhes + calculadora por meta ─────────────────────────────────────
    st.markdown("### 📌 Como executar cada oportunidade")

    for arb in arbs[:10]:
        label = (
            f"📌 {arb['jogo']}  |  {arb['mercado']}  |  "
            f"Lucro garantido: **{arb['profit_pct']:.2f}%**"
        )
        with st.expander(label):
            legs   = arb.get("legs", [])
            stakes = arb.get("optimal_stakes", [])
            leg_map = {(l.get("bookmaker"), l.get("side")): l for l in legs}

            # ── Execução padrão (stakes ótimas da API) ───────────────────
            st.markdown("#### Execução padrão")
            st.caption("Stakes calculadas pela API para maximizar o lucro proporcional.")

            cols_header = st.columns([3, 1, 1, 1])
            cols_header[0].markdown("**Casa / Lado**")
            cols_header[1].markdown("**Odd**")
            cols_header[2].markdown("**Stake**")
            cols_header[3].markdown("**Retorno**")

            for s in stakes:
                bk   = s.get("bookmaker", "")
                side = s.get("side", "")
                leg  = leg_map.get((bk, side), {})
                odd_val  = leg.get("odds", "?")
                link     = leg.get("directLink") or leg.get("href", "")
                stake_v  = s.get("stake", 0)
                retorno_v = s.get("potentialReturn", 0)

                row = st.columns([3, 1, 1, 1])
                row[0].markdown(
                    f"**{bk}** — {side.upper()}"
                    + (f"  [↗ abrir]({link})" if link else "")
                )
                row[1].markdown(f"`{odd_val}`")
                row[2].markdown(f"R$ {stake_v:.2f}")
                row[3].markdown(f"R$ {retorno_v:.2f}")

            if stakes:
                retorno_padrao = stakes[0].get("potentialReturn", 0)
                total_padrao   = sum(s.get("stake", 0) for s in stakes)
                lucro_padrao   = retorno_padrao - total_padrao
                st.success(
                    f"✅ Investimento: R$ {total_padrao:.2f}  →  "
                    f"Retorno: R$ {retorno_padrao:.2f}  →  "
                    f"Lucro: R$ {lucro_padrao:.2f} (qualquer resultado)"
                )

            st.divider()

            # ── Calculadora de meta de lucro ─────────────────────────────
            st.markdown("#### 🎯 Calculadora — defina sua meta")
            st.caption(
                "Informe quanto você tem disponível e quanto quer lucrar. "
                "O sistema recalcula as stakes para atingir exatamente essa meta."
            )

            calc_col1, calc_col2 = st.columns(2)
            with calc_col1:
                banca_calc = st.number_input(
                    "Banca disponível (R$)",
                    min_value=10.0,
                    max_value=1_000_000.0,
                    value=float(banca_sidebar),
                    step=10.0,
                    key=f"banca_calc_{arb['id']}",
                )
            with calc_col2:
                lucro_meta = st.number_input(
                    "Lucro desejado (R$)",
                    min_value=1.0,
                    max_value=1_000_000.0,
                    value=round(banca_calc * arb["profit_pct"] / 100, 2),
                    step=1.0,
                    key=f"lucro_meta_{arb['id']}",
                    help="O sistema verifica se sua banca é suficiente para essa meta.",
                )

            stakes_custom = _calcular_stakes_por_meta(legs, banca_calc, lucro_meta)

            if stakes_custom is None:
                lucro_max = round(banca_calc * arb["profit_pct"] / 100, 2)
                st.warning(
                    f"⚠️ Banca insuficiente para essa meta com esta arb. "
                    f"Com R$ {banca_calc:.2f} o lucro máximo possível aqui é "
                    f"**R$ {lucro_max:.2f}** ({arb['profit_pct']:.2f}%)."
                )
            else:
                cols_h = st.columns([3, 1, 1, 1])
                cols_h[0].markdown("**Casa / Lado**")
                cols_h[1].markdown("**Odd**")
                cols_h[2].markdown("**Stake**")
                cols_h[3].markdown("**Retorno**")

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

                total_inv = stakes_custom[0]["total_investido"]
                lucro_grt = stakes_custom[0]["lucro_garantido"]
                st.success(
                    f"✅ Investimento total: R$ {total_inv:.2f}  →  "
                    f"Lucro garantido: R$ {lucro_grt:.2f}  "
                    f"(sobra R$ {banca_calc - total_inv:.2f} de banca)"
                )

            st.caption(f"Atualizado: {arb.get('updated_at', '—')}")


# ---------------------------------------------------------------------------
# Dropping Odds
# ---------------------------------------------------------------------------

def _exibir_dropping(index: dict) -> None:
    if not index:
        st.info("Nenhuma odd com queda significativa encontrada.")
        return

    rows = []
    for eid, d in index.items():
        rows.append({
            "Event ID":     eid,
            "Mercado":      d.get("market", ""),
            "Lado":         d.get("bet_side", ""),
            "Odd abertura": d.get("odd_abertura"),
            "Odd atual":    d.get("odd_atual"),
            "Queda (%)":    d.get("drop_pct", 0),
        })

    df = pd.DataFrame(rows).sort_values("Queda (%)", ascending=False)
    styled = df.style.format({
        "Queda (%)":    "{:.1f}%",
        "Odd abertura": "{:.2f}",
        "Odd atual":    "{:.2f}",
    })
    st.dataframe(styled, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Execução principal
# ---------------------------------------------------------------------------

if buscar:

    aba_ev, aba_arb, aba_drop = st.tabs(
        ["📊 Oportunidades EV", "⚖️ Arbitragem", "📉 Dropping Odds"]
    )

    # ── Aba 1: EV ───────────────────────────────────────────────────────────
    with aba_ev:
        with st.spinner("Buscando oportunidades de EV..."):
            stats      = _cached_stats_ligas()
            resultados = rodar_sistema(odd_min, odd_max, mercados_filtro, stats_ligas=stats)

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
                .head(qtd)
                .reset_index(drop=True)
            )

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total de picks",   len(df))
            m2.metric("EV médio",         f"{df['ev'].mean():+.3f}")
            m3.metric("Odd média",        f"{df['odd'].mean():.2f}")
            m4.metric("Com sharp signal", int(df["drop_sinal"].sum()))

            _tabela_ev(df, key="sharp_toggle_ev")
            st.divider()
            _exibir_multipla(montar_multipla(resultados, banca))

    # ── Aba 2: Arbitragem (sem filtro de odds) ──────────────────────────────
    with aba_arb:
        with st.spinner("Buscando arbitragens ao vivo..."):
            arbs_raw = get_arbitrage(limit=50)
            arbs     = processar_arbitrage(arbs_raw)

        if arbs:
            a1, a2, a3 = st.columns(3)
            a1.metric("Oportunidades", len(arbs))
            a2.metric("Maior lucro",   f"{arbs[0]['profit_pct']:.2f}%")
            a3.metric("Lucro médio",   f"{sum(a['profit_pct'] for a in arbs) / len(arbs):.2f}%")

        _exibir_arbitrage(arbs, banca_sidebar=banca)

    # ── Aba 3: Dropping Odds ────────────────────────────────────────────────
    with aba_drop:
        with st.spinner("Buscando dropping odds..."):
            drop_index = _cached_dropping(float(min_drop))

        st.markdown(
            f"Odds com queda **≥ {min_drop}%** desde a abertura — sinal de sharp money."
        )
        d1, d2 = st.columns(2)
        d1.metric("Eventos com queda", len(drop_index))
        if drop_index:
            maior = max(drop_index.values(), key=lambda x: x.get("drop_pct", 0))
            d2.metric("Maior queda", f"{maior['drop_pct']:.1f}%")

        _exibir_dropping(drop_index)
