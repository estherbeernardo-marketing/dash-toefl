#!/usr/bin/env python3
"""Diagnostico: ve se o token tem acesso ao Instagram conectado a conta de ads."""
import os, requests, json
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv('META_ACCESS_TOKEN', '')
ACCOUNT = os.getenv('META_AD_ACCOUNT_ID', 'act_590951675637306')
BASE = 'https://graph.facebook.com/v21.0'

def get(endpoint, params=None):
    p = {'access_token': TOKEN}
    if params: p.update(params)
    r = requests.get(f'{BASE}/{endpoint}', params=p, timeout=30)
    return r.json()

def status(title, ok, detail=''):
    icon = 'OK' if ok else 'X '
    print(f'  [{icon}] {title}' + (f' -> {detail}' if detail else ''))

print('=' * 70)
print('DIAGNOSTICO INSTAGRAM API - TOEFL / MASTERTEST')
print('=' * 70)

print('\n[1] Verificando token...')
me = get('me', {'fields': 'id,name'})
if 'error' in me:
    status('Token valido', False, me['error']['message']); exit(1)
status('Token valido', True, f"id={me.get('id')} name={me.get('name','-')}")

print('\n[2] Verificando scopes do token...')
debug = get('debug_token', {'input_token': TOKEN})
if 'data' in debug:
    scopes = debug['data'].get('scopes', [])
    print(f'    Scopes: {", ".join(scopes) if scopes else "(nenhum retornado)"}')
    needed = ['instagram_basic', 'instagram_manage_insights', 'pages_read_engagement', 'pages_show_list']
    for s in needed:
        status(f'scope {s}', s in scopes)
else:
    print('    (debug_token nao retornou data)')

print(f'\n[3] Buscando paginas do Facebook conectadas a conta {ACCOUNT}...')
paginas = get(f'{ACCOUNT}/promote_pages', {'fields': 'id,name,instagram_business_account{id,name,username}'})
if 'error' in paginas:
    # tenta endpoint alternativo
    paginas = get(f'{ACCOUNT}', {'fields': 'business,name'})
    print(f'    promote_pages falhou, conta info: {json.dumps(paginas, ensure_ascii=False)[:200]}')

if 'data' in paginas:
    pgs = paginas.get('data', [])
    status(f'Paginas encontradas: {len(pgs)}', len(pgs) > 0)
    for p in pgs[:5]:
        ig = p.get('instagram_business_account')
        ig_str = f"IG: @{ig.get('username','?')} (id={ig.get('id')})" if ig else 'sem IG conectado'
        print(f'    - {p.get("name")} (page_id={p.get("id")}) | {ig_str}')

print('\n[4] Listando contas IG diretamente (me/accounts)...')
accs = get('me/accounts', {'fields': 'id,name,instagram_business_account{id,name,username}', 'limit': 50})
if 'data' in accs:
    igs = []
    for p in accs.get('data', []):
        ig = p.get('instagram_business_account')
        if ig:
            igs.append({'page': p.get('name'), 'page_id': p.get('id'), **ig})
    if igs:
        status(f'IG Business Accounts: {len(igs)}', True)
        for i in igs:
            print(f'    - @{i.get("username")} (ig_id={i.get("id")}) <- pagina {i.get("page")}')
    else:
        status('IG Business Accounts encontrados', False, 'nenhuma pagina tem IG vinculado')
else:
    status('me/accounts', False, accs.get('error',{}).get('message',''))

# Tenta puxar insights de uma IG account (se achou alguma)
ig_id_test = None
if 'data' in accs:
    for p in accs.get('data', []):
        ig = p.get('instagram_business_account')
        if ig:
            ig_id_test = ig.get('id'); ig_username = ig.get('username'); break

if ig_id_test:
    print(f'\n[5] Testando insights da conta IG @{ig_username} (id={ig_id_test})...')
    # follower_count diario
    fc = get(f'{ig_id_test}/insights', {'metric': 'follower_count', 'period': 'day', 'since': '2026-04-01', 'until': '2026-05-09'})
    if 'error' in fc:
        status('follower_count', False, fc['error']['message'])
    else:
        n = len(fc.get('data', [{}])[0].get('values', [])) if fc.get('data') else 0
        status('follower_count diario', n > 0, f'{n} pontos no periodo')

    # profile_views (precisa metric_type=total_value pra v22+)
    pv = get(f'{ig_id_test}/insights', {'metric': 'profile_views', 'period': 'day', 'since': '2026-04-01', 'until': '2026-05-09'})
    if 'error' in pv:
        # tenta total_value
        pv2 = get(f'{ig_id_test}/insights', {'metric': 'profile_views', 'metric_type': 'total_value', 'period': 'day'})
        if 'error' in pv2:
            status('profile_views', False, pv['error']['message'][:120])
        else:
            status('profile_views (total_value)', True)
    else:
        status('profile_views', True)

    print(f'\n[6] Testando lista de posts da conta IG...')
    media = get(f'{ig_id_test}/media', {'fields': 'id,caption,media_type,timestamp,permalink', 'limit': 5})
    if 'error' in media:
        status('listar media', False, media['error']['message'][:120])
    else:
        n = len(media.get('data', []))
        status('listar media', n > 0, f'{n} posts retornados')
        if n > 0:
            first = media['data'][0]
            mid = first['id']
            print(f'    Testando insights do post {mid} ({first.get("media_type")})...')
            ins = get(f'{mid}/insights', {'metric': 'reach,likes,comments,saved,shares'})
            if 'error' in ins:
                status('insights por post', False, ins['error']['message'][:120])
            else:
                status('insights por post', True, f'{len(ins.get("data",[]))} metricas')
                for m in ins.get('data', []):
                    val = m.get('values', [{}])[0].get('value', 0) if m.get('values') else m.get('total_value',{}).get('value',0)
                    print(f'      - {m.get("name")}: {val}')

print('\n' + '=' * 70)
print('DIAGNOSTICO CONCLUIDO')
print('=' * 70)
