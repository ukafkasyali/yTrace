import pytest

from data_sourcing.security import UnsafeSourceUrl, ensure_public_addresses, validate_source_url


@pytest.mark.parametrize(
    "url",
    [
        "http://zenodo.org/records/1",
        "https://localhost/records/1",
        "https://127.0.0.1/records/1",
        "https://github.com:444/owner/repo",
        "https://user:secret@github.com/owner/repo",
        "https://github.com.evil.example/owner/repo",
    ],
)
def test_rejects_unsafe_source_urls(url: str) -> None:
    with pytest.raises(UnsafeSourceUrl):
        validate_source_url(url)


def test_accepts_allowlisted_https_source() -> None:
    assert validate_source_url("https://zenodo.org/records/21941203")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_rejects_non_public_dns_answers(address: str) -> None:
    with pytest.raises(UnsafeSourceUrl):
        ensure_public_addresses([address])
