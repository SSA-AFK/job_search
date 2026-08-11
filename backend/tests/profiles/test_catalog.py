from app.profiles.catalog import PROFILE_FIELD_CATALOG, PROFILE_FIELDS_BY_KEY


def test_catalog_has_unique_keys_and_funding_requires_two_sources() -> None:
    assert len(PROFILE_FIELD_CATALOG) == len(PROFILE_FIELDS_BY_KEY)
    assert all(field.key.count(".") >= 1 for field in PROFILE_FIELD_CATALOG)
    assert all(
        field.minimum_sources_for_verified == 2
        for field in PROFILE_FIELD_CATALOG
        if field.category == "financing"
    )
