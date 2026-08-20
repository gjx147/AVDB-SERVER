"""clean xslist leftover urls and requeue profiles

Revision ID: d5e8f4c2b913
Revises: b3c7e2f1a901
Create Date: 2026-08-20 10:00:00.000000

回退 xslist 后遗留数据修复：
- xslist 头像 URL 受 Cloudflare 保护（实测 403），前端直连必然显示错误 → 置空
- xslist source_url 会误导「补齐作品」走 JavDB 爬取失败 → 置空
- 受影响演员重新入资料队列（profile_fetched=0），定时任务自动补头像/资料
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e8f4c2b913'
down_revision: Union[str, None] = 'b3c7e2f1a901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 记下受影响范围（重置队列用）
    op.execute(
        "UPDATE actors SET profile_fetched = 0 "
        "WHERE (avatar_url LIKE '%xslist.org%' OR source_url LIKE '%xslist.org%')"
    )
    op.execute(
        "UPDATE actors SET avatar_url = NULL WHERE avatar_url LIKE '%xslist.org%'"
    )
    op.execute(
        "UPDATE actors SET source_url = NULL WHERE source_url LIKE '%xslist.org%'"
    )


def downgrade() -> None:
    pass  # 数据清理不可逆（无需回滚）
