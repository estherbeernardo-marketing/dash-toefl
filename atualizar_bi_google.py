#!/usr/bin/env python3
"""
Atualiza dados de Google Ads no index.html.
Injeta:
- GOOGLE_ACCOUNT: metadata (id, nome, moeda)
- GOOGLE_DAILY: serie diaria (date, cost, impressions, clicks, conversions, conv_value)
- GOOGLE_CAMPAIGNS: lista de campanhas com totais e diario
- GOOGLE_KEYWORDS: top termos de pesquisa por gasto

Periodo: desde 2026-01-01 ate hoje.
"""
import os, sys, json, re
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
except ImportError:
    print('ERRO: pip install google-ads'); sys.exit(1)

ROOT = Path(__file__).parent
HTML_PATH = ROOT / 'index.html'
LOG_PATH = ROOT / 'atualizar_bi.log'
PERIODO_INICIO = os.getenv('PERIODO_INICIO', '2026-01-01')
CLIENT_CUSTOMER_ID = os.getenv('GOOGLE_CLIENT_CUSTOMER_ID')

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [GOOGLE] {msg}'
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def micros(v):
    return (v or 0) / 1_000_000

def get_client():
    config = {
        'developer_token': os.getenv('GOOGLE_DEVELOPER_TOKEN'),
        'client_id': os.getenv('GOOGLE_CLIENT_ID'),
        'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
        'refresh_token': os.getenv('GOOGLE_REFRESH_TOKEN'),
        'login_customer_id': os.getenv('GOOGLE_LOGIN_CUSTOMER_ID'),
        'use_proto_plus': True,
    }
    return GoogleAdsClient.load_from_dict(config)

def fetch_account(client):
    log('Buscando metadata da conta...')
    svc = client.get_service('GoogleAdsService')
    q = """
        SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone
        FROM customer LIMIT 1
    """
    for row in svc.search(customer_id=CLIENT_CUSTOMER_ID, query=q):
        c = row.customer
        return {
            'id': str(c.id),
            'name': c.descriptive_name,
            'currency': c.currency_code,
            'timezone': c.time_zone,
        }
    return {}

def fetch_daily(client, since, until):
    log(f'Buscando serie diaria ({since} -> {until})...')
    svc = client.get_service('GoogleAdsService')
    q = f"""
        SELECT
          segments.date,
          metrics.cost_micros, metrics.impressions, metrics.clicks,
          metrics.conversions, metrics.conversions_value,
          metrics.all_conversions
        FROM customer
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    rows_by_date = {}
    for row in svc.search(customer_id=CLIENT_CUSTOMER_ID, query=q):
        d = row.segments.date
        m = row.metrics
        rows_by_date.setdefault(d, {
            'date': d, 'cost': 0, 'impressions': 0, 'clicks': 0,
            'conversions': 0, 'conv_value': 0, 'all_conversions': 0
        })
        agg = rows_by_date[d]
        agg['cost'] = round(micros(m.cost_micros), 2)
        agg['impressions'] = m.impressions
        agg['clicks'] = m.clicks
        agg['conversions'] = round(m.conversions, 2)
        agg['conv_value'] = round(m.conversions_value, 2)
        agg['all_conversions'] = round(m.all_conversions, 2)
    rows = sorted(rows_by_date.values(), key=lambda x: x['date'])
    log(f'  {len(rows)} dias')
    return rows

def fetch_campaigns(client, since, until):
    log(f'Buscando campanhas ({since} -> {until})...')
    svc = client.get_service('GoogleAdsService')
    q = f"""
        SELECT
          campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,
          campaign.bidding_strategy_type, campaign_budget.amount_micros,
          metrics.cost_micros, metrics.impressions, metrics.clicks,
          metrics.conversions, metrics.conversions_value,
          metrics.average_cpc, metrics.average_cpm, metrics.ctr
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    by_id = {}
    for row in svc.search(customer_id=CLIENT_CUSTOMER_ID, query=q):
        c = row.campaign; m = row.metrics; b = row.campaign_budget
        cid = str(c.id)
        if cid not in by_id:
            by_id[cid] = {
                'id': cid, 'name': c.name,
                'status': c.status.name,
                'channel': c.advertising_channel_type.name,
                'bidding': c.bidding_strategy_type.name,
                'budget': round(micros(b.amount_micros), 2),
                'cost': 0, 'impressions': 0, 'clicks': 0,
                'conversions': 0, 'conv_value': 0
            }
        agg = by_id[cid]
        agg['cost'] += round(micros(m.cost_micros), 2)
        agg['impressions'] += m.impressions
        agg['clicks'] += m.clicks
        agg['conversions'] += m.conversions
        agg['conv_value'] += m.conversions_value
    # calcula derivados
    camps = []
    for c in by_id.values():
        c['cost'] = round(c['cost'], 2)
        c['conversions'] = round(c['conversions'], 2)
        c['conv_value'] = round(c['conv_value'], 2)
        c['ctr'] = (c['clicks'] / c['impressions'] * 100) if c['impressions'] else 0
        c['cpc'] = (c['cost'] / c['clicks']) if c['clicks'] else 0
        c['cpm'] = (c['cost'] / c['impressions'] * 1000) if c['impressions'] else 0
        c['cpa'] = (c['cost'] / c['conversions']) if c['conversions'] > 0 else 0
        c['roas'] = (c['conv_value'] / c['cost']) if c['cost'] > 0 else 0
        camps.append(c)
    camps.sort(key=lambda x: x['cost'], reverse=True)
    log(f'  {len(camps)} campanhas com gasto no periodo')
    return camps

