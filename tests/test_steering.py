"""Saying something to a job while it runs.

Stopping it was the only thing that could be said to a task in flight.
So a wrong turn had to be watched all the way to the end and then asked
for again from the start - which is not how anybody talks to somebody
doing something for them.
"""

from src.brain.agent import TaskAgent
from src.brain.tasks import TaskQueue
from src.tools.task_tool import SteerTaskTool


def _queue_with_running(goal="Open Steam and search for Hades."):
    queue = TaskQueue()
    task_id = queue.submit(goal, source="voice")
    queue._records[task_id].status = "running"
    return queue, task_id


# ------------------------------------------------------- the mailbox


def test_something_said_to_a_running_job_is_kept():
    queue, _ = _queue_with_running()

    assert queue.steer("not that one, Hollow Knight") is True
    assert queue._take_steers() == ["not that one, Hollow Knight"]


def test_it_is_read_once():
    """The agent keeps what it read in its own log after that."""
    queue, _ = _queue_with_running()
    queue.steer("the other one")

    queue._take_steers()

    assert queue._take_steers() == []


def test_there_is_nothing_to_say_it_to_when_nothing_runs():
    """Which is how the caller knows to treat it as a new request."""
    queue = TaskQueue()
    queue.submit("something", source="voice")   # queued, not running

    assert queue.steer("the other one") is False


def test_empty_words_are_not_steering():
    queue, _ = _queue_with_running()

    assert queue.steer("   ") is False


def test_a_new_job_does_not_inherit_the_last_one_s_corrections():
    queue, _ = _queue_with_running()
    queue.steer("not that one")

    # what run() does when it picks up the next job
    queue._take_steers()

    assert queue._take_steers() == []


def test_the_running_job_can_be_named():
    queue, _ = _queue_with_running("Open Steam and search for Hades.")

    assert queue.current().goal == "Open Steam and search for Hades."
    assert queue.running() is True


# ------------------------------------------------- the agent reads it


def _agent(said):
    agent = TaskAgent.__new__(TaskAgent)
    agent._steers = lambda: list(said)
    agent._said_since = []
    return agent


def test_the_agent_puts_it_in_front_of_the_model():
    told = _agent(["use the 1.21.11 instance"])._heard()

    assert "use the 1.21.11 instance" in told


def test_it_still_holds_three_steps_later():
    """"Not that one, the other one" is not advice about one click."""
    agent = _agent(["not that one"])

    agent._heard()
    agent._steers = lambda: []          # nothing new said since

    assert "not that one" in agent._heard()


def test_the_same_thing_said_twice_is_not_repeated():
    agent = _agent(["stop opening chrome"])

    agent._heard()
    agent._steers = lambda: ["stop opening chrome"]

    assert agent._heard().count("stop opening chrome") == 1


def test_nothing_said_means_nothing_in_the_prompt():
    assert _agent([])._heard() == ""


def test_a_broken_mailbox_does_not_take_the_task_down():
    agent = TaskAgent.__new__(TaskAgent)
    agent._steers = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    agent._said_since = []

    assert agent._heard() == ""


# ----------------------------------------------------------- the tool


def test_the_tool_passes_it_on():
    queue, _ = _queue_with_running()

    answer = SteerTaskTool(queue).execute({"said": "the other one"})

    assert answer["status"] == "success"
    assert queue._take_steers() == ["the other one"]


def test_the_tool_says_when_there_is_nothing_to_steer():
    """So the caller does the thing instead of dropping it."""
    answer = SteerTaskTool(TaskQueue()).execute({"said": "the other one"})

    assert answer["status"] == "not_running"
    assert "new request" in answer["error"]


def test_the_executor_is_told_the_person_wins():
    from src.brain.agent import _EXEC_SYSTEM

    assert "THE USER HAS SINCE SAID" in _EXEC_SYSTEM
    assert "the person wins" in _EXEC_SYSTEM
