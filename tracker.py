import os
import json
import base64
import logging
from dotenv import load_dotenv
from dateutil import parser as dateparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from anthropic import Anthropic
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()  # lê o arquivo .env local, se existir (não faz nada em produção se não houver)

# --- Configuração via variáveis de ambiente ---
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
UNIDADE_VALOR = float(os.environ.get("UNIDADE_VALOR", "50"))  # valor em R$ de 1 unidade de aposta

claude = Anthropic(api_key=ANTHROPIC_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Rastreia, por chat, qual campo está esperando o próximo texto digitado como novo valor
edicoes_pendentes = {}

PROMPT_EXTRACAO = """Você recebe o print de um ou mais bilhetes/cards de aposta esportiva (Betano, calculadoras
de odds, etc) e a legenda de texto que a pessoa (tipster) escreveu junto, descrevendo o que ela realmente apostou.

REGRAS IMPORTANTES:

1. A imagem pode mostrar um card combinado da casa de apostas (ex: "Dupla", "Tripla"), mas isso NÃO significa
   que é uma única aposta. O que define a estrutura real é a LEGENDA:
   - Se a legenda descreve um stake SEPARADO pra cada perna (ex: "1 Unidade" pra uma, "0,75 Unidades" pra outra),
     trate CADA PERNA como uma aposta "simples" independente, mesmo que o card mostre elas combinadas visualmente.
   - Só é "multipla" (combinada) quando a legenda menciona um ÚNICO stake cobrindo várias pernas juntas
     (ex: "0.25% tripla" cobrindo 3 jogos de uma vez).

2. Ignore quaisquer valores em R$ mostrados dentro dos cards da imagem (tipo "Ganha: R$1.020,00" ou um campo de
   valor tipo "600") — geralmente são só exemplos de uma calculadora de odds, não o valor real apostado.
   O valor real está descrito na legenda, em unidades.

3. "%" no contexto de stake do tipster significa UNIDADES, não porcentagem matemática de banca.
   "0.75%" vira 0.75, "1%" vira 1, "1,25 unidade" vira 1.25.

4. Cada aposta pode já ter um resultado marcado na legenda com ✅ (ganha) ou ❌ (perdida) logo após o stake
   daquela aposta específica. A ORDEM dos emojis na legenda corresponde à ORDEM em que as apostas aparecem no
   texto. Se não houver emoji de resultado pra uma aposta, ela está pendente (ainda sem resultado).

Retorne um JSON puro, sem markdown, com uma LISTA de apostas neste formato:
{{
  "apostas": [
    {{
      "casa_aposta": string ou null,
      "tipo": "simples" ou "multipla",
      "odd_total": number ou null,
      "stake_texto": string ou null,
      "stake_unidades": number ou null,
      "resultado": "ganha" ou "perdida" ou "anulada" ou null,
      "confianca": number,
      "selecoes": [
        {{
          "evento": string,
          "mercado": string,
          "odd": number,
          "data_evento": string ou null
        }}
      ]
    }}
  ]
}}

Datas em "data_evento" devem estar em ISO 8601 estrito (ex: "2026-08-24T20:00:00"). NUNCA use formato brasileiro (DD/MM/AAAA).

Legenda que acompanhou a imagem (pode estar vazia):
\"\"\"{legenda}\"\"\"
"""


def extrair_apostas(imagem_bytes: bytes, legenda: str) -> list[dict]:
    imagem_b64 = base64.b64encode(imagem_bytes).decode()
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagem_b64}},
                {"type": "text", "text": PROMPT_EXTRACAO.format(legenda=legenda)},
            ],
        }],
    )
    bruto = resp.content[0].text.strip()
    bruto = bruto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(bruto).get("apostas", [])


def normalizar_data(valor):
    """Converte qualquer formato de data que o Claude devolver pra ISO 8601.
    Se não conseguir interpretar, retorna None em vez de quebrar o insert."""
    if not valor:
        return None
    try:
        dt = dateparser.parse(valor, dayfirst=True)
        return dt.isoformat()
    except (ValueError, OverflowError):
        logger.warning(f"Não consegui interpretar a data: {valor!r}")
        return None


