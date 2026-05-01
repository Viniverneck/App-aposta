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
JANELA_HORAS_TENIS:   int = 24  # Tênis: torneios ao longo do dia
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

# Prefixos de slugs de tênis — usado para identificar value bets e arbs de tênis
SLUGS_TENIS_PREFIXOS: tuple[str, ...] = ("atp-", "wta-", "challenger-", "itf-", "utr-")


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

import re as _re

# Stat map para Player Props
_STAT_MAP = {
    "Goals": "Gols", "Assists": "Assistências", "Points": "Pontos",
    "Rebounds": "Rebotes", "Steals": "Roubos", "Blocks": "Bloqueios",
    "Saves": "Defesas", "Shots": "Chutes", "Threes": "3 Pontos",
}

# Mercados sim/não (NBA)
_MERCADOS_SIM_NAO: set[str] = {
    "Double Double", "Triple Double",
    "Player First Basket", "Player First Assist", "Player First Rebound",
}

# Mercados de milestone (NBA)
_MERCADOS_MILESTONE: set[str] = {
    "Player Points Milestones", "Player Rebounds Milestones",
    "Player Assists Milestones", "Player Threes Milestones",
}


def _resolver_tipo(market_name: str, lado: str, linha: Any, label: str | None) -> str:
    """
    Converte market + lado em descrição clara, igual à linguagem das casas de apostas.
    home = OVER / Mais de / Casa
    away = UNDER / Menos de / Fora
    draw = Empate
    """
    lado_l = lado.lower()

    if label:
        # Limpar labels de props NBA: 'LeBron James (1) (6.5)' → só o nome
        # Número de camisa e linha já ficam nas colunas próprias
        label_limpo = _re.sub(r'\s*\(\d+\)\s*', ' ', label).strip()  # remove nº camisa
        label_limpo = _re.sub(r'\s*\([\d.]+\)\s*$', '', label_limpo).strip()  # remove linha
        return label_limpo if label_limpo else label

    # ── ML / Moneyline ────────────────────────────────────────────────────
    if market_name in {"ML", "Moneyline"}:
        return {"home": "Vitória Casa", "away": "Vitória Fora", "draw": "Empate"}.get(lado_l, f"ML {lado}")

    # ── ML por período ────────────────────────────────────────────────────
    if market_name == "ML HT":
        return {"home": "Vitória Casa (1ºT)", "away": "Vitória Fora (1ºT)", "draw": "Empate (1ºT)"}.get(lado_l, f"ML HT {lado}")
    if market_name in {"ML Q1", "ML 1Q"}:
        return {"home": "Vitória Casa (1ºQ)", "away": "Vitória Fora (1ºQ)", "draw": "Empate (1ºQ)"}.get(lado_l, f"ML Q1 {lado}")

    # ── Totais de gols — Futebol ──────────────────────────────────────────
    if market_name in {"Totals", "Over/Under"}:
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} gols" if linha is not None else f"{direcao} gols"

    if market_name == "Totals HT":
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} gols (1ºT)" if linha is not None else f"{direcao} gols (1ºT)"

    # ── Escanteios ────────────────────────────────────────────────────────
    if market_name == "Corners Totals":
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} escanteios" if linha is not None else f"{direcao} escanteios"

    if market_name == "Corners Totals HT":
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} escanteios (1ºT)" if linha is not None else f"{direcao} escanteios (1ºT)"

    if market_name == "Corners Spread":
        time = "Casa" if lado_l == "home" else "Fora"
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap Escanteios {time}{hdp}"

    # ── Cartões ───────────────────────────────────────────────────────────
    if market_name == "Bookings Spread":
        time = "Casa" if lado_l == "home" else "Fora"
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap Cartões {time}{hdp}"

    # ── Spread / Handicap Asiático — Futebol ──────────────────────────────
    if market_name == "Spread":
        time = "Casa" if lado_l == "home" else "Fora"
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap {time}{hdp}"

    if market_name == "Spread HT":
        time = "Casa" if lado_l == "home" else "Fora"
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap {time}{hdp} (1ºT)"

    # ── NBA — Totais de pontos ────────────────────────────────────────────
    if market_name == "Totals 1Q":
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} pontos (1ºQ)" if linha is not None else f"{direcao} pts (1ºQ)"

    if market_name in {"Team Total Home", "Team Total Away"}:
        time = "Casa" if "Home" in market_name else "Fora"
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} pts ({time})" if linha is not None else f"{direcao} pts ({time})"

    if market_name in {"Alternative Totals", "Points O/U"}:
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} pontos" if linha is not None else f"{direcao} pontos"

    # ── NBA — Stats de jogadores ──────────────────────────────────────────
    _NBA_STATS = {
        "Rebounds O/U":   "Rebotes",
        "Assists O/U":    "Assistências",
        "Steals O/U":     "Roubos de bola",
        "Blocks O/U":     "Bloqueios",
        "Field Goals Made O/U": "Cestas convertidas",
        "Threes Made O/U":      "Cestas de 3 pts",
    }
    if market_name in _NBA_STATS:
        stat = _NBA_STATS[market_name]
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} {stat}" if linha is not None else f"{direcao} {stat}"

    _NBA_COMBO = {
        "Points & Rebounds O/U":              "Pts+Reb",
        "Points & Assists O/U":               "Pts+Ast",
        "Assists & Rebounds O/U":             "Ast+Reb",
        "Points, Assists & Rebounds O/U":     "Pts+Ast+Reb",
        "Steals & Blocks O/U":                "Roubo+Bloq",
    }
    if market_name in _NBA_COMBO:
        combo = _NBA_COMBO[market_name]
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} {combo}" if linha is not None else f"{direcao} {combo}"

    # ── NBA — Spread / Handicap ───────────────────────────────────────────
    if market_name in {"Alternative Spread", "Spread Q1"}:
        time = "Casa" if lado_l == "home" else "Fora"
        periodo = " (1ºQ)" if "Q1" in market_name else ""
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap {time}{hdp}{periodo}"

    # ── NBA — Sim/Não ─────────────────────────────────────────────────────
    if market_name in _MERCADOS_SIM_NAO:
        return f"{market_name} — {'Sim' if lado_l == 'home' else 'Não'}"

    # ── NBA — Milestones ──────────────────────────────────────────────────
    if market_name in _MERCADOS_MILESTONE:
        stat_nome = market_name.replace("Player ", "").replace(" Milestones", "")
        direcao = "Atinge" if lado_l == "home" else "Não atinge"
        return f"{direcao} {linha} {stat_nome}" if linha is not None else f"{direcao} {stat_nome}"

    # ── Player Props (NBA, NHL, etc.) ─────────────────────────────────────
    if market_name.startswith("Player Props - "):
        nome_limpo = _re.sub(r"\s*\(\d+\)\s*", " ", market_name).strip()
        m = _re.match(r"Player Props - (.+?)\s*\((.+?)\)\s*$", nome_limpo)
        if m:
            jogador  = m.group(1).strip()
            stat_en  = m.group(2).strip()
            stat_pt  = _STAT_MAP.get(stat_en, stat_en)
            direcao  = "Mais de" if lado_l == "home" else "Menos de"
            sufixo   = f" {linha}" if linha is not None else ""
            return f"{jogador} — {stat_pt}{sufixo} ({direcao})"
        return f"{market_name} {lado}"

    # ── Tênis ─────────────────────────────────────────────────────────────
    if market_name == "Spread (Games)":
        time = "Casa" if lado_l == "home" else "Fora"
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap Games {time}{hdp}"

    if market_name == "Totals (Games)":
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} games" if linha is not None else f"{direcao} games"

    # ── Esports ───────────────────────────────────────────────────────────
    if market_name == "Total Maps":
        direcao = "Mais de" if lado_l == "home" else "Menos de"
        return f"{direcao} {linha} mapas" if linha is not None else f"{direcao} mapas"

    if market_name == "Map Handicap":
        time = "Casa" if lado_l == "home" else "Fora"
        hdp = f" ({linha:+g})" if linha is not None else ""
        return f"Handicap Mapas {time}{hdp}"

    # ── Fallback ──────────────────────────────────────────────────────────
    lado_fmt = {"home": "Casa", "away": "Fora", "draw": "Empate"}.get(lado_l, lado)
    return f"{market_name} — {lado_fmt}"


