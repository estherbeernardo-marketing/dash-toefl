#!/usr/bin/env python3
"""
Atualiza dados de Instagram Organico no index.html do BI TOEFL.
Injeta:
- IG_ACCOUNT: metadata da conta (username, followers atuais, etc)
- IG_DAILY: serie diaria de follower_count, reach, profile_views, accounts_engaged
- IG_POSTS: lista de posts com insights (reach, likes, comments, saved, shares, plays)

Periodo: desde 2026-01-01 ate hoje.
"""
import os, sys, json, re, time, requests
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv('META_ACCESS_TOKEN', '')
ACCOUNT = os.getenv('META_AD_ACCOUNT_ID', 'act_590951675637306')
PERIODO_INICIO = os.getenv('PERIODO_INICIO', '2026-01-01')
BASE = 'https://graph.facebook.com/v21.0'
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'atualizar_bi.log')

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [IG] {msg}'
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def api_get(endpoint, params=None):
    p = {'access_token': TOKEN}
    if params: p.update(params)
    for attempt in range(3):
        try:
            r = requests.get(f'{BASE}/{endpoint}', params=p, timeout=60)
            data = r.json()
            if 'error' in data:
                msg = data['error'].get('message', '')
                if attempt < 2:
                    log(f'  retry ({attempt+1}): {msg[:100]}')
                    time.sleep(3); continue
            return data
        except Exception as e:
            if attempt < 2:
                log(f'  err ({attempt+1}): {e}')
                time.sleep(3)
    return {'error': {'message': 'falhou apos retries'}}

def descobrir_ig_account():
    """Retorna {'id': ig_id, 'username': '@xxx', 'name': '...', 'followers': N}"""
    accs = api_get('me/accounts', {'fields': 'id,name,instagram_business_account{id,name,username,followers_count}', 'limit': 50})
    for p in accs.get('data', []):
        ig = p.get('instagram_business_account')
        if ig:
            return {
                'id': ig['id'],
                'username': ig.get('username', ''),
                'name': ig.get('name', ''),
                'followers': ig.get('followers_count', 0),
                'page_name': p.get('name', ''),
                'page_id': p.get('id', '')
            }
    return None