def origem_do_encaminhamento(update: Update) -> str:
    """Tenta identificar de qual canal/pessoa a mensagem foi encaminhada."""
    origem = update.message.forward_origin
    if not origem:
        return "manual"
    chat = getattr(origem, "chat", None)
    if chat:
        return chat.username or chat.title or "canal desconhecido"
    sender = getattr(origem, "sender_user", None)
    if sender:
        return sender.username or sender.first_name or "usuário desconhecido"
    return getattr(origem, "sender_user_name", None) or "desconhecida"


def montar_card(dados: dict, selecoes: list, valor_apostado, grupo: str, resultado) -> str:
    linhas = []

    if len(selecoes) == 1:
        s = selecoes[0]
        linhas.append(f"⚽ <b>Jogo:</b> {s.get('evento') or '—'}")
        linhas.append(f"🎯 <b>Mercado:</b> {s.get('mercado') or '—'}")
    else:
        for i, s in enumerate(selecoes, 1):
            linhas.append(f"⚽ <b>Perna {i}:</b> {s.get('evento') or '—'} — {s.get('mercado') or '—'} (odd {s.get('odd') or '—'})")

    stake_unid = dados.get("stake_unidades")
    if valor_apostado is not None:
        stake_str = f"{stake_unid} un × R$ {UNIDADE_VALOR:.2f} = R$ {valor_apostado:.2f}"
    else:
        stake_str = dados.get("stake_texto") or "—"

    rotulo_status = {
        "ganha": "✅ Ganha",
        "perdida": "❌ Perdida",
        "anulada": "➖ Anulada",
    }.get(resultado, "⏳ Pendente")

    linhas += [
        f"📈 <b>Odd:</b> {dados.get('odd_total') or '—'}",
        f"💰 <b>Valor:</b> {stake_str}",
        f"🏛️ <b>Casa:</b> {dados.get('casa_aposta') or '—'}",
        f"🏷️ <b>Grupo:</b> {grupo}",
        f"📋 <b>Tipo:</b> {dados.get('tipo') or '—'}",
        f"📊 <b>Status:</b> {rotulo_status} (aguardando confirmação)",
    ]

    if (dados.get("confianca") or 1) < 0.7:
        linhas.append("\n⚠️ Confiança baixa na extração — confere os dados antes de confirmar.")

    return "\n".join(linhas)


async def registrar_e_enviar_card(update: Update, dados: dict, grupo: str, legenda: str, indice: int, total: int):
    stake_unidades = dados.get("stake_unidades")
    valor_apostado = round(stake_unidades * UNIDADE_VALOR, 2) if stake_unidades is not None else None
    resultado = dados.get("resultado")  # 'ganha' | 'perdida' | 'anulada' | None (pendente)

    resposta = supabase.table("apostas").insert({
        "grupo_origem": grupo,
        "tipo": dados.get("tipo"),
        "casa_aposta": dados.get("casa_aposta"),
        "odd_total": dados.get("odd_total"),
        "stake": dados.get("stake_texto"),
        "valor_apostado": valor_apostado,
        "status": "rascunho",  # só vira o status final quando você confirmar no botão
        "mensagem_original": legenda,
        "confianca_extracao": dados.get("confianca"),
        "revisar_manualmente": (dados.get("confianca") or 1) < 0.7,
    }).execute()

    aposta_id = resposta.data[0]["id"]
    selecoes = dados.get("selecoes", [])

    for selecao in selecoes:
        supabase.table("apostas_selecoes").insert({
            "aposta_id": aposta_id,
            "evento": selecao.get("evento"),
            "mercado": selecao.get("mercado"),
            "odd": selecao.get("odd"),
            "data_evento": normalizar_data(selecao.get("data_evento")),
        }).execute()

    texto = montar_card(dados, selecoes, valor_apostado, grupo, resultado)
    if total > 1:
        texto = f"<b>Aposta {indice} de {total}</b>\n\n" + texto

    alvo_status = resultado or "pendente"
    botoes = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data=f"confirmar:{aposta_id}:{alvo_status}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"cancelar:{aposta_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Editar casa", callback_data=f"editarcampo:{aposta_id}:casa_aposta"),
            InlineKeyboardButton("✏️ Editar odd", callback_data=f"editarcampo:{aposta_id}:odd_total"),
        ],
    ])
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=botoes)


