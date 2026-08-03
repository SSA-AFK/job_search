from app.cache.keys import list_key


def test_list_key_is_order_independent() -> None:
    assert list_key({"city": "Beijing", "q": "AI"}, version=3) == list_key(
        {"q": "AI", "city": "Beijing"}, version=3
    )
