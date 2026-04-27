import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dotenv import load_dotenv

from modelo_poisson import matriz_resultados, prob_vitoria
from stats_historicas import buscar_stats_ligas, get_medias_confronto

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

# Compatibilidade local (.env) + Streamlit Cloud (st.secrets)
try:
    import streamlit as st
    _st_key = st.secrets.get('API_KEY', '')
except Exception:
    _st_key = ''

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

API_KEY: str = _st_key or os.getenv("API_KEY", "")
BASE_URL: str = "https://api.odds-api.io/v3"
BOOKMAKERS: str = "Bet365,Betano"

MAX_EVENTOS: int = 30
LOTE_ODDS: int = 10
EV_MINIMO: float = -0.20
KELLY_MAX: float = 0.05
JANELA_HORAS_FUTEBOL: int = 24
JANELA_HORAS_NBA:     int = 48  # NBA joga à noite no fuso BR
FUSO_BRASIL = timezone(timedelta(hours=-3))

LIGAS_PERMITIDAS: set[str] = {
    # Futebol
    "spain-la-liga",
    "italy-serie-a",
    "england-premier-league",
    "germany-bundesliga",
    "france-ligue-1",
    "uefa-champions-league",
    "brazil-serie-a",
    "brazil-copa-do-brasil",
    # Basquete
    "usa-nba",
}

# Esportes mapeados por liga — usado para buscar eventos do esporte correto
LIGA_ESPORTE: dict[str, str] = {
    "spain-la-liga":          "football",
    "italy-serie-a":          "football",
    "england-premier-league": "football",
    "germany-bundesliga":     "football",
    "france-ligue-1":         "football",
    "uefa-champions-league":  "football",
    "brazil-serie-a":         "football",
    "brazil-copa-do-brasil":  "football",
    "usa-nba":                "basketball",
}


# ---------------------------------------------------------------------------
# TTLs de cache — usados pelo Streamlit via st.cache_data
# ---------------------------------------------------------------------------
# Cada endpoint tem velocidade de atualização diferente:
#   EVENTOS     → jogos novos aparecem raramente, 5 min é seguro
#   VALUE_BETS  → API atualiza a cada 5s, cache de 30s equilibra uso/frescor
#   ARBITRAGE   → janela de segundos, SEM cache (sempre ao vivo)
#   DROPPING    → movimento rápido de odds, 60s
#   HISTORICO   → dados estáticos de jogos passados, 24h

CACHE_TTL_EVENTOS:    int = 300    # 5 minutos
CACHE_TTL_VALUE_BETS: int = 30     # 30 segundos
CACHE_TTL_DROPPING:   int = 60     # 1 minuto
CACHE_TTL_HISTORICO:  int = 86_400 # 24 horas
# ARBITRAGE: sem cache — não defina TTL aqui, busque sempre ao vivo


# ---------------------------------------------------------------------------
# Helper HTTP
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 10) -> Any:
    """GET com timeout e raise_for_status."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _fmt_horario(iso: str) -> str:
    """Converte ISO 8601 UTC para horário Brasil formatado."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(FUSO_BRASIL).strftime("%d/%m %H:%M")
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Score de prioridade de evento
# ---------------------------------------------------------------------------