def chunk_30d(since_str, until_str):
    """IG limita follower_count a janelas de 30 dias."""
    s = datetime.strptime(since_str, '%Y-%m-%d')
    u = datetime.strptime(until_str, '%Y-%m-%d')
    chunks = []
    cur = s
    while cur <= u:
        end = min(cur + timedelta(days=29), u)
        chunks.append((cur.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')))
        cur = end + timedelta(days=1)
    return chunks

def to_unix(d_str):
    return int(datetime.strptime(d_str, '%Y-%m-%d').timestamp())

def fetch_daily_metrics(ig_id, since, until):
    """Puxa follower_count, reach, profile_views, accounts_engaged, website_clicks por dia."""
    log(f'Buscando metricas diarias da conta IG ({since} -> {until})...')
    chunks = chunk_30d(since, until)

    # follower_count: timeseries (precisa chunks de 30d)
    daily = {}  # {date: {follower_count, reach, profile_views, accounts_engaged}}

    for c_since, c_until in chunks:
        log(f'  chunk {c_since} -> {c_until}')
        # follower_count
        r = api_get(f'{ig_id}/insights', {
            'metric': 'follower_count',
            'period': 'day',
            'since': to_unix(c_since), 'until': to_unix(c_until) + 86400
        })
        if 'error' not in r:
            for m in r.get('data', []):
                for v in m.get('values', []):
                    d = v.get('end_time', '')[:10]
                    if d:
                        daily.setdefault(d, {})['follower_count'] = v.get('value', 0)

        # reach
        r = api_get(f'{ig_id}/insights', {
            'metric': 'reach',
            'period': 'day',
            'since': to_unix(c_since), 'until': to_unix(c_until) + 86400
        })
        if 'error' not in r:
            for m in r.get('data', []):
                for v in m.get('values', []):
                    d = v.get('end_time', '')[:10]
                    if d:
                        daily.setdefault(d, {})['reach'] = v.get('value', 0)

        # profile_views (precisa metric_type=total_value para v22+)
        r = api_get(f'{ig_id}/insights', {
            'metric': 'profile_views',
            'metric_type': 'total_value',
            'period': 'day',
            'since': to_unix(c_since), 'until': to_unix(c_until) + 86400
        })
        if 'error' not in r:
            for m in r.get('data', []):
                # total_value retorna valor unico do periodo, nao timeseries
                tv = m.get('total_value', {}).get('value', 0)
                # distribui no chunk como acumulado
                # IMPORTANTE: nao da granularidade por dia aqui no v21+
                # Vamos guardar o total do chunk
                if 'profile_views_chunks' not in daily:
                    daily['_profile_views_chunks'] = []
                daily['_profile_views_chunks'].append({'since': c_since, 'until': c_until, 'value': tv})

        # accounts_engaged
        r = api_get(f'{ig_id}/insights', {
            'metric': 'accounts_engaged',
            'metric_type': 'total_value',
            'period': 'day',
            'since': to_unix(c_since), 'until': to_unix(c_until) + 86400
        })
        if 'error' not in r:
            for m in r.get('data', []):
                tv = m.get('total_value', {}).get('value', 0)
                if '_engaged_chunks' not in daily:
                    daily['_engaged_chunks'] = []
                daily['_engaged_chunks'].append({'since': c_since, 'until': c_until, 'value': tv})

    # formata serie diaria
    rows = []
    chunks_pv = daily.pop('_profile_views_chunks', [])
    chunks_eng = daily.pop('_engaged_chunks', [])
    for d in sorted(daily.keys()):
        rec = daily[d]
        rows.append({
            'date': d,
            'followers': rec.get('follower_count', 0),
            'reach': rec.get('reach', 0)
        })
    log(f'  serie diaria: {len(rows)} dias com follower_count/reach')
    log(f'  profile_views: {len(chunks_pv)} chunks (granularidade mensal)')
    log(f'  accounts_engaged: {len(chunks_eng)} chunks')

    return {
        'daily': rows,
        'profile_views_chunks': chunks_pv,
        'engaged_chunks': chunks_eng
    }

def fetch_posts(ig_id, since, until):
    """Puxa todos os posts no periodo + insights de cada um."""
    log(f'Buscando posts da conta IG ({since} -> {until})...')
    since_unix = to_unix(since)
    until_unix = to_unix(until) + 86400

    # paginar media
    all_media = []
    next_url = None
    fields = 'id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count'
    params = {'fields': fields, 'since': since_unix, 'until': until_unix, 'limit': 100}
    r = api_get(f'{ig_id}/media', params)
    if 'error' in r:
        log(f'  erro ao listar media: {r["error"]["message"]}')
        return []
    all_media.extend(r.get('data', []))
    next_url = r.get('paging', {}).get('next')
    while next_url:
        try:
            r2 = requests.get(next_url, timeout=60).json()
            all_media.extend(r2.get('data', []))
            next_url = r2.get('paging', {}).get('next')
        except Exception as e:
            log(f'  paginate erro: {e}'); break

    log(f'  {len(all_media)} posts encontrados')

    # insights de cada post
    posts = []
    for i, m in enumerate(all_media):
        mid = m['id']
        mt = m.get('media_type', '')
        # metricas variam por tipo
        if mt == 'VIDEO' or mt == 'REEL':
            metric = 'reach,likes,comments,saved,shares,plays,total_interactions,views'
        elif mt == 'CAROUSEL_ALBUM':
            metric = 'reach,likes,comments,saved,shares,total_interactions,views'
        else:  # IMAGE
            metric = 'reach,likes,comments,saved,shares,total_interactions,views'

        ins = api_get(f'{mid}/insights', {'metric': metric})
        m_metrics = {}
        if 'error' not in ins:
            for d in ins.get('data', []):
                name = d.get('name')
                # values normal
                vals = d.get('values', [])
                if vals:
                    m_metrics[name] = vals[0].get('value', 0)
                # ou total_value
                elif d.get('total_value'):
                    m_metrics[name] = d['total_value'].get('value', 0)
        else:
            # tenta com lista menor (alguns metrics nao estao disponiveis pra todos os tipos)
            ins2 = api_get(f'{mid}/insights', {'metric': 'reach,likes,comments,saved,shares'})
            if 'error' not in ins2:
                for d in ins2.get('data', []):
                    vals = d.get('values', [])
                    if vals:
                        m_metrics[d.get('name')] = vals[0].get('value', 0)

        ts = m.get('timestamp', '')[:10]
        cap = (m.get('caption') or '').replace('\n', ' ')[:200]
        likes = m_metrics.get('likes', m.get('like_count', 0))
        comments = m_metrics.get('comments', m.get('comments_count', 0))
        saves = m_metrics.get('saved', 0)
        shares = m_metrics.get('shares', 0)
        engagement = likes + comments + saves + shares

        posts.append({
            'id': mid,
            'date': ts,
            'caption': cap,
            'media_type': mt,
            'thumb': m.get('thumbnail_url') or m.get('media_url') or '',
            'permalink': m.get('permalink', ''),
            'reach': m_metrics.get('reach', 0),
            'likes': likes,
            'comments': comments,
            'saved': saves,
            'shares': shares,
            'plays': m_metrics.get('plays', 0),
            'views': m_metrics.get('views', 0),
            'total_interactions': m_metrics.get('total_interactions', engagement),
            'engagement': engagement,
        })
        if (i+1) % 10 == 0:
            log(f'  insights: {i+1}/{len(all_media)}')

    # ordena por data desc
    posts.sort(key=lambda x: x['date'], reverse=True)
    log(f'  {len(posts)} posts com insights')
    return posts

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
    log('INICIO ATUALIZACAO BI INSTAGRAM ORGANICO')
    log('=' * 60)

    if not TOKEN:
        log('ERRO: META_ACCESS_TOKEN nao configurado'); sys.exit(1)

    if not os.path.exists(HTML_PATH):
        log(f'ERRO: nao encontrado: {HTML_PATH}'); sys.exit(1)

    ig = descobrir_ig_account()
    if not ig:
        log('ERRO: nenhuma conta IG Business encontrada'); sys.exit(1)
    log(f'Conta IG: @{ig["username"]} (id={ig["id"]}) - {ig.get("followers",0)} seguidores atuais')

    until = datetime.now().strftime('%Y-%m-%d')
    daily_data = fetch_daily_metrics(ig['id'], PERIODO_INICIO, until)
    posts = fetch_posts(ig['id'], PERIODO_INICIO, until)

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    html = replace_var(html, 'IG_ACCOUNT', ig)
    html = replace_var(html, 'IG_DAILY', daily_data['daily'])
    html = replace_var(html, 'IG_PROFILE_VIEWS_CHUNKS', daily_data['profile_views_chunks'])
    html = replace_var(html, 'IG_ENGAGED_CHUNKS', daily_data['engaged_chunks'])
    html = replace_var(html, 'IG_POSTS', posts)

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    log(f'HTML salvo: {len(html)} chars')
    log(f'Conta: @{ig["username"]} | Posts: {len(posts)} | Dias com follower_count: {len(daily_data["daily"])}')
    log('CONCLUIDO')

if __name__ == '__main__':
    main()
