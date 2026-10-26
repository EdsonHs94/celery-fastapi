"""Comprueba con un fork real que el hijo no reutiliza el socket del padre."""

import multiprocessing

from sqlalchemy import text


def test_forked_process_uses_its_own_connection(engine):
    from app.bootstrap.database import Database

    database = Database(engine.url)
    try:
        with database.session() as session:
            parent_pid = session.execute(text("select pg_backend_pid()")).scalar_one()
        context = multiprocessing.get_context("fork")
        receiver, sender = context.Pipe(duplex=False)

        def child():
            receiver.close()
            try:
                with database.session() as session:
                    sender.send(
                        session.execute(text("select pg_backend_pid()")).scalar_one()
                    )
            finally:
                database.dispose()
                sender.close()

        process = context.Process(target=child)
        process.start()
        sender.close()
        try:
            assert receiver.poll(10), "El hijo no obtuvo una conexión propia"
            child_pid = receiver.recv()
            process.join(timeout=10)
            assert process.exitcode == 0
            assert child_pid != parent_pid
            with database.session() as session:
                assert (
                    session.execute(text("select pg_backend_pid()")).scalar_one()
                    == parent_pid
                )
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            receiver.close()
    finally:
        database.dispose()
