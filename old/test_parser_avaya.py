from app.avaya.client import AvayaIPOfficeAPI, AvayaHTTPError


def test_xml_valido_com_usuario():
    xml = """
    <response status="1">
        <User GUID="abc-123">
            <Name>Ramal Teste</Name>
            <Extension>2001</Extension>
        </User>
    </response>
    """
    users = AvayaIPOfficeAPI._parse_users(xml)

    assert len(users) == 1
    assert users[0]["GUID"] == "abc-123"
    assert users[0]["Name"] == "Ramal Teste"


def test_status_xml_falha():
    xml = """
    <response status="0">
        <error_desc>Falha simulada</error_desc>
    </response>
    """
    try:
        AvayaIPOfficeAPI._parse_users(xml)
    except AvayaHTTPError:
        return

    raise AssertionError("Era esperado AvayaHTTPError para status XML 0")


def test_xml_vazio():
    try:
        AvayaIPOfficeAPI._parse_users("")
    except AvayaHTTPError:
        return

    raise AssertionError("Era esperado AvayaHTTPError para XML vazio")


def test_xml_invalido():
    try:
        AvayaIPOfficeAPI._parse_users("<response")
    except AvayaHTTPError:
        return

    raise AssertionError("Era esperado AvayaHTTPError para XML inválido")


if __name__ == "__main__":
    tests = [
        test_xml_valido_com_usuario,
        test_status_xml_falha,
        test_xml_vazio,
        test_xml_invalido,
    ]

    failures = 0

    for test in tests:
        try:
            test()
            print(f"PASSOU: {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FALHOU: {test.__name__}: {exc}")

    print(f"\nResultado: {len(tests) - failures}/{len(tests)} testes passaram.")

    if failures:
        raise SystemExit(1)
