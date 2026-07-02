from unittest.mock import Mock, patch

from aria import clickup


def _resp(json_body):
    r = Mock()
    r.raise_for_status = Mock()
    r.json.return_value = json_body
    return r


def test_find_existing_ticket_matches_signature():
    tasks_body = {"tasks": [{"id": "123", "description": "some text [aria-sig:abcd1234ef] more"}]}
    with patch("aria.clickup.requests.get", return_value=_resp(tasks_body)):
        found = clickup.find_existing_ticket("list1", "token", "abcd1234ef")
    assert found == "123"


def test_find_existing_ticket_returns_none_when_no_match():
    tasks_body = {"tasks": [{"id": "123", "description": "unrelated"}]}
    with patch("aria.clickup.requests.get", return_value=_resp(tasks_body)):
        found = clickup.find_existing_ticket("list1", "token", "abcd1234ef")
    assert found is None


def test_file_ticket_for_run_creates_new_when_none_exists():
    failures = [{"test": "tests/test_x.py::test_fail", "output": "AssertionError"}]
    with patch("aria.clickup.find_existing_ticket", return_value=None), \
         patch("aria.clickup.create_ticket", return_value="999") as create:
        task_id = clickup.file_ticket_for_run("list1", "token", failures, "https://ci/run/1")

    assert task_id == "999"
    create.assert_called_once()
    title, body = create.call_args[0][2], create.call_args[0][3]
    assert "1 generated test(s) failing" in title
    assert "test_fail" in body


def test_file_ticket_for_run_comments_on_existing():
    failures = [{"test": "tests/test_x.py::test_fail", "output": "AssertionError"}]
    with patch("aria.clickup.find_existing_ticket", return_value="555"), \
         patch("aria.clickup.comment_ticket") as comment:
        task_id = clickup.file_ticket_for_run("list1", "token", failures, "https://ci/run/1")

    assert task_id == "555"
    comment.assert_called_once()
