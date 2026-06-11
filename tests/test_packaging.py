"""Guard: schema.sql must ship as package data (regression test).

The container's schema bootstrap reads ghcproxy/db/schema.sql via
importlib.resources; if setuptools drops the .sql file from the wheel the
proxy fails to start. This test fails if the file is not packaged/readable.
"""
import importlib.resources as res


def test_schema_sql_is_packaged_and_has_core_tables():
    text = res.files("ghcproxy.db").joinpath("schema.sql").read_text()
    for table in ("accounts", "users", "api_keys", "bindings",
                  "device_sessions", "usage_rollup"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in text
