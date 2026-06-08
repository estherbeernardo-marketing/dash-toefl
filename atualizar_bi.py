#!/usr/bin/env python3
"""
Atualiza o BI TOEFL (index.html) com dados frescos da Meta Ads API.
Periodo: desde 2026-01-01 ate hoje (parcial).
Foco: vendas + engajamento Instagram.

Estrutura dos dados injetados no HTML:
- DAILY_DATA: agregado conta dia a dia
- CAMPAIGNS_META: metadata estatica (id, nome, status, bloco, produto)
- CAMPAIGNS_DAILY: array compacto [cid, date, spend, imp, clicks, lpv, leads, purchases]
- ADS_META: metadata (id, nome, campanha, criativo, ...)
- ADS_DAILY: array compacto por anuncio dia a dia
- ADS_VIDEO_TOTALS: totais de retencao de video (nao tem por dia, so total)
"""
import requests, json, re, os, sys, time
from datetime import datetime, timedelta

# Carrega .env local (opcional, usado em dev)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ========== CONFIG ==========
TOKEN = os.getenv('META_ACCESS_TOKEN', '')
ACCOUNT = os.getenv('META_AD_ACCOUNT_ID', 'act_590951675637306')
PERIODO_INICIO = os.getenv('PERIODO_INICIO', '2026-01-01')
BASE = 'https://graph.facebook.com/v21.0'
BI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')

if not TOKEN:
    print('ERRO: META_ACCESS_TOKEN nao configurado (use .env ou env vars)')
    sys.exit(1)
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'atualizar_bi.log')

LEAD_ACTION_TYPES = {
    'lead', 'leadgen.other',
    'onsite_conversion.lead_grouped',
    'onsite_conversion.messaging_conversation_started_7d',
    'offsite_conversion.fb_pixel_lead',
}
PURCHASE_ACTION_TYPES = {
    'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase',
}
# Add to cart / Initiate checkout — ordem de prioridade p/ dedup (omni > generico > pixel).
# Esses 3 reportam o MESMO evento; somar todos infla 2-3x (mesma logica das compras).
ADD_TO_CART_PRIORITY = ('omni_add_to_cart', 'add_to_cart', 'offsite_conversion.fb_pixel_add_to_cart')
INITIATE_CHECKOUT_PRIORITY = ('omni_initiated_checkout', 'initiate_checkout', 'offsite_conversion.fb_pixel_initiate_checkout')
# Acoes de engajamento (Instagram + post)
POST_ENGAGEMENT_TYPES = {'post_engagement'}
PAGE_ENGAGEMENT_TYPES = {'page_engagement'}
LIKE_TYPES = {'like', 'post_reaction'}
COMMENT_TYPES = {'comment'}
SHARE_TYPES = {'post'}
SAVE_TYPES = {'onsite_conversion.post_save'}
FOLLOW_TYPES = {'follow', 'onsite_conversion.follow'}
PROFILE_VISIT_TYPES = {
    'onsite_conversion.flow_complete',
    'onsite_conversion.view_content',
}