def fetch_search_terms(client, since, until, limit=50):
    log(f'Buscando top termos de pesquisa...')
    svc = client.get_service('GoogleAdsService')
    q = f"""
        SELECT
          search_term_view.search_term, campaign.name,
          metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions
        FROM search_term_view
        WHERE segments.date BETWEEN '{since}' AND '{until}'
        ORDER BY metrics.cost_micros DESC
        LIMIT {limit}
    """
    terms = []
    try:
        for row in svc.search(customer_id=CLIENT_CUSTOMER_ID, query=q):
            t = row.search_term_view; m = row.metrics
            terms.append({
                'term': t.search_term,
                'campaign': row.campaign.name,
                'cost': round(micros(m.cost_micros), 2),
                'impressions': m.impressions,
                'clicks': m.clicks,
                'conversions': round(m.conversions, 2),
                'ctr': (m.clicks / m.impressions * 100) if m.impressions else 0,
                'cpc': (micros(m.cost_micros) / m.clicks) if m.clicks else 0,
            })
    except GoogleAdsException as ex:
        log(f'  AVISO: {ex.error.code().name}: {ex.failure.errors[0].message[:100] if ex.failure.errors else ""}')
        return []
    log(f'  {len(terms)} termos')
    return terms

def fetch_keywords(client, since, until, limit=50):
    log(f'Buscando top palavras-chave...')
    svc = client.get_service('GoogleAdsService')
    q = f"""
        SELECT
          ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
          ad_group.name, campaign.name,
          metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions
        FROM keyword_view
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND ad_group_criterion.status = 'ENABLED'
        ORDER BY metrics.cost_micros DESC
        LIMIT {limit}
    """
    kws = []
    try:
        for row in svc.search(customer_id=CLIENT_CUSTOMER_ID, query=q):
            k = row.ad_group_criterion.keyword; m = row.metrics
            kws.append({
                'keyword': k.text,
                'match': k.match_type.name,
                'ad_group': row.ad_group.name,
                'campaign': row.campaign.name,
                'cost': round(micros(m.cost_micros), 2),
                'impressions': m.impressions,
                'clicks': m.clicks,
                'conversions': round(m.conversions, 2),
                'ctr': (m.clicks / m.impressions * 100) if m.impressions else 0,
                'cpc': (micros(m.cost_micros) / m.clicks) if m.clicks else 0,
            })
    except GoogleAdsException as ex:
        log(f'  AVISO: {ex.error.code().name}')
        return []
    log(f'  {len(kws)} keywords')
    return kws

def replace_var(html, varname, value):
    js = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    pattern = rf'var {varname} = .*?; /\*END_{varname}\*/'
    new_content = f'var {varname} = {js}; /*END_{varname}*/'
    if re.search(pattern, html, flags=re.DOTALL):
        return re.sub(pattern, lambda m: new_content, html, flags=re.DOTALL)
    log(f'  AVISO: marker END_{varname} nao encontrado')
    return html

def main():
    log('=' * 60)
    log('INICIO ATUALIZACAO BI GOOGLE ADS')
    log('=' * 60)

    if not all([os.getenv(k) for k in ['GOOGLE_DEVELOPER_TOKEN', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN', 'GOOGLE_LOGIN_CUSTOMER_ID', 'GOOGLE_CLIENT_CUSTOMER_ID']]):
        log('ERRO: credenciais Google Ads incompletas no .env'); sys.exit(1)

    if not HTML_PATH.exists():
        log(f'ERRO: nao encontrado: {HTML_PATH}'); sys.exit(1)

    until = datetime.now().strftime('%Y-%m-%d')
    client = get_client()
    account = fetch_account(client)
    log(f'Conta: {account.get("name")} ({account.get("currency")})')

    daily = fetch_daily(client, PERIODO_INICIO, until)
    campaigns = fetch_campaigns(client, PERIODO_INICIO, until)
    terms = fetch_search_terms(client, PERIODO_INICIO, until)
    keywords = fetch_keywords(client, PERIODO_INICIO, until)

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    html = replace_var(html, 'GOOGLE_ACCOUNT', account)
    html = replace_var(html, 'GOOGLE_DAILY', daily)
    html = replace_var(html, 'GOOGLE_CAMPAIGNS', campaigns)
    html = replace_var(html, 'GOOGLE_SEARCH_TERMS', terms)
    html = replace_var(html, 'GOOGLE_KEYWORDS', keywords)

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    log(f'HTML salvo: {len(html)} chars')
    log(f'Conta: {account.get("name")} | Campanhas: {len(campaigns)} | Termos: {len(terms)} | Keywords: {len(keywords)}')
    log('CONCLUIDO')

if __name__ == '__main__':
    main()
