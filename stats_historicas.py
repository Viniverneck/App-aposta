"""
stats_historicas.py
───────────────────
Busca eventos finalizados via /historical/events + /historical/odds
e calcula médias de gols por time (marcados e sofridos) para alimentar
o modelo Poisson com dados reais em vez de valores fixos.

Custo de API por execução:
  - 1 chamada por liga para /historical/events   → 8 chamadas (8 ligas)
  - 0 chamadas para /historical/odds (usamos apenas os placares dos eventos)
  ─────────────────────────────────────────────────────────────────────────
  Total: 8 chamadas — cacheia por 24h, portanto só gasta 1x por dia.

Disponibilidade: dados históricos a partir de dezembro 2025.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

# Compatibilidade local (.env) + Streamlit Cloud (st.secrets)
try:
    import streamlit as st
    _st_key = st.secrets.get('API_KEY', '')
except Exception:
    _st_key = ''

logger = logging.getLogger(__name__)

API_KEY: str = _st_key or os.getenv("API_KEY", "")
BASE_URL: str = "https://api.odds-api.io/v3"

# Janela de histórico: últimos N dias para calcular a média
JANELA_HISTORICO_DIAS: int = 30

# Média padrão usada quando não há dados suficientes para um time
MEDIA_GOLS_PADRAO: float = 1.2

# Mínimo de jogos para considerar a média confiável
MIN_JOGOS: int = 3

LIGAS_STATS: list[str] = [
    "spain-la-liga",
    "italy-serie-a",
    "england-premier-league",
    "germany-bundesliga",
    "france-ligue-1",
    "uefa-champions-league",
    "brazil-serie-a",
    "brazil-copa-do-brasil",
]


# ---------------------------------------------------------------------------
# Busca de eventos históricos por liga
# ---------------------------------------------------------------------------

def _get_historico_liga(liga_slug: str, de: str, ate: str) -> list[dict]:
    """
    Busca eventos finalizados de uma liga num intervalo de datas.
    de / ate: formato RFC3339, ex: "2025-03-01T00:00:00Z"
    """
    url = (
        f"{BASE_URL}/historical/events"
        f"?apiKey={API_KEY}"
        f"&sport=football"
        f"&league={liga_slug}"
        f"&from={de}"
        f"&to={ate}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.warning("historico_liga %s: resposta inesperada", liga_slug)
            return []
        # Filtrar só jogos com placar registrado
        com_placar = [
            e for e in data
            if e.get("scores", {}).get("home") is not None
            and e.get("scores", {}).get("away") is not None
        ]
        logger.debug("historico_liga %s: %d jogos com placar", liga_slug, len(com_placar))
        return com_placar
    except requests.RequestException as exc:
        logger.error("historico_liga %s falhou: %s", liga_slug, exc)
        return []


# ---------------------------------------------------------------------------
# Cálculo de médias por time
# ---------------------------------------------------------------------------

def _calcular_medias(eventos: list[dict]) -> dict[str, dict]:
    """
    Recebe lista de eventos com scores e retorna dict:
    {
        "Arsenal": {
            "media_marcados": 1.8,   # média de gols marcados por jogo
            "media_sofridos": 0.9,   # média de gols sofridos por jogo
            "jogos": 12
        },
        ...
    }
    Cada time aparece como home e como away — calcula combinado.
    """
    stats: dict[str, dict[str, Any]] = {}

    for e in eventos:
        home: str = e.get("home", "")
        away: str = e.get("away", "")
        scores = e.get("scores", {})
        gols_home = scores.get("home", 0) or 0
        gols_away = scores.get("away", 0) or 0

        for time, marcados, sofridos in [
            (home, gols_home, gols_away),
            (away, gols_away, gols_home),
        ]:
            if not time:
                continue
            if time not in stats:
                stats[time] = {"total_marcados": 0, "total_sofridos": 0, "jogos": 0}
            stats[time]["total_marcados"] += marcados
            stats[time]["total_sofridos"] += sofridos
            stats[time]["jogos"] += 1

    # Converter totais em médias
    medias: dict[str, dict] = {}
    for time, s in stats.items():
        jogos = s["jogos"]
        if jogos < MIN_JOGOS:
            continue  # dados insuficientes — vai usar o padrão
        medias[time] = {
            "media_marcados": round(s["total_marcados"] / jogos, 3),
            "media_sofridos": round(s["total_sofridos"] / jogos, 3),
            "jogos": jogos,
        }

    return medias


# ---------------------------------------------------------------------------
# Interface pública: buscar stats de todas as ligas
# ---------------------------------------------------------------------------

def buscar_stats_ligas(ligas: list[str] | None = None) -> dict[str, dict]:
    """
    Busca e agrega médias de gols de todas as ligas configuradas.
    Retorna dict unificado {nome_time: {media_marcados, media_sofridos, jogos}}.

    Custo: 1 chamada de API por liga.
    Cache de 24h recomendado na camada do Streamlit (st.cache_data ttl=86400).
    """
    if ligas is None:
        ligas = LIGAS_STATS

    agora = datetime.now(timezone.utc)
    ate = agora.strftime("%Y-%m-%dT%H:%M:%SZ")
    de = (agora - timedelta(days=JANELA_HISTORICO_DIAS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info("Buscando stats históricas — janela: %s → %s", de, ate)

    todos_eventos: list[dict] = []
    for liga in ligas:
        eventos = _get_historico_liga(liga, de, ate)
        todos_eventos.extend(eventos)
        logger.info("Liga %s: %d jogos", liga, len(eventos))

    medias = _calcular_medias(todos_eventos)
    logger.info("Stats calculadas para %d times", len(medias))
    return medias


# ---------------------------------------------------------------------------
# Lookup: retorna médias de ataque e defesa para um confronto específico
# ---------------------------------------------------------------------------

def get_medias_confronto(
    home: str,
    away: str,
    stats: dict[str, dict],
) -> tuple[float, float]:
    """
    Retorna (lambda_home, lambda_away) para o modelo Poisson.

    lambda_home = ataque_home * defesa_away  (ajuste relativo de força)
    lambda_away = ataque_away * defesa_home

    Se não houver dados para um dos times, usa MEDIA_GOLS_PADRAO.

    Exemplo de uso:
        stats = buscar_stats_ligas()
        lam_h, lam_a = get_medias_confronto("Arsenal", "Chelsea", stats)
        matriz = matriz_resultados(lam_h, lam_a)
    """
    # Média geral da liga como baseline
    if stats:
        medias_marcados = [v["media_marcados"] for v in stats.values()]
        media_liga = sum(medias_marcados) / len(medias_marcados)
    else:
        media_liga = MEDIA_GOLS_PADRAO

    h = stats.get(home, {})
    a = stats.get(away, {})

    ataque_home  = h.get("media_marcados", media_liga)
    defesa_home  = h.get("media_sofridos", media_liga)
    ataque_away  = a.get("media_marcados", media_liga)
    defesa_away  = a.get("media_sofridos", media_liga)

    # Dixon-Coles simplificado: força relativa normalizada pela média da liga
    if media_liga > 0:
        lambda_home = (ataque_home / media_liga) * (defesa_away / media_liga) * media_liga
        lambda_away = (ataque_away / media_liga) * (defesa_home / media_liga) * media_liga
    else:
        lambda_home = MEDIA_GOLS_PADRAO
        lambda_away = MEDIA_GOLS_PADRAO

    # Clamp: evitar valores absurdos em amostras pequenas
    lambda_home = max(0.3, min(lambda_home, 5.0))
    lambda_away = max(0.3, min(lambda_away, 5.0))

    logger.debug(
        "%s x %s → λ_home=%.2f λ_away=%.2f (atq_h=%.2f def_a=%.2f atq_a=%.2f def_h=%.2f)",
        home, away, lambda_home, lambda_away,
        ataque_home, defesa_away, ataque_away, defesa_home,
    )

    return round(lambda_home, 3), round(lambda_away, 3)
