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

# Compatibilidade local (.env) + Streamlit Cloud (st.secrets)
try:
    import streamlit as st
    _st_key = st.secrets.get('API_KEY', '')
except Exception:
    _st_key = ''
    
# Só carrega .env localmente
if not _st_key:
    load_dotenv()    

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

API_KEY: str = _st_key or os.getenv("API_KEY", "")
BASE_URL: str = "https://api.odds-api.io/v3"
BOOKMAKERS: str = "Bet365,Betano BR"

MAX_EVENTOS: int = 12
LOTE_ODDS: int = 6
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
CACHE_TTL_VALUE_BETS: int = 60     # 30 segundos
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


# Mercados que usam estrutura over/under por jogador (cada item em odds_info é um jogador)
_MERCADOS_PROPS_POR_JOGADOR: set[str] = {
    "Points O/U", "Rebounds O/U", "Assists O/U",
    "Steals O/U", "Blocks O/U", "Threes Made O/U",
    "Field Goals Made O/U",
    "Points & Rebounds O/U", "Points & Assists O/U",
    "Assists & Rebounds O/U", "Points, Assists & Rebounds O/U",
    "Steals & Blocks O/U",
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


def _resolver_tipo(
    market_name: str,
    bet_side: str,
    linha=None,
    jogador=None,
) -> str:

    market_lower = (market_name or "").lower()

    # =====================================================
    # EXTRAIR JOGADOR AUTOMATICAMENTE
    # =====================================================

    jogador_extraido = ""

    if "player props -" in market_lower:

        try:
            parte = market_name.split("Player Props -", 1)[1]

            if "(" in parte:
                jogador_extraido = parte.split("(")[0].strip()
            else:
                jogador_extraido = parte.strip()

        except Exception:
            jogador_extraido = ""

    if not jogador and jogador_extraido:
        jogador = jogador_extraido

    # =====================================================
    # DIREÇÃO DA APOSTA
    # =====================================================

    bet_side_lower = str(bet_side).lower()
    
    # =====================================================
    # PLAYER PROPS NBA/NHL
    # home = over
    # away = under
    # =====================================================
    
    if any(x in market_lower for x in [
        "points",
        "rebounds",
        "assists",
        "threes",
        "3 point",
        "pra",
    ]):
    
        props_map = {
            "home": "Mais de",
            "away": "Menos de",
            "over": "Mais de",
            "under": "Menos de",
        }
    
        lado_txt = props_map.get(
            bet_side_lower,
            bet_side
        )
    
    else:
    
        side_map = {
            "over": "Mais de",
            "under": "Menos de",
            "home": "Vitória Casa",
            "away": "Vitória Fora",
            "draw": "Empate",
            "yes": "Sim",
            "no": "Não",
        }
    
        lado_txt = side_map.get(
            bet_side_lower,
            bet_side
        )

    # =====================================================
    # NBA / NHL PROPS
    # =====================================================

    if (
        "points" in market_lower
        and "milestones" not in market_lower
    ):
        desc = f"{lado_txt} {linha} pontos"

    elif (
        "assists" in market_lower
        and "team" not in market_lower
    ):
        desc = f"{lado_txt} {linha} assistências"

    elif "rebounds" in market_lower:
        desc = f"{lado_txt} {linha} rebotes"

    elif (
        "3 point" in market_lower
        or "threes" in market_lower
    ):
        desc = f"{lado_txt} {linha} bolas de 3"

    elif "pra" in market_lower:
        desc = f"{lado_txt} {linha} PRA"

    elif "double double" in market_lower:
        desc = "Double Double"

    elif "triple double" in market_lower:
        desc = "Triple Double"

    # =====================================================
    # FUTEBOL
    # =====================================================

    elif "corners" in market_lower:
        desc = f"{lado_txt} {linha} escanteios"

    elif "bookings" in market_lower:
        desc = f"{lado_txt} {linha} cartões"

    elif "totals" in market_lower:
        desc = f"{lado_txt} {linha} gols"
        
    elif (
    "both teams to score" in market_lower
    or "btts" in market_lower
    ):
    
        desc = f"Ambos marcam — {lado_txt}"

    elif "spread" in market_lower:

        if bet_side_lower == "home":
           desc = f"Casa {linha}"

        elif bet_side_lower == "away":
            desc = f"Fora {linha}"
    
        else:
            desc = f"Handicap {linha}"
    
    elif market_lower in ("ml", "moneyline"):

        if bet_side_lower == "home":
            desc = "Vitória Casa"
    
        elif bet_side_lower == "away":
            desc = "Vitória Fora"
    
        elif bet_side_lower == "draw":
            desc = "Empate"
    
        else:
            desc = lado_txt

    # =====================================================
    # PADRÃO
    # =====================================================

    else:
        desc = market_name

    # =====================================================
    # PREFIXAR JOGADOR
    # =====================================================

    if jogador:
        return f"{jogador} — {desc}"

    return desc

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
@st.cache_data(ttl=CACHE_TTL_EVENTOS)
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
                # Evita payload gigante
                resultados.extend(data[:6])
        except requests.RequestException as exc:
            logger.error("get_odds_multi lote %d falhou: %s", i, exc)
    logger.info("get_odds_multi: %d jogos retornados", len(resultados))
    return resultados


# ---------------------------------------------------------------------------
# 3. Value Bets — EV calculado pela API
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_VALUE_BETS)
def get_value_bets(
    bookmakers: list[str] | None = None
) -> list[dict]:
    """
    Busca value bets diretamente da API.
    Faz 1 request por bookmaker para evitar erro 400.
    """

    if bookmakers is None:
        bookmakers = [
            "Bet365",
            "Betano BR"
        ]

    resultado = []

    for bookmaker in bookmakers:

        url = (
            f"{BASE_URL}/value-bets"
            f"?apiKey={API_KEY}"
            f"&bookmaker={bookmaker}"
            f"&includeEventDetails=true"
        )

        try:

            data = _get_json(url)

            if not isinstance(data, list):

                logger.warning(
                    "get_value_bets (%s): resposta inesperada",
                    bookmaker
                )

                continue

            logger.info(
                "get_value_bets (%s): %d value bets",
                bookmaker,
                len(data)
            )

            resultado.extend(data)

        except requests.RequestException as exc:

            logger.error(
                "get_value_bets (%s) falhou: %s",
                bookmaker,
                exc
            )

    logger.info(
        "get_value_bets TOTAL: %d value bets",
        len(resultado)
    )

    return resultado


