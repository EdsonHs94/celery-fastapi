import pytest


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:secret@localhost/production",
        "sqlite:///unit_test",
    ],
)
def test_cleanup_rejects_non_test_database(monkeypatch, request, url):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    with pytest.raises(pytest.UsageError, match="_test"):
        request.getfixturevalue("migration_config")


def test_missing_explicit_test_url_skips_without_using_database_url(
    monkeypatch, request
):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:secret@localhost/production"
    )
    with pytest.raises(pytest.skip.Exception, match="TEST_DATABASE_URL"):
        request.getfixturevalue("migration_config")
