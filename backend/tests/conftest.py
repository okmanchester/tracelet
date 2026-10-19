"""真实 MySQL 测试只使用显式管理连接创建的随机独立库。"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def mysql_engine():
    admin_url = os.environ.get("TRACELET_TEST_MYSQL_ADMIN_URL")
    if not admin_url:
        pytest.skip("需显式提供 TRACELET_TEST_MYSQL_ADMIN_URL（有建库权限的测试 MySQL）")
    url = make_url(admin_url)
    if url.drivername != "mysql+pymysql":
        pytest.fail("测试管理连接必须使用 mysql+pymysql")
    database = f"tracelet_init_test_{uuid4().hex}"
    admin = create_engine(url.set(database=""), connect_args={"connect_timeout": 5})
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
        created = True
        engine = create_engine(url.set(database=database), connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT DATABASE()")) == database
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                with admin.connect() as connection:
                    connection.execute(text(f"DROP DATABASE `{database}`"))
        finally:
            admin.dispose()
