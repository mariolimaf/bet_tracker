# Bot de Registro de Apostas

Bot de Telegram que registra apostas esportivas automaticamente num banco Supabase, a partir de prints de bilhetes encaminhados. Resolve o problema de planilhar apostas manualmente: você encaminha o print, o bot lê a imagem com IA, extrai os dados e pergunta antes de gravar de verdade.

## O que o bot faz

- **Lê o print da aposta com visão computacional** (Claude API) — identifica casa de aposta, jogo, mercado, odd e stake, direto da imagem.
- **Separa apostas combinadas de forma inteligente**: se um mesmo print tem várias apostas com investimentos diferentes (mesmo que a casa de apostas mostre elas visualmente agrupadas, tipo "Dupla" ou "Tripla"), o bot identifica isso pelo texto da legenda e registra cada uma como uma aposta independente — inclusive quando existe também uma aposta combinada (múltipla) das mesmas pernas, com stake próprio.
- **Reconhece resultado já marcado**: se você já marcou ✅ (ganhou) ou ❌ (perdeu) na legenda antes de encaminhar, o bot já registra com esse resultado, na mesma ordem em que aparecem no texto — sem precisar atualizar status depois.
- **Converte unidade automaticamente**: você configura o valor de 1 unidade (ex: R$ 50) e o bot calcula o valor real apostado a partir do stake informado pelo tipster (ex: "0,75 unidades" → R$ 37,50), sem depender de conta manual.
- **Pede confirmação antes de gravar como aposta de verdade**: toda extração entra primeiro como rascunho. O bot manda um card resumido com botões:
  - ✅ **Confirmar** — grava a aposta com o status correto (pendente, ganha, perdida ou anulada)
  - ❌ **Cancelar** — apaga o rascunho do banco
  - ✏️ **Editar casa** / ✏️ **Editar odd** — corrige o campo direto pelo chat, sem precisar abrir o Supabase
- **Lucro e ROI calculados automaticamente** no banco (colunas geradas a partir de valor apostado, odd e status) — nada de fazer conta na mão depois.

## Como funciona (fluxo)

1. Você encaminha um print de bilhete pro bot, no privado.
2. O bot baixa a imagem e manda pra API da Claude (modelo Haiku, com visão), junto com a legenda da mensagem.
3. A IA devolve os dados estruturados de cada aposta identificada no print.
4. O bot grava um rascunho no Supabase e responde no chat com um card por aposta, pedindo confirmação.
5. Você confirma, cancela ou corrige algum campo — só depois disso a aposta "existe" de verdade pra fins de análise.

## Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — integração com a API do Telegram
- [Anthropic Claude API](https://docs.claude.com) (modelo `claude-haiku-4-5`, com visão) — leitura e extração dos dados do print
- [Supabase](https://supabase.com) (Postgres) — armazenamento, com views prontas para dashboard (resumo mensal, por casa de apostas, por grupo de origem)

## Arquivos do projeto

| Arquivo | Descrição |
|---|---|
| `telegram_bot_apostas.py` | Código principal do bot |
| `requirements.txt` | Dependências Python |
| `supabase_schema.sql` | Schema das tabelas (`apostas`, `apostas_selecoes`) e views de dashboard |
| `.env.example` | Modelo das variáveis de ambiente necessárias |

## Configuração

1. Crie o banco: rode o conteúdo de `supabase_schema.sql` no SQL Editor do seu projeto Supabase.
2. Copie `.env.example` para `.env` e preencha:

```
TELEGRAM_BOT_TOKEN=      # obtido via @BotFather no Telegram
ANTHROPIC_API_KEY=       # gerado em platform.claude.com
SUPABASE_URL=            # Project Settings > API no Supabase
SUPABASE_KEY=            # chave service_role (ou "Secret key"), não a anon/publishable
UNIDADE_VALOR=50         # valor em R$ de 1 unidade de aposta
```

3. Instale as dependências:

```
pip install -r requirements.txt
```

4. Rode localmente pra testar:

```
python telegram_bot_apostas.py
```

## Deploy

Feito pra rodar como um processo simples e contínuo (long polling, sem precisar de webhook público). Compatível com Railway, Render (Background Worker), Fly.io ou qualquer VPS — basta configurar as mesmas variáveis de ambiente do `.env` na plataforma escolhida.

## Custo aproximado

Usando o modelo Haiku, o custo de API por bilhete processado fica na faixa de poucos centavos de dólar por mês, mesmo em volumes de centenas de apostas — o valor exato depende da quantidade de apostas por print e da resolução das imagens.
