from geo.db import normalize_url

HOST = "aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"


def test_adds_psycopg_driver():
    assert normalize_url(f"postgresql://u:pw@{HOST}") == f"postgresql+psycopg://u:pw@{HOST}"
    assert normalize_url(f"postgres://u:pw@{HOST}") == f"postgresql+psycopg://u:pw@{HOST}"


def test_password_with_special_characters_is_encoded():
    assert normalize_url(f"postgresql://postgres.ref:p@ss:w/rd#9456@{HOST}") == \
        f"postgresql+psycopg://postgres.ref:p%40ss%3Aw%2Frd%239456@{HOST}"


def test_already_encoded_password_is_not_double_encoded():
    assert normalize_url(f"postgresql://u:p%40ss@{HOST}") == f"postgresql+psycopg://u:p%40ss@{HOST}"


def test_query_string_after_db_name_is_untouched():
    url = "postgresql://u:a@b@host:5432/db?sslmode=require"
    assert normalize_url(url) == "postgresql+psycopg://u:a%40b@host:5432/db?sslmode=require"


def test_non_postgres_urls_pass_through():
    assert normalize_url("sqlite:///tmp/x.db") == "sqlite:///tmp/x.db"