def score_evento(evento: dict) -> float:
    """Quanto mais próximo do horário atual, maior o score (0-5)."""
    try:
        dt = datetime.fromisoformat(evento["date"].replace("Z", "+00:00"))
        diff_h = abs((dt - datetime.now(timezone.utc)).total_seconds()) / 3600
        return max(0.0, 5.0 - diff_h)
    except Exception as exc:
        logger.debug("score_evento: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# Helpers de mercado
# ---------------------------------------------------------------------------

# Mercados NBA que usam linha (spread/total) — exibem o valor da linha
_MERCADOS_COM_LINHA_NBA: set[str] = {
    "Spread", "Totals", "Spread HT", "Totals HT", "Totals 1Q", "Spread Q1",
    "Team Total Home", "Team Total Away", "Alternative Totals", "Alternative Spread",
    "Points O/U", "Rebounds O/U", "Assists O/U", "Steals O/U", "Blocks O/U",
    "Field Goals Made O/U", "Threes Made O/U",
    "Points & Rebounds O/U", "Points & Assists O/U",
    "Assists & Rebounds O/U", "Points, Assists & Rebounds O/U",
    "Steals & Blocks O/U",
}

# Mercados NBA sem linha — exibem só o lado (sim/não, ocorre/não ocorre)
_MERCADOS_SIM_NAO_NBA: set[str] = {
    "Double Double", "Triple Double",
    "Player First Basket", "Player First Assist", "Player First Rebound",
}

# Mercados de milestones — jogador atinge X pontos/rebotes/assistências
_MERCADOS_MILESTONE_NBA: set[str] = {
    "Player Points Milestones", "Player Rebounds Milestones",
    "Player Assists Milestones", "Player Threes Milestones",
}


def _resolver_tipo(market_name: str, lado: str, linha: Any, label: str | None) -> str:
    """Converte market + lado em descrição legível para futebol e NBA."""

    # ── Futebol ──────────────────────────────────────────────────────────────
    if market_name == "ML" and linha is None:
        return f"Vitoria {lado}"
    if label:
        return label
    if market_name in {"Totals", "Over/Under"} and linha is not None:
        return f"{lado.upper()} {linha} gols"
    if "Corner" in market_name:
        return f"{lado.upper()} {linha} escanteios" if linha else f"{lado.upper()} escanteios"

    # ── NBA — mercados com linha numérica ─────────────────────────────────
    if market_name in _MERCADOS_COM_LINHA_NBA:
        sufixo = f" {linha}" if linha is not None else ""
        return f"{market_name} {lado.upper()}{sufixo}"

    # ── NBA — sim/não (Double Double, Player First Basket etc.) ──────────
    if market_name in _MERCADOS_SIM_NAO_NBA:
        return f"{market_name} — {lado}"

    # ── NBA — milestones de jogador ───────────────────────────────────────
    if market_name in _MERCADOS_MILESTONE_NBA:
        sufixo = f" {linha}" if linha is not None else ""
        return f"{market_name}{sufixo} — {lado}"

    # ── NBA — ML por período (HT, Q1) ─────────────────────────────────────
    if market_name in {"ML HT", "ML Q1"}:
        return f"{market_name} Vitoria {lado}"

    # ── Fallback genérico ─────────────────────────────────────────────────
    return f"{market_name} {lado}"


def _extrair_linha_label(market: dict, odds_info: list) -> tuple[Any, str | None]:
    """Extrai linha (handicap/total) e label de um market."""
    linha = (
        market.get("line") or market.get("total")
        or market.get("points") or market.get("handicap")
    )
    label: str | None = None
    for item in odds_info:
        if not isinstance(item, dict):
            continue
        if not linha:
            linha = (
                item.get("line") or item.get("total") or item.get("points")
                or item.get("handicap") or item.get("hdp")
            )
        if not label:
            label = item.get("label")
    return linha, label


def _prob_para_lado(market_name: str, lado: str, probs: dict) -> float:
    """
    ML: usa Poisson. Outros mercados: 0.5 neutro.
    TODO: substituir 0.5 por modelo dedicado para Totals / Corners / BTTS.
    """
    if market_name == "ML":
        return probs.get(lado, 0.5)
    return 0.5


# ---------------------------------------------------------------------------
# 1. Eventos
# ---------------------------------------------------------------------------

def get_events() -> list[dict]:
    """
    Retorna eventos pendentes de futebol e basquete combinados.
    Faz 2 chamadas (1 por esporte) e une os resultados.
    """
    todos: list[dict] = []
    for esporte in ("football", "basketball"):
        url = f"{BASE_URL}/events?apiKey={API_KEY}&sport={esporte}&status=pending"
        try:
            data = _get_json(url)
            if isinstance(data, list):
                todos.extend(data)
                logger.info("get_events (%s): %d eventos", esporte, len(data))
            else:
                logger.warning("get_events (%s): resposta inesperada", esporte)
        except requests.RequestException as exc:
            logger.error("get_events (%s) falhou: %s", esporte, exc)
    logger.info("get_events total: %d eventos", len(todos))
    return todos


# ---------------------------------------------------------------------------
# Utilitário — listar ligas de basquete disponíveis na API
# ---------------------------------------------------------------------------

def listar_ligas_basquete() -> list[dict]:
    """
    Lista todas as ligas de basquete disponíveis na API.
    Use uma vez para confirmar o slug correto da NBA:

        from main_engine import listar_ligas_basquete
        for l in listar_ligas_basquete():
            print(l["slug"], "|", l["name"])
    """
    url = f"{BASE_URL}/leagues?apiKey={API_KEY}&sport=basketball"
    try:
        data = _get_json(url)
        if not isinstance(data, list):
            logger.warning("listar_ligas_basquete: resposta inesperada")
            return []
        logger.info("listar_ligas_basquete: %d ligas", len(data))
        return sorted(data, key=lambda x: x.get("eventsCount", 0), reverse=True)
    except requests.RequestException as exc:
        logger.error("listar_ligas_basquete falhou: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 2. Odds multi
# ---------------------------------------------------------------------------

def get_odds_multi(event_ids: list) -> list[dict]:
    """Busca odds em lotes de LOTE_ODDS para até MAX_EVENTOS eventos."""
    resultados: list[dict] = []
    for i in range(0, len(event_ids[:MAX_EVENTOS]), LOTE_ODDS):
        lote = event_ids[i : i + LOTE_ODDS]
        url = (
            f"{BASE_URL}/odds/multi"
            f"?apiKey={API_KEY}"
            f"&eventIds={','.join(map(str, lote))}"
            f"&bookmakers={BOOKMAKERS}"
            f"&includeEventDetails=true"
        )
        try:
            data = _get_json(url)
            if isinstance(data, list):
                resultados.extend(data)
        except requests.RequestException as exc:
            logger.error("get_odds_multi lote %d falhou: %s", i, exc)
    logger.info("get_odds_multi: %d jogos retornados", len(resultados))
    return resultados


# ---------------------------------------------------------------------------
# 3. Value Bets — EV calculado pela API (atualizado a cada 5s)
# ---------------------------------------------------------------------------

def get_value_bets(bookmaker: str = "Bet365") -> list[dict]:
    """
    Value bets com expectedValue ja calculado pela API.
    Mais confiavel que o Poisson fixo para ML e outros mercados.
    """
    url = (
        f"{BASE_URL}/value-bets"
        f"?apiKey={API_KEY}"
        f"&bookmaker={bookmaker}"
        f"&includeEventDetails=true"
    )
    try:
        data = _get_json(url)
        if not isinstance(data, list):
            logger.warning("get_value_bets: resposta inesperada")
            return []
        logger.info("get_value_bets (%s): %d value bets", bookmaker, len(data))
        return data
    except requests.RequestException as exc:
        logger.error("get_value_bets falhou: %s", exc)
        return []


def processar_value_bets(
    raw: list[dict],
    odd_min: float,
    odd_max: float,
    mercados_permitidos: set[str] | None,
) -> list[dict]:
    """
    Converte payload /value-bets no formato padrao de oportunidade.
    O EV vem do campo expectedValue da API — nao e estimativa manual.
    """
    resultado: dict[str, dict] = {}

    for vb in raw:
        market_name: str = vb.get("market", {}).get("name", "")
        if mercados_permitidos and market_name not in mercados_permitidos:
            continue

        ev_api = vb.get("expectedValue")
        if ev_api is None or ev_api < EV_MINIMO:
            continue

        bookmaker: str = vb.get("bookmaker", "")
        event_info = vb.get("event", {})
        home: str = event_info.get("home", "?")
        away: str = event_info.get("away", "?")
        liga: str = event_info.get("league", "")
        horario: str = _fmt_horario(event_info.get("date", ""))

        bet_side: str = vb.get("betSide", "")
        bk_odds = vb.get("bookmakerOdds", {})
        odd_raw = bk_odds.get(bet_side)
        try:
            odd = float(odd_raw)
        except (TypeError, ValueError):
            continue

        if not (odd_min <= odd <= odd_max):
            continue

        linha = vb.get("market", {}).get("hdp")
        tipo = _resolver_tipo(market_name, bet_side, linha, None)
        prob_impl = round((1 / odd) * 100, 2) if odd > 0 else 50.0

        chave = f"{home} x {away} | {tipo} | {bookmaker} | vb"
        resultado[chave] = {
            "jogo": f"{home} x {away}",
            "liga": liga,
            "horario": horario,
            "tipo": tipo,
            "mercado": market_name,
            "linha": linha,
            "casa": bookmaker,
            "odd": round(odd, 2),
            "prob_modelo": prob_impl,
            "ev": round(float(ev_api), 3),
            "score": round((prob_impl / 100) * odd, 2),
            "fonte": "value_bet_api",
            "drop_sinal": False,
        }

    logger.info("processar_value_bets: %d oportunidades", len(resultado))
    return list(resultado.values())


# ---------------------------------------------------------------------------
# 4. Arbitragem
# ---------------------------------------------------------------------------

def get_arbitrage(limit: int = 50) -> list[dict]:
    """
    Oportunidades de arbitragem com stakes otimas ja calculadas pela API.
    Retorna apenas arbs onde todos os legs usam os bookmakers configurados.
    """
    url = (
        f"{BASE_URL}/arbitrage-bets"
        f"?apiKey={API_KEY}"
        f"&bookmakers={BOOKMAKERS}"
        f"&limit={limit}"
        f"&includeEventDetails=true"
    )
    try:
        data = _get_json(url)
        if not isinstance(data, list):
            logger.warning("get_arbitrage: resposta inesperada")
            return []
        logger.info("get_arbitrage: %d oportunidades", len(data))
        return data
    except requests.RequestException as exc:
        logger.error("get_arbitrage falhou: %s", exc)
        return []


def processar_arbitrage(raw: list[dict], esporte: str = "todos") -> list[dict]:
    """
    Converte payload /arbitrage-bets em lista estruturada.
    esporte: "futebol" | "nba" | "todos"
    profitMargin = lucro garantido em % (ex: 2.3 = R$2.30 por R$100 apostados).
    """
    SLUGS_NBA = {"usa-nba"}
    SLUGS_FUT = {s for s, e in LIGA_ESPORTE.items() if e == "football"}

    resultado = []
    for arb in raw:
        event  = arb.get("event", {})
        market = arb.get("market", {})
        slug   = event.get("leagueSlug") or event.get("league_slug", "")

        if esporte == "nba"     and slug not in SLUGS_NBA: continue
        if esporte == "futebol" and slug not in SLUGS_FUT: continue

        resultado.append({
            "id": arb.get("id", ""),
            "jogo": f"{event.get('home', '?')} x {event.get('away', '?')}",
            "liga": event.get("league", ""),
            "horario": _fmt_horario(event.get("date", "")),
            "mercado": market.get("name", ""),
            "profit_pct": round(arb.get("profitMargin", 0.0), 2),
            "implied_prob": round(arb.get("impliedProbability", 0.0), 4),
            "legs": arb.get("legs", []),
            "optimal_stakes": arb.get("optimalStakes", []),
            "updated_at": arb.get("updatedAt", ""),
        })
    resultado.sort(key=lambda x: x["profit_pct"], reverse=True)
    return resultado


# ---------------------------------------------------------------------------
# 5. Dropping Odds — sinal de sharp money
# ---------------------------------------------------------------------------

def get_dropping_odds(
    sport: str = "football",
    min_drop_pct: float = 5.0,
    time_window: str = "opening",
) -> dict[int, dict]:
    """
    Retorna odds com queda significativa indexadas por eventId.
    Queda >= min_drop_pct indica sharp money apostando contra essa odd.
    """
    url = (
        f"{BASE_URL}/dropping-odds"
        f"?apiKey={API_KEY}"
        f"&sport={sport}"
        f"&timeWindow={time_window}"
        f"&minDrop={min_drop_pct}"
        f"&includeEventDetails=true"
        f"&limit=200"
    )
    try:
        data = _get_json(url)
        if not isinstance(data, list):
            logger.warning("get_dropping_odds: resposta inesperada")
            return {}

        index: dict[int, dict] = {}
        for item in data:
            eid = item.get("eventId")
            if not eid:
                continue
            drop_val = item.get("odds", {}).get("drop", {}).get(time_window) or 0.0
            # Guardar a maior queda por evento
            if eid not in index or drop_val > index[eid].get("drop_pct", 0):
                index[eid] = {
                    "drop_pct": drop_val,
                    "bet_side": item.get("betSide"),
                    "odd_atual": item.get("odds", {}).get("current"),
                    "odd_abertura": item.get("odds", {}).get("opening"),
                    "market": item.get("market", {}).get("name"),
                }

        logger.info("get_dropping_odds: %d eventos com queda >= %.0f%%", len(index), min_drop_pct)
        return index
    except requests.RequestException as exc:
        # 403 = endpoint não incluído no plano — silencioso
        if "403" in str(exc):
            logger.debug("get_dropping_odds: não disponível no plano atual (403)")
        else:
            logger.error("get_dropping_odds falhou: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# 6. Motor principal
# ---------------------------------------------------------------------------

def rodar_sistema(
    odd_min: float,
    odd_max: float,
    mercados_permitidos: set[str] | None = None,
    stats_ligas: dict | None = None,
    modo: str = "todos",
) -> list[dict]:
    """
    Pipeline completo.
    modo: "futebol" | "nba" | "todos"
    stats_ligas: médias de gols — injetado pelo app (cache 24h).
    """
    logger.info("=== SISTEMA INICIADO [modo=%s] ===", modo)
    if mercados_permitidos:
        logger.info("Mercados filtrados: %s", sorted(mercados_permitidos))

    janela_horas = JANELA_HORAS_NBA if modo == "nba" else JANELA_HORAS_FUTEBOL

    # Stats históricas para o Poisson (injetadas pelo app com cache 24h)
    if stats_ligas is None:
        logger.warning("stats_ligas não fornecidas — buscando agora (sem cache)")
        stats_ligas = buscar_stats_ligas()
    logger.info("Stats disponíveis para %d times", len(stats_ligas))

    # A. Value bets (filtradas por modo após processar)
    vb_raw = get_value_bets("Bet365")
    oportunidades_vb_todos = processar_value_bets(vb_raw, odd_min, odd_max, mercados_permitidos)

    LIGAS_NBA_NOMES = {"USA - NBA", "NBA"}
    LIGAS_FUT_NOMES = {l for l, e in LIGA_ESPORTE.items() if e == "football"}

    if modo == "nba":
        oportunidades_vb = [v for v in oportunidades_vb_todos if v.get("liga","") in LIGAS_NBA_NOMES]
    elif modo == "futebol":
        oportunidades_vb = [v for v in oportunidades_vb_todos if v.get("liga","") not in LIGAS_NBA_NOMES]
    else:
        oportunidades_vb = oportunidades_vb_todos

    # B. Odds multi + Poisson
    eventos = get_events()
    agora = datetime.now(timezone.utc)
    janela_fim = agora + timedelta(hours=janela_horas)

    eventos_filtrados = []
    for e in eventos:
        try:
            dt = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            if agora <= dt <= janela_fim:
                eventos_filtrados.append(e)
        except Exception as exc:
            logger.debug("Evento ignorado: %s", exc)

    logger.info("Eventos nas proximas %dh: %d", janela_horas, len(eventos_filtrados))

    eventos_ligas = [
        e for e in eventos_filtrados
        if e.get("league", {}).get("slug") in LIGAS_PERMITIDAS
    ]
    logger.info("Eventos em ligas fortes: %d", len(eventos_ligas))

    # Filtrar por modo: só futebol, só NBA ou ambos
    if modo == "futebol":
        eventos_ord = sorted(
            [e for e in eventos_ligas if LIGA_ESPORTE.get(e.get("league",{}).get("slug")) == "football"],
            key=score_evento, reverse=True,
        )[:MAX_EVENTOS]
    elif modo == "nba":
        eventos_ord = sorted(
            [e for e in eventos_ligas if LIGA_ESPORTE.get(e.get("league",{}).get("slug")) == "basketball"],
            key=score_evento, reverse=True,
        )[:MAX_EVENTOS]
        if not eventos_ord:
            # Fallback: todos os eventos de basquete nas próximas 48h
            eventos_ord = sorted(
                [e for e in eventos_filtrados if e.get("sport","").lower() in ("basketball","basquete")],
                key=score_evento, reverse=True,
            )[:MAX_EVENTOS]
    else:
        # Modo todos: cota dividida
        ev_fut = sorted(
            [e for e in eventos_ligas if LIGA_ESPORTE.get(e.get("league",{}).get("slug")) == "football"],
            key=score_evento, reverse=True,
        )[:MAX_EVENTOS - 10]
        ev_bsk = sorted(
            [e for e in eventos_ligas if LIGA_ESPORTE.get(e.get("league",{}).get("slug")) == "basketball"],
            key=score_evento, reverse=True,
        )[:10]
        eventos_ord = ev_fut + ev_bsk or sorted(eventos_filtrados, key=score_evento, reverse=True)[:MAX_EVENTOS]

    logger.info("Eventos selecionados [modo=%s]: %d", modo, len(eventos_ord))

    event_ids = [e["id"] for e in eventos_ord]
    logger.info("IDs enviados para odds: %d", len(event_ids))

    odds_lista = get_odds_multi(event_ids)

    # C. Dropping odds — futebol e basquete combinados
    drop_fut  = get_dropping_odds(sport="football",   min_drop_pct=5.0)
    drop_bask = get_dropping_odds(sport="basketball", min_drop_pct=5.0)
    dropping_index = {**drop_fut, **drop_bask}
    logger.info("Dropping odds: %d futebol + %d basquete", len(drop_fut), len(drop_bask))

    # D. Processar odds multi
    resultados_poisson: dict[str, dict] = {}

    for jogo in odds_lista:
        bookmakers = jogo.get("bookmakers", {})
        if not bookmakers:
            continue

        home: str = jogo.get("home", "?")
        away: str = jogo.get("away", "?")
        event_id: int = jogo.get("id", 0)
        liga: str = jogo.get("league", {}).get("name", "")
        horario: str = _fmt_horario(jogo.get("date", ""))

        # Poisson com médias reais de gols via stats históricas.
        # stats_ligas é injetado pelo motor após buscar_stats_ligas() (cache 24h).
        lam_h, lam_a = get_medias_confronto(home, away, stats_ligas)
        matriz = matriz_resultados(lam_h, lam_a)
        p_home, p_draw, p_away = prob_vitoria(matriz)
        probs = {"home": p_home, "draw": p_draw, "away": p_away}

        drop_info = dropping_index.get(event_id)

        skip_sem_odds = skip_formato = skip_faixa = skip_ev = aceitos = 0

        for casa, markets in bookmakers.items():
            for market in markets:
                market_name: str = market.get("name", "")
                if mercados_permitidos and market_name not in mercados_permitidos:
                    continue

                odds_info: list = market.get("odds", [])
                if not odds_info:
                    skip_sem_odds += 1
                    continue

                linha, label = _extrair_linha_label(market, odds_info)

                if not isinstance(odds_info[0], dict):
                    skip_formato += 1
                    logger.debug("%s x %s | %s | odds[0] tipo: %s", home, away, market_name, type(odds_info[0]))
                    continue

                for lado, odd_valor in odds_info[0].items():
                    if lado in {"hdp", "label"}:
                        continue
                    try:
                        odd = float(odd_valor)
                    except (TypeError, ValueError):
                        continue

                    if not (odd_min <= odd <= odd_max):
                        skip_faixa += 1
                        continue

                    tipo = _resolver_tipo(market_name, lado, linha, label)
                    prob = _prob_para_lado(market_name, lado, probs)
                    ev = round((prob * odd) - 1, 3)

                    if ev < EV_MINIMO:
                        skip_ev += 1
                        continue

                    aceitos += 1
                    drop_sinal = bool(drop_info and drop_info.get("bet_side") == lado)

                    chave = f"{home} x {away} | {tipo} | {casa}"
                    resultados_poisson[chave] = {
                        "jogo": f"{home} x {away}",
                        "liga": liga,
                        "horario": horario,
                        "tipo": tipo,
                        "mercado": market_name,
                        "linha": linha,
                        "casa": casa,
                        "odd": round(odd, 2),
                        "prob_modelo": round(prob * 100, 2),
                        "ev": ev,
                        "score": round(prob * odd, 2),
                        "fonte": "poisson",
                        "drop_sinal": drop_sinal,
                    }

        logger.debug("%s x %s | aceitos=%d sem_odds=%d fmt=%d faixa=%d ev=%d",
                     home, away, aceitos, skip_sem_odds, skip_formato, skip_faixa, skip_ev)

    # E. Unificar: value bets tem prioridade sobre Poisson
    chaves_vb = {v["jogo"] + v["tipo"] + v["casa"] for v in oportunidades_vb}
    poisson_extra = [
        p for p in resultados_poisson.values()
        if (p["jogo"] + p["tipo"] + p["casa"]) not in chaves_vb
    ]

    todos = oportunidades_vb + poisson_extra
    todos.sort(key=lambda x: x["ev"], reverse=True)

    logger.info(
        "Oportunidades: %d value_bet_api + %d poisson = %d total",
        len(oportunidades_vb), len(poisson_extra), len(todos),
    )
    return todos


# ---------------------------------------------------------------------------
# 7. Múltipla inteligente — 3 jogos DISTINTOS
# ---------------------------------------------------------------------------

def montar_multipla(resultados: list[dict], banca: float) -> dict:
    """
    Seleciona os 3 melhores picks de JOGOS DIFERENTES (sem repetir mesmo jogo).
    Stake via Kelly fracionado com cap de KELLY_MAX.
    """
    vistos: set[str] = set()
    picks: list[dict] = []

    for pick in sorted(resultados, key=lambda x: x["ev"], reverse=True):
        jogo = pick["jogo"]
        if jogo in vistos:
            continue
        vistos.add(jogo)
        picks.append(pick)
        if len(picks) == 3:
            break

    if not picks:
        return {"picks": [], "odd_total": 1.0, "prob_total": 0.0, "ev": 0.0, "stake": 0.0}

    odd_total: float = 1.0
    prob_total: float = 1.0
    for p in picks:
        odd_total *= p["odd"]
        prob_total *= p["prob_modelo"] / 100

    ev = round((prob_total * odd_total) - 1, 3)

    kelly = 0.0
    if odd_total > 1:
        kelly = ((prob_total * odd_total) - 1) / (odd_total - 1)
        kelly = max(0.0, min(kelly, KELLY_MAX))

    return {
        "picks": picks,
        "odd_total": round(odd_total, 2),
        "prob_total": round(prob_total * 100, 2),
        "ev": ev,
        "stake": round(banca * kelly, 2),
    }
