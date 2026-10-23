import os
import re
import requests
import urllib3
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# CONFIGURAÇÃO
# ============================================================

HOST = os.getenv("AVAYA_HOST", "172.16.36.50")
PORT = int(os.getenv("AVAYA_PORT", "7070"))
USERNAME = os.getenv("AVAYA_USERNAME", "apiuser")
PASSWORD = os.getenv("AVAYA_PASSWORD")

VERIFY_SSL = False

BASE_URL = f"https://{HOST}:{PORT}/WebManagement/ws/sdk"

AUTH_URL = f"{BASE_URL}/security/authenticate"
USERS_URL = f"{BASE_URL}/admin/v1/users"

HEADERS = {
    "X-User-Client": "Avaya-WebAdmin",
    "X-User-Agent": "Avaya-SDKUser",
    "Content-Type": "application/xml",
    "Accept": "application/xml",
}


# ============================================================
# FUNÇÕES
# ============================================================

def xml_status(xml_text):
    """Extrai response/@status sem exibir dados sensíveis."""
    try:
        root = ET.fromstring(xml_text)
        return root.attrib.get("status")
    except Exception:
        return None


def extract_error_code(xml_text):
    """Extrai error_code do XML, se existir."""
    match = re.search(
        r"<error_code>\s*(.*?)\s*</error_code>",
        xml_text,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        return match.group(1).strip()

    return None


def extract_error_desc(xml_text):
    """Extrai error_desc do XML, se existir."""
    match = re.search(
        r"<error_desc>\s*(.*?)\s*</error_desc>",
        xml_text,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        return match.group(1).strip()

    return None


# ============================================================
# TESTE
# ============================================================

if not PASSWORD:
    print("ERRO: variável AVAYA_PASSWORD não configurada.")
    print()
    print("No PowerShell:")
    print('$env:AVAYA_PASSWORD="SUA_SENHA"')
    raise SystemExit(1)


session = requests.Session()

print("=" * 65)
print("TESTE ISOLADO - AVAYA IP OFFICE MANAGEMENT API")
print("=" * 65)

print(f"HOST: {HOST}")
print(f"PORT: {PORT}")
print(f"USER: {USERNAME}")
print(f"SSL VERIFY: {VERIFY_SSL}")
print()

# ------------------------------------------------------------
# 1. AUTHENTICATION
# ------------------------------------------------------------

print("[1] AUTHENTICATION")
print("-" * 65)

try:
    response = session.get(
        AUTH_URL,
        headers=HEADERS,
        auth=(USERNAME, PASSWORD),
        verify=VERIFY_SSL,
        timeout=15,
    )

    print(f"AUTH HTTP: {response.status_code}")
    print(f"AUTH STATUS: {xml_status(response.text)}")

    print(
        "AUTH SET-COOKIE:",
        "SIM" if response.headers.get("Set-Cookie") else "NAO"
    )

    print(
        "SESSION COOKIES:",
        list(session.cookies.keys())
    )

    if "JSESSIONID" in session.cookies:
        print("JSESSIONID: RECEBIDO")
    else:
        print("JSESSIONID: NAO RECEBIDO")

    if "X-SDK-Token" in session.cookies:
        print("X-SDK-Token COOKIE: RECEBIDO")
    else:
        print("X-SDK-Token COOKIE: NAO RECEBIDO")

    print(
        "X-SDK-Token HEADER:",
        "RECEBIDO" if response.headers.get("X-SDK-Token") else "NAO RECEBIDO"
    )

    print(f"AUTH BYTES: {len(response.content)}")

except Exception as e:
    print(f"ERRO AUTH: {type(e).__name__}: {e}")
    raise SystemExit(1)


# ------------------------------------------------------------
# 2. COOKIE QUE SERÁ ENVIADO
# ------------------------------------------------------------

print()
print("[2] SESSION")
print("-" * 65)

prepared = requests.Request(
    "GET",
    USERS_URL,
    headers=HEADERS,
).prepare()

# Monta uma requisição preparada usando a sessão
prepared = session.prepare_request(
    requests.Request(
        "GET",
        USERS_URL,
        headers=HEADERS,
    )
)

cookie_header = prepared.headers.get("Cookie")

if cookie_header:
    # NÃO imprime o valor do cookie.
    print("COOKIE HEADER: PRESENTE")

    if "JSESSIONID=" in cookie_header:
        print("JSESSIONID NO HEADER: SIM")
    else:
        print("JSESSIONID NO HEADER: NAO")
else:
    print("COOKIE HEADER: AUSENTE")


# ------------------------------------------------------------
# 3. GET USERS
# ------------------------------------------------------------

print()
print("[3] GET USERS")
print("-" * 65)

try:
    response_users = session.get(
        USERS_URL,
        headers=HEADERS,
        verify=VERIFY_SSL,
        timeout=15,
    )

    print(f"USERS HTTP: {response_users.status_code}")
    print(f"USERS STATUS: {xml_status(response_users.text)}")
    print(f"USERS BYTES: {len(response_users.content)}")

    error_code = extract_error_code(response_users.text)
    error_desc = extract_error_desc(response_users.text)

    print(
        "ERROR CODE:",
        error_code if error_code else "NENHUM"
    )

    print(
        "ERROR DESC:",
        error_desc if error_desc else "NENHUM"
    )

    if response_users.status_code == 200:
        print()
        print("RESULTADO: HTTP 200")

        status = xml_status(response_users.text)

        if status == "1":
            print("RESULTADO XML: SUCESSO")
        elif status == "0":
            print("RESULTADO XML: FALHA")
        else:
            print("RESULTADO XML: STATUS NAO IDENTIFICADO")

    elif response_users.status_code == 461:
        print()
        print("RESULTADO: 461 - SESSION TIMEOUT")

    else:
        print()
        print(f"RESULTADO: HTTP {response_users.status_code}")

except Exception as e:
    print(
        f"ERRO GET USERS: {type(e).__name__}: {e}"
    )


print()
print("=" * 65)
print("FIM DO TESTE")
print("=" * 65)