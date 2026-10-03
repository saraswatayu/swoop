"""Send and lazy response-body failures stay inside Swoop's error contract."""

from types import SimpleNamespace

import primp
import pytest

from swoop import SwoopTransportError, TransportConfig
import swoop.rpc as rpc


@pytest.mark.parametrize("phase", ["post", "body"])
def test_http_post_wraps_send_and_lazy_body_timeouts(monkeypatch, phase):
    error = primp.TimeoutError(f"{phase} timed out")

    class LazyResponse:
        status_code = 200

        @property
        def text(self):
            raise error

    def post(*args, **kwargs):
        if phase == "post":
            raise error
        return LazyResponse()

    monkeypatch.setattr(rpc, "_get_client", lambda *args: SimpleNamespace(post=post))
    with pytest.raises(SwoopTransportError, match="transport") as raised:
        rpc._http_post(rpc.SHOPPING_RPC_URL, b"body", transport=TransportConfig(retries=0))
    assert raised.value.__cause__ is error


def test_response_body_is_read_once_inside_transport_boundary(monkeypatch):
    reads = []

    class LazyResponse:
        status_code = 200

        @property
        def text(self):
            reads.append(1)
            if len(reads) > 1:
                raise primp.TimeoutError("second lazy read escaped transport boundary")
            return "decoded body"

    monkeypatch.setattr(rpc, "_get_client", lambda *args: SimpleNamespace(post=lambda *args, **kwargs: LazyResponse()))
    response = rpc._http_post(rpc.SHOPPING_RPC_URL, b"body")
    assert response.text == "decoded body"
    assert response.text == "decoded body"
    assert len(reads) == 1


def test_existing_http_error_is_not_reclassified_as_transport(monkeypatch):
    monkeypatch.setattr(rpc, "_get_client", lambda *args: SimpleNamespace(post=lambda *args, **kwargs: SimpleNamespace(status_code=503)))
    with pytest.raises(rpc.SwoopHTTPError) as raised:
        rpc._http_post(rpc.SHOPPING_RPC_URL, b"body", transport=TransportConfig(retries=0))
    assert raised.value.status_code == 503