# Mapeamento otimizacao -> action_type + label do "Resultado"
# Pra OFFSITE_CONVERSIONS depende do custom_event_type do promoted_object
OPTIMIZATION_TO_RESULT = {
    'LEAD_GENERATION':       {'action_type': 'leadgen.other',                                  'label': 'Cadastros (form)'},
    'CONVERSATIONS':         {'action_type': 'onsite_conversion.messaging_conversation_started_7d', 'label': 'Conversas'},
    'VISIT_INSTAGRAM_PROFILE':{'action_type': 'link_click',                                    'label': 'Visitas IG'},
    'LANDING_PAGE_VIEWS':    {'action_type': 'landing_page_view',                              'label': 'LP Views'},
    'LINK_CLICKS':           {'action_type': 'link_click',                                     'label': 'Cliques no link'},
    'POST_ENGAGEMENT':       {'action_type': 'post_engagement',                                'label': 'Engajamento'},
    'IMPRESSIONS':           {'action_type': 'impressions',                                    'label': 'Impressoes'},
    'REACH':                 {'action_type': 'reach',                                          'label': 'Alcance'},
}
OFFSITE_EVENT_MAP = {
    'LEAD':                  {'action_type': 'offsite_conversion.fb_pixel_lead',               'label': 'Leads (pixel)'},
    'COMPLETE_REGISTRATION': {'action_type': 'offsite_conversion.fb_pixel_complete_registration','label': 'Cadastros (pixel)'},
    'PURCHASE':              {'action_type': 'offsite_conversion.fb_pixel_purchase',           'label': 'Compras (pixel)'},
    # OTHER (custom event): NAO usa fb_pixel_custom (que agrega todos custom events do pixel).
    # Em vez disso, somamos as actions com prefixo "offsite_conversion.custom.{event_id}"
    # que correspondem aos eventos custom especificos. Marcamos com prefix:
    'OTHER':                 {'action_type': 'offsite_conversion.custom.*',                   'label': 'Conv. custom'},
    'VIEW_CONTENT':          {'action_type': 'offsite_conversion.fb_pixel_view_content',       'label': 'View Content'},
    'ADD_TO_CART':           {'action_type': 'offsite_conversion.fb_pixel_add_to_cart',        'label': 'Add Cart'},
    'INITIATE_CHECKOUT':     {'action_type': 'offsite_conversion.fb_pixel_initiate_checkout',  'label': 'Checkout'},
}

def somar_action_match(actions, action_type_pattern):
    """Soma actions: se pattern terminar em '*', faz prefix match; senao match exato."""
    if not actions or not action_type_pattern: return 0
    if action_type_pattern.endswith('.*'):
        prefix = action_type_pattern[:-1]  # remove o '*'
        return sum(int(a.get('value', 0)) for a in actions if a.get('action_type','').startswith(prefix))
    return somar_actions(actions, {action_type_pattern})

def resolver_resultado(optimization_goal, custom_event_type):
    """Dado o optimization_goal + custom_event_type retorna {action_type, label}."""
    if optimization_goal == 'OFFSITE_CONVERSIONS':
        return OFFSITE_EVENT_MAP.get(custom_event_type or 'OTHER', OFFSITE_EVENT_MAP['OTHER'])
    return OPTIMIZATION_TO_RESULT.get(optimization_goal, {'action_type': '', 'label': optimization_goal or '-'})

PRODUTOS = [
    ('TOEFL', ['TOEFL']),
]

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def api_get(endpoint, params):
    params['access_token'] = TOKEN
    for attempt in range(3):
        try:
            r = requests.get(f'{BASE}/{endpoint}', params=params, timeout=90)
            data = r.json()
            if 'error' in data:
                log(f'  API error (tent {attempt+1}): {data["error"]["message"]}')
                if attempt < 2: time.sleep(5); continue
            return data
        except Exception as e:
            log(f'  Request error (tent {attempt+1}): {e}')
            if attempt < 2: time.sleep(5)
    return {'error': {'message': 'Falhou'}}

def paginate(first_response):
    all_data = list(first_response.get('data', []))
    next_url = first_response.get('paging', {}).get('next')
    while next_url:
        try:
            r = requests.get(next_url, timeout=90)
            data = r.json()
            all_data.extend(data.get('data', []))
            next_url = data.get('paging', {}).get('next')
        except Exception as e:
            log(f'  paginate error: {e}'); break
    return all_data

def detectar_produto(nome):
    nome_up = (nome or '').upper()
    for produto, padroes in PRODUTOS:
        for p in padroes:
            if p in nome_up: return produto
    return 'Outros'

def detectar_bloco(objective, nome):
    nome_up = (nome or '').upper()
    if objective == 'OUTCOME_SALES': return 'Vendas'
    if objective == 'OUTCOME_ENGAGEMENT': return 'Engajamento'
    if objective == 'OUTCOME_AWARENESS': return 'Engajamento'
    if objective == 'OUTCOME_TRAFFIC':
        if 'PERFIL' in nome_up or 'INSTAGRAM' in nome_up or 'SEGUIDORES' in nome_up: return 'Engajamento'
        return 'Trafego'
    if objective == 'OUTCOME_LEADS': return 'Leads'
    if 'COMPRA' in nome_up or 'VENDAS' in nome_up or 'MATRIC' in nome_up: return 'Vendas'
    if 'ENGAJ' in nome_up or 'PERFIL' in nome_up or 'SEGUIDORES' in nome_up: return 'Engajamento'
    if 'CADASTRO' in nome_up or 'LEAD' in nome_up or 'FORMS' in nome_up: return 'Leads'
    return 'Outros'

