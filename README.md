# 🚀 Trader PRO — Deploy no Streamlit Community Cloud

Guia completo para rodar o sistema no celular ou em qualquer lugar,
sem precisar do computador.

---

## Arquivos do projeto

```
trader-pro/
├── app.py                  # Interface principal (Streamlit)
├── main_engine.py          # Motor de busca e análise
├── stats_historicas.py     # Médias de gols via API histórica
├── modelo_poisson.py       # Modelo estatístico
├── alert.py                # Módulo de alertas
├── requirements.txt        # Dependências Python
├── .streamlit/
│   └── secrets.toml        # Variáveis de ambiente (NÃO sobe pro GitHub)
└── .gitignore              # Arquivos ignorados pelo Git
```

---

## Passo 1 — Criar o repositório no GitHub

1. Acesse [github.com](https://github.com) e faça login (crie uma conta se não tiver)
2. Clique em **New repository**
3. Dê um nome: `trader-pro` (pode ser privado — recomendado)
4. Clique em **Create repository**
5. Suba os arquivos do projeto pelo botão **uploading an existing file**
   ou via terminal:

```bash
# No terminal, dentro da pasta do projeto:
git init
git add app.py main_engine.py stats_historicas.py modelo_poisson.py alert.py requirements.txt
git commit -m "primeiro deploy"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/trader-pro.git
git push -u origin main
```

> ⚠️ **Nunca suba o arquivo `.env`** com sua API_KEY.
> A chave vai em outro lugar (veja o Passo 3).

---

## Passo 2 — Criar o arquivo .gitignore

Crie um arquivo chamado `.gitignore` na raiz do projeto com este conteúdo
para garantir que sua chave nunca vá ao GitHub:

```
.env
.streamlit/secrets.toml
__pycache__/
*.pyc
.DS_Store
```

---

## Passo 3 — Configurar a API Key no Streamlit

O Streamlit Cloud tem um cofre de segredos que substitui o `.env`.

1. Acesse [share.streamlit.io](https://share.streamlit.io)
2. Faça login com sua conta do GitHub
3. Clique em **New app**
4. Selecione o repositório `trader-pro`, branch `main`, arquivo `app.py`
5. Antes de finalizar, clique em **Advanced settings**
6. Na seção **Secrets**, cole exatamente isso:

```toml
API_KEY = "sua_chave_aqui"
```

7. Clique em **Deploy**

O Streamlit vai instalar as dependências automaticamente e em 1-2 minutos
seu app estará no ar em um endereço como:
`https://trader-pro-seunome.streamlit.app`

---

## Passo 4 — Ajustar o código para ler o secret

O Streamlit Cloud não usa `.env` — ele expõe os segredos via `st.secrets`.
Adicione estas duas linhas no topo do `main_engine.py` e `stats_historicas.py`,
logo após o `load_dotenv()`:

```python
# Compatibilidade: lê do .env localmente e do st.secrets no Streamlit Cloud
import streamlit as st
load_dotenv()
API_KEY = st.secrets.get("API_KEY") or os.getenv("API_KEY", "")
```

> Isso garante que funciona nos dois ambientes:
> no seu computador lê do `.env`, na nuvem lê do painel do Streamlit.

---

## Passo 5 — Acessar no celular

Após o deploy, você terá uma URL pública.
Salve como atalho na tela inicial do celular:

**Android (Chrome):**
Abra a URL → menu (⋮) → "Adicionar à tela inicial"

**iPhone (Safari):**
Abra a URL → botão compartilhar → "Adicionar à Tela de Início"

Vira um ícone igual a um app nativo. 📱

---

## Avisos importantes

### Hibernação (plano gratuito)
O app "dorme" após ~7 dias sem acesso. Na primeira abertura depois disso,
demora cerca de 30-60 segundos para acordar — é normal.

### Limites do plano gratuito
- 1 GB de RAM
- CPU compartilhada
- Sem limite de requisições HTTP (suas chamadas à Odds API continuam funcionando)

### Segurança
- Mantenha o repositório **privado** no GitHub
- Nunca compartilhe a URL publicamente se não quiser que outros usem sua API Key
- A API Key fica criptografada nos Secrets do Streamlit

### Atualizações
Toda vez que você fizer `git push` com novidades, o Streamlit atualiza
o app automaticamente em alguns segundos.

---

## Solução de problemas comuns

| Problema | Causa | Solução |
|---|---|---|
| `ModuleNotFoundError` | Dependência faltando | Adicionar no `requirements.txt` e fazer novo push |
| `API_KEY not found` | Secret não configurado | Verificar em Settings > Secrets no painel |
| App não atualiza | Cache do browser | Ctrl+Shift+R ou limpar cache |
| Erro 429 da API | Limite de chamadas | Aguardar 1 hora ou reduzir frequência de uso |

---

## Estrutura de custos

| Opção | Custo | Prós | Contras |
|---|---|---|---|
| Streamlit Community Cloud | **Gratuito** | Fácil, sem configuração de servidor | Hiberna sem uso |
| VPS (Hetzner/DigitalOcean) | ~R$ 25/mês | Sempre no ar, mais rápido | Requer configuração de servidor |

Para uso pessoal e esporádico, o plano gratuito é mais que suficiente.
