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
    "User",
]
