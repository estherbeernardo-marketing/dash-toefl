#!/usr/bin/env python3
"""
Gera o refresh_token do Google Ads via OAuth2.
Roda uma unica vez. Abre o navegador, voce autoriza, ele salva o token no .env.
"""
import os, sys, re, json
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print('ERRO: instale as dependencias primeiro:')
    print('  pip install google-auth-oauthlib')
    sys.exit(1)

SCOPES = ['https://www.googleapis.com/auth/adwords']
ROOT = Path(__file__).parent
JSON_PATH = ROOT / 'oauth_client.json'
ENV_PATH = ROOT / '.env'

def main():
    if not JSON_PATH.exists():
        print(f'ERRO: oauth_client.json nao encontrado em {JSON_PATH}'); sys.exit(1)

    print('=' * 60)
    print('GERAR REFRESH TOKEN - GOOGLE ADS')
    print('=' * 60)
    print()
    print('1) Vai abrir uma pagina do navegador')
    print('2) Faca login com a conta Google que administra a MCC X Station')
    print('3) Aceite as permissoes (Google Ads access)')
    print('4) Vai voltar pra ca automaticamente')
    print()
    print('Abrindo navegador em 2 segundos...')

    flow = InstalledAppFlow.from_client_secrets_file(str(JSON_PATH), scopes=SCOPES)
    # Usa um servidor local efemero (porta livre)
    creds = flow.run_local_server(
        port=0,
        prompt='consent',
        access_type='offline',
        authorization_prompt_message='Abrindo navegador para autorizar...',
        success_message='Autorizado! Pode fechar essa aba.'
    )

    refresh_token = creds.refresh_token
    if not refresh_token:
        print('ERRO: nao recebeu refresh_token. Tente novamente com prompt=consent.')
        sys.exit(1)

    print()
    print(f'OK! Refresh token gerado: {refresh_token[:30]}...')

    # Atualiza .env
    if ENV_PATH.exists():
        content = ENV_PATH.read_text(encoding='utf-8')
        if 'GOOGLE_REFRESH_TOKEN=' in content:
            content = re.sub(r'GOOGLE_REFRESH_TOKEN=.*', f'GOOGLE_REFRESH_TOKEN={refresh_token}', content)
        else:
            content += f'\nGOOGLE_REFRESH_TOKEN={refresh_token}\n'
        ENV_PATH.write_text(content, encoding='utf-8')
        print(f'.env atualizado em {ENV_PATH}')
    else:
        print('AVISO: .env nao existe, criando...')
        ENV_PATH.write_text(f'GOOGLE_REFRESH_TOKEN={refresh_token}\n', encoding='utf-8')

    print()
    print('PRONTO. Agora pode rodar atualizar_bi_google.py')

if __name__ == '__main__':
    main()