def somar_actions(actions, types_set):
    if not actions: return 0
    total = 0
    for a in actions:
        if a.get('action_type') in types_set:
            try: total += int(a.get('value', 0))
            except: pass
    return total

def somar_purchases_unique(actions):
    """Pega 1 valor unico de purchase, evitando duplicacao entre tipos sobrepostos.
    No Meta: 'purchase', 'omni_purchase' e 'offsite_conversion.fb_pixel_purchase'
    geralmente reportam a MESMA compra. Soma-los inflaciona 2-3x."""
    if not actions: return 0
    counts = {}
    for a in actions:
        at = a.get('action_type')
        if at in PURCHASE_ACTION_TYPES:
            try: counts[at] = int(a.get('value', 0))
            except: pass
    # prioridade: omni > purchase > pixel (todos costumam ser iguais)
    for key in ('omni_purchase', 'purchase', 'offsite_conversion.fb_pixel_purchase'):
        if counts.get(key): return counts[key]
    return 0

def somar_unique_prioridade(actions, priority_keys):
    """Soma 1 valor unico, escolhendo o 1o action_type presente na ordem de prioridade.
    Evita inflar quando omni/generico/pixel reportam o MESMO evento (ATC, checkout)."""
    if not actions: return 0
    counts = {}
    for a in actions:
        at = a.get('action_type')
        if at in priority_keys:
            try: counts[at] = int(a.get('value', 0))
            except: pass
    for key in priority_keys:
        if counts.get(key): return counts[key]
    return 0

# ========== 1. DAILY DATA (conta) ==========
def fetch_daily_data():
    log('Buscando dados diarios da conta...')
    until = datetime.now().strftime('%Y-%m-%d')
    data = api_get(f'{ACCOUNT}/insights', {
        'fields': 'impressions,clicks,spend,reach,actions',
        'time_range': json.dumps({'since': PERIODO_INICIO, 'until': until}),
        'time_increment': 1, 'limit': 500
    })
    if 'error' in data: log(f'  ERRO: {data["error"]["message"]}'); return []
    all_data = paginate(data)
    rows = []
    for day in all_data:
        actions = day.get('actions', [])
        rows.append({
            'date': day['date_start'],
            'impressions': int(day.get('impressions', 0)),
            'clicks': int(day.get('clicks', 0)),
            'spend': round(float(day.get('spend', 0)), 2),
            'reach': int(day.get('reach', 0)),
            'link_clicks': somar_actions(actions, {'link_click'}),
            'lp_views': somar_actions(actions, {'landing_page_view'}),
            'leads': somar_actions(actions, LEAD_ACTION_TYPES),
            'add_to_cart': somar_unique_prioridade(actions, ADD_TO_CART_PRIORITY),
            'initiate_checkout': somar_unique_prioridade(actions, INITIATE_CHECKOUT_PRIORITY),
            'purchases': somar_purchases_unique(actions),
            'post_engagement': somar_actions(actions, POST_ENGAGEMENT_TYPES),
            'page_engagement': somar_actions(actions, PAGE_ENGAGEMENT_TYPES),
            'likes': somar_actions(actions, LIKE_TYPES),
            'comments': somar_actions(actions, COMMENT_TYPES),
            'shares': somar_actions(actions, SHARE_TYPES),
            'saves': somar_actions(actions, SAVE_TYPES),
            'follows': somar_actions(actions, FOLLOW_TYPES),
            'video_views': somar_actions(actions, {'video_view'}),
            'profile_visits': somar_actions(actions, PROFILE_VISIT_TYPES),
        })
    rows.sort(key=lambda x: x['date'])
    log(f'  {len(rows)} dias (ate {until})')
    return rows