# ---------------------------------------------------------------------------
# PROCESSAR VALUE BETS
# ---------------------------------------------------------------------------

def processar_value_bets(
    raw: list[dict],
    odd_min: float,
    odd_max: float,
    mercados_permitidos: set[str] | None,
    modo: str = "todos",
) -> list[dict]:

    resultado: dict[str, dict] = {}

    for vb in raw:

        # ---------------------------------------------------
        # EVENTO
        # ---------------------------------------------------

        event_info = vb.get("event", {})

        esporte_vb = (
            event_info.get("sport")
            or vb.get("sport")
            or ""
        ).strip().lower()

        if "basket" in esporte_vb:
            esporte_vb = "basketball"

        elif "foot" in esporte_vb:
            esporte_vb = "football"

        elif "tennis" in esporte_vb:
            esporte_vb = "tennis"

        # ---------------------------------------------------
        # FILTRO ESPORTE
        # ---------------------------------------------------

        if esporte_vb not in {
            "football",
            "basketball",
            "tennis",
        }:
            continue

        # ---------------------------------------------------
        # MARKET ORIGINAL
        # ---------------------------------------------------

        market_name_original = (
            vb.get("marketName")
            or vb.get("market", {}).get("rawName")
            or vb.get("market", {}).get("name", "")
        ).strip()

        market_lower = str(market_name_original).casefold()

        # ---------------------------------------------------
        # NORMALIZAÇÃO
        # ---------------------------------------------------

        market_name = market_name_original

        if (
            "points" in market_lower
            and "milestones" not in market_lower
        ):
            market_name = "Points O/U"

        elif "rebounds" in market_lower:
            market_name = "Rebounds O/U"

        elif "assists" in market_lower:
            market_name = "Assists O/U"

        elif (
            "3 point" in market_lower
            or "threes" in market_lower
        ):
            market_name = "Player Threes Milestones"

        elif "double double" in market_lower:
            market_name = "Double Double"

        elif "triple double" in market_lower:
            market_name = "Triple Double"

        logger.info(
            "VB esporte=%s | mercado=%s",
            esporte_vb,
            market_name
        )

        # ---------------------------------------------------
        # FILTRO NBA
        # ---------------------------------------------------

        if modo == "nba" and mercados_permitidos:
        
            mercado_ok = any(
                m.lower() in market_name.lower()
                for m in mercados_permitidos
            )
        
            if not mercado_ok:
                continue
        
        # ---------------------------------------------------
        # EV
        # ---------------------------------------------------

        ev_api = vb.get("expectedValue")

        if ev_api is None:
            continue

        try:
            ev_api = float(ev_api)
            if ev_api > 1:
                ev_api = ev_api / 100
        except (TypeError, ValueError):
            continue

        if ev_api < EV_MINIMO:
            continue

        # ---------------------------------------------------
        # ODDS
        # ---------------------------------------------------

        bookmaker = vb.get("bookmaker", "")

        bet_side = vb.get("betSide", "")

        bk_odds = vb.get("bookmakerOdds", {})

        odd_raw = bk_odds.get(bet_side)

        try:
            odd = float(odd_raw)
        except (TypeError, ValueError):
            continue

        if not (odd_min <= odd <= odd_max):
            continue

        # ---------------------------------------------------
        # DADOS EVENTO
        # ---------------------------------------------------

        home = event_info.get("home", "?")

        away = event_info.get("away", "?")

        liga = event_info.get("league", "")

        horario = _fmt_horario(
            event_info.get("date", "")
        )

        # ---------------------------------------------------
        # LINHA
        # ---------------------------------------------------

        linha = vb.get("market", {}).get("hdp")

        # ---------------------------------------------------
        # DESCRIÇÃO
        # ---------------------------------------------------

        tipo = _resolver_tipo(
            market_name_original,
            bet_side,
            linha,
            None
        )

        # ---------------------------------------------------
        # PROBABILIDADE
        # ---------------------------------------------------

        prob_impl = (
            round((1 / odd) * 100, 2)
            if odd > 0 else 50.0
        )
        # ---------------------------------------------------
        # SCORE / EDGE
        # ---------------------------------------------------
        
        prob_decimal = prob_impl / 100
        
        score = round(
            prob_decimal * odd,
            3
        )
        
        edge_score = round(
             ev_api * 1,
             2
        )
        
        # ---------------------------------------------------
        # QUALIDADE
        # ---------------------------------------------------
        
        qualidade = "RUIM"
        
        if ev_api >= 0.10:
            qualidade = "🔥 EXCELENTE"
        
        elif ev_api >= 0.05:
            qualidade = "✅ BOA"
        
        elif ev_api > 0:
            qualidade = "⚡ VALOR"

        # ---------------------------------------------------
        # LINKS
        # ---------------------------------------------------

        def _fix_link(url: str) -> str:

            return (
                url
                .replace(
                    "https://www.bet365.com",
                    "https://www.bet365.bet.br"
                )
                .replace(
                    "https://bet365.com",
                    "https://www.bet365.bet.br"
                )
                .replace(
                    "https://www.betano.com",
                    "https://www.betano.bet.br"
                )
                .replace(
                    "https://betano.com",
                    "https://www.betano.bet.br"
                )
            )

        link_vb = _fix_link(
            bk_odds.get("href", "")
        )

        # ---------------------------------------------------
        # CHAVE
        # ---------------------------------------------------

        chave = (
            f"{home} x {away}"
            f" | {tipo}"
            f" | {bookmaker}"
            f" | vb"
        )

        resultado[chave] = {

            "jogo":
                f"{home} x {away}",

            "liga":
                liga,

            "horario":
                horario,

            "tipo":
                tipo,

            "mercado":
                market_name,

            "linha":
                linha,

            "casa":
                bookmaker,

            "odd":
                round(odd, 2),

            "prob_modelo":
                prob_impl,

            "ev":
                round(ev_api, 3),

            "score":
                score,
            
            "edge_score":
                edge_score,
            
            "qualidade":
                qualidade,

            "fonte":
                "value_bet_api",

            "drop_sinal":
                False,

            "link":
                link_vb,
        }

    logger.info(
        "processar_value_bets: %d oportunidades",
        len(resultado)
    )

    # Evita excesso de payload no mobile
    return list(resultado.values())[:60]
