from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from dotenv import load_dotenv


# ============================================================
# LOCALIZAÇÃO DO PROJETO
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# CARREGA CONFIGURAÇÃO DO .ENV
# ============================================================

ENV_FILE = PROJECT_ROOT / ".env"

if not ENV_FILE.exists():
    print(
        f"[AVISO] Arquivo .env não encontrado: {ENV_FILE}"
    )
else:
    load_dotenv(ENV_FILE)
    print(
        f"[CONFIG] .env carregado: {ENV_FILE}"
    )


# ============================================================
# IMPORTA O CLIENTE
# ============================================================

from app.avaya.client import AvayaIPOfficeAPI


# ============================================================
# CONFIGURAÇÃO DO TESTE
# ============================================================

PRIMARY_IP = "172.16.36.50"
EXPANSION_IP = "172.16.70.10"

TEST_NAMES = {
    "TesteProv01",
    "TesteProv02",
}

# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def print_separator(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def find_names_in_users(users: list[dict]) -> None:
    """
    Procura os usuários de teste no resultado normalizado
    retornado pelo AvayaIPOfficeAPI.
    """

    found = {
        name: []
        for name in TEST_NAMES
    }

    for user in users:
        name = (
            user.get("Name")
            or user.get("name")
            or ""
        )

        name = str(name).strip()

        if name in found:
            found[name].append(user)

    for name in sorted(TEST_NAMES):
        matches = found[name]

        if not matches:
            print(f"[NÃO ENCONTRADO] {name}")
            continue

        print(f"[ENCONTRADO] {name}")

        for user in matches:
            print(
                "  GUID       =",
                user.get("GUID")
                or user.get("guid"),
            )

            print(
                "  Extension  =",
                user.get("Extension")
                or user.get("extension")
                or "",
            )

            print(
                "  SIP        =",
                user.get("SIP")
                or user.get("sip")
                or user.get("SIPName")
                or "",
            )

            print(
                "  Category   =",
                user.get("Category")
                or user.get("category")
                or "",
            )


def print_users_summary(
    label: str,
    users: list[dict],
) -> None:

    print_separator(label)

    print(f"Quantidade de usuários: {len(users)}")

    find_names_in_users(users)


# ============================================================
# TESTE DIRETO DO ENDPOINT /systems
# ============================================================

def test_systems(client: AvayaIPOfficeAPI) -> None:

    print_separator(
        "TESTE 1 - GET /admin/v1/systems"
    )

    path = (
        "/WebManagement/ws/sdk/admin/v1/systems"
    )

    try:

        response = client.request(
            "GET",
            path,
        )

        print(
            "HTTP status:",
            response.status_code,
        )

        print(
            "Content-Type:",
            response.headers.get(
                "Content-Type",
                "",
            ),
        )

        body = response.text

        print(
            "Response length:",
            len(body),
        )

        print()
        print("XML retornado:")
        print(body)

    except Exception as exc:

        print(
            "[ERRO] Falha ao consultar /systems:"
        )

        print(
            repr(exc)
        )


# ============================================================
# TESTE DE USUÁRIOS POR IPADDRESS
# ============================================================

def test_users_by_ip(
    client: AvayaIPOfficeAPI,
    ip_address: str,
) -> None:

    print_separator(
        f"TESTE - GET /admin/v1/users?ipaddress={ip_address}"
    )

    path = (
        "/WebManagement/ws/sdk/admin/v1/users"
        f"?ipaddress={ip_address}"
    )

    try:

        response = client.request(
            "GET",
            path,
        )

        print(
            "HTTP status:",
            response.status_code,
        )

        print(
            "Content-Type:",
            response.headers.get(
                "Content-Type",
                "",
            ),
        )

        body = response.text

        print(
            "Response length:",
            len(body),
        )

        print()

        # ----------------------------------------------------
        # Análise simples do XML
        # ----------------------------------------------------

        try:

            root = ET.fromstring(body)

            print(
                "XML root:",
                root.tag,
            )

            print(
                "API status:",
                root.attrib.get(
                    "status"
                ),
            )

            users = root.findall(
                ".//User"
            )

            print(
                "User nodes encontrados:",
                len(users),
            )

            found_names = set()

            for user in users:

                name_node = user.find(
                    "Name"
                )

                if name_node is None:
                    continue

                name = (
                    name_node.text
                    or ""
                ).strip()

                if name in TEST_NAMES:

                    found_names.add(name)

                    print()
                    print(
                        "[USUÁRIO ENCONTRADO]"
                    )

                    print(
                        "  Name:",
                        name,
                    )

                    print(
                        "  GUID:",
                        user.attrib.get(
                            "GUID",
                            "",
                        ),
                    )

                    extension = user.find(
                        "Extension"
                    )

                    print(
                        "  Extension:",
                        (
                            extension.text
                            if extension is not None
                            else ""
                        ),
                    )

                    sip_name = user.find(
                        "SIPName"
                    )

                    print(
                        "  SIPName:",
                        (
                            sip_name.text
                            if sip_name is not None
                            else ""
                        ),
                    )

            print()

            for name in sorted(
                TEST_NAMES
            ):

                if name in found_names:
                    print(
                        f"[ENCONTRADO] {name}"
                    )
                else:
                    print(
                        f"[NÃO ENCONTRADO] {name}"
                    )

        except ET.ParseError as exc:

            print(
                "[ERRO] Resposta não é XML válido:"
            )

            print(
                repr(exc)
            )

    except Exception as exc:

        print(
            "[ERRO] Falha na consulta:"
        )

        print(
            repr(exc)
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print_separator(
        "AVAYA SOLUTION NODE DIAGNOSTIC"
    )

    print(
        "Projeto:",
        PROJECT_ROOT,
    )

    print(
        "Primary:",
        PRIMARY_IP,
    )

    print(
        "Expansion:",
        EXPANSION_IP,
    )

    print()
    print(
        "Usuários procurados:",
        ", ".join(
            sorted(TEST_NAMES)
        ),
    )

    print()

    # --------------------------------------------------------
    # Inicializa o mesmo cliente usado pelo FastAPI
    # --------------------------------------------------------

    try:

        client = AvayaIPOfficeAPI()

    except Exception as exc:

        print(
            "[ERRO] Não foi possível "
            "inicializar AvayaIPOfficeAPI:"
        )

        print(
            repr(exc)
        )

        sys.exit(1)

    # --------------------------------------------------------
    # 1. Systems
    # --------------------------------------------------------

    test_systems(
        client
    )

    # --------------------------------------------------------
    # 2. Usuários do Primary
    # --------------------------------------------------------

    test_users_by_ip(
        client,
        PRIMARY_IP,
    )

    # --------------------------------------------------------
    # 3. Usuários do Expansion
    # --------------------------------------------------------

    test_users_by_ip(
        client,
        EXPANSION_IP,
    )

    print_separator(
        "FIM DO DIAGNÓSTICO"
    )

    print(
        "Nenhuma operação de criação, alteração "
        "ou exclusão foi executada."
    )


if __name__ == "__main__":
    main()