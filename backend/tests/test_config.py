from app.core.config import Settings


def test_database_url_is_built_from_postgres_settings() -> None:
    settings = Settings(
        _env_file=None,
        database_url="",
        postgres_user="test user",
        postgres_password="p@ssword",
        postgres_host="localhost",
        postgres_port=55432,
        postgres_db="test database",
    )

    assert settings.database_url == (
        "postgresql+psycopg://test+user:p%40ssword"
        "@localhost:55432/test+database"
    )


def test_explicit_database_url_takes_precedence() -> None:
    explicit_url = "postgresql+psycopg://user:password@database:5432/example"

    settings = Settings(_env_file=None, database_url=explicit_url)

    assert settings.database_url == explicit_url