# ---------------------------------------------------------------------------
# 4. Arbitragem
# ---------------------------------------------------------------------------

def get_arbitrage(limit: int = 50) -> list[dict]:
    """
    Oportunidades de arbitragem com stakes ótimas.
    Retorna apenas arbs válidas da API.
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

        # DEBUG
        logger.info(f"ARB RAW TYPE: {type(data)}")

        if not isinstance(data, list):
            logger.warning("get_arbitrage: resposta inesperada")
            logger.info(f"ARB RAW: {data}")
            return []

        logger.info("get_arbitrage: %d oportunidades", len(data))

        # DEBUG detalhado
        for arb in data[:5]:

            event = arb.get("event", {})
            market = arb.get("market", {})
            legs = arb.get("legs", [])

            logger.info(
                f"ARB EVENTO="
                f"{event.get('home', '?')} x {event.get('away', '?')} | "
                f"mercado={market.get('name', '')} | "
                f"legs={len(legs)} | "
                f"profit={arb.get('profitMargin', 0)}%"
            )

            for leg in legs:
                logger.info(
                    f"  BOOK={leg.get('bookmaker')} | "
                    f"odd={leg.get('odd')} | "
                    f"side={leg.get('betSide')}"
                )

        return data

    except requests.RequestException as exc:
        logger.error("get_arbitrage falhou: %s", exc)
        return []


def processar_arbitrage(raw: list[dict], esporte: str = "todos") -> list[dict]:
    """
    Converte payload /arbitrage-bets em lista estruturada.
    """

    SLUGS_NBA = {"usa-nba"}
    SLUGS_FUT = {s for s, e in LIGA_ESPORTE.items() if e == "football"}

    resultado = []

    logger.info(f"processar_arbitrage: recebidos {len(raw)} itens")

    for arb in raw:

        event = arb.get("event", {})
        market = arb.get("market", {})
        slug = event.get("leagueSlug") or event.get("league_slug", "")

        # DEBUG
        logger.info(
            f"PROCESSANDO ARB | "
            f"slug={slug} | "
            f"liga={event.get('league')} | "
            f"mercado={market.get('name')}"
        )

        # FILTROS
        if esporte == "nba" and slug not in SLUGS_NBA:
            continue

        if esporte == "futebol" and slug not in SLUGS_FUT:
            continue

        if esporte == "tenis":
            if not any(slug.startswith(p) for p in SLUGS_TENIS_PREFIXOS):
                continue

        legs = arb.get("legs", [])

        # DEBUG bookmakers
        logger.info(f"legs encontrados: {len(legs)}")

        if len(legs) < 2:
            logger.info("ARB IGNORADA: menos de 2 legs")
            continue

        resultado.append({
            "id": arb.get("id", ""),

            "jogo": (
                f"{event.get('home', '?')} x "
                f"{event.get('away', '?')}"
            ),

            "liga": event.get("league", ""),

            "horario": _fmt_horario(event.get("date", "")),

            "mercado": market.get("name", ""),

            "profit_pct": round(
                arb.get("profitMargin", 0.0), 2
            ),

            "implied_prob": round(
                arb.get("impliedProbability", 0.0), 4
            ),

            "legs": legs,

            "optimal_stakes": arb.get(
                "optimalStakes", []
            ),

            "updated_at": arb.get("updatedAt", ""),
        })

    resultado.sort(
        key=lambda x: x["profit_pct"],
        reverse=True
    )

    logger.info(
        f"processar_arbitrage: {len(resultado)} oportunidades finais"
    )

    return resultado[:30]


# ---------------------------------------------------------------------------
# 5. Dropping Odds — sinal de sharp money
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_DROPPING)
def get_dropping_odds(
    sport: str = "basketball",
    min_drop_pct: float = 1.5,
    time_window: str = "opening",
) -> dict[int, dict]:

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

        logger.info("Dropping RAW TYPE: %s", type(data))

        if not isinstance(data, list):
            logger.warning("get_dropping_odds: resposta inesperada")
            return {}

        logger.info("Dropping RAW itens: %d", len(data))

        index = {}

        for item in data:

            eid = item.get("eventId")
            if not eid:
                continue

            drop_val = (
                item.get("odds", {})
                    .get("drop", {})
                    .get(time_window)
            ) or 0.0

            if eid not in index or drop_val > index[eid].get("drop_pct", 0):

                index[eid] = {
                    "drop_pct": drop_val,
                    "bet_side": item.get("betSide"),
                    "odd_atual": item.get("odds", {}).get("current"),
                    "odd_abertura": item.get("odds", {}).get("opening"),
                    "market": item.get("market", {}).get("name"),
                }

        logger.info(
            "get_dropping_odds: %d eventos com queda >= %.1f%%",
            len(index),
            min_drop_pct
        )

        return index

    except requests.RequestException as exc:

        if "403" in str(exc):
            logger.debug("Dropping odds indisponível no plano")
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

    logger.info("=== SISTEMA INICIADO [modo=%s] ===", modo)

    if mercados_permitidos:
        logger.info(
            "Mercados filtrados: %s",
            sorted(mercados_permitidos)
        )

    # -------------------------------------------------------
    # MODO TÊNIS
    # -------------------------------------------------------

    if modo == "tenis":

        vb_raw = get_value_bets(["Bet365", "Betano BR"]
        )

        result = processar_value_bets(
            vb_raw,
            odd_min,
            odd_max,
            mercados_permitidos,
            modo="tenis"
        )

        result.sort(
            key=lambda x: x["ev"],
            reverse=True
        )

        logger.info(
            "Tênis: %d oportunidades",
            len(result)
        )

        return result

    # -------------------------------------------------------
    # Janela de horas
    # -------------------------------------------------------

    janela_horas = (
        JANELA_HORAS_NBA
        if modo == "nba"
        else JANELA_HORAS_FUTEBOL
    )

    # -------------------------------------------------------
    # Stats históricas
    # -------------------------------------------------------

    if stats_ligas is None:

        logger.warning(
            "stats_ligas não fornecidas — buscando agora"
        )

        stats_ligas = buscar_stats_ligas()

    if not stats_ligas:

        logger.warning(
            "stats_ligas vazias — usando padrão"
        )

        stats_ligas = {}

    logger.info(
        "Stats disponíveis para %d times",
        len(stats_ligas)
    )

    # -------------------------------------------------------
    # VALUE BETS
    # -------------------------------------------------------

    vb_raw = get_value_bets(["Bet365", "Betano BR"]
    )

    oportunidades_vb_todos = processar_value_bets(
        vb_raw,
        odd_min,
        odd_max,
        mercados_permitidos,
        modo=modo
    )

    LIGAS_NBA_NOMES = {
        "USA - NBA",
        "NBA"
    }

    if modo == "nba":

        oportunidades_vb = [
            v for v in oportunidades_vb_todos
            if v.get("liga", "") in LIGAS_NBA_NOMES
        ]

    elif modo == "futebol":

        oportunidades_vb = [
            v for v in oportunidades_vb_todos
            if v.get("liga", "") not in LIGAS_NBA_NOMES
        ]

    else:

        oportunidades_vb = oportunidades_vb_todos

    logger.info(
        "Value bets [modo=%s]: %d",
        modo,
        len(oportunidades_vb)
    )

    # -------------------------------------------------------
    # EVENTOS
    # -------------------------------------------------------

    eventos = get_events()

    agora = datetime.now(timezone.utc)

    janela_fim = agora + timedelta(
        hours=janela_horas
    )

    eventos_filtrados = []

    for e in eventos:

        try:

            dt = datetime.fromisoformat(
                e["date"].replace("Z", "+00:00")
            )

            if agora <= dt <= janela_fim:
                eventos_filtrados.append(e)

        except Exception as exc:

            logger.debug(
                "Evento ignorado: %s",
                exc
            )

    logger.info(
        "Eventos nas próximas %dh: %d",
        janela_horas,
        len(eventos_filtrados)
    )

    eventos_ligas = [

        e for e in eventos_filtrados

        if e.get("league", {}).get("slug")
        in LIGAS_PERMITIDAS
    ]

    logger.info(
        "Eventos em ligas fortes: %d",
        len(eventos_ligas)
    )

    # -------------------------------------------------------
    # FILTRO POR MODO
    # -------------------------------------------------------

    if modo == "futebol":

        eventos_ord = sorted(

            [
                e for e in eventos_ligas
                if LIGA_ESPORTE.get(
                    e.get("league", {}).get("slug")
                ) == "football"
            ],

            key=score_evento,
            reverse=True

        )[:MAX_EVENTOS]

    elif modo == "nba":

        eventos_ord = sorted(

            [
                e for e in eventos_ligas
                if LIGA_ESPORTE.get(
                    e.get("league", {}).get("slug")
                ) == "basketball"
            ],

            key=score_evento,
            reverse=True

        )[:MAX_EVENTOS]

    else:

        eventos_ord = sorted(
            eventos_ligas,
            key=score_evento,
            reverse=True
        )[:MAX_EVENTOS]

    logger.info(
        "Eventos selecionados [modo=%s]: %d",
        modo,
        len(eventos_ord)
    )

    # -------------------------------------------------------
    # ODDS
    # -------------------------------------------------------

    event_ids = [

        e["id"]

        for e in eventos_ord

        if e.get("id")
    ]

    logger.info(
        "IDs enviados para odds: %d",
        len(event_ids)
    )

    odds_lista = get_odds_multi(event_ids)

    # -------------------------------------------------------
    # DROPPING ODDS
    # -------------------------------------------------------

    drop_fut = get_dropping_odds(
        sport="football",
        min_drop_pct=5.0
    )

    dropping_index = {
        **drop_fut
    }

    logger.info(
        "Dropping odds: %d eventos",
        len(drop_fut)
    )

    # -------------------------------------------------------
    # RESULTADOS POISSON
    # -------------------------------------------------------

    resultados_poisson: dict[str, dict] = {}

    for jogo in odds_lista:

        bookmakers = jogo.get(
            "bookmakers",
            {}
        )
        
        if len(bookmakers) == 0:
           continue

        if not bookmakers:
            continue

        home = jogo.get("home", "?")
        away = jogo.get("away", "?")

        event_id = jogo.get("id", 0)

        liga = jogo.get(
            "league",
            {}
        ).get("name", "")

        horario = _fmt_horario(
            jogo.get("date", "")
        )

        urls_jogo = jogo.get("urls", {})

        # ---------------------------------------------------
        # POISSON
        # ---------------------------------------------------

        lam_h, lam_a = get_medias_confronto(
            home,
            away,
            stats_ligas
        )

        matriz = matriz_resultados(
            lam_h,
            lam_a
        )

        p_home, p_draw, p_away = prob_vitoria(
            matriz
        )

        probs = {
            "home": p_home,
            "draw": p_draw,
            "away": p_away
        }

        drop_info = dropping_index.get(
            event_id
        )

        for casa, markets in bookmakers.items():
            
            # Evita processar casas desnecessárias
            if casa not in ["Bet365", "Betano BR"]:
                continue

            for market in markets:

                market_name = market.get(
                    "name",
                    ""
                )

                if (
                    mercados_permitidos
                    and market_name not in mercados_permitidos
                ):
                    continue

                odds_info = market.get(
                    "odds",
                    []
                )

                if not odds_info:
                    continue

                casa_base = casa.split(" ")[0]

                raw_link = (
                    urls_jogo.get(casa_base)
                    or urls_jogo.get("Bet365", "")
                )

                link = (
                    raw_link
                    .replace(
                        "https://www.bet365.com",
                        "https://www.bet365.bet.br"
                    )
                    .replace(
                        "https://bet365.com",
                        "https://www.bet365.bet.br"
                    )
                    .replace(
                        "https://www.betano.com",
                        "https://www.betano.bet.br"
                    )
                    .replace(
                        "https://betano.com",
                        "https://www.betano.bet.br"
                    )
                )

                drop_sinal = bool(
                    drop_info
                    and drop_info.get("market") == market_name
                )

                linha, label = _extrair_linha_label(
                    market,
                    odds_info
                )

                if not isinstance(
                    odds_info[0],
                    dict
                ):
                    continue

                for lado, odd_valor in odds_info[0].items():

                    if lado in {
                        "hdp",
                        "label",
                        "over",
                        "under"
                    }:
                        continue

                    try:
                        odd = float(odd_valor)

                    except (
                        TypeError,
                        ValueError
                    ):
                        continue

                    if not (
                        odd_min <= odd <= odd_max
                    ):
                        continue

                    tipo = _resolver_tipo(
                        market_name,
                        lado,
                        linha,
                        label
                    )

                    prob = _prob_para_lado(
                        market_name,
                        lado,
                        probs
                    )

                    ev = round(
                        (prob * odd) - 1,
                        3
                    )

                    if ev < EV_MINIMO:
                        continue

                    # ---------------------------------------------
                    # SCORE / EDGE
                    # ---------------------------------------------

                    prob_pct = round(
                        prob * 100,
                        2
                    )

                    # score simples
                    score = round(
                        prob * odd,
                        3
                    )

                    # força do edge
                    edge_score = round(
                        score * (1 + ev),
                        3
                    )

                    # ---------------------------------------------
                    # QUALIDADE
                    # ---------------------------------------------

                    qualidade = "RUIM"

                    if ev >= 0.10:
                        qualidade = "🔥 EXCELENTE"

                    elif ev >= 0.05:
                        qualidade = "✅ BOA"

                    elif ev > 0:
                        qualidade = "⚡ VALOR"

                    # ---------------------------------------------
                    # CHAVE
                    # ---------------------------------------------

                    chave = (
                        f"{home} x {away}"
                        f" | {tipo}"
                        f" | {casa}"
                    )

                    # ---------------------------------------------
                    # RESULTADO FINAL
                    # ---------------------------------------------

                    resultados_poisson[chave] = {

                        "jogo":
                            f"{home} x {away}",

                        "liga":
                            liga,

                        "horario":
                            horario,

                        "tipo":
                            tipo,

                        "mercado":
                            market_name,

                        "linha":
                            linha,

                        "casa":
                            casa,

                        "odd":
                            round(odd, 2),

                        "prob_modelo":
                            prob_pct,

                        "ev":
                            round(ev, 3),

                        "score":
                            score,

                        "edge_score":
                            edge_score,

                        "qualidade":
                            qualidade,

                        "fonte":
                            "poisson",

                        "drop_sinal":
                            drop_sinal,

                        "link":
                            link,
                    }

    # -------------------------------------------------------
    # UNIFICAR
    # -------------------------------------------------------

    chaves_vb = {

        v["jogo"] + v["tipo"] + v["casa"]

        for v in oportunidades_vb
    }

    poisson_extra = [

        p for p in resultados_poisson.values()

        if (
            p["jogo"]
            + p["tipo"]
            + p["casa"]
        ) not in chaves_vb
    ]

    todos = (
        oportunidades_vb
        + poisson_extra
    )

    todos.sort(
        key=lambda x: (
            x.get("horario", ""),
            -x["ev"]
        )
    )

    logger.info(
        "Oportunidades: %d value_bet_api + %d poisson = %d total",
        len(oportunidades_vb),
        len(poisson_extra),
        len(todos),
    )
    # Limita quantidade para não travar mobile/iPad
    return todos[:80]


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
    vb_b365   = get_value_bets(["Bet365"])
    vb_betano = get_value_bets(["Betano BR"])

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
        jogador = (
            vb.get("player")
            or vb.get("market", {}).get("player")
            or vb.get("market", {}).get("playerName")
            or ""
        )
        
        market_name_original = (
            vb.get("marketName")
            or vb.get("market", {}).get("rawName")
            or vb.get("market", {}).get("name", "")
        )
        
        tipo_desc = _resolver_tipo(
            market_name_original,
            bet_side,
            linha,
            jogador
        )
        
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
# Live — Detector de Crossing Odds (futebol ao vivo, Bet365)
# ---------------------------------------------------------------------------
# Crossing odds = momento em que Casa e Fora se cruzam durante o jogo ao vivo.
# É o ponto de maior equilíbrio e onde divergências entre casas são maiores.
# Útil como sinal de timing mesmo com só uma casa disponível.
# ---------------------------------------------------------------------------

ESPORTES_LIVE: list[str] = ["football", "basketball"]  # só futebol tem odds ao vivo na API atual
MERCADOS_LIVE: list[str] = ["ML", "Spread", "Totals", "Points O/U","Rebounds O/U","Assists O/U","Player Points Milestones","Player Rebounds Milestones","Player Assists Milestones","Player Threes Milestones"
]
MAX_EVENTOS_LIVE: int = 20


def get_eventos_live(esportes: list[str] | None = None) -> list[dict]:
    """Retorna eventos com status=live. Custo: 1 chamada por esporte."""
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


def _get_odds_live(event_ids: list[int]) -> list[dict]:
    """Busca odds ao vivo em lotes. Custo: ceil(N/10) chamadas."""
    resultados: list[dict] = []
    for i in range(0, len(event_ids[:MAX_EVENTOS_LIVE]), LOTE_ODDS):
        lote = event_ids[i:i + LOTE_ODDS]
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
            logger.error("odds live lote %d falhou: %s", i, exc)
    return resultados


def buscar_crossing_odds(
    threshold: float = 0.15,
    mercados: list[str] | None = None,
) -> list[dict]:
    """
    Detecta crossing odds em jogos de futebol ao vivo.

    threshold: diferença máxima entre odd Casa e odd Fora para considerar
               que estão cruzando (ex: 0.15 → odds dentro de 0.15 uma da outra)

    Classifica cada jogo em:
      🎯 Cruzado    — odds já se cruzaram (Fora < Casa)
      ⚡ Cruzando   — diferença <= threshold (prestes a cruzar)
      📊 Monitorar  — diferença > threshold mas vale acompanhar

    Custo: 1 chamada (eventos) + ceil(N/10) chamadas (odds)
    """
    if mercados is None:
        mercados = MERCADOS_LIVE
    mercados_set = set(mercados)

    # 1. Eventos ao vivo de futebol
    eventos = get_eventos_live(["football"])
    if not eventos:
        return []

    event_ids = [e["id"] for e in eventos[:MAX_EVENTOS_LIVE]]
    odds_lista = _get_odds_live(event_ids)

    resultado: list[dict] = []

    for jogo in odds_lista:
        home    = jogo.get("home", "?")
        away    = jogo.get("away", "?")
        liga    = jogo.get("league", {}).get("name", "")
        horario = _fmt_horario(jogo.get("date", ""))
        urls_jogo = jogo.get("urls", {})
        link = (
            urls_jogo.get("Bet365", "")
            .replace("www.bet365.com", "bet365.bet.br")
            .replace("//bet365.com", "//bet365.bet.br")
        )

        bookmakers = jogo.get("bookmakers", {})
        
        if len(bookmakers) == 0:
           continue

        for casa_raw, markets in bookmakers.items():
            casa_base = casa_raw.split(" ")[0]
            if casa_base not in ("Bet365", "Betano"):
                continue

            for market in markets:
                mkt_name = market.get("name", "")
                if mkt_name not in mercados_set:
                    continue

                odds_info = market.get("odds", [{}])
                if not odds_info or not isinstance(odds_info[0], dict):
                    continue

                entry = odds_info[0]

                # ML — comparar home vs away
                if mkt_name == "ML":
                    odd_home = _safe_float(entry.get("home"))
                    odd_away = _safe_float(entry.get("away"))
                    odd_draw = _safe_float(entry.get("draw"))

                    if odd_home <= 1 or odd_away <= 1:
                        continue

                    diff = abs(odd_home - odd_away)
                    cruzado   = odd_away < odd_home   # favorito virou
                    cruzando  = diff <= threshold
                    prob_home = round(1/odd_home * 100, 1)
                    prob_away = round(1/odd_away * 100, 1)

                    if cruzado:
                        status = "🎯 Cruzado"
                        urgencia = 0
                    elif cruzando:
                        status = "⚡ Cruzando"
                        urgencia = 1
                    else:
                        status = "📊 Monitorar"
                        urgencia = 2

                    resultado.append({
                        "jogo":       f"{home} x {away}",
                        "liga":       liga,
                        "horario":    horario,
                        "casa":       casa_base,
                        "mercado":    "Resultado Final (1X2)",
                        "desc_home":  f"Vitória {home}",
                        "desc_away":  f"Vitória {away}",
                        "desc_draw":  "Empate" if odd_draw > 1 else None,
                        "odd_home":   round(odd_home, 3),
                        "odd_away":   round(odd_away, 3),
                        "odd_draw":   round(odd_draw, 3) if odd_draw > 1 else None,
                        "diff":       round(diff, 3),
                        "prob_home":  prob_home,
                        "prob_away":  prob_away,
                        "cruzado":    cruzado,
                        "cruzando":   cruzando,
                        "status":     status,
                        "urgencia":   urgencia,
                        "link":       link,
                        "apostar_em": f"Vitória {away}" if cruzado else (
                            f"Vitória {away} (tendência)" if odd_away > odd_home
                            else f"Vitória {home} (tendência)"
                        ),
                    })

                # Totals — comparar OVER vs UNDER
                elif mkt_name == "Totals":
                    over  = _safe_float(entry.get("over") or entry.get("home"))
                    under = _safe_float(entry.get("under") or entry.get("away"))
                    hdp   = entry.get("hdp")

                    if over <= 1 or under <= 1:
                        continue

                    diff     = abs(over - under)
                    cruzado  = under < over
                    cruzando = diff <= threshold

                    if cruzado:     status, urgencia = "🎯 Cruzado", 0
                    elif cruzando:  status, urgencia = "⚡ Cruzando", 1
                    else:           status, urgencia = "📊 Monitorar", 2

                    resultado.append({
                        "jogo":      f"{home} x {away}",
                        "liga":      liga,
                        "horario":   horario,
                        "casa":      casa_base,
                        "mercado":   f"Total de Gols ({hdp})" if hdp else "Total de Gols",
                        "desc_home": f"Mais de {hdp} gols" if hdp else "Mais de",
                        "desc_away": f"Menos de {hdp} gols" if hdp else "Menos de",
                        "desc_draw": None,
                        "odd_home":  round(over, 3),
                        "odd_away":  round(under, 3),
                        "odd_draw":  None,
                        "diff":      round(diff, 3),
                        "prob_home": round(1/over * 100, 1),
                        "prob_away": round(1/under * 100, 1),
                        "cruzado":   cruzado,
                        "cruzando":  cruzando,
                        "status":    status,
                        "urgencia":  urgencia,
                        "link":      link,
                        "apostar_em": f"Menos de {hdp} gols" if cruzado else f"Mais de {hdp} gols",
                    })

    # Ordenar: cruzados primeiro, depois cruzando, depois monitorar
    # Dentro de cada grupo, menor diferença primeiro
    resultado.sort(key=lambda x: (x["urgencia"], x["diff"]))
    logger.info("crossing odds: %d sinais encontrados", len(resultado))
    return resultado
