"""ORM 模型层。

所有表模型集中在此导出，方便 ``from models import Task, Actor, ...``。
Alembic 通过 ``Base.metadata`` 自动发现全部表。
"""

from models.task import Task
from models.list_source import ListSource
from models.actor import Actor, ActorMovie, actor_movies
from models.ranking import Ranking
from models.setting import Setting
from models.log import CrawlLog
from models.subscription import Subscription
from models.new_release import NewRelease
from models.insight import InsightReport
from models.llm_cache import LLMCache, ContentFilterRule
from models.collection import Collection, task_collections
from models.download import Download
from models.notify_log import NotifyLog
from models.rating_history import RatingHistory
from models.rule import Rule
from models.share_token import ShareToken
from models.ai_usage import AiUsage
from models.chat_session import ChatSession, ChatMessage
from models.user_pref import UserPref
from models.agent_action import AgentAction
from models.config_audit import ConfigAudit
from models.user import User

__all__ = [
    "Task",
    "ListSource",
    "Actor",
    "ActorMovie",
    "actor_movies",
    "Ranking",
    "Setting",
    "CrawlLog",
    "Subscription",
    "NewRelease",
    "InsightReport",
    "LLMCache",
    "ContentFilterRule",
    "Collection",
    "task_collections",
    "Download",
    "NotifyLog",
    "User",
    "ConfigAudit",
    "AgentAction",
    "UserPref",
    "ChatSession",
    "ChatMessage",
    "AiUsage",
    "RatingHistory",
    "Rule",
    "ShareToken",
]
