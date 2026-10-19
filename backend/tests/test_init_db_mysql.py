"""受控初始化必须在真实独立 MySQL 库中保留记录和关联。"""

from unittest.mock import Mock

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts import init_db


def test_repeated_init_preserves_mysql_records_and_relationships(monkeypatch, mysql_engine):
    from app import db, models
    from app.rag import store

    monkeypatch.setattr(db, "sync_engine", mysql_engine)
    vector = Mock()
    vector.has_collection.return_value = True
    monkeypatch.setattr(store, "get_client", lambda: vector)
    init_db.main([])
    with Session(mysql_engine) as session:
        session.add(models.Plan(id="plan_test", name="test", qps_limit=1, monthly_quota=10))
        session.flush()
        session.add(models.Tenant(id="tenant_test", name="test", plan_id="plan_test"))
        session.flush()
        session.add(models.User(
            id="user_test", tenant_id="tenant_test", username="test_init_user",
            password_hash="synthetic-test-value", role="customer_dev", display_name="test",
        ))
        session.add(models.Conversation(id="conv_test", tenant_id="tenant_test", user_id="user_test"))
        session.flush()
        session.add(models.Message(
            id="msg_test", conversation_id="conv_test", role="user", content="keep this",
            meta={"synthetic": True},
        ))
        session.add(models.Ticket(
            ticket_id="ticket_test", tenant_id="tenant_test", user_id="user_test",
            category="test", priority="P2", title="keep ticket", conversation_id="conv_test",
        ))
        session.commit()

    def snapshot():
        with mysql_engine.connect() as connection:
            return {
                table.name: list(connection.execute(select(table)).mappings())
                for table in db.Base.metadata.sorted_tables
            }

    before = snapshot()
    init_db.main([])
    init_db.main([])
    assert snapshot() == before
    vector.drop_collection.assert_not_called()
    vector.create_collection.assert_not_called()
