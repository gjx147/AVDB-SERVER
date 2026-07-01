"""ORM 模型层。

所有表模型集中在此导出，方便 ``from models import Task, Actor, ...``。
Alembic 通过 ``Base.metadata`` 自动发现全部表。
"""

from models.task import Task
from models.list_source import ListSource
from models.actor import Actor, ActorMovie
from models.ranking import Ranking
from models.setting import Setting
from models.log import CrawlLog

__all__ = [
    "Task",
    "ListSource",
    "Actor",
    "ActorMovie",
    "Ranking",
    "Setting",
    "CrawlLog",
]
