from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from dotenv import load_dotenv


# ============================================================
# 1. Ambiente
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 2. Imports do projeto
# ============================================================

from app.avaya.client import AvayaIPOfficeAPI
from app.avaya.provisioning import AvayaProvisioning


# ============================================================
# 3. Configuração do teste
# ============================================================

PRIMARY_IP = "172.16.36.50"
EXPANSION_IP = "172.16.70.10"

TEST_NAME = "TesteProv03"
TEST_EXTENSION = "1334"

# IMPORTANTE:
# O teste será explicitamente direcionado ao Primary.
TARGET_IP = PRIMARY_IP


# ============================================================
# 4. Funções auxiliares
# ============================================================

def local_name(tag: str) -> str:
    """
    Remove namespace XML, caso exista.
    """
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def find_text(element, name: str):
    """
    Procura o primeiro elemento XML pelo nome local.
    """
    for node in element.iter():
        if local_name(node.tag) == name:
            return node.text
    return None


def find_user_by_name(xml_text: str, username: str):
    """
    Procura um <User> cujo <Name> corresponda ao nome informado.
    """
    root = ET.fromstring(xml_text)

    for user in root.iter():
        if local_name(user.tag) != "User":
            continue

        name = None

        for child in user:
            if local_name(child.tag) == "Name":
                name = (child.text or "").strip()
                break

        if name == username:
            return {
                "name": name,
                "guid": user.attrib.get("GUID"),
            }

    return None


def list_users(xml_text: str):
    """
    Retorna uma lista simplificada dos usuários encontrados.
    """
    root = ET.fromstring(xml_text)

    users = []

    for user in root.iter():
        if local_name(user.tag) != "User":
            continue

        item = {
            "guid": user.attrib.get("GUID"),
            "name": None,
            "extension": None,
        }

        for child in user:
            tag = local_name(child.tag)

            if tag == "Name":
                item["name"] = (child.text or "").strip()

            elif tag == "Extension":
                item["extension"] = (child.text or "").strip()

        users.append(item)

    return users


def query_users(client, ipaddress: str):
    """
    Consulta usuários de um IPO específico.
    """
    path = f"/WebManagement/ws/sdk/admin/v1/users?ipaddress={ipaddress}"

    print()
    print("=" * 70)
    print(f"CONSULTANDO USUÁRIOS")
    print(f"IPO: {ipaddress}")
    print(f"PATH: {path}")
    print("=" * 70)

    response = client.request("GET", path)

    print(f"HTTP Status : {response.status_code}")
    print(f"XML Length  : {len(response.text):,} bytes")

    try:
        root = ET.fromstring(response.text)
        print(f"API Status  : {root.attrib.get('status')}")
    except Exception as exc:
        print(f"[WARN] Não foi possível interpretar XML: {exc}")

    return response.text


# ============================================================
# 5. Execução
# ============================================================

