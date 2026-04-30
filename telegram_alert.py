"""
telegram_alert.py
──────────────────
Envia alertas via Telegram quando uma arb real é detectada
no painel de Comparação de Odds.

Configuração no .env:
    TELEGRAM_TOKEN  = "123456:ABCdef..."   # token do BotFather
    TELEGRAM_CHAT_ID = "123456789"          # seu chat ID

Como pegar o Chat ID (se não souber):
    1. Abra o Telegram e mande qualquer mensagem pro seu bot
    2. Acesse: https://api.telegram.org/bot<SEU_TOKEN>/getUpdates
    3. Procure o campo "chat" -> "id" no JSON retornado
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Compatibilidade .env local + Streamlit Cloud secrets
try:
    import streamlit as st
    TELEGRAM_TOKEN   = st.secrets.get("TELEGRAM_TOKEN", "")   or os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")
except Exception:
    TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

FUSO_BRASIL = timezone(timedelta(hours=-3))
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


# ---------------------------------------------------------------------------
# Envio de mensagem
# ---------------------------------------------------------------------------

def _enviar_mensagem(texto: str) -> bool:
    """
    Envia uma mensagem via Telegram.
    Retorna True se enviou com sucesso, False caso contrário.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram não configurado — defina TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no .env")
        return False

    url = TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram: mensagem enviada com sucesso")
        return True
    except requests.RequestException as exc:
        logger.error("Telegram: falha ao enviar — %s", exc)
        return False


# ---------------------------------------------------------------------------
# Formatar alerta de arb
# ---------------------------------------------------------------------------

def _formatar_arb(d: dict, valor_invest: float) -> str:
    """
    Monta a mensagem HTML de uma arb real para o Telegram.
    """
    agora_br = datetime.now(FUSO_BRASIL).strftime("%d/%m %H:%M")

    # Calcular stakes
    melhor_vb = d.get("melhor_vb", 0)
    melhor_op = d.get("melhor_op", 0)

    if melhor_vb > 1 and melhor_op > 1:
        retorno_alvo = valor_invest / (1/melhor_vb + 1/melhor_op)
        stake_vb     = round(retorno_alvo / melhor_vb, 2)
        stake_op     = round(retorno_alvo / melhor_op, 2)
        total        = round(stake_vb + stake_op, 2)
        lucro        = round(retorno_alvo - total, 2)
    else:
        stake_vb = stake_op = total = lucro = 0

    desc_vb = {"home": "Vitória Casa", "away": "Vitória Fora", "draw": "Empate"}.get(
        d.get("lado_vb", ""), d.get("lado_vb", "").upper()
    )
    desc_op = {"home": "Vitória Casa", "away": "Vitória Fora", "draw": "Empate"}.get(
        d.get("lado_op", ""), d.get("lado_op", "").upper()
    )

    link_b365  = d.get("link_b365", "")
    link_betano = d.get("link_betano", "")

    linhas = [
        f"🚨 <b>ARB REAL DETECTADA</b> — {agora_br}",
        f"",
        f"⚽ <b>{d.get('jogo','')}</b>",
        f"🏆 {d.get('liga','')}",
        f"🕐 {d.get('horario','')}",
        f"",
        f"📊 Mercado: <b>{d.get('mercado','')}</b>",
        f"📌 Tipo: {d.get('tipo','')}",
        f"",
        f"💰 Margem: <b>{d.get('margem_pct', 0):.2f}%</b> (arb confirmada)",
        f"",
        f"━━━ COMO APOSTAR ━━━",
        f"",
        f"<b>Lado 1 — {desc_vb}</b>",
        f"   Odd: {melhor_vb:.3f} | Stake: R$ {stake_vb:.2f}",
        f"",
        f"<b>Lado 2 — {desc_op}</b>",
        f"   Odd: {melhor_op:.3f} | Stake: R$ {stake_op:.2f}",
        f"",
        f"📥 Total investido: R$ {total:.2f}",
        f"✅ Lucro garantido: <b>R$ {lucro:.2f}</b>",
    ]

    if link_b365:
        linhas.append(f"")
        linhas.append(f"🔗 <a href='{link_b365}'>Abrir Bet365</a>")
    if link_betano:
        linhas.append(f"🔗 <a href='{link_betano}'>Abrir Betano</a>")

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------

def alertar_arbs(
    dados: list[dict],
    valor_invest: float = 100.0,
    ids_ja_enviados: set | None = None,
) -> set:
    """
    Verifica a lista de comparação de odds e envia alerta Telegram
    para cada arb real que ainda não foi notificada.

    ids_ja_enviados: set de IDs já enviados (evita spam de repetição).
                     Guarde no session_state entre reruns.

    Retorna o set atualizado de IDs enviados.
    """
    if ids_ja_enviados is None:
        ids_ja_enviados = set()

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram não configurado — alertas desativados")
        return ids_ja_enviados

    arbs_reais = [d for d in dados if d.get("eh_arb")]

    for arb in arbs_reais:
        # ID único por arb: jogo + mercado + lado + margem truncada
        arb_id = f"{arb.get('jogo')}|{arb.get('mercado')}|{arb.get('lado_vb')}|{arb.get('margem_pct',0):.1f}"

        if arb_id in ids_ja_enviados:
            logger.debug("Telegram: arb já notificada — %s", arb_id)
            continue

        mensagem = _formatar_arb(arb, valor_invest)
        sucesso  = _enviar_mensagem(mensagem)

        if sucesso:
            ids_ja_enviados.add(arb_id)
            logger.info("Telegram: alerta enviado — %s", arb.get("jogo"))

    return ids_ja_enviados


def testar_conexao() -> bool:
    """
    Envia uma mensagem de teste para verificar se o bot está configurado.
    Use para confirmar que o token e chat_id estão corretos.
    """
    agora = datetime.now(FUSO_BRASIL).strftime("%d/%m/%Y %H:%M")
    texto = (
        f"✅ <b>Trader PRO — Bot conectado!</b>\n\n"
        f"🤖 Alertas de Arbitragem ativados.\n"
        f"📅 {agora}\n\n"
        f"Você receberá uma mensagem aqui cada vez que uma arb real for detectada."
    )
    return _enviar_mensagem(texto)