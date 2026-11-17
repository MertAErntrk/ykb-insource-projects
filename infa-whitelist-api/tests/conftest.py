from app.config import Settings

KEY_1 = "k1-" + "a" * 40
KEY_2 = "k2-" + "b" * 40


def make_settings(**over) -> Settings:
    """Yerel .env okunmaz (_env_file=None); testler gerçek ayara bağlı kalmaz."""
    base = dict(db_user="u", db_password="p", db_dsn="h:1521/s", api_keys=[KEY_1, KEY_2])
    base.update(over)
    return Settings(_env_file=None, **base)
