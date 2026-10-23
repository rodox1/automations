import pytest

from app.avaya.client import AvayaIPOfficeAPI, AvayaHTTPError


def test_parse_users_accepts_successful_response():
    xml = """<response status="1">
        <User GUID="guid-001">
            <Name>Ramal Teste</Name>
        </User>
    </response>"""

    users = AvayaIPOfficeAPI._parse_users(xml)

    assert len(users) == 1
    assert users[0]["GUID"] == "guid-001"


def test_parse_users_rejects_api_failure_even_if_xml_is_valid():
    xml = """<response status="0">
        <error_desc>Falha simulada</error_desc>
    </response>"""

    with pytest.raises(AvayaHTTPError):
        AvayaIPOfficeAPI._parse_users(xml)


@pytest.mark.parametrize("xml", ["", "   ", "<response>"])
def test_parse_users_rejects_empty_or_malformed_xml(xml):
    with pytest.raises(AvayaHTTPError):
        AvayaIPOfficeAPI._parse_users(xml)


def test_parse_users_rejects_unexpected_root():
    xml = "<html><User GUID='guid-001'/></html>"

    with pytest.raises(AvayaHTTPError):
        AvayaIPOfficeAPI._parse_users(xml)


def test_parse_users_allows_successful_empty_response():
    # O parser aceita status=1 sem usu?rios.
    # A prote??o do cache deve impedir a sincroniza??o vazia.
    xml = '<response status="1"></response>'

    assert AvayaIPOfficeAPI._parse_users(xml) == []


def test_update_user_puts_guid_in_xml_body_not_query_params(monkeypatch):
    api = AvayaIPOfficeAPI(host="192.0.2.10")
    sent = {}

    class FakeResponse:
        status_code = 200
        text = '<response status="1" />'

    def fake_request(method, url, **kwargs):
        sent.update(method=method, url=url, **kwargs)
        return FakeResponse()

    monkeypatch.setattr(api, "request", fake_request)
    api.update_user('<data><ws_object><User><Name>New</Name></User></ws_object></data>', guid="guid-001", target_ip="192.0.2.10")

    assert sent["method"] == "PUT"
    assert sent["params"] == {"ipaddress": "192.0.2.10"}
    assert 'User GUID="guid-001"' in sent["data"]