def main():

    print()
    print("=" * 70)
    print("TESTE DIRECIONADO DE PROVISIONAMENTO AVAYA")
    print("=" * 70)

    print(f"Projeto       : {PROJECT_ROOT}")
    print(f"Primary       : {PRIMARY_IP}")
    print(f"Expansion     : {EXPANSION_IP}")
    print(f"Nome teste    : {TEST_NAME}")
    print(f"Extensão teste: {TEST_EXTENSION}")
    print(f"Destino POST  : {TARGET_IP}")

    print()
    print("[INFO] Inicializando AvayaIPOfficeAPI...")

    client = AvayaIPOfficeAPI()

    # ========================================================
    # 6. Construção do XML
    # ========================================================

    provisioning = AvayaProvisioning(client)

    xml_bytes = provisioning.build_user_xml(
    name=TEST_NAME,
    password="AvayaTest#2026",
    full_name=TEST_NAME,
    extension=TEST_EXTENSION,

    voicemail_email="",
    xdirectory=False,
    voicemail_on=True,
    voicemail_code="",

    receptionist=False,
    assigned_package=1,
    remote_worker=False,
    twinning_type=0,
    mobility_features=False,
    ums_web_services=False,
    flare_enabled=False,

    login_code="123456",

    sip_name=TEST_NAME,
    sip_contact=TEST_NAME,

    web_collaboration=False,
    )
    print()
    print("=" * 70)
    print("XML GERADO")
    print("=" * 70)

    # Não há credenciais aqui.
    xml_display = xml_bytes.decode("utf-8", errors="replace")

    xml_display = xml_display.replace(
    "<Password>AvayaTest#2026</Password>",
    "<Password>********</Password>",
    )

    print(xml_display)

    # ========================================================
    # 7. POST DIRECIONADO
    # ========================================================

    target_path = (
        "/WebManagement/ws/sdk/admin/v1/users"
        f"?ipaddress={TARGET_IP}"
    )

    print()
    print("=" * 70)
    print("EXECUTANDO POST DIRECIONADO")
    print("=" * 70)

    print(f"Destino: {target_path}")

    response = client.request(
        "POST",
        target_path,
        data=xml_bytes,
        headers={
            "Content-Type": "application/xml",
            "Accept": "application/xml",
            "X-User-Client": "Avaya-WebAdmin",
            "X-User-Agent": "Avaya-SDKUser",
        },
    )

    print()
    print(f"HTTP Status : {response.status_code}")
    print(f"Response Len: {len(response.text):,} bytes")

    # ========================================================
    # 8. Resposta da API
    # ========================================================

    print()
    print("=" * 70)
    print("RESPOSTA DA API")
    print("=" * 70)

    print(response.text)

    guid = None
    api_status = None

    try:
        root = ET.fromstring(response.text)

        api_status = root.attrib.get("status")

        print()
        print(f"API Status: {api_status}")

        # Procurar GUID em qualquer User
        for user in root.iter():
            if local_name(user.tag) == "User":
                guid = user.attrib.get("GUID")
                if guid:
                    break

    except Exception as exc:
        print(f"[ERROR] Falha interpretando resposta: {exc}")

    print()
    print("=" * 70)
    print("RESULTADO DO POST")
    print("=" * 70)

    print(f"API Status : {api_status}")
    print(f"GUID       : {guid}")

    if api_status != "1":
        print()
        print("[ERRO] O Avaya não confirmou a criação.")
        print("[INFO] Nenhuma conclusão sobre o IPO de destino será feita.")
        return

    print()
    print("[OK] O Avaya confirmou a operação.")

    # ========================================================
    # 9. Consultar Primary
    # ========================================================

    primary_xml = query_users(
        client,
        PRIMARY_IP,
    )

    primary_user = find_user_by_name(
        primary_xml,
        TEST_NAME,
    )

    # ========================================================
    # 10. Consultar Expansion
    # ========================================================

    expansion_xml = query_users(
        client,
        EXPANSION_IP,
    )

    expansion_user = find_user_by_name(
        expansion_xml,
        TEST_NAME,
    )

    # ========================================================
    # 11. Resultado final
    # ========================================================

    print()
    print()
    print("#" * 70)
    print("RESULTADO FINAL DO DIAGNÓSTICO")
    print("#" * 70)

    print()
    print(f"Usuário testado : {TEST_NAME}")
    print(f"GUID retornado  : {guid}")
    print(f"POST direcionado: {TARGET_IP}")

    print()
    print("Primary 172.16.36.50:")
    print(f"  Encontrado: {primary_user}")

    print()
    print("Expansion 172.16.70.10:")
    print(f"  Encontrado: {expansion_user}")

    print()

    if primary_user and expansion_user:
        print("[ATENÇÃO]")
        print("O usuário foi localizado nos DOIS IPOs pela API.")

    elif primary_user:
        print("[RESULTADO]")
        print("O usuário foi localizado no PRIMARY 172.16.36.50.")

    elif expansion_user:
        print("[RESULTADO]")
        print("O usuário foi localizado no EXPANSION 172.16.70.10.")

    else:
        print("[RESULTADO]")
        print("O usuário não foi localizado em nenhum dos dois GETs.")

    print()
    print("#" * 70)
    print("FIM DO TESTE")
    print("#" * 70)


if __name__ == "__main__":
    main()