def _extrair_linha_label(market: dict, odds_info: list) -> tuple[Any, str | None]:
    """
    Extrai linha (handicap/total) e label de um market.
    Para props NBA, o label vem como "LeBron James (1) (6.5)" —
    extrai a linha numérica e limpa o nome do jogador.
    """
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

    # Se ainda não temos linha mas temos label com valor entre parênteses no final,
    # ex: "LeBron James (1) (6.5)" → extrai 6.5 como linha
    if label and linha is None:
        m = _re.search(r"\(([\d.]+)\)\s*$", label)
        if m:
            try:
                linha = float(m.group(1))
            except ValueError:
                pass

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
    modo: str = "todos",
) -> list[dict]:
    """
    Converte payload /value-bets no formato padrao de oportunidade.
    O EV vem do campo expectedValue da API — nao e estimativa manual.
    modo: "futebol" | "nba" | "tenis" | "todos"
    """
    resultado: dict[str, dict] = {}

    for vb in raw:
        esporte_vb = vb.get("event", {}).get("sport", "").lower()
        liga_vb    = vb.get("event", {}).get("league", "")
        slug_vb    = liga_vb.lower().replace(" ", "-").replace(",", "")

        # Filtro de esporte por modo — corrige o bug de tênis aparecendo no futebol
        # e bloqueia esportes não suportados (Esports, Cricket, Volleyball etc.)
        ESPORTES_SUPORTADOS = {"football", "basketball", "tennis"}
        if esporte_vb not in ESPORTES_SUPORTADOS:
            continue
        if modo == "futebol" and esporte_vb != "football":
            continue
        if modo == "nba" and esporte_vb != "basketball":
            continue
        if modo == "tenis" and esporte_vb != "tennis":
            continue

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

        # Deep link direto — corrige domínio para versão brasileira
        def _fix_link(url: str) -> str:
            return (url
                .replace("https://www.bet365.com", "https://www.bet365.bet.br")
                .replace("https://bet365.com",     "https://www.bet365.bet.br")
                .replace("https://www.betano.com",  "https://www.betano.bet.br")
                .replace("https://betano.com",      "https://www.betano.bet.br"))
        link_vb = _fix_link(vb.get("bookmakerOdds", {}).get("href", ""))

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
            "link": link_vb,
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
        if esporte == "tenis"   and not any(slug.startswith(p) for p in SLUGS_TENIS_PREFIXOS): continue

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
    modo: "futebol" | "nba" | "tenis" | "todos"
    stats_ligas: médias de gols — injetado pelo app (cache 24h).
    Tênis usa SOMENTE value bets (odds/multi retorna 400 para tênis).
    """
    logger.info("=== SISTEMA INICIADO [modo=%s] ===", modo)
    if mercados_permitidos:
        logger.info("Mercados filtrados: %s", sorted(mercados_permitidos))

    # Modo tênis: só value bets, sem pipeline Poisson
    if modo == "tenis":
        vb_raw = get_value_bets("Bet365")
        result = processar_value_bets(vb_raw, odd_min, odd_max, mercados_permitidos, modo="tenis")
        result.sort(key=lambda x: x["ev"], reverse=True)
        logger.info("Tênis: %d oportunidades (value bets only)", len(result))
        return result

    janela_horas = JANELA_HORAS_NBA if modo == "nba" else JANELA_HORAS_FUTEBOL

    # Stats históricas para o Poisson (injetadas pelo app com cache 24h)
    if stats_ligas is None:
        logger.warning("stats_ligas não fornecidas — buscando agora (sem cache)")
        stats_ligas = buscar_stats_ligas()
    logger.info("Stats disponíveis para %d times", len(stats_ligas))

    # A. Value bets (filtradas por modo após processar)
    vb_raw = get_value_bets("Bet365")
    oportunidades_vb_todos = processar_value_bets(vb_raw, odd_min, odd_max, mercados_permitidos, modo=modo)

    LIGAS_NBA_NOMES = {"USA - NBA", "NBA"}
    LIGAS_FUT_NOMES = {l for l, e in LIGA_ESPORTE.items() if e == "football"}

    if modo == "nba":
        oportunidades_vb = [v for v in oportunidades_vb_todos if v.get("liga","") in LIGAS_NBA_NOMES]
    elif modo == "futebol":
        oportunidades_vb = [v for v in oportunidades_vb_todos if v.get("liga","") not in LIGAS_NBA_NOMES]
    else:
        oportunidades_vb = oportunidades_vb_todos

    logger.info("Value bets [modo=%s]: %d", modo, len(oportunidades_vb))

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
        # Links diretos por casa: {"Bet365": "https://...", "Betano": "https://..."}
        urls_jogo: dict = jogo.get("urls", {})

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

                    # Link direto: tenta a casa exata, fallback para Bet365
                    casa_base = casa.split(" ")[0]  # "Bet365 (no latency)" → "Bet365"
                    raw_link = urls_jogo.get(casa_base) or urls_jogo.get("Bet365", "")
                    link = (raw_link
                        .replace("https://www.bet365.com", "https://www.bet365.bet.br")
                        .replace("https://bet365.com",     "https://www.bet365.bet.br")
                        .replace("https://www.betano.com",  "https://www.betano.bet.br")
                        .replace("https://betano.com",      "https://www.betano.bet.br"))

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
                        "link": link,
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
    # Ordenar por horário (mais próximo primeiro) e dentro do mesmo horário por EV
    todos.sort(key=lambda x: (x.get("horario", ""), -x["ev"]))

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

# ---------------------------------------------------------------------------
# Comparação de odds Bet365 x Betano
# ---------------------------------------------------------------------------


def _safe_float(val, default=0.0):
    try:
        f = float(val)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


def _corrigir_link_betano(href: str) -> str:
    """Converte links Betano para domínio brasileiro."""
    return (
        href
        .replace("www.betano.de", "betano.bet.br")
        .replace("www.betano.com", "betano.bet.br")
        .replace("//betano.de", "//betano.bet.br")
        .replace("//betano.com", "//betano.bet.br")
    )


def buscar_comparacao_odds(
    esportes: list[str] | None = None,
    margem_max: float = 5.0,
) -> list[dict]:
    """
    Cruza value bets de Bet365 e Betano pelo mesmo eventId + market + lado.
    Calcula a margem da casa (soma das probs implícitas) e a divergência.

    margem_max: filtra só linhas onde a soma das probs < (100 + margem_max)%.
                Quanto menor, mais próximo de arb real.
                Ex: 2.0 → só mostra onde soma < 102% (arb possível)
                    5.0 → mostra divergências relevantes

    Retorna lista ordenada por margem crescente (menores primeiro = mais perto de arb).
    Custo: 2 chamadas de API (1 por bookmaker).
    """
    if esportes is None:
        esportes = ["Football", "Basketball", "Tennis"]

    ESPORTES_SET = {e.lower() for e in esportes}

    # Buscar value bets das duas casas
    vb_b365   = get_value_bets("Bet365")
    vb_betano = get_value_bets("Betano")

    # Indexar Betano por (eventId, market, lado) para lookup O(1)
    idx_betano: dict[tuple, dict] = {}
    for vb in vb_betano:
        esp = vb.get("event", {}).get("sport", "").lower()
        if esp not in ESPORTES_SET:
            continue
        chave = (
            vb.get("eventId"),
            vb.get("market", {}).get("name", ""),
            vb.get("betSide", ""),
        )
        idx_betano[chave] = vb

    # Cruzar com Bet365
    resultado: list[dict] = []
    vistos: set[tuple] = set()

    for vb in vb_b365:
        esp = vb.get("event", {}).get("sport", "").lower()
        if esp not in ESPORTES_SET:
            continue

        event_id    = vb.get("eventId")
        market_name = vb.get("market", {}).get("name", "")
        bet_side    = vb.get("betSide", "")
        linha       = vb.get("market", {}).get("hdp")

        # Para ML de futebol há 3 lados (home/draw/away).
        # lado_oposto é usado para buscar a Betano no lado complementar.
        # Para draw, usamos 'home' como referência de comparação.
        bk_odds  = vb.get("bookmakerOdds", {})
        mkt_odds = vb.get("market", {})
        lados_mkt = [l for l in ("home", "draw", "away") if mkt_odds.get(l)]

        odd_b365_vb = _safe_float(bk_odds.get(bet_side))
        if odd_b365_vb <= 1:
            continue

        if bet_side == "home":
            lado_oposto = "away"
        elif bet_side == "away":
            lado_oposto = "home"
        else:
            lado_oposto = "home"  # draw — compara com home

        odd_b365_op = _safe_float(bk_odds.get(lado_oposto))
        if odd_b365_op <= 1:
            continue

        # Margem completa considerando todos os lados do mercado
        soma_probs_b365 = sum(
            1 / _safe_float(bk_odds.get(l), default=999)
            for l in lados_mkt
            if _safe_float(bk_odds.get(l)) > 1
        )

        # Procurar o mesmo mercado + lado oposto na Betano
        chave_op = (event_id, market_name, lado_oposto)
        vb_betano_op = idx_betano.get(chave_op)

        if vb_betano_op:
            # Betano tem value bet no lado oposto — comparação direta
            odd_betano_op = _safe_float(vb_betano_op.get("bookmakerOdds", {}).get(lado_oposto))
            odd_betano_vb = _safe_float(vb_betano_op.get("bookmakerOdds", {}).get(bet_side))
            link_betano = _corrigir_link_betano(vb_betano_op.get("bookmakerOdds", {}).get("href", ""))
        else:
            # Betano não tem value bet, mas pode ter odd no mesmo payload de odds/multi
            # Usamos a odd da Bet365 como referência para o lado oposto
            odd_betano_op = 0.0
            odd_betano_vb = 0.0
            link_betano = ""

        # Melhor odd de cada lado entre as duas casas
        melhor_vb = max(odd_b365_vb, odd_betano_vb) if odd_betano_vb > 1 else odd_b365_vb
        melhor_op = max(odd_b365_op, odd_betano_op) if odd_betano_op > 1 else odd_b365_op

        if melhor_vb <= 1 or melhor_op <= 1:
            continue

        # Margem: para 2 lados usa prob_vb+prob_op; para 3 lados (ML futebol) usa soma_probs_b365
        if len(lados_mkt) == 3 and soma_probs_b365 > 0:
            margem = round(soma_probs_b365 * 100, 2)
        else:
            margem = round((1/melhor_vb + 1/melhor_op) * 100, 2)

        if margem > (100 + margem_max):
            continue

        # Evitar duplicatas (mesmo evento + mercado)
        chave_linha = (event_id, market_name, frozenset([bet_side, lado_oposto]))
        if chave_linha in vistos:
            continue
        vistos.add(chave_linha)

        event_info = vb.get("event", {})
        link_b365  = (
            vb.get("bookmakerOdds", {}).get("href", "")
            .replace("www.bet365.com", "bet365.bet.br")
            .replace("//bet365.com", "//bet365.bet.br")
        )

        # Descrição clara: para ML mostra o lado específico da value bet
        tipo_desc = _resolver_tipo(market_name, bet_side, linha, None)

        resultado.append({
            "jogo":         f"{event_info.get('home','?')} x {event_info.get('away','?')}",
            "esporte":      event_info.get("sport", ""),
            "liga":         event_info.get("league", ""),
            "horario":      _fmt_horario(event_info.get("date", "")),
            "mercado":      market_name,
            "tipo":         tipo_desc,
            "linha":        linha,
            "lado_vb":      bet_side,
            "lado_op":      lado_oposto,
            # Odds Bet365
            "odd_b365_vb":  round(odd_b365_vb, 3),
            "odd_b365_op":  round(odd_b365_op, 3),
            # Odds Betano
            "odd_betano_vb": round(odd_betano_vb, 3) if odd_betano_vb > 1 else None,
            "odd_betano_op": round(odd_betano_op, 3) if odd_betano_op > 1 else None,
            # Melhor odd de cada lado
            "melhor_vb":    round(melhor_vb, 3),
            "melhor_op":    round(melhor_op, 3),
            # Margem total (< 100% = arb real)
            "margem_pct":   margem,
            "eh_arb":       margem < 100.0,
            # Links
            "link_b365":    link_b365,
            "link_betano":  link_betano,
        })

    # Ordenar por data/horário e depois por margem dentro do mesmo horário
    resultado.sort(key=lambda x: (x["horario"], x["margem_pct"]))
    logger.info("Comparação: %d linhas com margem <= %.1f%%", len(resultado), margem_max)
    return resultado

# ---------------------------------------------------------------------------
# Live Arbitrage — odds ao vivo comparadas entre Bet365 e Betano
# ---------------------------------------------------------------------------

ESPORTES_LIVE: list[str] = ["football", "basketball", "tennis"]
MAX_EVENTOS_LIVE: int = 20  # por esporte — respeita limite de 100 chamadas/h
MERCADOS_LIVE: list[str] = ["ML", "Spread", "Totals", "Over/Under"]


def get_eventos_live(esportes: list[str] | None = None) -> list[dict]:
    """
    Retorna todos os eventos com status=live para os esportes selecionados.
    Custo: 1 chamada por esporte.
    """
    if esportes is None:
        esportes = ESPORTES_LIVE

    todos: list[dict] = []
    for esporte in esportes:
        url = f"{BASE_URL}/events?apiKey={API_KEY}&sport={esporte}&status=live"
        try:
            data = _get_json(url)
            if isinstance(data, list):
                todos.extend(data)
                logger.info("live eventos (%s): %d", esporte, len(data))
        except requests.RequestException as exc:
            logger.error("get_eventos_live (%s) falhou: %s", esporte, exc)
    logger.info("live eventos total: %d", len(todos))
    return todos


def buscar_live_arb(
    esportes: list[str] | None = None,
    mercados: list[str] | None = None,
    margem_max: float = 5.0,
) -> list[dict]:
    """
    Detecta oportunidades de arbitragem em jogos AO VIVO.

    Pipeline:
      1. Busca eventos live por esporte
      2. Busca odds via /odds/multi (Bet365 + Betano)
      3. Compara lados e calcula margem
      4. Retorna oportunidades ordenadas por margem crescente

    Custo: 1 chamada por esporte + N/10 chamadas para odds
           (N = eventos live, lotes de 10)
    """
    if esportes is None:
        esportes = ESPORTES_LIVE
    if mercados is None:
        mercados = MERCADOS_LIVE

    mercados_set = set(mercados)

    # 1. Eventos ao vivo
    eventos = get_eventos_live(esportes)
    if not eventos:
        logger.info("live arb: nenhum evento ao vivo no momento")
        return []

    # Limitar por esporte para não estourar chamadas
    por_esporte: dict[str, list] = {}
    for e in eventos:
        esp = e.get("sport", "").lower()
        por_esporte.setdefault(esp, []).append(e)

    selecionados: list[dict] = []
    for esp, evts in por_esporte.items():
        selecionados.extend(evts[:MAX_EVENTOS_LIVE])

    logger.info("live arb: %d eventos selecionados", len(selecionados))

    # 2. Odds em lotes de 10
    resultados: list[dict] = []
    ids = [e["id"] for e in selecionados]

    for i in range(0, len(ids), LOTE_ODDS):
        lote = ids[i:i + LOTE_ODDS]
        url = (
            f"{BASE_URL}/odds/multi"
            f"?apiKey={API_KEY}"
            f"&eventIds={','.join(map(str, lote))}"
            f"&bookmakers={BOOKMAKERS}"
            f"&includeEventDetails=true"
        )
        try:
            odds_lista = _get_json(url)
            if not isinstance(odds_lista, list):
                continue

            for jogo in odds_lista:
                home     = jogo.get("home", "?")
                away     = jogo.get("away", "?")
                liga     = jogo.get("league", {}).get("name", "")
                esporte  = jogo.get("sport", "")
                horario  = _fmt_horario(jogo.get("date", ""))
                urls_jogo = jogo.get("urls", {})

                bookmakers = jogo.get("bookmakers", {})
                if not bookmakers:
                    continue

                # Indexar odds por mercado e lado para cada casa
                odds_por_casa: dict[str, dict[str, dict]] = {}
                for casa, markets in bookmakers.items():
                    casa_base = casa.split(" ")[0]
                    if casa_base not in ("Bet365", "Betano"):
                        continue
                    for market in markets:
                        mkt_name = market.get("name", "")
                        if mkt_name not in mercados_set:
                            continue
                        odds_info = market.get("odds", [{}])
                        if not odds_info or not isinstance(odds_info[0], dict):
                            continue
                        key = mkt_name
                        odds_por_casa.setdefault(key, {})[casa_base] = odds_info[0]

                # 3. Comparar lados entre casas
                for mkt_name, casas_odds in odds_por_casa.items():
                    if len(casas_odds) < 1:
                        continue

                    # Pegar odds de cada casa
                    odds_b365   = casas_odds.get("Bet365", {})
                    odds_betano = casas_odds.get("Betano", {})
                    linha = odds_b365.get("hdp") or odds_betano.get("hdp")

                    # Lados disponíveis
                    lados_b365   = {k: _safe_float(v) for k, v in odds_b365.items()
                                    if k not in ("hdp","max","label") and _safe_float(v) > 1}
                    lados_betano = {k: _safe_float(v) for k, v in odds_betano.items()
                                    if k not in ("hdp","max","label") and _safe_float(v) > 1}

                    todos_lados = set(lados_b365) | set(lados_betano)
                    if len(todos_lados) < 2:
                        continue

                    # Melhor odd por lado entre as casas
                    melhores: dict[str, float] = {}
                    casa_melhor: dict[str, str] = {}
                    for lado in todos_lados:
                        ob = lados_b365.get(lado, 0)
                        on = lados_betano.get(lado, 0)
                        if ob > on:
                            melhores[lado] = ob
                            casa_melhor[lado] = "Bet365"
                        elif on > 0:
                            melhores[lado] = on
                            casa_melhor[lado] = "Betano"
                        else:
                            melhores[lado] = ob
                            casa_melhor[lado] = "Bet365"

                    # Margem com melhores odds
                    soma_probs = sum(1/v for v in melhores.values() if v > 1)
                    margem = round(soma_probs * 100, 2)

                    if margem > (100 + margem_max):
                        continue

                    # Montar legs
                    legs = []
                    for lado, odd in melhores.items():
                        casa = casa_melhor[lado]
                        link = ""
                        if casa == "Bet365":
                            link = (urls_jogo.get("Bet365","")
                                    .replace("www.bet365.com","bet365.bet.br")
                                    .replace("//bet365.com","//bet365.bet.br"))
                        tipo_desc = _resolver_tipo(mkt_name, lado, linha, None)
                        legs.append({
                            "lado":  lado,
                            "tipo":  tipo_desc,
                            "casa":  casa,
                            "odd":   odd,
                            "link":  link,
                        })

                    resultados.append({
                        "jogo":      f"{home} x {away}",
                        "esporte":   esporte,
                        "liga":      liga,
                        "horario":   horario,
                        "status":    "🔴 AO VIVO",
                        "mercado":   mkt_name,
                        "linha":     linha,
                        "margem_pct": margem,
                        "eh_arb":    margem < 100.0,
                        "legs":      legs,
                        # Odds detalhadas por casa
                        "odds_b365":   {k: round(v,3) for k,v in lados_b365.items()},
                        "odds_betano": {k: round(v,3) for k,v in lados_betano.items()},
                    })

        except requests.RequestException as exc:
            logger.error("live arb odds lote %d falhou: %s", i, exc)
            continue

    resultados.sort(key=lambda x: x["margem_pct"])
    logger.info("live arb: %d oportunidades (margem <= %.1f%%)", len(resultados), margem_max)
    return resultados
