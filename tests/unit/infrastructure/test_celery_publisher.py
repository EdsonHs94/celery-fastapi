from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest

from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app


def test_publisher_uses_dedicated_connection_and_only_json_uuid(caplog):
    from app.infrastructure.celery_publisher import CeleryReportTaskPublisher

    app = MagicMock()
    report_id = uuid4()
    app.send_task.return_value.get.side_effect = AssertionError("must not wait")
    with caplog.at_level("INFO"):
        assert CeleryReportTaskPublisher(app).publish(report_id) is None
    app.connection_for_write.return_value.__enter__.return_value.ensure_connection.assert_called_once_with(
        max_retries=0
    )
    options = app.send_task.call_args.kwargs
    assert options["args"] == [str(report_id)]
    assert options["kwargs"] == {}
    assert options["queue"] == "reports"
    assert options["serializer"] == "json"
    assert options["ignore_result"] is True
    assert options["retry"] is False
    assert options["timeout"] == options["confirm_timeout"] == 5
    assert options["producer"] is not None
    app.send_task.return_value.get.assert_not_called()
    assert str(report_id) in caplog.text and "stage=published" in caplog.text


def test_publisher_propagates_publication_error(caplog):
    from app.infrastructure.celery_publisher import CeleryReportTaskPublisher

    app = MagicMock()
    error = OSError("private broker credentials")
    app.send_task.side_effect = error
    report_id = uuid4()
    with pytest.raises(OSError) as raised:
        CeleryReportTaskPublisher(app).publish(report_id)
    assert raised.value is error
    assert str(report_id) in caplog.text
    assert "private broker" not in caplog.text


def test_transport_constructs_amqp_connection_with_timeouts_and_confirms():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@db/reports",
        broker_url="amqp://u:p@broker/reports",
    )
    with create_celery_app(settings) as app:
        with app.connection_for_write() as connection:
            transport = connection.transport
            transport.Connection = Mock()
            transport.establish_connection()
            options = transport.Connection.call_args.kwargs
            assert options["connect_timeout"] == 5
            assert options["read_timeout"] == options["write_timeout"] == 5
            assert options["confirm_publish"] is True
        assert app.conf.task_publish_retry is False
        assert app.conf.broker_pool_limit == 0
        assert app.conf.worker_concurrency == app.conf.worker_prefetch_multiplier == 1
        queue = app.amqp.queues["reports"]
        assert queue.durable
        assert app.conf.task_default_delivery_mode == "persistent"