# ========== 2. ADSETS META (para resolver "Resultado") ==========
def fetch_adsets_optimization():
    """Retorna dict {campaign_id: {action_type, label}} a partir do optimization_goal mais comum dos adsets."""
    log('Buscando otimizacao dos adsets...')
    data = api_get(f'{ACCOUNT}/adsets', {
        'fields': 'campaign_id,optimization_goal,promoted_object',
        'limit': 200
    })
    if 'error' in data: log(f'  ERRO: {data["error"]["message"]}'); return {}
    all_adsets = paginate(data)
    # Conta combinacoes (goal, custom_event_type) por campaign
    from collections import Counter
    by_camp = {}
    for a in all_adsets:
        cid = a.get('campaign_id')
        goal = a.get('optimization_goal', '')
        po = a.get('promoted_object') or {}
        cet = po.get('custom_event_type')
        if cid not in by_camp: by_camp[cid] = Counter()
        by_camp[cid][(goal, cet)] += 1
    result = {}
    for cid, counter in by_camp.items():
        most_common = counter.most_common(1)[0][0]
        goal, cet = most_common
        result[cid] = resolver_resultado(goal, cet)
        result[cid]['optimization_goal'] = goal
        result[cid]['custom_event_type'] = cet
    log(f'  {len(result)} campanhas mapeadas')
    return result

# ========== 3. CAMPAIGNS META ==========
def fetch_campaigns_meta(adset_opt_map=None):
    log('Buscando metadata de campanhas...')
    data = api_get(f'{ACCOUNT}/campaigns', {
        'fields': 'name,status,effective_status,objective,daily_budget,lifetime_budget',
        'limit': 200
    })
    if 'error' in data: log(f'  ERRO: {data["error"]["message"]}'); return []
    all_camps = paginate(data)
    metas = []
    for c in all_camps:
        nome = c.get('name', '')
        objective = c.get('objective', '')
        opt = (adset_opt_map or {}).get(c['id'], {}) or {}
        metas.append({
            'id': c['id'],
            'nome': nome,
            'status': c.get('effective_status', c.get('status', '')),
            'objective': objective,
            'bloco': detectar_bloco(objective, nome),
            'produto': detectar_produto(nome),
            'daily_budget': int(c.get('daily_budget', 0)) / 100 if c.get('daily_budget') else 0,
            'result_action_type': opt.get('action_type', ''),
            'result_label': opt.get('label', '-'),
            'optimization_goal': opt.get('optimization_goal', ''),
        })
    log(f'  {len(metas)} campanhas')
    return metas

# ========== 3. CAMPAIGNS DAILY (level=campaign, time_increment=1) ==========
def fetch_campaigns_daily(camp_meta_by_id=None):
    log('Buscando insights diarios por campanha...')
    until = datetime.now().strftime('%Y-%m-%d')
    # Inclui campo "results" que retorna EXATAMENTE o que o Meta UI mostra na coluna Resultados
    data = api_get(f'{ACCOUNT}/insights', {
        'level': 'campaign',
        'fields': 'campaign_id,impressions,clicks,spend,reach,actions,results',
        'time_range': json.dumps({'since': PERIODO_INICIO, 'until': until}),
        'time_increment': 1, 'limit': 500
    })
    if 'error' in data: log(f'  ERRO: {data["error"]["message"]}'); return []
    all_data = paginate(data)
    rows = []
    camp_meta_by_id = camp_meta_by_id or {}
    for r in all_data:
        actions = r.get('actions', [])
        cid = r.get('campaign_id')
        # 1) Tenta usar o campo "results" da API (mesmo numero que o Meta UI mostra)
        result_count = 0
        results_arr = r.get('results', [])
        if results_arr:
            try:
                vals = results_arr[0].get('values', [])
                if vals:
                    result_count = int(vals[0].get('value', 0))
            except (ValueError, KeyError, TypeError):
                pass
        # 2) Fallback: usa o action_type resolvido pelo optimization_goal
        if not result_count:
            meta = camp_meta_by_id.get(cid, {})
            result_at = meta.get('result_action_type', '')
            result_count = somar_action_match(actions, result_at) if result_at else 0
        rows.append([
            cid,
            r.get('date_start'),
            round(float(r.get('spend', 0)), 2),
            int(r.get('impressions', 0)),
            int(r.get('clicks', 0)),
            somar_actions(actions, {'landing_page_view'}),
            somar_actions(actions, LEAD_ACTION_TYPES),
            somar_purchases_unique(actions),
            result_count,  # <-- bate com a coluna "Resultados" do Meta Ads Manager
            somar_actions(actions, POST_ENGAGEMENT_TYPES),  # 9 - engajamento
            somar_actions(actions, FOLLOW_TYPES),           # 10 - novos seguidores
            somar_actions(actions, {'video_view'}),         # 11 - video views
            somar_actions(actions, PROFILE_VISIT_TYPES),    # 12 - visitas perfil (proxy)
            somar_actions(actions, LIKE_TYPES),             # 13 - curtidas
            somar_actions(actions, COMMENT_TYPES),          # 14 - comentarios
            somar_actions(actions, SHARE_TYPES),            # 15 - shares
            somar_actions(actions, SAVE_TYPES),             # 16 - saves
            int(r.get('reach', 0)) if r.get('reach') else 0,# 17 - reach
        ])
    log(f'  {len(rows)} linhas (camp x dia)')
    return rows

