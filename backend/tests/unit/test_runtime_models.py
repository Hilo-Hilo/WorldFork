from app.db import models


def test_runtime_models_are_exported_with_expected_links():
    tick_execution = models.TickExecution
    checkpoint = models.TickCheckpoint
    execution_node = models.ExecutionNode
    node_attempt = models.NodeAttempt
    intervention = models.Intervention
    replay_session = models.ReplaySession
    operation_log = models.OperationLog

    assert tick_execution.__tablename__ == "tick_executions"
    assert checkpoint.__tablename__ == "tick_checkpoints"
    assert execution_node.__tablename__ == "execution_nodes"
    assert node_attempt.__tablename__ == "node_attempts"
    assert intervention.__tablename__ == "interventions"
    assert replay_session.__tablename__ == "replay_sessions"
    assert operation_log.__tablename__ == "operation_logs"

    assert "execution_node_id" in node_attempt.__table__.c
    assert "tick_execution_id" in execution_node.__table__.c
    assert "checkpoint_order" in checkpoint.__table__.c
    assert "node_attempt_id" in intervention.__table__.c
    assert "replay_session_id" in operation_log.__table__.c
