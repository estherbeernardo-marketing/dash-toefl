# BI TOEFL

Dashboard de acompanhamento de campanhas Meta Ads do cliente TOEFL.
Foco: **vendas + engajamento Instagram**.

## Estrutura

- `index.html` — dashboard
- `atualizar_bi.py` — script que busca dados da Meta API e injeta no HTML
- `.env` — credenciais (não commitar)
- `requirements.txt`

## Conta

- Ad Account: `act_590951675637306`
- Período: desde 2026-01-01

## Setup local

```bash
pip install -r requirements.txt
python atualizar_bi.py
```

Abra o `index.html` no navegador.

## Deploy

Mesmo processo do BI Fogaça (ver `dashboard-fogaca/README.md`):

1. Repo privado no GitHub `bi-toefl`
2. Secrets: `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID=act_590951675637306`
3. Cloudflare Pages → conectar repo → URL `https://bi-toefl.pages.dev`