# ========== 4. ADS META ==========
def fetch_ads_meta():
    log('Buscando metadata de anuncios...')
    data = api_get(f'{ACCOUNT}/ads', {
        'fields': 'name,status,effective_status,campaign{id,name},adset{id,name,status},'
                  'creative{thumbnail_url,image_url,instagram_permalink_url}',
        'limit': 300
    })
    if 'error' in data: log(f'  ERRO: {data["error"]["message"]}'); return []
    all_ads = paginate(data)
    metas = []
    for ad in all_ads:
        camp = ad.get('campaign', {}) or {}
        adset = ad.get('adset', {}) or {}
        creative = ad.get('creative', {}) or {}
        # effective_status considera heranca campanha+adset+ad
        # ACTIVE = todos os 3 niveis ativos
        eff = ad.get('effective_status', ad.get('status', ''))
        metas.append({
            'id': ad['id'],
            'nome': ad.get('name', ''),
            'status': eff,
            'campaign_id': camp.get('id', ''),
            'adset_id': adset.get('id', ''),
            'campanha': camp.get('name', ''),
            'thumb': creative.get('thumbnail_url', '') or creative.get('image_url', ''),
            'ig_url': creative.get('instagram_permalink_url', ''),
        })
    log(f'  {len(metas)} anuncios')
    return metas

