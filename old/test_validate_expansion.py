import os
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("AVAYA_HOST")
PORT = os.getenv("AVAYA_PORT", "7070")
USERNAME = os.getenv("AVAYA_USERNAME")
PASSWORD = os.getenv("AVAYA_PASSWORD")

TARGET_IP = "172.16.70.10"
EXPECTED_NAME = "TesteExp01"
EXPECTED_EXTENSION = "1337"
EXPECTED_GUID = "49ce1b8093b011ea96d300e0070e89e0"

BASE_URL = f"https://{HOST}:{PORT}/WebManagement/ws/sdk"

HEADERS = {
    "X-User-Client": "Avaya-WebAdmin",
    "X-User-Agent": "Avaya-SDKUser",
    "Content-Type": "application/xml",
    "Accept": "application/xml",
}

VERIFY_SSL = os.getenv("AVAYA_VERIFY_SSL", "false").lower() == "true"


def authenticate(session):
    url = f"{BASE_URL}/security/authenticate"

    response = session.get(
        url,
        headers=HEADERS,
        auth=(USERNAME, PASSWORD),
        verify=VERIFY_SSL,
        timeout=30,
    )

    print(f"[AUTH] HTTP {response.status_code}")

    if response.status_code != 200:
        print(response.text[:2000])
        raise RuntimeError("Falha na autenticação")

    root = ET.fromstring(response.text)

    print(f"[AUTH] API status={root.attrib.get('status')}")

    if root.attrib.get("status") != "1":
        print(response.text[:2000])
        raise RuntimeError("Autenticação Avaya recusada")


def get_target_users(session):
    url = (
        f"{BASE_URL}/admin/v1/users"
        f"?ipaddress={TARGET_IP}"
    )

    response = session.get(
        url,
        headers=HEADERS,
        verify=VERIFY_SSL,
        timeout=60,
    )

    print(f"[GET] HTTP {response.status_code}")
    print(f"[GET] Target IP={TARGET_IP}")

    if response.status_code != 200:
        print(response.text[:3000])
        raise RuntimeError("GET de usuários falhou")

    root = ET.fromstring(response.text)

    api_status = root.attrib.get("status")

    print(f"[GET] API status={api_status}")

    if api_status != "1":
        print(response.text[:3000])
        raise RuntimeError("Avaya retornou status diferente de 1")

    users = []

    for user in root.findall(".//User"):
        guid = user.attrib.get("GUID", "")

        name = user.findtext("Name", default="")
        extension = user.findtext("Extension", default="")
        sip_name = user.findtext("SIPName", default="")
        sip_contact = user.findtext("SIPContact", default="")

        users.append(
            {
                "guid": guid,
                "name": name,
                "extension": extension,
                "sip_name": sip_name,
                "sip_contact": sip_contact,
            }
        )

    return users


def main():
    print("=" * 70)
    print("VALIDAÇÃO DE PROVISIONAMENTO - EXPANSION")
    print("=" * 70)
    print(f"Target IP : {TARGET_IP}")
    print(f"Name      : {EXPECTED_NAME}")
    print(f"Extension : {EXPECTED_EXTENSION}")
    print(f"GUID      : {EXPECTED_GUID}")
    print("=" * 70)

    with requests.Session() as session:

        authenticate(session)

        users = get_target_users(session)

        print(f"[GET] Usuários encontrados={len(users)}")

        matches = [
            user
            for user in users
            if user["name"] == EXPECTED_NAME
        ]

        if not matches:
            print()
            print("[RESULTADO] ❌ USUÁRIO NÃO ENCONTRADO")
            print()
            print(
                f"O usuário {EXPECTED_NAME} não foi localizado "
                f"na Expansion {TARGET_IP}."
            )
            return 1

        print()
        print("[RESULTADO] Usuário encontrado:")
        print()

        for user in matches:
            print(f"  Name       : {user['name']}")
            print(f"  Extension  : {user['extension']}")
            print(f"  GUID       : {user['guid']}")
            print(f"  SIPName    : {user['sip_name']}")
            print(f"  SIPContact : {user['sip_contact']}")
            print()

        user = matches[0]

        checks = {
            "NAME": user["name"] == EXPECTED_NAME,
            "EXTENSION": user["extension"] == EXPECTED_EXTENSION,
            "GUID": user["guid"].lower() == EXPECTED_GUID.lower(),
            "SIPName": user["sip_name"] == EXPECTED_NAME,
            "SIPContact": user["sip_contact"] == EXPECTED_NAME,
        }

        print("=" * 70)
        print("VALIDAÇÕES")
        print("=" * 70)

        for key, result in checks.items():
            print(f"{key:<12}: {'OK' if result else 'FALHA'}")

        print("=" * 70)

        if all(checks.values()):
            print("✅ VALIDAÇÃO COMPLETA COM SUCESSO")
            print()
            print(
                "O usuário foi confirmado diretamente na "
                f"Expansion {TARGET_IP}."
            )
            print(
                "O GUID retornado pelo POST coincide com o GUID "
                "retornado pelo GET."
            )
            return 0

        print("❌ VALIDAÇÃO PARCIAL/FALHOU")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())