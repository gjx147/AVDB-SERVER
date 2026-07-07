"""内容过滤引擎 —— 规则匹配 + 动作应用。

参考 JavdBviewed contentFilterManager，服务端化：
- 规则存 content_filter_rules 表（可批量/共享/统计）
- evaluate_rule: 按字段匹配（title/actor/maker/video_code），支持正则/关键字
- apply_filters: 对任务列表应用所有规则，返回带标记的结果
- 动作：hide（隐藏）/highlight（高亮）/blur（模糊）/mark（标记）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select

from database import SessionLocal
from models import ContentFilterRule, Task

logger = logging.getLogger("avdb.filter")

DEFAULT_FIELDS = ["title", "actors", "maker", "video_code"]


def _match_keyword(text: str, keyword: str, is_regex: bool, case_sensitive: bool) -> bool:
    """单字段关键字匹配。"""
    if not text or not keyword:
        return False
    flags = 0 if case_sensitive else re.IGNORECASE
    if is_regex:
        try:
            return bool(re.search(keyword, text, flags))
        except re.error:
            return False
    if case_sensitive:
        return keyword in text
    return keyword.lower() in text.lower()


def evaluate_rule(task: Task, rule: ContentFilterRule) -> bool:
    """检查 task 是否命中该规则。"""
    try:
        fields = json.loads(rule.fields_json) if rule.fields_json else DEFAULT_FIELDS
    except Exception:
        fields = DEFAULT_FIELDS

    field_map = {
        "title": task.title,
        "actor": task.actors,
        "actors": task.actors,
        "studio": task.maker,
        "maker": task.maker,
        "video-id": task.video_code,
        "video_code": task.video_code,
    }
    for field in fields:
        value = field_map.get(field, "")
        if _match_keyword(value or "", rule.keyword, rule.is_regex, rule.case_sensitive):
            return True
    return False


def apply_filters(tasks: list[Task], db) -> list[dict]:
    """对任务列表应用所有启用的规则。返回带过滤标记的结果。"""
    rules = db.execute(
        select(ContentFilterRule).where(ContentFilterRule.enabled == True)  # noqa: E712
    ).scalars().all()

    results = []
    for task in tasks:
        actions = []
        matched_rules = []
        hidden = False
        for rule in rules:
            if evaluate_rule(task, rule):
                matched_rules.append(rule.name)
                if rule.action == "hide":
                    hidden = True
                else:
                    actions.append({"action": rule.action, "rule": rule.name, "message": rule.message})
        results.append({
            "task_id": task.id,
            "video_code": task.video_code,
            "title": task.title,
            "hidden": hidden,
            "actions": actions,
            "matched_rules": matched_rules,
        })
    return results


def get_rules(db) -> list[ContentFilterRule]:
    return db.execute(
        select(ContentFilterRule).order_by(ContentFilterRule.id)
    ).scalars().all()