# ========== 5. ADS DAILY ==========
def chunk_ranges(since, until, days_per_chunk=15):
    """Quebra um intervalo em chunks de N dias para evitar truncamento da Meta API."""
    s = datetime.strptime(since, '%Y-%m-%d')
    u = datetime.strptime(until, '%Y-%m-%d')
    chunks = []
    cur = s
    while cur <= u:
        chunk_end = min(cur + timedelta(days=days_per_chunk-1), u)
        chunks.append((cur.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cur = chunk_end + timedelta(days=1)
    return chunks

def fetch_ads_daily():
    log('Buscando insights diarios por anuncio (em chunks)...')
    until = datetime.now().strftime('%Y-%m-%d')
    chunks = chunk_ranges(PERIODO_INICIO, until, 15)
    all_data = []
    for i, (since, end) in enumerate(chunks):
        log(f'  chunk {i+1}/{len(chunks)}: {since} a {end}')
        data = api_get(f'{ACCOUNT}/insights', {
            'level': 'ad',
            'fields': 'ad_id,impressions,clicks,spend,actions,'
                      'video_p25_watched_actions,video_p50_watched_actions,'
                      'video_p75_watched_actions,video_p100_watched_actions',
            'time_range': json.dumps({'since': since, 'until': end}),
            'time_increment': 1, 'limit': 500
        })
        if 'error' in data:
            log(f'    ERRO: {data["error"]["message"]}')
            continue
        chunk_data = paginate(data)
        all_data.extend(chunk_data)
        log(f'    {len(chunk_data)} linhas')

    # daily simples (sem video pra economizar)
    rows = []
    # totais de video (acumulado)
    vid_totals = {}

    def get_vid(d, key):
        arr = d.get(key, [])
        if isinstance(arr, list) and arr:
            try: return int(arr[0].get('value', 0))
            except: return 0
        return 0

    for r in all_data:
        actions = r.get('actions', [])
        aid = r.get('ad_id')
        rows.append([
            aid,
            r.get('date_start'),
            round(float(r.get('spend', 0)), 2),
            int(r.get('impressions', 0)),
            int(r.get('clicks', 0)),
            somar_actions(actions, {'landing_page_view'}),
            somar_actions(actions, LEAD_ACTION_TYPES),
            somar_purchases_unique(actions),
            somar_actions(actions, {'video_view'}),
        ])
        # video totals
        if aid not in vid_totals:
            vid_totals[aid] = {'p25':0, 'p50':0, 'p75':0, 'p100':0, 'vv':0, 'imp':0}
        vid_totals[aid]['p25'] += get_vid(r, 'video_p25_watched_actions')
        vid_totals[aid]['p50'] += get_vid(r, 'video_p50_watched_actions')
        vid_totals[aid]['p75'] += get_vid(r, 'video_p75_watched_actions')
        vid_totals[aid]['p100'] += get_vid(r, 'video_p100_watched_actions')
        vid_totals[aid]['vv'] += somar_actions(actions, {'video_view'})
        vid_totals[aid]['imp'] += int(r.get('impressions', 0))

    log(f'  {len(rows)} linhas (ad x dia), {len(vid_totals)} ads com totais de video')
    return rows, vid_totals

# ========== INJECAO ==========
def replace_var(html, varname, value):
    js = json.dumps(value, ensure_ascii=False, separators=(',',':'))
    pattern = rf'var {varname} = .*?; /\*END_{varname}\*/'
    new_content = f'var {varname} = {js}; /*END_{varname}*/'
    if re.search(pattern, html, flags=re.DOTALL):
        # IMPORTANTE: usa funcao no replacement para NAO interpretar \r \n etc
        return re.sub(pattern, lambda m: new_content, html, flags=re.DOTALL)
    log(f'  AVISO: marker END_{varname} nao encontrado, var nao substituida')
    return html

def update_timestamp(html):
    now = datetime.now().strftime('%d/%m %H:%M')
    return re.sub(r'Atualizado [^<]*', f'Atualizado {now}', html)

# ========== MAIN ==========
def main():
    log('=' * 60)
    log('INICIO ATUALIZACAO BI TOEFL')
    log('=' * 60)

    if not os.path.exists(BI_PATH):
        log(f'ERRO: nao encontrado: {BI_PATH}'); sys.exit(1)
    with open(BI_PATH, 'r', encoding='utf-8') as f:
        html = f.read()
    log(f'HTML lido: {len(html)} chars')

    daily = fetch_daily_data()
    adset_opt = fetch_adsets_optimization()
    camp_meta = fetch_campaigns_meta(adset_opt_map=adset_opt)
    camp_meta_by_id = {c['id']: c for c in camp_meta}
    camp_daily = fetch_campaigns_daily(camp_meta_by_id=camp_meta_by_id)
    ad_meta = fetch_ads_meta()
    ad_daily, ad_vid = fetch_ads_daily()

    if daily: html = replace_var(html, 'DAILY_DATA', daily)
    if camp_meta: html = replace_var(html, 'CAMPAIGNS_META', camp_meta)
    if camp_daily: html = replace_var(html, 'CAMPAIGNS_DAILY', camp_daily)
    if ad_meta: html = replace_var(html, 'ADS_META', ad_meta)
    if ad_daily: html = replace_var(html, 'ADS_DAILY', ad_daily)
    if ad_vid: html = replace_var(html, 'ADS_VIDEO_TOTALS', ad_vid)

    html = update_timestamp(html)

    with open(BI_PATH + '.bak', 'w', encoding='utf-8') as f:
        f.write(html)
    with open(BI_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    log(f'HTML salvo: {len(html)} chars')
    log('CONCLUIDO')
    log('')

if __name__ == '__main__':
    main()
