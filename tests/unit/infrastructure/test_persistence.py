import pytest

from app.infrastructure import persistence


def test_commit_exception_rolls_back_even_before_session_failure():
    class CommitFailureSession:
        rolled_back = False

        def in_transaction(self):
            return False

        def commit(self):
            raise OSError("connection failed during commit")

        def rollback(self):
            self.rolled_back = True

    session = CommitFailureSession()
    repo = persistence.SQLModelReportRepository(session)
    with pytest.raises(OSError, match="during commit"), repo.transaction():
        pass
    assert session.rolled_back
