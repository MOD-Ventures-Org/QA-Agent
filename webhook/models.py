from pydantic import BaseModel
from typing import List, Optional


class GitHubPushEvent(BaseModel):
    event_type: str
    repo_name: str
    branch: str
    author: str
    commit_messages: List[str]
    changed_files: List[str]
    diff_summary: str
    pr_title: Optional[str] = None


class WebhookPayload(BaseModel):
    raw: dict