async def handler_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    foto = update.message.photo[-1]  # maior resolução disponível
    arquivo = await foto.get_file()
    imagem_bytes = bytes(await arquivo.download_as_bytearray())
    legenda = update.message.caption or ""
    grupo = origem_do_encaminhamento(update)

    try:
        apostas = extrair_apostas(imagem_bytes, legenda)
    except Exception as e:
        logger.error(f"Falha ao extrair: {e}")
        await update.message.reply_text("Não consegui ler esse bilhete, tenta de novo ou manda mais nítido.")
        return

    if not apostas:
        await update.message.reply_text("Não identifiquei nenhuma aposta nesse print.")
        return

    total = len(apostas)
    for i, dados in enumerate(apostas, 1):
        await registrar_e_enviar_card(update, dados, grupo, legenda, indice=i, total=total)


async def handler_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    partes = query.data.split(":")
    acao = partes[0]
    aposta_id = partes[1]

    if acao == "confirmar":
        alvo_status = partes[2] if len(partes) > 2 else "pendente"
        supabase.table("apostas").update({"status": alvo_status}).eq("id", aposta_id).execute()
        rotulo = {
            "ganha": "✅ Ganha", "perdida": "❌ Perdida",
            "anulada": "➖ Anulada", "pendente": "⏳ Pendente",
        }.get(alvo_status, alvo_status)
        novo_texto = query.message.text_html + f"\n\n✅ <b>Confirmada — status: {rotulo}</b>"
        await query.edit_message_text(novo_texto, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([]))

    elif acao == "cancelar":
        supabase.table("apostas").delete().eq("id", aposta_id).execute()
        await query.edit_message_text("❌ Aposta cancelada e removida do banco.", reply_markup=InlineKeyboardMarkup([]))

    elif acao == "editarcampo":
        campo = partes[2]
        chat_id = query.message.chat_id
        aviso_anterior = ""
        if chat_id in edicoes_pendentes:
            aviso_anterior = "⚠️ Cancelei a edição anterior que ainda não tinha recebido valor.\n\n"
        edicoes_pendentes[chat_id] = {
            "aposta_id": aposta_id,
            "campo": campo,
            "message_id": query.message.message_id,
            "texto_atual": query.message.text_html,
            "botoes_atuais": query.message.reply_markup,
        }
        rotulo_campo = {"casa_aposta": "a casa de aposta", "odd_total": "a odd"}.get(campo, campo)
        await context.bot.send_message(chat_id, f"{aviso_anterior}✏️ Manda o novo valor pra {rotulo_campo}:")


async def handler_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    pendente = edicoes_pendentes.get(chat_id)
    if not pendente:
        return  # nenhuma edição em andamento — mensagem de texto solta, ignora

    campo = pendente["campo"]
    aposta_id = pendente["aposta_id"]
    valor_novo = update.message.text.strip()

    if campo == "odd_total":
        try:
            valor_novo = float(valor_novo.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Isso não parece um número válido de odd, tenta de novo.")
            return

    supabase.table("apostas").update({campo: valor_novo}).eq("id", aposta_id).execute()
    del edicoes_pendentes[chat_id]

    # Reescreve a linha correspondente no card original, mantendo o resto igual
    prefixo_linha = {"casa_aposta": "🏛️", "odd_total": "📈"}[campo]
    rotulo = {"casa_aposta": "Casa", "odd_total": "Odd"}[campo]
    linhas = pendente["texto_atual"].split("\n")
    linhas = [
        f"{prefixo_linha} <b>{rotulo}:</b> {valor_novo}" if linha.startswith(prefixo_linha) else linha
        for linha in linhas
    ]
    texto_atualizado = "\n".join(linhas)

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=pendente["message_id"],
        text=texto_atualizado,
        parse_mode="HTML",
        reply_markup=pendente["botoes_atuais"],
    )


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handler_foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler_texto))
    app.add_handler(CallbackQueryHandler(handler_callback))
    logger.info("Bot rodando, esperando bilhetes...")
    app.run_polling()


if __name__ == "__main__":
    main()
